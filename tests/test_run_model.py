"""run_model.py: step_flags() a daemon_command() bez robota/LM Studia — jen na configu."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_model import cameras_json, daemon_command, step_flags

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "CHYBA"), name, ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


CFG = {
    "python": "python.exe", "device": "cuda", "robot_type": "so101_follower",
    "robot_id": "my_follower_arm", "robot_port": "COM3", "fps": 30,
    "camera_name": "top", "camera_index": "0", "camera_width": 640, "camera_height": 480, "camera_fps": 30,
    "camera2_name": "wrist", "camera2_index": "1", "camera2_width": 640, "camera2_height": 480, "camera2_fps": 30,
    "protocol_a_threshold_rad": 0.5, "protocol_a_target_threshold_rad": 5.25, "protocol_a_patience": 7,
    "protocol_a_grasp_patience_extra": 5, "protocol_a_grace_s": 1.0, "protocol_b_limit_ma": 300.0,
    "protocol_b_patience": 3, "protocol_b_grace_s": 0.75, "protocol_b_stability_slope": 30.0,
    "episode_time_s": 20,
    "steps": [
        {"slug": "catch_cube", "grasp": True, "timeout_s": None},
        {"slug": "carry_cube", "release": True, "timeout_s": None},
        {"slug": "homing", "reset": True, "timeout_s": 15},
    ],
}

check("catch_cube: jen grasp", step_flags(CFG, "catch_cube") == (True, False, False, None))
check("carry_cube: jen release", step_flags(CFG, "carry_cube") == (False, False, True, None))
check("homing: reset + vlastni timeout", step_flags(CFG, "homing") == (False, True, False, 15.0))
check("neznamy krok: vsechny priznaky vypnute", step_flags(CFG, "neco") == (False, False, False, None))

cams = cameras_json(CFG)
check("cameras_json: obe kamery", '"top"' in cams and '"wrist"' in cams, cams)

cmd = daemon_command(CFG, "outputs/training/diplomka_3_catch_cube_60ep_act_cs30")
check("daemon_command: policy.path sedi", "--policy.path=outputs/training/diplomka_3_catch_cube_60ep_act_cs30" in cmd)
check("daemon_command: robot.port sedi", "--robot.port=COM3" in cmd)
check("daemon_command: protokol B limit sedi", "--protocol-b.limit=300.0" in cmd)
check("daemon_command: bez temporal-ensemble, kdyz je vypnuty", not any(a.startswith("--temporal-ensemble") for a in cmd))

cmd2 = daemon_command(dict(CFG, temporal_ensemble=True, temporal_ensemble_coeff=0.02), "x")
check("daemon_command: temporal-ensemble se propise, kdyz je zapnuty",
      "--temporal-ensemble.coeff=0.02" in cmd2)

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    sys.exit(1)
print("OK — step_flags i daemon_command sedi.")
