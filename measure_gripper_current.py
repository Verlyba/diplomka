#!/usr/bin/env python3
"""
Live gripper load/current readout — for picking real Protocol B /
holding-limit values instead of guessing them.

Whatever register extract_gripper_load() in inference_daemon.py resolves to
(Present_Load or Present_Current — on this project's SO-101/STS3215 hardware
it's Present_Load; Present_Current reads a flat 0 in every state, see
DENIK.md) is a raw register value, not calibrated to milliamps, so this
script doesn't try to convert it. It just prints the number live so real
numbers can be read off directly: what it shows with the gripper open, closed
on nothing, and closed on the actual object being picked. The difference
between those is what protocol_b_limit_ma / holding_limit_ma should be set to
(the names say "mA" but both are compared as raw-register deltas now).

Usage:
    python measure_gripper_current.py --robot.port=COM3

Keys (press then Enter):
    o        open gripper
    c        close gripper
    q        quit
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import inference_daemon as daemon

# Last commanded gripper position, re-sent every tick (see the loop in
# main()). A single one-shot send_action() only pulses the servo briefly: it
# drives toward the target, and once it can't get closer (blocked by an
# object) it stops actively demanding torque and current falls back to ~0.
# The real daemon re-issues its target every control tick for exactly this
# reason (see hold_action / freeze_robot() in inference_daemon.py) — without
# that continuous re-assertion, current never sustains long enough to read.
gripper_target: float | None = None


def stdin_commands(action_key: str | None) -> None:
    global gripper_target
    for line in sys.stdin:
        cmd = line.strip().lower()
        if cmd == "q":
            os._exit(0)
        elif action_key is None:
            continue
        elif cmd == "o":
            gripper_target = 100.0
        elif cmd == "c":
            gripper_target = 0.0


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--robot.type", dest="robot_type", default="so101_follower")
    ap.add_argument("--robot.port", dest="robot_port", required=True)
    ap.add_argument("--robot.id", dest="robot_id", default="my_follower_arm")
    ap.add_argument("--hz", type=float, default=10.0, help="readout rate (default 10)")
    args = ap.parse_args()

    daemon.connect_robot(args.robot_type, args.robot_port, args.robot_id, "")
    # connect_robot() only sets up the bus; it never touches daemon.simulated
    # (that flip happens in inference_daemon.main(), which this script never
    # calls). extract_gripper_load() gates its only working read path — the
    # direct motor-bus read — on "not simulated", so leaving it at its default
    # True made load always read back as 0.0 here.
    daemon.simulated = False
    action_key = next((k for k in daemon.action_keys if k.startswith("gripper")), None)
    if action_key is None:
        print(f"Nenašel jsem gripper mezi action_keys: {daemon.action_keys} "
              "— otevírej/zavírej rukou přes leader rameno, tohle jen čte proud.")
    else:
        print(f"Gripper action key: {action_key}. Napiš 'o' + Enter pro otevřít, "
              "'c' + Enter pro zavřít, 'q' + Enter pro konec.")
    print("Dej předmět mezi čelisti a sleduj hodnotu při otevřeno / zavřeno naprázdno / sevřeno.\n")

    threading.Thread(target=stdin_commands, args=(action_key,), daemon=True).start()

    # Print Present_Voltage/Present_Temperature alongside the resolved load
    # register purely as a sanity check: if this project's servos ever change
    # and the resolved register goes flat again, seeing THOSE still move
    # confirms the bus read path itself is fine and it's the sensor, not a bug.
    bus = getattr(daemon.robot, "bus", None)
    extra_regs = ["Present_Voltage", "Present_Temperature"]

    try:
        while True:
            if action_key is not None and gripper_target is not None:
                daemon.robot.send_action({action_key: gripper_target})
            obs = daemon.robot.get_observation()
            load = daemon.extract_gripper_load(obs)
            extras = []
            if bus is not None and hasattr(bus, "read"):
                for reg in extra_regs:
                    try:
                        val = bus.read(reg, "gripper")
                        extras.append(f"{reg}={val}")
                    except Exception:
                        pass
            line = f"{daemon._load_register or '?'}: {load:8.1f}   " + "  ".join(extras)
            print(f"\r{line.ljust(70)}", end="", flush=True)
            time.sleep(1.0 / args.hz)
    except KeyboardInterrupt:
        pass
    finally:
        print()
        # A gripper left clamped on an object can make the servo refuse the
        # Torque_Enable=0 write during disconnect (Feetech raises "Overload
        # error!") — release it first so disconnect doesn't raise, and don't
        # let a disconnect failure mask what was actually being debugged here.
        if action_key is not None:
            try:
                daemon.robot.send_action({action_key: 100.0})
                time.sleep(0.5)
            except Exception:
                pass
        try:
            daemon.robot.disconnect()
        except Exception as e:
            print(f"(disconnect selhal, ignoruji: {e})")


if __name__ == "__main__":
    main()
