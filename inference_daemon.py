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
    Protocol A — all joints moved less than the threshold since the PREVIOUS
                 tick, for N consecutive frames (the arm physically stopped —
                 not "close to the policy's current prediction", which can
                 stay a few degrees off indefinitely even at a dead stop).
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

# Výstup daemonu čte orchestrátor jako UTF-8 a příkazy (SET_TASK: s českým
# popisem kroku) mu posílá taky jako UTF-8 (viz orchestrator.py Popen(...,
# encoding="utf-8")). Bez tohohle by Python na Windows použil kódování
# konzole (cp1250) pro stdout/stderr i stdin a české hlášky/příkazy by
# dorazily rozsypané — a znak mimo cp1250 by proces rovnou shodil.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np

HERE = Path(__file__).resolve().parent

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
# Max joint movement between two consecutive ticks to count as "not moving".
# Only used this way for |reset and |grasp steps (see active_is_reset /
# active_is_grasp in the main loop) — confirmed on real telemetry
# (2026-08-27) that for a RESET step, comparing joints to the policy's own
# per-tick TARGET prediction never settles even once the arm has visibly and
# completely stopped moving (tick-by-tick joint reading froze bit-for-bit for
# 10+ seconds while target-joints sat at a stable ~2.5-2.9 unit residual the
# whole time — that's prediction noise around the hold point, not motion).
# Ordinary (non-reset, non-grasp) steps keep comparing against the predicted
# target as before — a positioning step's own target IS the thing that
# matters there, unlike a reset whose only job is "arm stops moving somewhere
# safe" or a grasp whose completion protocol B already judges independently
# (this is corroborating evidence for that, not a replacement).
# Despite the name/flag (--protocol-a.threshold, kept for compatibility with
# existing configs), this is NOT compared against radians of tracking error —
# and note the robot's own ".pos" observations here are whatever unit the
# robot driver reports (degrees for this project's SO-101 setup, since its
# config uses use_degrees=true) — "rad" in the flag name predates this
# rewrite and is now just a legacy label, not a unit guarantee. 0.5 is a
# starting point with real margin above single-tick encoder noise (observed
# up to ~0.5 at 5x/s telemetry sampling, so native-tick noise is smaller
# still) and well below actual motion (tens of units) — tune from the
# `joint_velocity` field now logged in telemetry rather than guessing further.
PROTOCOL_A_THRESHOLD = 0.5
PROTOCOL_A_PATIENCE = 5        # consecutive frames, used for |reset steps
# Grasping can involve a touch more residual jostling right after contact
# than a clean return-to-home does, so a grasp step waits a bit longer than a
# reset step before trusting "stopped moving" as corroborating evidence —
# per-protocol distinction, not a guess: same physical signal, just a
# modestly longer patience window for the noisier of the two cases.
PROTOCOL_A_GRASP_PATIENCE_EXTRA = 5   # extra consecutive frames on top of PROTOCOL_A_PATIENCE, for |grasp steps
PROTOCOL_B_LOAD_LIMIT = 250.0  # mA rise over the idle baseline (see idle_load_baseline)
# A closing gripper draws a brief current spike while it's actively moving,
# whether or not anything ends up between the jaws — a single tick over
# PROTOCOL_B_LOAD_LIMIT can't tell "still closing" from "closed and holding".
# Requiring the rise to hold for several consecutive frames (same idea as
# PROTOCOL_A_PATIENCE for joint settling) targets the post-motion PLATEAU
# instead of the motion transient.
PROTOCOL_B_PATIENCE = 3        # consecutive frames
# Confirmed on real telemetry (2026-08-27): a fresh step's opening motion —
# repositioning toward wherever the demonstrated trajectory starts — spiked
# gripper load past the patience-filtered threshold within the first tick(s)
# of RUNNING, well before any approach to the object could plausibly have
# happened. Neither a higher limit nor more patience alone fixes this: the
# real confirmed grasp (rise 108) sits BETWEEN two false triggers (72 and
# 139) from this startup transient, so no single threshold/patience pair
# separates "just started moving" from "gripping" on rise value alone — the
# two distributions overlap. A grace period sidesteps the overlap instead of
# trying to threshold through it: don't evaluate Protocol B at all until the
# step has been running long enough that a startup transient has settled out.
PROTOCOL_B_GRACE_S = 0.75      # seconds since SET_TASK before Protocol B is evaluated
# Confirmed on real telemetry (2026-08-27, two consecutive false triggers):
# both firings happened at slope 336 and 212 — i.e. while the load was still
# rapidly RISING, not once it had plateaued. Raising PROTOCOL_B_LOAD_LIMIT
# alone can't fix this: a motion transient passes through every load value on
# its way up, including whatever the limit is, at some tick. The genuine held
# grasp observed in the same diagnostic run stayed flat (slope near 0) for
# seconds at a time. So Protocol B now requires the same kind of evidence
# Protocol A already requires for joints: not just "above a level", but
# "stopped changing while above it" — a PLATEAU, not a rising edge. This
# makes the exact value of PROTOCOL_B_LOAD_LIMIT much less fragile, since a
# transient can no longer satisfy patience just by climbing through it.
PROTOCOL_B_STABILITY_SLOPE = 30.0  # |load_slope| must stay under this to count as "settled"
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
active_is_reset = False   # protokol A měří fyzický klid (rychlost), ne odstup od predikce?
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
task_started_at = 0.0
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
# Set by stdin_reader() on QUIT, checked by main()'s loop. Not os._exit()'d
# directly from that thread: this lets main() return normally so the
# `finally: robot.disconnect()` in __main__ actually runs (and with it the
# configured disable_torque_on_disconnect) instead of being skipped by an
# immediate process-level exit.
_quit_requested = threading.Event()
# How many real samples idle_load_baseline has been averaged over. A single
# lucky/unlucky first reading (motor bus still settling right after connect,
# a transient current spike) would otherwise freeze the whole run's baseline
# on one noisy data point — see MIN_BASELINE_SAMPLES below.
_baseline_samples = 0
# ~0.5s of real ticks at the default 30 fps: enough for the EMA to average out
# a single bad first sample before Protocol B is allowed to fire, short enough
# not to meaningfully delay the very first task of the run.
MIN_BASELINE_SAMPLES = 15

# Rolling window (in RUNNING ticks) for the load DERIVATIVE — how fast the
# gripper current is rising right now, not just how far above idle it is.
# Hitting resistance (an object, or the gripper's own mechanical end-stop
# closing on nothing) saturates the servo's PID and spikes current quickly;
# free movement should climb more smoothly.
#
# The raw slope alone doesn't separate "just started moving" from "made
# contact" either, though — going from idle (~flat) to any motion is itself
# a big jump against a near-zero baseline, same problem as the absolute rise
# had. What should actually distinguish contact is a KINK: the slope over
# the last window suddenly running higher than the slope over the window
# right before it, i.e. the rate of rise itself changing, not just being
# positive. `_load_history` keeps 2x SLOPE_WINDOW samples so both the recent
# and the preceding window can be compared — `trend:` in telemetry is that
# difference. Neither slope nor trend is wired into a termination decision
# yet — telemetry-only until watched across a real grasp and real ordinary
# movement, same discipline as PROTOCOL_B_LOAD_LIMIT was tuned with.
SLOPE_WINDOW = 5   # ticks (~0.15s at 30 fps)
_load_history: list[float] = []

# Structured, file-based telemetry log — one JSON object per line, written
# alongside the human-readable [TELEMETRY] stdout prints (same ~5x/s tick
# rows) plus explicit "task_started"/"task_done" event rows. stdout is fine
# for watching a run live, but it's not something anyone can load back and
# actually analyze the load/slope/trend curve from afterwards — this is.
# One file per daemon process (i.e. per orchestration run, since one daemon
# stays alive across all of a run's steps via SET_POLICY hot-swaps).
_telemetry_log_fh = None  # opened in main(), closed on QUIT
# Guards writes to _telemetry_log_fh: called from both the main loop and the
# stdin_reader thread (on SET_TASK), and a bare write()+flush() from two
# threads can interleave and corrupt a JSONL line.
_telemetry_lock = threading.Lock()


def _log_telemetry(**fields) -> None:
    if _telemetry_log_fh is None:
        return
    try:
        fields.setdefault("t", time.time())
        line = json.dumps(fields, ensure_ascii=False) + "\n"
        with _telemetry_lock:
            _telemetry_log_fh.write(line)
            _telemetry_log_fh.flush()
    except Exception:
        pass


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
    global state, active_task, active_is_grasp, active_is_reset, task_deadline, task_started_at, _any_task_started
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        if line.startswith("SET_TASK:"):
            raw = line[len("SET_TASK:"):].strip()
            parts = raw.split("|")
            task = parts[0].strip()
            is_grasp = False
            is_reset = False
            timeout_s = 0.0
            for p in parts[1:]:
                p = p.strip()
                if p == "grasp":
                    is_grasp = True
                elif p == "reset":
                    is_reset = True
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
            active_is_reset = is_reset
            _any_task_started = True
            if policy is not None and hasattr(policy, "reset"):
                try:
                    policy.reset()
                except Exception:
                    pass
            eff_timeout = timeout_s if timeout_s > 0 else max_seconds
            task_deadline = time.time() + eff_timeout if eff_timeout > 0 else 0.0
            task_started_at = time.time()
            state = "RUNNING"
            print(f"[STATUS] TASK_STARTED: {task}", flush=True)
            _log_telemetry(event="task_started", task=task, is_grasp=is_grasp, is_reset=is_reset, timeout_s=eff_timeout)

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
            _quit_requested.set()
            return


# ── Main loop ───────────────────────────────────────────────────────────────

def main() -> None:
    global state, active_task, active_is_grasp, active_is_reset, device, simulated, use_triggers
    global use_protocol_a, use_protocol_b
    global PROTOCOL_A_THRESHOLD, PROTOCOL_A_PATIENCE, PROTOCOL_A_GRASP_PATIENCE_EXTRA, PROTOCOL_B_LOAD_LIMIT, PROTOCOL_B_PATIENCE, PROTOCOL_B_GRACE_S, PROTOCOL_B_STABILITY_SLOPE
    global idle_load_baseline, _baseline_samples, _load_history, _telemetry_log_fh

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
                    help="Disable protocol A (joints stopped moving, tick-to-tick).")
    ap.add_argument("--no-protocol-b", dest="no_protocol_b", action="store_true",
                    help="Disable protocol B (gripper servo current threshold).")
    ap.add_argument("--protocol-a.threshold", dest="protocol_a_threshold", type=float,
                    default=PROTOCOL_A_THRESHOLD,
                    help=f"Protocol A: max joint movement between ticks, native robot position unit "
                         f"(default {PROTOCOL_A_THRESHOLD}).")
    ap.add_argument("--protocol-a.patience", dest="protocol_a_patience", type=int,
                    default=PROTOCOL_A_PATIENCE,
                    help=f"Protocol A: consecutive settled frames for |reset steps (default {PROTOCOL_A_PATIENCE}).")
    ap.add_argument("--protocol-a.grasp-patience-extra", dest="protocol_a_grasp_patience_extra", type=int,
                    default=PROTOCOL_A_GRASP_PATIENCE_EXTRA,
                    help="Protocol A: EXTRA consecutive settled frames on top of --protocol-a.patience "
                         f"for |grasp steps (default {PROTOCOL_A_GRASP_PATIENCE_EXTRA}).")
    ap.add_argument("--protocol-b.limit", dest="protocol_b_limit", type=float,
                    default=PROTOCOL_B_LOAD_LIMIT,
                    help="Protocol B: gripper current rise over the idle baseline, in mA "
                         f"(default {PROTOCOL_B_LOAD_LIMIT}).")
    ap.add_argument("--protocol-b.patience", dest="protocol_b_patience", type=int,
                    default=PROTOCOL_B_PATIENCE,
                    help=f"Protocol B: consecutive frames the rise must hold (default {PROTOCOL_B_PATIENCE}).")
    ap.add_argument("--protocol-b.grace", dest="protocol_b_grace", type=float,
                    default=PROTOCOL_B_GRACE_S,
                    help="Protocol B: seconds after SET_TASK before it's evaluated at all, to skip "
                         f"the opening-motion current transient (default {PROTOCOL_B_GRACE_S}).")
    ap.add_argument("--protocol-b.stability", dest="protocol_b_stability", type=float,
                    default=PROTOCOL_B_STABILITY_SLOPE,
                    help="Protocol B: max |load slope| to count as settled/plateaued rather than "
                         f"still rising (default {PROTOCOL_B_STABILITY_SLOPE}).")
    ap.add_argument("--telemetry-log", dest="telemetry_log", default=None,
                    help="Path to a JSONL file to append structured per-tick telemetry to "
                         "(load/baseline/rise/settle/grasp_hold/slope/trend + task_started/task_done "
                         "events). Default: auto-generated telemetry/<timestamp>.jsonl next to this "
                         "script. Pass --telemetry-log=off to disable.")
    args = ap.parse_args()

    use_triggers = not args.no_triggers
    use_protocol_a = not args.no_protocol_a
    use_protocol_b = not args.no_protocol_b
    PROTOCOL_A_THRESHOLD = args.protocol_a_threshold
    PROTOCOL_A_PATIENCE = args.protocol_a_patience
    PROTOCOL_A_GRASP_PATIENCE_EXTRA = args.protocol_a_grasp_patience_extra
    PROTOCOL_B_LOAD_LIMIT = args.protocol_b_limit
    PROTOCOL_B_PATIENCE = args.protocol_b_patience
    PROTOCOL_B_GRACE_S = args.protocol_b_grace
    PROTOCOL_B_STABILITY_SLOPE = args.protocol_b_stability

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

    if args.telemetry_log != "off":
        log_path = Path(args.telemetry_log) if args.telemetry_log else \
            (HERE / "telemetry" / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            _telemetry_log_fh = open(log_path, "a", encoding="utf-8")
            log.info("Telemetrie se loguje do %s", log_path)
            _log_telemetry(event="daemon_start", policy_path=args.policy_path,
                           protocol_a_threshold=PROTOCOL_A_THRESHOLD, protocol_a_patience=PROTOCOL_A_PATIENCE,
                           protocol_b_limit=PROTOCOL_B_LOAD_LIMIT, protocol_b_patience=PROTOCOL_B_PATIENCE,
                           protocol_b_grace_s=PROTOCOL_B_GRACE_S)
        except Exception as e:
            log.warning("Telemetrii se nepodařilo logovat do souboru (%s) — jen na stdout.", e)

    period = 1.0 / max(args.fps, 1)
    settled = 0
    settle_patience = PROTOCOL_A_PATIENCE
    grasp_hold = 0
    load_slope = 0.0
    load_trend = 0.0
    prev_joints: np.ndarray | None = None
    mode = "SIMULATED" if simulated else "HARDWARE"
    print(f"[STATUS] DAEMON_READY: mode={mode}", flush=True)

    while True:
        if _quit_requested.is_set():
            return
        tick = time.time()
        load = 0.0

        if state == "WAITING":
            settled = 0
            settle_patience = PROTOCOL_A_PATIENCE
            grasp_hold = 0
            prev_joints = None
            _load_history.clear()
            if not simulated and robot is not None:
                try:
                    obs = robot.get_observation()
                    cache_frame(obs)
                    load = extract_gripper_load(obs)
                    if not _any_task_started:
                        idle_load_baseline = load if idle_load_baseline is None \
                            else 0.9 * idle_load_baseline + 0.1 * load
                        _baseline_samples += 1
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
            #
            # For |reset and |grasp steps, "settled" means joint VELOCITY near
            # zero — how much a joint moved since last tick — not how far the
            # current position is from the policy's own per-tick prediction.
            # Confirmed on real telemetry (2026-08-27) for a reset step:
            # comparing joints to TARGET never settled even once the arm had
            # visibly and completely stopped moving — the raw tick-by-tick
            # joint reading froze bit-for-bit (0.0000 change) for 10+ seconds
            # straight while `target - joints` sat at a stable ~2.5-2.9 unit
            # residual the whole time. That's the policy's own prediction
            # noise around its hold point, not motion.
            #
            # Ordinary positioning steps (neither flag set) keep the original
            # target-tracking comparison — there, reaching the policy's own
            # predicted target is exactly what matters, unlike a reset (whose
            # only job is "stop moving somewhere safe") or a grasp (where this
            # is corroborating evidence alongside protocol B, not the primary
            # signal — see PROTOCOL_A_GRASP_PATIENCE_EXTRA above for why grasp
            # gets a longer patience window than reset).
            # The gripper is excluded from "settled" for ordinary/reset steps
            # — its own closing motion (part of many trajectories) shouldn't
            # block the ARM from being recognized as having arrived. But for
            # a GRASP step that exclusion is exactly backwards: confirmed on
            # real telemetry (2026-08-27) that the arm's positioning joints
            # stop moving BEFORE the gripper finishes closing (arm still for
            # ~1s, then gripper starts closing, load spiking to 453 right as
            # protocol A — arm-only — fired and cut the step off). A grasp
            # isn't done until the gripper ALSO stops, so it's included here.
            n_pos = joints.size if active_is_grasp else max(joints.size - 1, 1)
            use_velocity_settle = active_is_reset or active_is_grasp
            if use_velocity_settle:
                if prev_joints is not None and prev_joints.size == joints.size:
                    deltas = np.abs(joints[:n_pos] - prev_joints[:n_pos])
                else:
                    deltas = np.full(n_pos, np.inf, dtype=np.float32)  # first tick: can't measure velocity yet
            else:
                deltas = np.abs(target[:n_pos] - joints[:n_pos])
            settled = settled + 1 if bool(np.all(deltas < PROTOCOL_A_THRESHOLD)) else 0
            prev_joints = joints.copy()
            settle_patience = PROTOCOL_A_PATIENCE + (PROTOCOL_A_GRASP_PATIENCE_EXTRA if active_is_grasp else 0)

            reason = ""
            baseline_ready = idle_load_baseline is not None and _baseline_samples >= MIN_BASELINE_SAMPLES
            baseline = idle_load_baseline if idle_load_baseline is not None else 0.0
            rise = load - baseline

            # Load derivative and trend — see SLOPE_WINDOW comment above.
            # history[-1] = now, history[-1-W] = one window ago, history[-1-2W]
            # = two windows ago. slope_now compares the last two; slope_prev
            # the two before that; trend is how much the rate of rise itself
            # just changed, which is what should flag "just made contact"
            # rather than "still in the middle of moving".
            _load_history.append(load)
            if len(_load_history) > 2 * SLOPE_WINDOW + 1:
                _load_history.pop(0)
            hist = _load_history
            n = len(hist)
            load_slope = (hist[-1] - hist[-1 - SLOPE_WINDOW]) if n > SLOPE_WINDOW else 0.0
            if n > 2 * SLOPE_WINDOW:
                slope_prev = hist[-1 - SLOPE_WINDOW] - hist[-1 - 2 * SLOPE_WINDOW]
                load_trend = load_slope - slope_prev
            else:
                load_trend = 0.0
            # Guarded on baseline_ready — without it "idle" is either the
            # None->0.0 placeholder above or an average of a handful of
            # noisy first samples, and comparing a real load against either
            # is a coin flip, not a measurement (see MIN_BASELINE_SAMPLES).
            # Also guarded on the grace period (PROTOCOL_B_GRACE_S) — the
            # counter doesn't even start climbing until it's elapsed, so the
            # full patience window has to happen AFTER the opening-motion
            # transient, not overlapping with it.
            grace_elapsed = (time.time() - task_started_at) >= PROTOCOL_B_GRACE_S
            # "Elevated" alone isn't enough — a rising transient passes
            # through every value on its way up. Require it to also have
            # stopped changing (plateaued), the same "settled" idea Protocol A
            # uses for joints, applied here to the load signal instead. See
            # PROTOCOL_B_STABILITY_SLOPE comment above for the evidence.
            load_settled = abs(load_slope) < PROTOCOL_B_STABILITY_SLOPE
            over_limit = baseline_ready and grace_elapsed and rise > PROTOCOL_B_LOAD_LIMIT and load_settled
            grasp_hold = grasp_hold + 1 if over_limit else 0
            if use_triggers and use_protocol_b and active_is_grasp and grasp_hold >= PROTOCOL_B_PATIENCE:
                reason = (f"Protokol B (zátěž gripperu {load:.0f}, nárůst {rise:.0f} "
                          f"nad klid {baseline:.0f} > limit {PROTOCOL_B_LOAD_LIMIT:.0f}, "
                          f"usazeno slope {load_slope:.0f}, drženo {grasp_hold}/{PROTOCOL_B_PATIENCE} snímků)")
            elif use_triggers and use_protocol_a and settled >= settle_patience:
                max_d = float(np.max(deltas)) if deltas.size else 0.0
                basis = "klouby se přestaly hýbat" if use_velocity_settle else "klouby dosedly na predikci"
                reason = f"Protokol A ({basis}, max pohyb {max_d:.5f}/tik, drženo {settled}/{settle_patience})"
            elif task_deadline and time.time() > task_deadline:
                reason = "Časový limit kroku"

            if reason:
                freeze_robot()
                print(f"[STATUS] TASK_DONE: {active_task} | {reason}", flush=True)
                _log_telemetry(event="task_done", task=active_task, reason=reason,
                               load=load, baseline=baseline, rise=rise,
                               settled=settled, grasp_hold=grasp_hold,
                               slope=load_slope, trend=load_trend)
                state, active_task, active_is_grasp, active_is_reset, settled, grasp_hold = "WAITING", "", False, False, 0, 0
                prev_joints = None

        # Telemetry ~5x per second
        if int(tick * 5) != int((tick - period) * 5):
            n_pos = max(joints.size - 1, 1)
            # Diagnostic only (not used for any decision): how far the
            # policy's current single-tick prediction sits from where the
            # arm actually is. Logged alongside the real (velocity-based)
            # settle signal so a discrepancy like the one that motivated this
            # rewrite — physically still, but target tracking error stuck at
            # several degrees — is visible directly in telemetry next time,
            # not something that has to be reconstructed from joints/target
            # arrays after the fact.
            target_error_max = float(np.max(np.abs(target[:n_pos] - joints[:n_pos]))) if n_pos else 0.0
            velocity_max = float(np.max(deltas)) if state == "RUNNING" and deltas.size and np.all(np.isfinite(deltas)) else 0.0
            print(
                f"[TELEMETRY] joints:{','.join(f'{x:.3f}' for x in joints)} | "
                f"target:{','.join(f'{x:.3f}' for x in target)} | "
                f"load:{load:.0f} | "
                f"baseline:{(idle_load_baseline if idle_load_baseline is not None else 0.0):.0f} | "
                f"settle:{settled}/{settle_patience} (vel {velocity_max:.4f}, target_err {target_error_max:.3f}) | "
                f"grasp_hold:{grasp_hold}/{PROTOCOL_B_PATIENCE} | "
                f"slope:{(load_slope if state == 'RUNNING' else 0.0):.0f} | "
                f"trend:{(load_trend if state == 'RUNNING' else 0.0):.0f}",
                flush=True)
            _log_telemetry(event="tick", state=state, task=active_task, is_grasp=active_is_grasp, is_reset=active_is_reset,
                           load=load, baseline=(idle_load_baseline if idle_load_baseline is not None else None),
                           rise=(load - idle_load_baseline) if idle_load_baseline is not None else None,
                           settled=settled, protocol_a_patience=settle_patience,
                           joint_velocity=velocity_max, target_error=target_error_max,
                           grasp_hold=grasp_hold, protocol_b_patience=PROTOCOL_B_PATIENCE,
                           slope=(load_slope if state == "RUNNING" else 0.0),
                           trend=(load_trend if state == "RUNNING" else 0.0),
                           joints=[round(float(x), 3) for x in joints],
                           target=[round(float(x), 3) for x in target])

        elapsed = time.time() - tick
        if elapsed < period:
            time.sleep(period - elapsed)


if __name__ == "__main__":
    try:
        main()
    finally:
        # Both cleanups belong here rather than in the QUIT handler: main()
        # can also end via --max-seconds, Ctrl+C or an exception, and the
        # telemetry log must be flushed/closed in every one of those cases.
        if _telemetry_log_fh is not None:
            try:
                _telemetry_log_fh.close()
            except Exception:
                pass
        if robot is not None:
            try:
                robot.disconnect()
            except Exception:
                pass
