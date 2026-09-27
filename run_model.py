r"""Spustí jeden natrénovaný model samostatně, bez CEO/inspektora/re-plánů — pro rychlé
ruční ověření nového checkpointu (plynulost, přesnost) hned po tréninku.

Standardně bere --policy diplomka_3_catch_cube_60ep_act_cs30 (dnešní výsledek fronty).

    python run_model.py
        # katalogový krok catch_cube (příznak |grasp odvozen z config.json), opakovaně:
        # polož kostku, stiskni Enter, sleduj pokus, výsledek se vypíše, znovu Enter na další

    python run_model.py --policy outputs/training/diplomka_3_carry_cube_120ep_act_cs30 --step carry_cube
    python run_model.py --step homing --timeout 15
    python run_model.py --attempts 5           # po 5 pokusech samo skončí, jinak dokud nezmáčkneš q

Čte robota/kamery/protokoly z config.json (stejné jako appka), ať je test za stejných
podmínek jako živé měření — mění se jen to, co je tady výslovně vidět (--policy/--step/
--timeout). Protokoly A/B rozhodují o konci kroku úplně stejně jako v orchestraci; žádný
CEO ani inspektor tu není — jen se vypíše, jak a proč démon krok ukončil.

Beze změny orchestrator.py/inference_daemon.py/server.py/web/ — samostatný skript, žádný
zásah do zamrzlého nástroje pro měření (viz poznamky/DENIK.md).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"

# Dnešní výsledek fronty (viz poznamky/DENIK.md, 2026-09-27) — nejmenší model, natrénovaný
# jako první, aby šla plynulost/přesnost cs30 ověřit dřív, než dotrénuje zbytek.
DEFAULT_POLICY = "outputs/training/diplomka_3_catch_cube_60ep_act_cs30"
DEFAULT_STEP = "catch_cube"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"config.json nenalezen ({CONFIG_PATH}) — spusť appku aspoň jednou, ať vznikne.")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def cameras_json(cfg: dict) -> str:
    """Kopie orchestrator.cameras_json() — jedno/dvě kamery podle camera_*/camera2_* v
    configu. Zkopírováno záměrně (stejný důvod jako u train_queue.py): ať tenhle skript
    netahá zbytek orchestrator.py a jeho závislosti."""
    def entry(prefix: str) -> dict:
        name = (cfg.get(f"{prefix}_name") or "").strip()
        source = str(cfg.get(f"{prefix}_index", "")).strip()
        if not name or source == "":
            return {}
        try:
            source_val = int(source)
        except ValueError:
            source_val = source
        return {name: {"index_or_path": source_val, "width": int(cfg.get(f"{prefix}_width", 640)),
                       "height": int(cfg.get(f"{prefix}_height", 480)), "fps": int(cfg.get(f"{prefix}_fps", 30))}}

    cams = {**entry("camera"), **entry("camera2")}
    return json.dumps(cams) if cams else ""


def step_flags(cfg: dict, step_slug: str) -> tuple[bool, bool, bool, float | None]:
    """(grasp, reset, release, timeout_s) pro `step_slug` z config.json — stejný katalog,
    jaký by použila orchestrace, takže se démon ukončuje stejným protokolem jako v ostrém
    běhu. Neznámý krok (např. "baseline") dostane všechny příznaky vypnuté."""
    for s in cfg.get("steps", []):
        if s.get("slug") == step_slug:
            t = s.get("timeout_s")
            return bool(s.get("grasp")), bool(s.get("reset")), bool(s.get("release")), (float(t) if t else None)
    return False, False, False, None


def daemon_command(cfg: dict, policy_path: str) -> list[str]:
    cmd = [
        cfg.get("python") or sys.executable, "-u", str(HERE / "inference_daemon.py"),
        f"--robot.type={cfg.get('robot_type', 'so101_follower')}",
        f"--robot.id={cfg.get('robot_id', 'my_follower_arm')}",
        f"--policy.path={policy_path}",
        f"--fps={cfg.get('fps', 30)}",
    ]
    if cfg.get("robot_port"):
        cmd.append(f"--robot.port={cfg['robot_port']}")
    if cfg.get("device"):
        cmd.append(f"--device={cfg['device']}")
    cams = cameras_json(cfg)
    if cams:
        cmd.append(f"--robot.cameras={cams}")
    if not cfg.get("protocol_a_enabled", True):
        cmd.append("--no-protocol-a")
    if not cfg.get("protocol_b_enabled", True):
        cmd.append("--no-protocol-b")
    cmd += [
        f"--protocol-a.threshold={float(cfg.get('protocol_a_threshold_rad', 0.5))}",
        f"--protocol-a.target-threshold={float(cfg.get('protocol_a_target_threshold_rad', 5.0))}",
        f"--protocol-a.patience={int(cfg.get('protocol_a_patience', 5))}",
        f"--protocol-a.grasp-patience-extra={int(cfg.get('protocol_a_grasp_patience_extra', 5))}",
        f"--protocol-a.grace={float(cfg.get('protocol_a_grace_s', 1.0))}",
        f"--protocol-b.limit={float(cfg.get('protocol_b_limit_ma', 250))}",
        f"--protocol-b.patience={int(cfg.get('protocol_b_patience', 3))}",
        f"--protocol-b.grace={float(cfg.get('protocol_b_grace_s', 0.75))}",
        f"--protocol-b.stability={float(cfg.get('protocol_b_stability_slope', 30.0))}",
    ]
    if cfg.get("temporal_ensemble", False):
        cmd.append(f"--temporal-ensemble.coeff={float(cfg.get('temporal_ensemble_coeff', 0.01))}")
    return cmd


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", default=DEFAULT_POLICY, help=f"cesta k checkpointu (výchozí: {DEFAULT_POLICY})")
    ap.add_argument("--step", default=DEFAULT_STEP,
                    help=f"krok z config.json, určuje |grasp/|reset/|release a výchozí timeout (výchozí: {DEFAULT_STEP})")
    ap.add_argument("--timeout", type=float, default=None, help="časový limit pokusu v s (jinak krokový, jinak episode_time_s)")
    ap.add_argument("--attempts", type=int, default=None, help="po kolika pokusech skončit (jinak dokud nezmáčkneš q)")
    args = ap.parse_args()

    cfg = load_config()
    grasp, reset, release, step_timeout = step_flags(cfg, args.step)
    timeout = args.timeout or step_timeout or float(cfg.get("episode_time_s", 20))
    flags = "".join(f"|{f}" for f, on in (("grasp", grasp), ("reset", reset), ("release", release)) if on)

    cmd = daemon_command(cfg, args.policy)
    print("Spouštím:", " ".join(cmd))
    print(f"Krok '{args.step}'{flags or ' (bez příznaků)'}, timeout {timeout:.0f} s.\n")

    proc = subprocess.Popen(cmd, cwd=str(HERE), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)

    ready = threading.Event()
    task_done = threading.Event()
    last_reason = ""
    stop_reading = threading.Event()

    def reader():
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith("[STATUS] DAEMON_READY"):
                print("daemon:", line); ready.set()
            elif line.startswith("[STATUS] TASK_DONE"):
                nonlocal last_reason
                last_reason = line.split("|", 1)[-1].strip() if "|" in line else line
                print("daemon:", line); task_done.set()
            elif line.startswith("[STATUS] POLICY_ERROR") or "Traceback" in line:
                print("daemon:", line)
            # [TELEMETRY]/ostatní [STATUS] řádky se schválně nevypisují — 5x/s by
            # zaplavilo terminál; kdo je potřeba, ať sleduje telemetry/*.jsonl.
            if stop_reading.is_set():
                return

    threading.Thread(target=reader, daemon=True).start()

    if not ready.wait(timeout=120):
        print("Démon se nespustil do 120 s — koukni se na výpis výš."); proc.terminate(); return

    print("\nPolož kostku a stiskni Enter pro pokus (q + Enter pro konec).\n")
    n = 0
    try:
        while args.attempts is None or n < args.attempts:
            try:
                line = input(f"[pokus {n + 1}] Enter = spustit, q = konec: ").strip().lower()
            except EOFError:
                break
            if line == "q":
                break
            n += 1
            task_done.clear()
            proc.stdin.write(f"SET_TASK:{args.step}{flags}|timeout={timeout}\n")
            proc.stdin.flush()
            t0 = time.time()
            if not task_done.wait(timeout=timeout + 10):
                print("  (TASK_DONE nepřišlo včas — démon možná spadl, koukni se na výpis výš)")
                continue
            print(f"  -> {last_reason}  ({time.time() - t0:.1f} s)\n")
    except KeyboardInterrupt:
        print("\nPřerušeno.")
    finally:
        stop_reading.set()
        try:
            proc.stdin.write("QUIT\n"); proc.stdin.flush()
            proc.wait(timeout=10)
        except Exception:
            proc.terminate()
        print(f"\nHotovo — {n} pokus(ů).")


if __name__ == "__main__":
    main()
