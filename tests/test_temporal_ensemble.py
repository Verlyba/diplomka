"""Zapnutí temporal ensemblingu (temporal_ensemble.apply_temporal_ensemble).

Nepotřebuje LeRobota ani robota — konfigurace politiky je tu jednoduchý objekt.

    python tests/test_temporal_ensemble.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from temporal_ensemble import apply_temporal_ensemble

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "CHYBA"), name, ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


def act_cfg(**kw):
    base = dict(type="act", chunk_size=15, n_action_steps=15, temporal_ensemble_coeff=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ── základní přepsání ───────────────────────────────────────────────────────
c = act_cfg()
msg = apply_temporal_ensemble(c, 0.01)
check("koeficient se nastaví", c.temporal_ensemble_coeff == 0.01, c.temporal_ensemble_coeff)
check("n_action_steps se srazí na 1", c.n_action_steps == 1, c.n_action_steps)
check("chunk_size zůstává (ensembler ho potřebuje shodný s modelem)", c.chunk_size == 15)
check("věta pro log jmenuje koeficient i změnu kroků", "0.01" in msg and "15 -> 1" in msg, msg)

# ── obě hodnoty vždy spolu (LeRobot jinak odmítne konfiguraci) ─────────────
for coeff in (0.0, 0.01, 0.5, -0.01):
    c = act_cfg(chunk_size=100, n_action_steps=100)
    apply_temporal_ensemble(c, coeff)
    check(f"coeff {coeff:+g}: dvojice (coeff, n_action_steps=1) souhlasí",
          c.temporal_ensemble_coeff == coeff and c.n_action_steps == 1)

# ── odmítnutí nesmyslů, konfigurace se pak nesmí změnit ───────────────────
for name, cfg, coeff in (
    ("jiná architektura než ACT", act_cfg(type="diffusion"), 0.01),
    ("NaN koeficient", act_cfg(), float("nan")),
    ("nekonečný koeficient", act_cfg(), float("inf")),
    ("chunk_size 0", act_cfg(chunk_size=0), 0.01),
):
    try:
        apply_temporal_ensemble(cfg, coeff)
        check(f"{name}: vyhodí ValueError", False, "prošlo")
    except ValueError:
        check(f"{name}: vyhodí ValueError", True)
        check(f"{name}: konfigurace nedotčená",
              cfg.temporal_ensemble_coeff is None and cfg.n_action_steps == 15,
              (cfg.temporal_ensemble_coeff, cfg.n_action_steps))

print()
if failures:
    print(f"NEPROSLO: {len(failures)} — {failures}")
    sys.exit(1)
print("OK — zapnutí temporal ensemblingu sedí ve všech kontrolovaných případech.")
