#!/usr/bin/env python3
"""
Persistent inference daemon — the "muscles" layer of the orchestration scheme.

One process owns the robot's serial port and the cameras for the whole run.
A trained LeRobot policy (ACT / Diffusion / ...) is loaded once into memory and
can be hot-swapped between steps without dropping the hardware connection.

It is driven line-by-line over stdin, so both branches of the thesis experiment
(monolithic baseline and orchestrated per-step models) run through exactly the
same execution stack — only the loaded weights differ.

stdin commands
    SET_POLICY:<path>   hot-swap weights, keeping robot + cameras connected
    SET_TASK:<task>     start executing a task (policy is conditioned on it)
    SNAP                emit one base64 JPEG of the current camera frame
    STOP                freeze motors, back to WAITING
    QUIT                exit

stdout markers (parsed by orchestrator.py)
    [STATUS] DAEMON_READY: mode=HARDWARE|SIMULATED
    [STATUS] POLICY_LOADED: <path>
    [STATUS] POLICY_ERROR: <message>
    [STATUS] TASK_STARTED: <task>
    [STATUS] TASK_DONE: <task> | <reason>
    [STATUS] TASK_STOPPED
    [SNAPSHOT] <base64 jpeg>
    [TELEMETRY] joints:... | target:... | load:... | baseline:... | settle:n/5

Step termination (why a step ends without anybody telling it to):
    Protocol A — all joints settled under 0.005 rad from the target for 5
                 consecutive frames (the motion is finished).
    Protocol B — gripper servo load/current risen above the idle baseline
                 (contact with an object), used for grasping steps so the
                 policy does not keep squeezing. Reads Present_Load or
                 Present_Current, whichever the hardware actually populates.
Once a step ends (either protocol, a timeout, or an explicit STOP) the arm's
current position is captured and re-issued every tick until the next SET_TASK
(see freeze_robot()) — otherwise whatever the orchestrator's LLM/VLM round
trip between steps takes, the arm just sits on the last policy-predicted
target, which may be mid-motion (e.g. a gripper that hadn't finished closing
when a timeout fired) rather than a stable hold.

Without LeRobot / torch / hardware the daemon falls back to a simulated arm so
the whole pipeline stays testable on a laptop.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from copy import copy
from pathlib import Path
from typing import Any

os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")

# Výstup daemonu čte orchestrátor jako UTF-8. Bez tohohle by Python na Windows
# poslal do roury kódování konzole (cp1250) a české hlášky by v logu na webu
# dorazily rozsypané — a znak mimo cp1250 by proces rovnou shodil.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] daemon: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("daemon")

try:
    import torch
    TORCH_OK = True
except ImportError:
    torch = None  # type: ignore
    TORCH_OK = False

# ── LeRobot imports (helper modules move between 0.5.x and 0.6.x) ────────────
LEROBOT_OK = False


def _import_first(*candidates: tuple[str, str]):
    """Return the first (module, attribute) pair that resolves."""
    last_err: Exception | None = None
    for module_name, attr_name in candidates:
        try:
            mod = __import__(module_name, fromlist=[attr_name])
            return getattr(mod, attr_name)
        except (ImportError, AttributeError) as e:
            last_err = e
    assert last_err is not None
    raise last_err


try:
    from lerobot.configs.policies import PreTrainedConfig
    # lerobot/robots/__init__.py only exports RobotConfig itself — it does NOT
    # import the concrete robot submodules, so @RobotConfig.register_subclass
    # decorators (e.g. "so101_follower" in robots/so_follower) never run unless
    # something imports those submodules explicitly. Without this, get_choice_class
    # raises KeyError and connect_robot() silently falls back to SIMULATED mode.
    from lerobot.robots import (  # noqa: F401 — registers subclasses
        RobotConfig,
        bi_openarm_follower,
        bi_rebot_b601_follower,
        bi_so_follower,
        hope_jr,
        koch_follower,
        lekiwi,
        omx_follower,
        openarm_follower,
        rebot_b601_follower,
        so_follower,
    )
    from lerobot.robots.utils import make_robot_from_config

    get_policy_class = _import_first(
        ("lerobot.policies.factory", "get_policy_class"),
        ("lerobot.policies", "get_policy_class"),
    )
    make_pre_post_processors = _import_first(
        ("lerobot.policies", "make_pre_post_processors"),
        ("lerobot.policies.factory", "make_pre_post_processors"),
    )

    hw_to_dataset_features = _import_first(
        ("lerobot.utils.feature_utils", "hw_to_dataset_features"),
        ("lerobot.datasets.feature_utils", "hw_to_dataset_features"),
        ("lerobot.datasets.utils", "hw_to_dataset_features"),
    )
    build_dataset_frame = _import_first(
        ("lerobot.utils.feature_utils", "build_dataset_frame"),
        ("lerobot.datasets.feature_utils", "build_dataset_frame"),
        ("lerobot.datasets.utils", "build_dataset_frame"),
    )
    prepare_observation_for_inference = _import_first(
        ("lerobot.policies.utils", "prepare_observation_for_inference"),
        ("lerobot.utils.control_utils", "prepare_observation_for_inference"),
    )
    # 0.6.x ships the two helpers the official rollout loop uses; on older
    # versions we rebuild the same steps by hand further below.
    try:
        from lerobot.policies.utils import build_inference_frame, make_robot_action
    except (ImportError, AttributeError):
        build_inference_frame = None  # type: ignore
        make_robot_action = None  # type: ignore
    LEROBOT_OK = True
except (ImportError, AttributeError) as e:  # pragma: no cover - env dependent
    log.warning("LeRobot unavailable (%s) — simulated mode only.", e)


# ── Termination protocol settings ───────────────────────────────────────────
# Defaults; every one of them is overridable from the command line because the
# right values are task- and hardware-specific. A different gripper, a heavier
# object or a task with completely different steps needs different thresholds,
# and some tasks have no grasping phase at all — hence the on/off switches.
PROTOCOL_A_THRESHOLD = 0.005   # rad
PROTOCOL_A_PATIENCE = 5        # consecutive frames
PROTOCOL_B_LOAD_LIMIT = 280.0  # mA rise over the idle baseline (see idle_load_baseline)
use_protocol_a = True
use_protocol_b = True
# Pozn.: dřív tu byla záložní heuristika GRASP_WORDS, která úchopový krok
# poznala podle názvu, když ho orchestrátor neoznačil příznakem |grasp.
# Odstraněna záměrně — hádat protokol ukončení z názvu kroku je u univerzálního
# robota s libovolně pojmenovanými kroky spíš past než pomoc; jediným zdrojem
# pravdy je teď zaškrtnutý „úchop" v konfiguraci, který dorazí jako |grasp.

# ── Mutable daemon state ────────────────────────────────────────────────────
state = "WAITING"
active_task = ""
active_is_grasp = False   # ukončovat krok protokolem B (sevření objektu)?
robot = None
policy = None
preprocessor = None
postprocessor = None
obs_features = None
dataset_features: dict = {}
action_keys: list[str] = []
device = "cpu"
current_policy_path = ""
simulated = True
use_triggers = True
task_deadline = 0.0
hold_action: dict | None = None  # last commanded position, re-sent while WAITING to hold torque

# Present_Current/Present_Load are raw register values, not calibrated mA —
# an absolute threshold silently assumes a unit that may not hold on a given
# servo/firmware. Comparing the RISE over an idle baseline sidesteps that: it
# doesn't matter what "0" means, only how far the reading has moved from it.
# The baseline is sampled during the first WAITING stretch of the run (gripper
# open, nothing grasped yet) and then frozen — later WAITING periods can occur
# mid-task with an object already held, so they must not be allowed to drag
# the baseline upward.
idle_load_baseline: float | None = None
_any_task_started = False

_frame_lock = threading.Lock()
last_frames: dict[str, np.ndarray] = {}


# ── Policy / robot bootstrap ────────────────────────────────────────────────

def resolve_policy_dir(path: str) -> str:
    """Accept either a pretrained model dir or a training output dir.

    `lerobot-train --output_dir=X` writes the loadable model to
    `X/checkpoints/last/pretrained_model`, so both spellings are accepted and
    the user never has to remember which one inference wants.
    """
    p = Path(path)
    if (p / "config.json").exists():
        return str(p)
    nested = p / "checkpoints" / "last" / "pretrained_model"
    if (nested / "config.json").exists():
        return str(nested)
    return str(p)


def load_policy(policy_path: str, dev: str) -> None:
    """Load a pretrained policy plus its pre/post-processing pipelines."""
    global policy, preprocessor, postprocessor, current_policy_path
    resolved = resolve_policy_dir(policy_path)
    cfg = PreTrainedConfig.from_pretrained(resolved)
    cfg.pretrained_path = resolved
    policy = get_policy_class(cfg.type).from_pretrained(resolved)
    policy.to(dev)
    policy.eval()
    try:
        preprocessor, postprocessor = make_pre_post_processors(
            policy_cfg=cfg, pretrained_path=resolved)
    except Exception as e:
        log.warning("Processor pipelines unavailable (%s) — using raw select_action.", e)
        preprocessor, postprocessor = None, None
    current_policy_path = policy_path
    log.info("Policy '%s' loaded from %s onto %s.", cfg.type, resolved, dev)


def connect_robot(robot_type: str, port: str, robot_id: str, cameras_json: str) -> None:
    """Connect the follower arm (and its cameras) through LeRobot."""
    global robot, obs_features, dataset_features, action_keys
    cfg_cls = RobotConfig.get_choice_class(robot_type)
    kwargs: dict = {"port": port, "id": robot_id}

    if cameras_json:
        try:
            from lerobot.cameras.opencv.configuration_opencv import OpenCVCameraConfig
            cams = {}
            for name, c in json.loads(cameras_json).items():
                cams[name] = OpenCVCameraConfig(
                    index_or_path=c.get("index_or_path", 0),
                    width=c.get("width", 640),
                    height=c.get("height", 480),
                    fps=c.get("fps", 30),
                )
            kwargs["cameras"] = cams
        except Exception as e:
            log.warning("Camera config parse failed (%s) — connecting without cameras.", e)

    try:
        robot_cfg = cfg_cls(**kwargs)
    except TypeError:
        kwargs.pop("port", None)  # network robots take no serial port
        robot_cfg = cfg_cls(**kwargs)

    robot = make_robot_from_config(robot_cfg)
    robot.connect()
    try:
        bus = getattr(robot, "bus", None)
        if bus is not None:
            c_val = bus.read("Present_Current", "gripper") if hasattr(bus, "read") else None
            l_val = bus.read("Present_Load", "gripper") if hasattr(bus, "read") else None
            log.info("Feetech motor bus test read — Present_Current: %s, Present_Load: %s", c_val, l_val)
    except Exception as e:
        log.warning("Feetech motor bus test read failed: %s", e)

    # Same feature dict the official rollout loop builds: observation features
    # pick the right keys out of the raw observation, action features name the
    # columns of the policy's output.
    obs_features = hw_to_dataset_features(robot.observation_features, "observation")
    action_features = hw_to_dataset_features(robot.action_features, "action")
    dataset_features = {**action_features, **obs_features}
    action_keys = list(robot.action_features)
    log.info("Robot '%s' connected on %s (%d action dims).", robot_type, port, len(action_keys))


# ── Camera snapshot (the daemon owns the camera, so the VLM asks it) ────────

def cache_frame(obs: dict) -> None:
    global last_frames
    new_frames = {}
    for key, value in obs.items():
        arr = value
        if hasattr(arr, "cpu") and hasattr(arr, "numpy"):
            try:
                arr = arr.cpu().numpy()
            except Exception:
                continue
        if isinstance(arr, np.ndarray) and arr.ndim == 3:
            if arr.shape[0] in (1, 3, 4) and arr.shape[2] not in (1, 3, 4):
                arr = np.transpose(arr, (1, 2, 0))
            if arr.shape[2] in (1, 3, 4):
                if arr.dtype != np.uint8:
                    if arr.max() <= 1.0:
                        arr = (arr * 255.0).clip(0, 255).astype(np.uint8)
                    else:
                        arr = arr.clip(0, 255).astype(np.uint8)
                cam_name = key.split(".")[-1]
                new_frames[cam_name] = arr
    if new_frames:
        with _frame_lock:
            last_frames.update(new_frames)


def snapshot_b64() -> str:
    """Return JSON string mapping camera names to base64 JPEGs for all active cameras."""
    with _frame_lock:
        frames = dict(last_frames)

    if not frames and robot is not None and getattr(robot, "cameras", None):
        try:
            for name, cam in robot.cameras.items():
                cam_frame = cam.async_read() if hasattr(cam, "async_read") else cam.read()
                if cam_frame is not None:
                    if hasattr(cam_frame, "cpu") and hasattr(cam_frame, "numpy"):
                        cam_frame = cam_frame.cpu().numpy()
                    if isinstance(cam_frame, np.ndarray) and cam_frame.ndim == 3:
                        if cam_frame.shape[0] in (1, 3, 4) and cam_frame.shape[2] not in (1, 3, 4):
                            cam_frame = np.transpose(cam_frame, (1, 2, 0))
                        frames[name] = cam_frame
        except Exception as e:
            log.warning("Direct camera read failed: %s", e)

    if not frames:
        return ""

    import cv2
    import base64
    b64_dict = {}
    for name, frame in frames.items():
        try:
            if frame is None or frame.size == 0 or frame.ndim != 3 or frame.shape[0] < 10 or frame.shape[1] < 10:
                continue
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if frame.ndim == 3 and frame.shape[2] == 3 else frame
            ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if ok and len(buf) > 1000:
                b64_dict[name] = base64.b64encode(buf.tobytes()).decode("ascii")
        except Exception as e:
            log.warning("Failed encoding frame '%s': %s", name, e)

    if not b64_dict:
        return ""

    return json.dumps(b64_dict)


_logged_obs_keys = False
_load_register: str = "Present_Load"
_last_valid_load: float = 0.0


def extract_gripper_load(obs: dict) -> float:
    """Extract gripper servo load directly from Feetech motor bus or observation dict."""
    global _logged_obs_keys, _load_register, _last_valid_load
    if not _logged_obs_keys and obs:
        non_img_keys = [k for k in obs.keys() if not k.startswith("observation.images")]
        log.info("Robot observation keys: %s", non_img_keys)
        _logged_obs_keys = True

    if robot is not None and not simulated:
        bus = getattr(robot, "bus", getattr(getattr(robot, "follower_arm", None), "bus", None))
        if bus is not None and hasattr(bus, "read"):
            for reg in (_load_register, "Present_Load", "Present_Current"):
                try:
                    val = bus.read(reg, "gripper")
                    if val is not None:
                        fval = abs(float(val.item() if hasattr(val, "item") else val))
                        _load_register = reg
                        _last_valid_load = fval
                        return fval
                except Exception:
                    pass
            return _last_valid_load

    # 2. Fallback to parsing observation dictionary
    def _to_float(val: Any) -> float | None:
        if val is None:
            return None
        if hasattr(val, "item"):
            val = val.item()
        try:
            return abs(float(val))
        except (TypeError, ValueError):
            return None

    if obs:
        for key in (
            "gripper.current", "gripper_current", "observation.gripper_current",
            "gripper.load", "gripper_load", "observation.gripper.load",
            "gripper.present_current", "gripper_present_current",
        ):
            if key in obs:
                val = _to_float(obs[key])
                if val is not None and val > 0:
                    return val

        for key in ("present_current", "current", "observation.current", "observation.present_current"):
            if key in obs and obs[key] is not None:
                arr = obs[key]
                try:
                    if hasattr(arr, "tolist"):
                        arr = arr.tolist()
                    if isinstance(arr, (list, tuple)) and len(arr) >= 6:
                        val = _to_float(arr[-1])
                        if val is not None and val > 0:
                            return val
                except Exception:
                    pass

        for k, v in obs.items():
            if "gripper" in k.lower() and any(x in k.lower() for x in ("current", "load", "ma")):
                val = _to_float(v)
                if val is not None and val > 0:
                    return val

    return 0.0


# ── One inference step on real hardware ─────────────────────────────────────

def predict_and_act(task: str) -> tuple[np.ndarray, np.ndarray, float, dict]:
    """observation -> action -> robot. Returns (joints, target, gripper_load, action)."""
    obs = robot.get_observation()
    cache_frame(obs)

    joints = np.array(
        [float(v) for k, v in obs.items() if isinstance(v, (int, float)) and k.endswith(".pos")],
        dtype=np.float32)
    load = extract_gripper_load(obs)

    with torch.inference_mode():
        if build_inference_frame is not None:
            frame = build_inference_frame(
                observation=obs, device=torch.device(device),
                ds_features=dataset_features, task=task, robot_type=robot.name)
        else:
            frame = build_dataset_frame(obs_features, copy(obs), prefix="observation")
            frame = prepare_observation_for_inference(frame, torch.device(device), task, robot.name)

        if preprocessor is not None:
            frame = preprocessor(frame)
        values = policy.select_action(frame)
        if postprocessor is not None:
            values = postprocessor(values)

        if make_robot_action is not None:
            action = make_robot_action(values, dataset_features)
        else:
            # select_action returns a batch of one — drop it before indexing
            values = values.squeeze(0).to("cpu")
            action = {key: float(values[i]) for i, key in enumerate(action_keys)}

    robot.send_action(action)
    try:
        target = np.array([action[k] for k in action_keys], dtype=np.float32)
    except KeyError:  # action names differ from the robot's own feature names
        target = np.array(list(action.values()), dtype=np.float32)
    return joints, target, load, action


def freeze_robot() -> None:
    """Hold the arm exactly where it physically is right now.

    Called the moment a step ends (protocol A/B or timeout) and on an
    explicit STOP. Without this, "ending" a step just means the daemon stops
    *sending new* actions — but during the potentially long wait for the
    LLM/VLM round trip that follows, the last command sent might be whatever
    the policy predicted the instant the deadline hit, i.e. mid-motion, not a
    stable grip. Re-issuing the arm's own current position as the target
    every tick while WAITING (see the main loop) keeps it locked there
    instead of drifting or, worse, continuing to open a gripper that was
    mid-close when the clock ran out.
    """
    global hold_action
    if simulated or robot is None:
        return
    try:
        obs = robot.get_observation()
        hold_action = {k: v for k, v in obs.items() if k in action_keys and isinstance(v, (int, float))}
        if hold_action:
            robot.send_action(hold_action)
    except Exception as e:
        log.warning("Freeze/hold failed: %s", e)


# ── stdin command loop ──────────────────────────────────────────────────────

def stdin_reader(max_seconds: float) -> None:
    global state, active_task, active_is_grasp, task_deadline, _any_task_started
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        if line.startswith("SET_TASK:"):
            raw = line[len("SET_TASK:"):].strip()
            parts = raw.split("|")
            task = parts[0].strip()
            is_grasp = False
            timeout_s = 0.0
            for p in parts[1:]:
                p = p.strip()
                if p == "grasp":
                    is_grasp = True
                elif p.startswith("timeout="):
                    try:
                        timeout_s = float(p[len("timeout="):])
                    except ValueError:
                        pass
            if not task:
                continue
            # Bez tohohle přiřazení zůstane active_task prázdný řetězec: policy
            # se pak podmiňuje na "" místo na jméno kroku (u jazykově
            # podmíněných politik typu SmolVLA zásadní) a [STATUS] TASK_DONE
            # hlásí krok bez jména.
            active_task = task
            active_is_grasp = is_grasp
            _any_task_started = True
            if policy is not None and hasattr(policy, "reset"):
                try:
                    policy.reset()
                except Exception:
                    pass
            eff_timeout = timeout_s if timeout_s > 0 else max_seconds
            task_deadline = time.time() + eff_timeout if eff_timeout > 0 else 0.0
            state = "RUNNING"
            print(f"[STATUS] TASK_STARTED: {task}", flush=True)

        elif line.startswith("SET_POLICY:"):
            path = line[len("SET_POLICY:"):].strip()
            if path == current_policy_path and policy is not None:
                print(f"[STATUS] POLICY_LOADED: {path} (already active)", flush=True)
                continue
            state = "WAITING"  # freeze motors during the swap
            try:
                if LEROBOT_OK and TORCH_OK:
                    load_policy(path, device)
                print(f"[STATUS] POLICY_LOADED: {path}", flush=True)
            except Exception as e:
                log.exception("Policy hot-swap failed")
                print(f"[STATUS] POLICY_ERROR: {e}", flush=True)

        elif line == "SNAP":
            print(f"[SNAPSHOT] {snapshot_b64()}", flush=True)

        elif line == "STOP":
            state, active_task = "WAITING", ""
            freeze_robot()
            print("[STATUS] TASK_STOPPED", flush=True)

        elif line == "QUIT":
            log.info("QUIT received.")
            os._exit(0)


# ── Main loop ───────────────────────────────────────────────────────────────

def main() -> None:
    global state, active_task, active_is_grasp, device, simulated, use_triggers
    global use_protocol_a, use_protocol_b
    global PROTOCOL_A_THRESHOLD, PROTOCOL_A_PATIENCE, PROTOCOL_B_LOAD_LIMIT
    global idle_load_baseline

    ap = argparse.ArgumentParser(description="Persistent inference daemon")
    ap.add_argument("--robot.type", dest="robot_type", default="so101_follower")
    ap.add_argument("--robot.id", dest="robot_id", default="my_follower_arm")
    ap.add_argument("--robot.port", dest="robot_port", default="")
    ap.add_argument("--robot.cameras", dest="robot_cameras", default="",
                    help='JSON map {name: {index_or_path, width, height, fps}}')
    # Shell-friendly alternative to --robot.cameras for hand-typed runs — no
    # braces/quotes to escape, so it's safe to paste into any shell as-is.
    ap.add_argument("--camera.name", dest="camera_name", default="")
    ap.add_argument("--camera.index", dest="camera_index", default="0")
    ap.add_argument("--camera.width", dest="camera_width", type=int, default=640)
    ap.add_argument("--camera.height", dest="camera_height", type=int, default=480)
    ap.add_argument("--camera.fps", dest="camera_fps", type=int, default=30)
    ap.add_argument("--camera2.name", dest="camera2_name", default="")
    ap.add_argument("--camera2.index", dest="camera2_index", default="1")
    ap.add_argument("--camera2.width", dest="camera2_width", type=int, default=640)
    ap.add_argument("--camera2.height", dest="camera2_height", type=int, default=480)
    ap.add_argument("--camera2.fps", dest="camera2_fps", type=int, default=30)
    ap.add_argument("--policy.path", dest="policy_path", required=True)
    ap.add_argument("--device", dest="device", default="")
    ap.add_argument("--fps", dest="fps", type=int, default=30)
    ap.add_argument("--max-seconds", dest="max_seconds", type=float, default=0.0,
                    help="Hard time limit per task (0 = none). Used for the baseline run.")
    ap.add_argument("--no-triggers", dest="no_triggers", action="store_true",
                    help="Disable protocols A/B — the task ends only on STOP or --max-seconds "
                         "(this is how the monolithic baseline is run).")
    # Individual protocol switches and thresholds. The right values depend on
    # the task and the hardware, so nothing here is hardcoded: a task with no
    # grasping phase can turn protocol B off entirely, a different gripper or
    # a heavier object needs a different current limit, and so on.
    ap.add_argument("--no-protocol-a", dest="no_protocol_a", action="store_true",
                    help="Disable protocol A (joints settled near the predicted target).")
    ap.add_argument("--no-protocol-b", dest="no_protocol_b", action="store_true",
                    help="Disable protocol B (gripper servo current threshold).")
    ap.add_argument("--protocol-a.threshold", dest="protocol_a_threshold", type=float,
                    default=PROTOCOL_A_THRESHOLD,
                    help=f"Protocol A: max joint delta in rad (default {PROTOCOL_A_THRESHOLD}).")
    ap.add_argument("--protocol-a.patience", dest="protocol_a_patience", type=int,
                    default=PROTOCOL_A_PATIENCE,
                    help=f"Protocol A: consecutive settled frames (default {PROTOCOL_A_PATIENCE}).")
    ap.add_argument("--protocol-b.limit", dest="protocol_b_limit", type=float,
                    default=PROTOCOL_B_LOAD_LIMIT,
                    help="Protocol B: gripper current rise over the idle baseline, in mA "
                         f"(default {PROTOCOL_B_LOAD_LIMIT}).")
    args = ap.parse_args()

    use_triggers = not args.no_triggers
    use_protocol_a = not args.no_protocol_a
    use_protocol_b = not args.no_protocol_b
    PROTOCOL_A_THRESHOLD = args.protocol_a_threshold
    PROTOCOL_A_PATIENCE = args.protocol_a_patience
    PROTOCOL_B_LOAD_LIMIT = args.protocol_b_limit

    def _cam_entry(name: str, index: str, width: int, height: int, fps: int) -> dict:
        try:
            source: object = int(index)
        except ValueError:
            source = index
        return {name: {"index_or_path": source, "width": width, "height": height, "fps": fps}}

    cameras = args.robot_cameras
    if not cameras and args.camera_name:
        cams: dict = _cam_entry(args.camera_name, args.camera_index,
                                 args.camera_width, args.camera_height, args.camera_fps)
        if args.camera2_name:
            cams.update(_cam_entry(args.camera2_name, args.camera2_index,
                                    args.camera2_width, args.camera2_height, args.camera2_fps))
        cameras = json.dumps(cams)

    # 1. Device
    if args.device:
        device = args.device
    elif TORCH_OK and torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
    if device == "cuda" and not (TORCH_OK and torch.cuda.is_available()):
        device = "cpu"
        log.warning("CUDA unavailable — falling back to CPU.")

    # 2. Policy
    if LEROBOT_OK and TORCH_OK:
        try:
            load_policy(args.policy_path, device)
        except Exception as e:
            log.error("Policy load failed (%s) — simulating inference.", e)
    else:
        log.warning("LeRobot/torch missing — simulating inference.")

    # 3. Hardware
    simulated = True
    if LEROBOT_OK and args.robot_port:
        try:
            connect_robot(args.robot_type, args.robot_port, args.robot_id, cameras)
            simulated = False
        except Exception as e:
            log.warning("Follower offline or busy (%s) — SIMULATED mode.", e)
    elif not args.robot_port:
        log.warning("No --robot.port given — SIMULATED mode.")

    n = len(action_keys) if action_keys else 6
    joints = np.zeros(n, dtype=np.float32)
    target = np.zeros(n, dtype=np.float32)

    threading.Thread(target=stdin_reader, args=(args.max_seconds,), daemon=True).start()

    period = 1.0 / max(args.fps, 1)
    settled = 0
    mode = "SIMULATED" if simulated else "HARDWARE"
    print(f"[STATUS] DAEMON_READY: mode={mode}", flush=True)

    while True:
        tick = time.time()
        load = 0.0

        if state == "WAITING":
            settled = 0
            if not simulated and robot is not None:
                try:
                    obs = robot.get_observation()
                    cache_frame(obs)
                    load = extract_gripper_load(obs)
                    if not _any_task_started:
                        idle_load_baseline = load if idle_load_baseline is None \
                            else 0.9 * idle_load_baseline + 0.1 * load
                    current = np.array(
                        [float(v) for k, v in obs.items()
                         if isinstance(v, (int, float)) and k.endswith(".pos")],
                        dtype=np.float32)
                    if current.size:
                        joints = current
                    # Re-affirm the hold every tick — this is what actually
                    # keeps the arm (and a held object) in place for however
                    # long the LLM/VLM round trip between steps takes, not
                    # just at the instant the step ended.
                    if hold_action:
                        robot.send_action(hold_action)
                except Exception:
                    pass

        elif state == "RUNNING":
            if not simulated and robot is not None and policy is not None:
                try:
                    current, predicted, load, _ = predict_and_act(active_task)
                    if current.size:
                        joints = current
                    target = predicted
                except Exception as e:
                    log.warning("Inference step failed: %s", e)
            else:
                # Simulated arm: slide toward a canned target so the whole
                # orchestration loop (triggers, VLM, re-planning) is testable.
                if active_is_grasp:
                    target = np.resize(np.array([0.1, -0.2, 0.4, 0.1, 0.0, 1.2], np.float32), joints.shape)
                    load = 280.0 if (joints.size >= 6 and joints[5] > 0.8) else 40.0
                else:
                    target = np.resize(np.array([0.8, 0.3, -0.2, 0.5, 0.0, 0.0], np.float32), joints.shape)
                    load = 20.0
                joints = joints + (target - joints) * 0.15

            # Termination protocols
            n_pos = max(joints.size - 1, 1)  # the gripper is excluded from "settled"
            deltas = np.abs(target[:n_pos] - joints[:n_pos])
            settled = settled + 1 if bool(np.all(deltas < PROTOCOL_A_THRESHOLD)) else 0

            reason = ""
            baseline = idle_load_baseline if idle_load_baseline is not None else 0.0
            rise = load - baseline
            if use_triggers and use_protocol_b and active_is_grasp and rise > PROTOCOL_B_LOAD_LIMIT:
                reason = (f"Protokol B (zátěž gripperu {load:.0f}, nárůst {rise:.0f} "
                          f"nad klid {baseline:.0f} > limit {PROTOCOL_B_LOAD_LIMIT:.0f})")
            elif use_triggers and use_protocol_a and settled >= PROTOCOL_A_PATIENCE:
                max_d = float(np.max(deltas)) if deltas.size else 0.0
                reason = f"Protokol A (klouby dosedly, max delta {max_d:.5f} rad)"
            elif task_deadline and time.time() > task_deadline:
                reason = "Časový limit kroku"

            if reason:
                freeze_robot()
                print(f"[STATUS] TASK_DONE: {active_task} | {reason}", flush=True)
                state, active_task, active_is_grasp, settled = "WAITING", "", False, 0

        # Telemetry ~5x per second
        if int(tick * 5) != int((tick - period) * 5):
            n_pos = max(joints.size - 1, 1)
            deltas = np.abs(target[:n_pos] - joints[:n_pos])
            print(
                f"[TELEMETRY] joints:{','.join(f'{x:.3f}' for x in joints)} | "
                f"target:{','.join(f'{x:.3f}' for x in target)} | "
                f"load:{load:.0f} | "
                f"baseline:{(idle_load_baseline if idle_load_baseline is not None else 0.0):.0f} | "
                f"settle:{settled}/{PROTOCOL_A_PATIENCE}",
                flush=True)

        elapsed = time.time() - tick
        if elapsed < period:
            time.sleep(period - elapsed)


if __name__ == "__main__":
    try:
        main()
    finally:
        if robot is not None:
            try:
                robot.disconnect()
            except Exception:
                pass
