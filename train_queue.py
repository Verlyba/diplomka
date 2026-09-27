r"""Fronta tréninků bez dozoru: baseline + 3 dovednosti x {60, 120} epizod, chunk_size=50,
výstupy pojmenované pod projekt diplomka_3.

Proč vzniklo. Retrénink na chunk_size=15 (viz web/retrain.html) sice dodržel padding pod 20 %
(poznamky/DENIK.md, 2026-09-19), ale živé běhy z 25.-26. 9. (tamtéž, 2026-09-25/26) ukázaly
trhaný, neplynulý pohyb a přesnost proti staršímu chunk_size=100 se citelně nezlepšila.
Uživatel 2026-09-27 rozhodl přeučit na chunk_size=50 — kompromis mezi tím a starým chování
(commituje se na delší úsek plánu najednou, míň re-plánování uvnitř kroku) — a chce to spustit
bez toho, aby u toho seděl: tenhle skript proto řadu (queue) 8 tréninků odjede sám, jeden po
druhém, přeskočí, co je už hotové, doběhne přerušené, a nezastaví se na první chybě.

Zdrojová data zůstávají tam, kde jsou (`local/diplomka_1[...]`, živý rostoucí dataset, viz
komentář v web/retrain.js) — mění se jen NÁZEV výstupu (checkpointy jdou rovnou pod jméno
`diplomka_3_*`, bez dodatečného kopírovacího/přejmenovacího kroku, jaký byl potřeba pro
diplomka_2, protože tehdy šlo o retrénink na chunk_size=15 s cílem "appka nepozná, že je to
předěláno" — tady žádné takové maskování není, diplomka_3 je od začátku svůj vlastní projekt).
20 epizod se vynechává na výslovné přání uživatele — s tak málo daty se model dřív netrefil
ani jednou (testováno na začátku projektu, mimo tuhle appku, proto to v mereni/ není vidět).

Konfigurace (python/zařízení/batch_size/...) se čte z config.json, aby skript sledoval
stejné nastavení jako zbytek appky. Beze změny orchestrator.py/inference_daemon.py/server.py/
web/ — samostatný skript, žádný zásah do zamrzlého nástroje pro měření.

Spuštění (ideálně na pozadí, ať přežije zavření terminálu):
    powershell -NoProfile -Command "Start-Process -FilePath 'C:\Users\green\miniconda3\envs\lerobot\python.exe' -ArgumentList 'train_queue.py','-y' -WorkingDirectory 'C:\Users\green\diplomka' -RedirectStandardOutput 'train_queue.out.log' -RedirectStandardError 'train_queue.err.log' -WindowStyle Hidden"

Bez -y se nejdřív vypíše celý plán (8 běhů, odhadované kroky, upozornění na padding) a čeká na
potvrzení — pro kontrolu před tím, než se to nechá běžet přes noc. S -y start rovnou.

    python train_queue.py --dry-run     # jen ukázat plán a příkazy, nic nespouštět
    python train_queue.py -y            # spustit bez dotazu
    python train_queue.py --only catch_cube --tier 60   # jen jeden cíl/tier

Stav se ukládá do train_queue_status.json vedle skriptu — opakované spuštění přeskočí
hotové běhy a doběhne rozdělané (--resume), takže přerušení (Ctrl+C, výpadek, restart stroje)
neztratí postup.

Na téhle větvi už jednou existoval `train_queue.py` (commit 1d6c8a1, patrně z jiné relace) —
tahle verze ho nahrazuje, ne rozšiřuje. Důvod: ten starší skript odvozoval zdrojový dataset
POUZE z `local/<aktivní task_slug>` a výstup pojmenovával stejným jménem — pro projekt
pojmenovaný jinak než zdrojová data (přesně tenhle případ: zdroj `diplomka_1`, cíl
`diplomka_3`) by tak nikdy nenaběhl (datasety `local/diplomka_3*` neexistují). Navíc vyžadoval
běžící `python server.py` po celou dobu fronty (hodiny až dny) a rozdělaný checkpoint po
přerušení nedoučil, jen ho přeskočil s poznámkou "řeš ručně" — pro běh bez dozoru nevhodné.
Tahle verze čte data přímo z disku (žádná závislost na běžícím serveru), zdroj a cíl má
oddělené (SOURCE_SLUG/DEST_SLUG níže) a rozdělaný checkpoint sama doučí (--resume).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATUS_PATH = HERE / "train_queue_status.json"
LOCK_PATH = HERE / "train_queue.lock"
LOG_DIR = HERE / "outputs" / "training" / "logs"
LOCAL_DATASETS_DIR = Path.home() / ".cache" / "huggingface" / "lerobot" / "local"


class QueueLock:
    """Zabrání dvěma instancím fronty běžet zároveň (obě by soupeřily o GPU a přepisovaly
    si train_queue_status.json). Přenositelné — bez fcntl/msvcrt, jen atomické vytvoření
    souboru (os.O_CREAT|O_EXCL) — funguje stejně na Windows i jinde, na rozdíl od fcntl.flock
    (POSIX-only, na Windows by se zámek tiše přeskočil)."""

    def __enter__(self):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise SystemExit(
                f"{LOCK_PATH.name} už existuje — buď fronta běží v jiném okně, nebo zůstal "
                "po pádu. Pokud víš, že neběží, smaž ten soubor a spusť skript znovu.")
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass

# ── Rozhodnutí uživatele 2026-09-27 (poznamky/DENIK.md) ────────────────────
SOURCE_SLUG = "diplomka_1"        # odkud se čtou demonstrace (živý dataset, beze změny)
DEST_SLUG = "diplomka_3"          # jak se pojmenují výstupní checkpointy
CHUNK_SIZE = 50
TIERS = (60, 120)                 # 20 epizod záměrně vynecháno
SKILLS = ("catch_cube", "carry_cube", "homing")   # + baseline (slug=None)

# Empirický cíl "epoch" (steps*batch_size/frames) odvozený 2026-09-19 z historických
# checkpointů (web/retrain.js, TARGET_EPOCHS) — stejná konstanta, ať nový výchozí počet
# kroků drží stejné měřítko jako dřívější retrainy (cs15/cs100).
TARGET_EPOCHS = 33.4


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"config.json nenalezen ({CONFIG_PATH}) — spusť appku aspoň jednou, ať vznikne.")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def episode_lengths(repo_dir: Path) -> list[int]:
    """(episode_index, length) seřazené podle episode_index, čtené přímo z meta/episodes/*.parquet
    — stejný postup jako server.dataset_episode_lengths(), zkopírovaný sem, aby skript nezávisel
    na běžícím serveru ani na importu server.py (ten má vedlejší efekty při načtení modulu)."""
    try:
        import pyarrow.parquet as pq
    except ImportError as e:
        raise SystemExit(
            "Chybí pyarrow — spusť tenhle skript stejným pythonem jako appku/lerobot "
            f"(config.json 'python': {load_config().get('python')}), ne systémovým.") from e
    pairs: list[tuple[int, int]] = []
    for pq_file in sorted((repo_dir / "meta" / "episodes").glob("chunk-*/*.parquet")):
        table = pq.read_table(pq_file, columns=["episode_index", "length"])
        pairs.extend(zip(table.column("episode_index").to_pylist(), table.column("length").to_pylist()))
    if not pairs:
        raise SystemExit(f"Dataset '{repo_dir}' nemá meta/episodes/*.parquet — neexistuje nebo je prázdný.")
    pairs.sort(key=lambda p: p[0])
    return [int(length) for _, length in pairs]


def compute_steps_default(frames: int, batch_size: int) -> int:
    """Stejný vzorec jako web/retrain.js computeStepsDefault(): zaokrouhleno na stovky,
    minimálně 100 kroků."""
    raw = TARGET_EPOCHS * frames / batch_size
    return max(100, round(raw / 100) * 100)


def checkpoint_status(output_dir: Path, target_steps: int | None = None) -> dict:
    """Kopie orchestrator.checkpoint_status() — jestli <output_dir>/checkpoints/last vede na
    reálný, načtitelný checkpoint a na kolika krocích. Zkopírováno záměrně (ne importováno),
    ať tenhle skript netahá zbytek orchestrator.py (a jeho závislosti na robotu/LM Studiu)."""
    last = output_dir / "checkpoints" / "last"
    resolved = last.resolve() if last.exists() else None
    pretrained = (resolved / "pretrained_model") if resolved else None
    trained = bool(pretrained and (pretrained / "config.json").exists())
    steps = None
    if trained:
        try:
            steps = int(resolved.name)
        except ValueError:
            steps = None
    sufficient = trained and (target_steps is None or (steps or 0) >= target_steps)
    return {"path": str(output_dir), "trained": trained, "steps": steps,
            "target_steps": target_steps, "sufficient": sufficient}


def build_jobs(cfg: dict, only: str | None, only_tier: int | None) -> list[dict]:
    py = cfg.get("python") or sys.executable
    device = cfg.get("device", "cuda")
    batch_size = int(cfg.get("batch_size", 8))
    save_freq = int(cfg.get("save_freq", 5000))
    output_root = HERE / (cfg.get("output_root") or "outputs/training")
    policy_type = cfg.get("policy_type", "act")

    targets = [{"slug": None, "title": "baseline (celá úloha)", "src": SOURCE_SLUG}]
    for slug in SKILLS:
        targets.append({"slug": slug, "title": f"krok {slug}", "src": f"{SOURCE_SLUG}_{slug}"})
    if only:
        targets = [t for t in targets if (t["slug"] or "baseline") == only]
        if not targets:
            raise SystemExit(f"--only {only!r} neodpovídá žádnému cíli (baseline, {', '.join(SKILLS)}).")

    jobs = []
    for t in targets:
        repo_dir = LOCAL_DATASETS_DIR / t["src"]
        lengths = episode_lengths(repo_dir)
        total = len(lengths)
        base = f"{DEST_SLUG}_{t['slug']}" if t["slug"] else DEST_SLUG
        for n in TIERS:
            if only_tier is not None and n != only_tier:
                continue
            if n > total:
                print(f"  přeskakuji {base}_{n}ep — dataset '{t['src']}' má jen {total} epizod, ne {n}.")
                continue
            sub = lengths[:n]
            frames = sum(sub)
            min_len = min(sub)
            steps = compute_steps_default(frames, batch_size)
            out_dir = output_root / f"{base}_{n}ep_{policy_type}_cs{CHUNK_SIZE}"
            padding_frac = min(CHUNK_SIZE, min_len) / min_len
            jobs.append({
                "key": f"{base}_{n}ep_cs{CHUNK_SIZE}",
                "title": f"{t['title']}, {n} epizod",
                "repo_id": f"local/{t['src']}",
                "slug": t["slug"], "n": n, "total_eps": total, "frames": frames,
                "min_len": min_len, "padding_frac": padding_frac, "steps": steps,
                "out_dir": out_dir, "job_name": f"{base}_retrain_{n}ep_cs{CHUNK_SIZE}",
                "py": py, "device": device, "batch_size": batch_size,
                "save_freq": save_freq, "policy_type": policy_type,
            })
    return jobs


def fresh_command(job: dict) -> list[str]:
    cmd = [job["py"], "-m", "lerobot.scripts.lerobot_train",
           f"--policy.type={job['policy_type']}", f"--dataset.repo_id={job['repo_id']}"]
    if job["n"] < job["total_eps"]:
        episodes_arg = "[" + ",".join(str(i) for i in range(job["n"])) + "]"
        cmd.append(f"--dataset.episodes={episodes_arg}")
    cmd += [
        f"--steps={job['steps']}", f"--batch_size={job['batch_size']}",
        f"--save_freq={min(job['save_freq'], job['steps'])}", f"--job_name={job['job_name']}",
        f"--policy.device={job['device']}", "--wandb.enable=false",
        f"--output_dir={job['out_dir']}", "--policy.push_to_hub=false",
        f"--policy.chunk_size={CHUNK_SIZE}", f"--policy.n_action_steps={CHUNK_SIZE}",
    ]
    return cmd


def resume_command(job: dict) -> list[str]:
    """--config_path na uložený train_config.json poslední ('last') checkpointu; --steps se
    přepíše na nový cíl (draccus config z config_path bere jako výchozí hodnoty, CLI argumenty
    za ním je přepíšou — ověřeno v lerobot/configs/train.py, TrainPipelineConfig.validate())."""
    cfg_path = job["out_dir"] / "checkpoints" / "last" / "pretrained_model" / "train_config.json"
    return [job["py"], "-m", "lerobot.scripts.lerobot_train",
            f"--config_path={cfg_path}", "--resume=true", f"--steps={job['steps']}"]


def load_status() -> dict:
    if STATUS_PATH.exists():
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_status(status: dict) -> None:
    tmp = STATUS_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    tmp.replace(STATUS_PATH)


def run_job(job: dict, cmd: list[str], log_fh) -> int:
    """Spustí trénink, řádek po řádku tiskne na konzoli i do logu (stejný vzorec čtení
    výstupu po řádcích jako orchestrator.Daemon._read_output). Vrací návratový kód."""
    line = f"$ {' '.join(cmd)}"
    print(line); log_fh.write(line + "\n"); log_fh.flush()
    proc = subprocess.Popen(cmd, cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    try:
        for out_line in proc.stdout:
            sys.stdout.write(out_line)
            log_fh.write(out_line)
            log_fh.flush()
    except KeyboardInterrupt:
        proc.terminate()
        raise
    return proc.wait()


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-y", "--yes", action="store_true", help="nečekat na potvrzení, spustit rovnou")
    ap.add_argument("--dry-run", action="store_true", help="jen vypsat plán a příkazy, nic nespustit")
    ap.add_argument("--only", help="omezit na jeden cíl: baseline, " + ", ".join(SKILLS))
    ap.add_argument("--tier", type=int, help="omezit na jeden tier (60 nebo 120)")
    args = ap.parse_args()

    cfg = load_config()
    print(f"Čtu config.json ({cfg.get('python')}, zařízení {cfg.get('device')}, "
          f"batch_size {cfg.get('batch_size')}) — zdroj dat '{SOURCE_SLUG}', cíl '{DEST_SLUG}', "
          f"chunk_size={CHUNK_SIZE}, tiery {TIERS}.\n")
    jobs = build_jobs(cfg, args.only, args.tier)
    if not jobs:
        raise SystemExit("Žádný běh k naplánování (zkontroluj --only/--tier).")

    status = load_status()
    print(f"{'#':>2} {'cíl':30s} {'epizod':>6s} {'kroků':>7s} {'padding nejhorší':>16s} {'stav':>10s}  výstup")
    for i, j in enumerate(jobs, 1):
        st = checkpoint_status(j["out_dir"], j["steps"])
        state = "hotovo" if st["sufficient"] else ("doběhne" if st["trained"] else "nové")
        warn = " ⚠" if j["padding_frac"] > 0.5 else (" !" if j["padding_frac"] > 0.2 else "")
        print(f"{i:>2} {j['title']:30s} {j['n']:>6d} {j['steps']:>7d} "
              f"{j['padding_frac']*100:>14.1f} %{warn} {state:>10s}  {j['out_dir'].name}")
    if any(j["padding_frac"] > 0.5 for j in jobs):
        print("\n⚠ U některých cílů je při chunk_size=50 přes polovinu nejkratší epizody v paddingu "
              "(carry_cube má nejkratší epizody, viz mereni/README.md) — vědomé rozhodnutí uživatele "
              "2026-09-27, ne přehlédnutí.")

    if args.dry_run:
        print("\n--- příkazy (--dry-run, nic se nespouští) ---")
        for j in jobs:
            st = checkpoint_status(j["out_dir"], j["steps"])
            cmd = resume_command(j) if (st["trained"] and not st["sufficient"]) else fresh_command(j)
            print(f"\n# {j['key']}")
            print(" ".join(cmd))
        return

    if not args.yes:
        try:
            reply = input("\nSpustit frontu podle plánu výše? [a/n] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("a", "ano", "y", "yes"):
            print("Zrušeno, nic se nespustilo.")
            return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nStartuji frontu ({len(jobs)} běhů). Postup se ukládá do {STATUS_PATH.name} "
          f"— přerušení (Ctrl+C, výpadek) lze doběhnout opětovným spuštěním tohohle skriptu.\n")

    with QueueLock():
        for i, j in enumerate(jobs, 1):
            key = j["key"]
            st = checkpoint_status(j["out_dir"], j["steps"])
            if st["sufficient"]:
                print(f"[{i}/{len(jobs)}] {key}: už hotovo ({st['steps']} kr.), přeskakuji.")
                status[key] = {"state": "done", "steps": st["steps"], "checked_at": dt.datetime.now().isoformat()}
                save_status(status)
                continue

            resuming = st["trained"]
            cmd = resume_command(j) if resuming else fresh_command(j)
            print(f"[{i}/{len(jobs)}] {key}: {'doběhnu (' + str(st['steps']) + ' -> ' + str(j['steps']) + ' kr.)' if resuming else 'nový trénink, ' + str(j['steps']) + ' kr.'}")
            status[key] = {"state": "running", "started_at": dt.datetime.now().isoformat(), "target_steps": j["steps"]}
            save_status(status)

            log_path = LOG_DIR / f"{key}.log"
            t0 = time.time()
            with open(log_path, "a", encoding="utf-8") as log_fh:
                log_fh.write(f"\n===== {dt.datetime.now().isoformat()} ({'resume' if resuming else 'fresh'}) =====\n")
                try:
                    rc = run_job(j, cmd, log_fh)
                except KeyboardInterrupt:
                    elapsed = fmt_duration(time.time() - t0)
                    print(f"\n[{i}/{len(jobs)}] {key}: přerušeno uživatelem po {elapsed}.")
                    status[key] = {"state": "interrupted", "elapsed_s": time.time() - t0}
                    save_status(status)
                    print("Fronta zastavena. Spusť skript znovu, ať doběhne zbytek (rozdělaný běh se doučí).")
                    return
            elapsed = time.time() - t0

            final = checkpoint_status(j["out_dir"], j["steps"])
            if final["sufficient"]:
                print(f"[{i}/{len(jobs)}] {key}: hotovo za {fmt_duration(elapsed)} ({final['steps']} kr.).")
                status[key] = {"state": "done", "steps": final["steps"], "elapsed_s": elapsed}
            else:
                print(f"[{i}/{len(jobs)}] {key}: SELHALO (návratový kód {rc}) po {fmt_duration(elapsed)} — "
                      f"log v {log_path}. Pokračuji dalším během ve frontě.")
                status[key] = {"state": "failed", "returncode": rc, "elapsed_s": elapsed,
                              "log": str(log_path)}
            save_status(status)

    print("\n--- souhrn ---")
    for j in jobs:
        s = status.get(j["key"], {})
        print(f"  {j['key']:40s} {s.get('state', '?'):12s} {s.get('steps', '')}")
    failed = [k for k, v in status.items() if v.get("state") == "failed"]
    if failed:
        print(f"\n{len(failed)} běh(ů) selhalo: {', '.join(failed)} — zkontroluj logy a spusť skript znovu.")


if __name__ == "__main__":
    main()
