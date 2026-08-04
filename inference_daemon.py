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
    [TELEMETRY] joints:... | target:... | load:... | settle:n/5

Step termination (why a step ends without anybody telling it to):
    Protocol A — all joints settled under 0.005 rad from the target for 5
                 consecutive frames (the motion is finished).
    Protocol B — gripper servo current above 250 mA (contact with an object),
                 used for grasping steps so the policy does not keep squeezing.

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
    from lerobot.robots import RobotConfig  # noqa: F401 — registers subclasses
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


# ── Termination protocol constants ──────────────────────────────────────────
PROTOCOL_A_THRESHOLD = 0.005   # rad
PROTOCOL_A_PATIENCE = 5        # consecutive frames
PROTOCOL_B_LOAD_LIMIT = 250.0  # mA
# Záložní heuristika: pokud orchestrátor krok neoznačí příznakem |grasp,
# pozná se úchopový krok podle názvu.
GRASP_WORDS = ("uchop", "grab", "grasp", "pick", "close", "sevri", "chyt")

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

_frame_lock = threading.Lock()
last_frame: np.ndarray | None = None


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
    global last_frame
    for value in obs.values():
        if isinstance(value, np.ndarray) and value.ndim == 3 and value.shape[2] in (1, 3, 4):
            with _frame_lock:
                last_frame = value
            return


def snapshot_b64() -> str:
    with _frame_lock:
        frame = last_frame
    if frame is None:
        return ""
    try:
        import base64
        import cv2
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if frame.shape[2] == 3 else frame
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""
    except Exception as e:
        log.warning("Snapshot encoding failed: %s", e)
        return ""


# ── One inference step on real hardware ─────────────────────────────────────

def predict_and_act(task: str) -> tuple[np.ndarray, np.ndarray, float]:
    """observation -> action -> robot. Returns (joints, target, gripper_load).

    The order of the calls mirrors LeRobot's own rollout loop
    (build_inference_frame -> preprocessor -> select_action -> postprocessor ->
    make_robot_action -> send_action); on versions that do not ship those two
    helpers the same steps are done by hand.
    """
    obs = robot.get_observation()
    cache_frame(obs)

    joints = np.array(
        [float(v) for k, v in obs.items() if isinstance(v, (int, float)) and k.endswith(".pos")],
        dtype=np.float32)
    load = float(obs.get("gripper.current", obs.get("observation.gripper_current", 0.0)) or 0.0)

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
    return joints, target, load


# ── stdin command loop ──────────────────────────────────────────────────────

def stdin_reader(max_seconds: float) -> None:
    global state, active_task, active_is_grasp, task_deadline
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        if line.startswith("SET_TASK:"):
            # "SET_TASK:<úkol>" nebo "SET_TASK:<úkol>|grasp" — druhý tvar říká,
            # že krok končí sevřením objektu (protokol B).
            task = line[len("SET_TASK:"):].strip()
            is_grasp = task.endswith("|grasp")
            if is_grasp:
                task = task[:-len("|grasp")].strip()
            if not task:
                continue
            active_task = task
            active_is_grasp = is_grasp or any(w in task.lower() for w in GRASP_WORDS)
            if policy is not None and hasattr(policy, "reset"):
                try:
                    policy.reset()
                except Exception:
                    pass
            task_deadline = time.time() + max_seconds if max_seconds > 0 else 0.0
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
            print("[STATUS] TASK_STOPPED", flush=True)

        elif line == "QUIT":
            log.info("QUIT received.")
            os._exit(0)


# ── Main loop ───────────────────────────────────────────────────────────────

def main() -> None:
    global state, active_task, active_is_grasp, device, simulated, use_triggers

    ap = argparse.ArgumentParser(description="Persistent inference daemon")
    ap.add_argument("--robot.type", dest="robot_type", default="so101_follower")
    ap.add_argument("--robot.id", dest="robot_id", default="my_follower_arm")
    ap.add_argument("--robot.port", dest="robot_port", default="")
    ap.add_argument("--robot.cameras", dest="robot_cameras", default="",
                    help='JSON map {name: {index_or_path, width, height, fps}}')
    # Shell-friendly alternative to --robot.cameras for hand-typed runs
    ap.add_argument("--camera.name", dest="camera_name", default="")
    ap.add_argument("--camera.index", dest="camera_index", default="0")
    ap.add_argument("--camera.width", dest="camera_width", type=int, default=640)
    ap.add_argument("--camera.height", dest="camera_height", type=int, default=480)
    ap.add_argument("--camera.fps", dest="camera_fps", type=int, default=30)
    ap.add_argument("--policy.path", dest="policy_path", required=True)
    ap.add_argument("--device", dest="device", default="")
    ap.add_argument("--fps", dest="fps", type=int, default=30)
    ap.add_argument("--max-seconds", dest="max_seconds", type=float, default=0.0,
                    help="Hard time limit per task (0 = none). Used for the baseline run.")
    ap.add_argument("--no-triggers", dest="no_triggers", action="store_true",
                    help="Disable protocols A/B — the task ends only on STOP or --max-seconds "
                         "(this is how the monolithic baseline is run).")
    args = ap.parse_args()

    use_triggers = not args.no_triggers

    cameras = args.robot_cameras
    if not cameras and args.camera_name:
        try:
            source: object = int(args.camera_index)
        except ValueError:
            source = args.camera_index
        cameras = json.dumps({args.camera_name: {
            "index_or_path": source, "width": args.camera_width,
            "height": args.camera_height, "fps": args.camera_fps}})

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
                    current = np.array(
                        [float(v) for k, v in obs.items()
                         if isinstance(v, (int, float)) and k.endswith(".pos")],
                        dtype=np.float32)
                    if current.size:
                        joints = current
                except Exception:
                    pass

        elif state == "RUNNING":
            if not simulated and robot is not None and policy is not None:
                try:
                    current, predicted, load = predict_and_act(active_task)
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
            if use_triggers and active_is_grasp and load > PROTOCOL_B_LOAD_LIMIT:
                reason = f"Protokol B (proud gripperu {load:.0f} mA > {PROTOCOL_B_LOAD_LIMIT:.0f} mA)"
            elif use_triggers and settled >= PROTOCOL_A_PATIENCE:
                max_d = float(np.max(deltas)) if deltas.size else 0.0
                reason = f"Protokol A (klouby dosedly, max delta {max_d:.5f} rad)"
            elif task_deadline and time.time() > task_deadline:
                reason = "Časový limit kroku"

            if reason:
                print(f"[STATUS] TASK_DONE: {active_task} | {reason}", flush=True)
                state, active_task, active_is_grasp, settled = "WAITING", "", False, 0

        # Telemetry ~5x per second
        if int(tick * 5) != int((tick - period) * 5):
            n_pos = max(joints.size - 1, 1)
            deltas = np.abs(target[:n_pos] - joints[:n_pos])
            print(
                f"[TELEMETRY] joints:{','.join(f'{x:.3f}' for x in joints)} | "
                f"target:{','.join(f'{x:.3f}' for x in target)} | "
                f"load:{load:.0f} | settle:{settled}/{PROTOCOL_A_PATIENCE}",
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
