#!/usr/bin/env python3
"""
Fronta na trénink chybějících ACT modelů.

web/retrain.html umí pro každou kombinaci (cíl × velikost datasetu) vygenerovat
přesný `lerobot_train` příkaz a ukázat, jestli už na disku existuje natrénovaný
checkpoint se stejným jménem — ale spustit ty chybějící příkazy za sebou musel
dosud člověk ručně, jeden po druhém ("kopírovat" -> vložit do terminálu ->
počkat 3 hodiny -> zopakovat). Tenhle skript dělá přesně tohle samo: zjistí
stejným způsobem jako retrain.js, které kombinace ještě chybí, seřadí je (od
nejmenší po největší, ať se první případná chyba v konfiguraci projeví rychle,
ne až po 3 hodinách) a pustí je jednu po druhé — jakmile jedna dotrénuje,
hned naváže další, dokud fronta nedojde nebo dokud něco neselže.

Vyžaduje spuštěný `python server.py` (výchozí http://localhost:8000) — odsud
se čte /api/config, /api/models a /api/dataset-episode-lengths, aby seznam
"co chybí" a znění příkazu byly zaručeně stejné jako to, co ukazuje
retrain.html (žádná druhá, potenciálně rozjetá kopie stejné logiky).

Použití:
    python train_queue.py                      # ukáže frontu, zeptá se na potvrzení
    python train_queue.py --dry-run             # jen ukáže frontu, nic nespustí
    python train_queue.py --chunk-size 50 -y    # vynutí chunk_size=50 všem, spustí bez ptaní
    python train_queue.py --only baseline --only catch_cube   # jen vybrané cíle

Pozor: tohle NEKONTROLUJE, jestli GPU zrovna nepoužívá něco jiného (jiný
ruční trénink, inference_daemon.py...) — jen hlídá, aby neběžely dvě instance
tyhle fronty najednou (soubor .lock v --log-dir).

Doporučení pro běh přes noc / přes školu: spusť to v `tmux`/`screen`, ne
v obyčejném terminálu, který zemře s odhlášením SSH nebo zavřením notebooku:

    tmux new -s train
    python train_queue.py --chunk-size 50 -y
    # Ctrl+B, D pro odpojení od tmuxu (trénink běží dál); `tmux attach -t train` pro návrat
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

try:
    import fcntl
except ImportError:  # ne-POSIX systém — zámek proti dvojímu spuštění se prostě vynechá
    fcntl = None

HERE = Path(__file__).resolve().parent

# Viz stejná konstanta a komentář ve web/retrain.js — kolik "epoch" (steps *
# batch_size / frames) měly historicky dobře natrénované checkpointy.
TARGET_EPOCHS = 33.4
CHUNK_CANDIDATES = [10, 15, 20, 30, 50, 100]
PAD_THRESHOLD = 0.20


def fetch_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"nepodařilo se spojit s {url} — běží `python server.py`? ({e})"
        ) from e


# ── Stejná logika jako web/retrain.js (viz komentáře tam) ───────────────────

def choose_global_chunk_size(length_arrays: list[list[int]], threshold: float = PAD_THRESHOLD) -> dict:
    global_min = min(min(lengths) for lengths in length_arrays)
    best = None
    for c in CHUNK_CANDIDATES:
        if min(c, global_min) / global_min <= threshold:
            best = c
    return {"chunk_size": best if best is not None else CHUNK_CANDIDATES[0],
            "global_min": global_min, "forced": best is None}


def canonicalize_tiers(all_detected: list[int], tolerance: int = 3) -> dict[int, int]:
    counts: dict[int, int] = {}
    for n in all_detected:
        counts[n] = counts.get(n, 0) + 1
    uniq = sorted(counts)
    clusters: list[list[int]] = []
    for n in uniq:
        if clusters and n - clusters[-1][-1] <= tolerance:
            clusters[-1].append(n)
        else:
            clusters.append([n])
    mapping: dict[int, int] = {}
    for cluster in clusters:
        def sort_key(x: int) -> tuple:
            return (0 if x % 10 == 0 else 1, -counts[x], x)
        canonical = sorted(cluster, key=sort_key)[0]
        for n in cluster:
            mapping[n] = canonical
    return mapping


def detect_tiers(checkpoints: list[dict], current_eps: int,
                  canonical_map: dict[int, int]) -> list[tuple[int, int | None]]:
    corrected_from: dict[int, int] = {}
    tiers = {current_eps}
    for c in checkpoints or []:
        m = re.search(r"_(\d+)ep_", c.get("name", ""))
        if not m:
            continue
        raw = int(m.group(1))
        canonical = canonical_map.get(raw, raw)
        tiers.add(canonical)
        if canonical != raw:
            corrected_from[canonical] = raw
    return sorted((n, corrected_from.get(n)) for n in tiers if 0 < n <= current_eps)


def compute_steps_default(frames: int, batch_size: int) -> int:
    raw = TARGET_EPOCHS * frames / batch_size
    return max(100, round(raw / 100) * 100)


def episodes_arg(n: int) -> str:
    return "[" + ",".join(str(i) for i in range(n)) + "]"


def train_command(*, py: str, repo: str, out_dir: str, steps: int, batch_size: int,
                   save_freq: int, device: str, policy_type: str, job_name: str,
                   chunk_size: int, episodes_n: int, total_eps: int) -> list[str]:
    """Stejné argumenty jako trainCommand() ve web/retrain.js, ale jako argv
    pole — subprocess je pustí přímo bez shellu, takže žádné ruční quotování
    není potřeba."""
    save_freq_clamped = min(save_freq, steps)
    argv = [py, "-m", "lerobot.scripts.lerobot_train",
            f"--policy.type={policy_type}", f"--dataset.repo_id={repo}"]
    if episodes_n < total_eps:
        argv.append(f"--dataset.episodes={episodes_arg(episodes_n)}")
    argv += [
        f"--steps={steps}", f"--batch_size={batch_size}",
        f"--save_freq={save_freq_clamped}", f"--job_name={job_name}",
        f"--policy.device={device}", "--wandb.enable=false",
        f"--output_dir={out_dir}", "--policy.push_to_hub=false",
        f"--policy.chunk_size={chunk_size}", f"--policy.n_action_steps={chunk_size}",
    ]
    return argv


def disk_status(existing_checkpoints: list[dict], out_dir: str) -> tuple[str, int | None]:
    base = out_dir.rstrip("/").split("/")[-1]
    for c in existing_checkpoints or []:
        if c.get("name") == base:
            return ("trained" if c.get("trained") else "partial", c.get("steps"))
    return ("missing", None)


@dataclass
class Job:
    target_title: str
    slug: str | None
    repo_id: str
    n: int
    total_eps: int
    corrected_from: int | None
    chunk_size: int
    steps: int
    out_dir: str
    job_name: str
    status: str  # 'missing' | 'partial' | 'trained'
    pad_frac: float
    py: str
    batch_size: int
    save_freq: int
    device: str
    policy_type: str

    def argv(self) -> list[str]:
        return train_command(
            py=self.py, repo=self.repo_id, out_dir=self.out_dir, steps=self.steps,
            batch_size=self.batch_size, save_freq=self.save_freq, device=self.device,
            policy_type=self.policy_type, job_name=self.job_name, chunk_size=self.chunk_size,
            episodes_n=self.n, total_eps=self.total_eps,
        )


def build_jobs(server: str, forced_chunk_size: int | None,
                forced_steps: int | None) -> list[Job]:
    cfg = fetch_json(f"{server}/api/config")
    status = fetch_json(f"{server}/api/models")

    task_slug = cfg.get("task_slug", "task")
    output_root = cfg.get("output_root") or "outputs/training"
    policy_type = cfg.get("policy_type", "act")
    py = cfg.get("python") or "python"
    device = cfg.get("device") or "cuda"
    batch_size = int(cfg.get("batch_size") or 8)
    save_freq = int(cfg.get("save_freq") or 5000)

    targets = [{"title": "Baseline (celá úloha)", "slug": None, "repo_id": f"local/{task_slug}"}]
    for s in cfg.get("steps") or []:
        slug = s.get("slug")
        if slug:
            targets.append({"title": f"Krok: {slug}", "slug": slug,
                             "repo_id": f"local/{task_slug}_{slug}"})

    ds_infos: list[dict | None] = []
    for t in targets:
        try:
            qs = urlencode({"repo_id": t["repo_id"]})
            ds_infos.append(fetch_json(f"{server}/api/dataset-episode-lengths?{qs}"))
        except Exception as e:
            print(f"[VAROVÁNÍ] {t['title']} ({t['repo_id']}): {e}", file=sys.stderr)
            ds_infos.append(None)

    ok_lengths = [d["lengths"] for d in ds_infos if d]
    if not ok_lengths:
        raise RuntimeError("žádný cílový dataset není dostupný — zkontroluj /api/config a nahraná data.")

    if forced_chunk_size is not None:
        chunk_size_default = forced_chunk_size
    else:
        suggestion = choose_global_chunk_size(ok_lengths)
        chunk_size_default = suggestion["chunk_size"]
        if suggestion["forced"]:
            print(f"[VAROVÁNÍ] nejkratší dataset (min {suggestion['global_min']} snímků) drží pod "
                  f"{PAD_THRESHOLD * 100:.0f}% paddingu jen s nejmenším kandidátem "
                  f"({CHUNK_CANDIDATES[0]}) — zvaž --chunk-size ručně.", file=sys.stderr)

    all_detected: list[int] = []
    for t in targets:
        st = status["steps"].get(t["slug"], {}) if t["slug"] else status.get("baseline", {})
        for c in st.get("checkpoints") or []:
            m = re.search(r"_(\d+)ep_", c.get("name", ""))
            if m:
                all_detected.append(int(m.group(1)))
    canonical_map = canonicalize_tiers(all_detected)

    jobs: list[Job] = []
    for t, ds in zip(targets, ds_infos):
        if ds is None:
            continue
        eps = ds["measured_episodes"]
        lengths = ds["lengths"]
        st = status["steps"].get(t["slug"], {}) if t["slug"] else status.get("baseline", {})
        existing = st.get("checkpoints") or []
        tiers = detect_tiers(existing, eps, canonical_map)
        job_name_base = f"{task_slug}_{t['slug']}_retrain" if t["slug"] else f"{task_slug}_retrain"
        base_name = f"{task_slug}_{t['slug']}" if t["slug"] else task_slug

        for n, corrected_from in tiers:
            sub_lengths = lengths[:n]
            min_len = min(sub_lengths)
            frames = sum(sub_lengths)
            steps = forced_steps if forced_steps is not None else compute_steps_default(frames, batch_size)
            out_dir = f"{output_root}/{base_name}_{n}ep_{policy_type}_cs{chunk_size_default}"
            state, _ = disk_status(existing, out_dir)
            jobs.append(Job(
                target_title=t["title"], slug=t["slug"], repo_id=t["repo_id"],
                n=n, total_eps=eps, corrected_from=corrected_from,
                chunk_size=chunk_size_default, steps=steps, out_dir=out_dir,
                job_name=f"{job_name_base}_{n}ep", status=state,
                pad_frac=min(chunk_size_default, min_len) / min_len,
                py=py, batch_size=batch_size, save_freq=save_freq,
                device=device, policy_type=policy_type,
            ))
    return jobs


def acquire_lock(log_dir: Path):
    if fcntl is None:
        return None
    lock_path = log_dir / "train_queue.lock"
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise RuntimeError(
            f"{lock_path} je zamčený jiným během train_queue.py — už fronta neběží jinde?"
        )
    return f


def print_plan(jobs: list[Job]) -> None:
    missing = [j for j in jobs if j.status == "missing"]
    partial = [j for j in jobs if j.status == "partial"]
    trained = [j for j in jobs if j.status == "trained"]
    print(f"Celkem {len(jobs)} kombinací: {len(trained)} hotovo, {len(partial)} nedokončeno "
          f"na disku (přeskočeno — potřebuje ruční --resume), {len(missing)} chybí.\n")
    if partial:
        print("Nedokončené na disku (fronta je NEspustí, řeš ručně):")
        for j in partial:
            print(f"  - {j.out_dir}")
        print()
    if not missing:
        print("Nic k dotrénování — fronta je prázdná.")
        return
    print(f"Fronta ({len(missing)} běhů, v tomto pořadí):")
    for i, j in enumerate(missing, 1):
        note = f" (opraveno z {j.corrected_from})" if j.corrected_from else ""
        pad_note = f", nejhorší epizoda {j.pad_frac * 100:.0f}% padding" if j.pad_frac > 0.25 else ""
        print(f"  [{i}] {j.target_title} — {j.n}/{j.total_eps} ep{note}: "
              f"{j.steps} kroků, chunk_size={j.chunk_size}{pad_note}")
        print(f"        -> {j.out_dir}")


def run_job(job: Job, log_dir: Path, index: int, total: int) -> int:
    ts = time.strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"{ts}_{job.job_name}.log"
    argv = job.argv()
    header = (f"# [{index}/{total}] {job.job_name}\n"
              f"# start: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
              f"# cmd: {' '.join(argv)}\n\n")
    print(f"\n=== [{index}/{total}] spouštím {job.job_name} "
          f"({job.n}/{job.total_eps} ep, {job.steps} kroků, chunk_size={job.chunk_size}) ===")
    print(f"log: {log_path}")

    start = time.monotonic()
    with open(log_path, "w", encoding="utf-8") as logf:
        logf.write(header)
        logf.flush()
        proc = subprocess.Popen(argv, cwd=HERE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                sys.stdout.write(line)
                logf.write(line)
            proc.wait()
        except KeyboardInterrupt:
            print("\n[přerušeno] ukončuji běžící trénink...")
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise
    elapsed = time.monotonic() - start
    mins = elapsed / 60
    if proc.returncode == 0:
        print(f"=== [{index}/{total}] hotovo za {mins:.1f} min: {job.job_name} ===")
    else:
        print(f"=== [{index}/{total}] SELHALO (exit {proc.returncode}) po {mins:.1f} min: "
              f"{job.job_name} — viz {log_path} ===")
    return proc.returncode


def notify(message: str) -> None:
    """Best-effort desktop notifikace — pokud notify-send není nainstalované
    nebo nefunguje (headless stroj bez X/Wayland), prostě se přeskočí."""
    try:
        subprocess.run(["notify-send", "train_queue.py", message], timeout=5,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://localhost:8000",
                     help="kde běží server.py (výchozí http://localhost:8000)")
    ap.add_argument("--chunk-size", type=int, default=None,
                     help="vynutí tenhle chunk_size všem běhům místo auto-návrhu")
    ap.add_argument("--steps", type=int, default=None,
                     help="vynutí tenhle počet trénovacích kroků všem běhům")
    ap.add_argument("--only", action="append", default=None,
                     help="omezí frontu na tenhle cíl (slug kroku, nebo 'baseline') — lze opakovat")
    ap.add_argument("--dry-run", action="store_true", help="jen ukáže frontu, nic nespustí")
    ap.add_argument("-y", "--yes", action="store_true", help="nespouštěj interaktivní potvrzení")
    ap.add_argument("--continue-on-error", action="store_true",
                     help="pokračuj ve frontě i po selhání jednoho běhu (výchozí: zastavit)")
    ap.add_argument("--log-dir", default="runs/train_queue", help="kam ukládat logy jednotlivých běhů")
    args = ap.parse_args()

    log_dir = (HERE / args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        lock = acquire_lock(log_dir)
    except RuntimeError as e:
        print(f"[chyba] {e}", file=sys.stderr)
        return 1

    try:
        jobs = build_jobs(args.server, args.chunk_size, args.steps)
    except RuntimeError as e:
        print(f"[chyba] {e}", file=sys.stderr)
        return 1

    if args.only:
        only = set(args.only)
        jobs = [j for j in jobs if (j.slug or "baseline") in only]

    print_plan(jobs)
    missing = [j for j in jobs if j.status == "missing"]
    if not missing:
        return 0
    if args.dry_run:
        return 0

    if not args.yes:
        reply = input(f"\nSpustit těchto {len(missing)} běhů teď? [y/N] ").strip().lower()
        if reply not in ("y", "yes", "ano"):
            print("zrušeno.")
            return 0

    done, failed = 0, 0
    total_start = time.monotonic()
    for i, job in enumerate(missing, 1):
        rc = run_job(job, log_dir, i, len(missing))
        if rc == 0:
            done += 1
        else:
            failed += 1
            if not args.continue_on_error:
                msg = f"fronta zastavena po selhání {job.job_name} ({done} hotovo, {len(missing) - i} zbývá)"
                print(f"\n[konec] {msg}")
                notify(msg)
                return 1

    elapsed_h = (time.monotonic() - total_start) / 3600
    msg = f"fronta dokončena: {done} hotovo, {failed} selhalo, za {elapsed_h:.1f} h"
    print(f"\n[konec] {msg}")
    notify(msg)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
