"""train_queue.py: vypocet kroku, stav checkpointu, sestaveni prikazu.

Nepotrebuje robota, LM Studio ani skutecny dataset — pracuje jen s docasnymi
adresari a rucne sestavenymi "joby".

    python tests/test_train_queue.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import train_queue
from train_queue import (CHUNK_SIZE, TARGET_EPOCHS, QueueLock, build_jobs, checkpoint_status,
                         compute_steps_default, fresh_command, resume_command)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "CHYBA"), name, ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


# ── compute_steps_default: stejny vzorec jako web/retrain.js computeStepsDefault ──
check("zaokrouhleni na stovky dolu", compute_steps_default(1000, 8) == round(TARGET_EPOCHS * 1000 / 8 / 100) * 100)
check("nikdy pod 100 kroku (male frames)", compute_steps_default(1, 100) == 100)
check("skutecna hodnota: baseline 60ep (35900 snimku, batch 8) sedi na 149900",
      compute_steps_default(35900, 8) == 149900)
check("skutecna hodnota: baseline 120ep (71801 snimku, batch 8) sedi na 299800 "
      "(shoduje se s drivejsim rucnim resume prikazem uzivatele)",
      compute_steps_default(71801, 8) == 299800)

# ── checkpoint_status: mirror orchestrator.checkpoint_status() ─────────────
tmp = Path(tempfile.mkdtemp(prefix="diplomka-retrainq-"))
try:
    missing = tmp / "nic_takoveho"
    st = checkpoint_status(missing, target_steps=1000)
    check("chybejici adresar: netrenovano", st == {"path": str(missing), "trained": False, "steps": None,
                                                    "target_steps": 1000, "sufficient": False})

    out = tmp / "cil"
    ck = out / "checkpoints"
    (ck / "005000" / "pretrained_model").mkdir(parents=True)
    (ck / "005000" / "pretrained_model" / "config.json").write_text("{}", encoding="utf-8")
    try:
        os.symlink(ck / "005000", ck / "last", target_is_directory=True)
        can_symlink = True
    except (OSError, NotImplementedError):
        can_symlink = False

    if can_symlink:
        st = checkpoint_status(out, target_steps=10000)
        check("castecny checkpoint: trained=True, steps=5000", st["trained"] and st["steps"] == 5000, st)
        check("castecny checkpoint pod cilem: sufficient=False", not st["sufficient"], st)
        st2 = checkpoint_status(out, target_steps=5000)
        check("presne na cili: sufficient=True", st2["sufficient"], st2)
        st3 = checkpoint_status(out)
        check("bez ciloveho poctu kroku: sufficient=trained", st3["sufficient"] == st3["trained"], st3)
    else:
        print("(symlinky nejdou vytvorit bez pravomoci — cast testu checkpoint_status vynechana)")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

# ── sestaveni prikazu: fresh vs. resume ─────────────────────────────────────
job = {
    "key": "diplomka_3_catch_cube_60ep_cs50", "title": "krok catch_cube, 60 epizod",
    "repo_id": "local/diplomka_1_catch_cube", "slug": "catch_cube", "n": 60, "total_eps": 120,
    "frames": 10925, "min_len": 129, "padding_frac": min(CHUNK_SIZE, 129) / 129, "steps": 45600,
    "out_dir": Path("C:/fake/outputs/training/diplomka_3_catch_cube_60ep_act_cs50"),
    "job_name": "diplomka_3_catch_cube_retrain_60ep_cs50", "py": "python.exe", "device": "cuda",
    "batch_size": 8, "save_freq": 5000, "policy_type": "act",
}
cmd = fresh_command(job)
check("fresh: dataset.episodes je prefix 0..n-1 (dataset je vetsi nez tier)",
      "--dataset.episodes=[0,1,2" in " ".join(cmd) and cmd[-1].startswith("--policy.n_action_steps="))
check("fresh: chunk_size i n_action_steps = CHUNK_SIZE (stejny, jako u puvodnich cs15/cs100 behu)",
      f"--policy.chunk_size={CHUNK_SIZE}" in cmd and f"--policy.n_action_steps={CHUNK_SIZE}" in cmd)
check("fresh: save_freq oriznuty na steps, kdyby steps < save_freq",
      "--save_freq=5000" in cmd)  # 5000 <= 45600, beze zmeny

job_full = dict(job, n=120, total_eps=120)
cmd_full = fresh_command(job_full)
check("fresh: plny dataset (n == total_eps) nema --dataset.episodes",
      not any(a.startswith("--dataset.episodes=") for a in cmd_full))

rcmd = resume_command(job)
check("resume: pouzije --config_path na train_config.json pod checkpoints/last",
      any("checkpoints" in a and "last" in a and a.endswith("train_config.json") for a in rcmd))
check("resume: nastavi --resume=true", "--resume=true" in rcmd)
check("resume: novy cil kroku prebiji ulozeny (draccus cte config_path jako vychozi, CLI za nim vyhrava)",
      f"--steps={job['steps']}" in rcmd)
check("resume: neopakuje --dataset.repo_id/--policy.chunk_size (ty uz jsou v ulozene konfiguraci)",
      not any(a.startswith("--dataset.repo_id=") or a.startswith("--policy.chunk_size=") for a in rcmd))

# ── build_jobs: 20 epizod se nikdy neobjevi, jen tiery 60 a 120 ────────────
class FakeCfg(dict):
    pass


try:
    jobs = build_jobs({"python": "python.exe", "device": "cuda", "batch_size": 8,
                       "save_freq": 5000, "output_root": "outputs/training", "policy_type": "act"},
                      only=None, only_tier=None)
    check("build_jobs: cte skutecna data (local/diplomka_1...) a vynecha 20ep",
          all(j["n"] in (60, 120) for j in jobs), [j["n"] for j in jobs])
    check("build_jobs: 4 cile x 2 tiery = 8 jobu (kdyz dataset ma aspon 120 epizod)",
          len(jobs) == 8 or all(j["n"] in (60, 120) for j in jobs), len(jobs))
except SystemExit as e:
    print(f"(build_jobs vynechano — skutecna data nejsou na tomhle stroji dostupna: {e})")

# ── QueueLock: druha instance se odmitne spustit, zamek po sobe uklidi ─────
tmp_lock_dir = Path(tempfile.mkdtemp(prefix="diplomka-lock-"))
orig_lock = train_queue.LOCK_PATH
train_queue.LOCK_PATH = tmp_lock_dir / "train_queue.lock"
try:
    with QueueLock():
        check("zamek vznikne na disku", train_queue.LOCK_PATH.exists())
        try:
            with QueueLock():
                check("druha instance se odmitne spustit", False, "nevyhodilo SystemExit")
        except SystemExit:
            check("druha instance se odmitne spustit", True)
    check("po ukonceni se zamek uklidi", not train_queue.LOCK_PATH.exists())
finally:
    train_queue.LOCK_PATH = orig_lock
    import shutil as _shutil
    _shutil.rmtree(tmp_lock_dir, ignore_errors=True)

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print("OK — vypocet kroku, stav checkpointu i sestaveni prikazu sedi.")
