"""Zapnutí temporal ensemblingu u ACT politiky při načtení (jen inference, váhy se nemění).

ACT normálně předpoví `chunk_size` akcí a odjede jich `n_action_steps` naslepo, než
se znovu podívá na obraz. S temporal ensemblingem (Algorithm 2 v článku o ACT,
arXiv 2304.13705; `ACTTemporalEnsembler` v lerobot/policies/act/modeling_act.py) se
model pouští v KAŽDÉM ticku, pokaždé předpoví celý chunk a do robota jde vážený
průměr všech dosud předpovězených akcí pro daný okamžik, váha exp(-coeff * i),
i = 0 nejstarší.

LeRobot to vyžaduje ve dvojici: `temporal_ensemble_coeff` nastavené a
`n_action_steps == 1` (jinak `ACTConfig.__post_init__` vyhodí chybu). Konfigurace se
tu ale mění až po načtení z checkpointu, takže se ta kontrola přeskočí — proto ji
tenhle modul dělá sám a obě hodnoty nastavuje vždy společně.

Modul je záměrně bez závislostí, ať jde otestovat bez LeRobotu.
"""
from __future__ import annotations

import math
from typing import Any


def apply_temporal_ensemble(cfg: Any, coeff: float) -> str:
    """Přepíše konfiguraci ACT politiky in-place a vrátí větu pro log.

    Musí se zavolat PŘED vytvořením politiky — `ACTPolicy.__init__` staví ensembler
    jen tehdy, když je `temporal_ensemble_coeff` v konfiguraci nastavené.
    Vyhodí ValueError, když jde o jinou architekturu než ACT nebo o nesmyslný koeficient.
    """
    ptype = getattr(cfg, "type", None)
    if ptype != "act":
        raise ValueError(f"temporal ensembling umí jen ACT, tahle politika je '{ptype}'")
    coeff = float(coeff)
    if not math.isfinite(coeff):
        raise ValueError(f"koeficient temporal ensemblingu musí být konečné číslo, dostal jsem {coeff}")
    chunk = int(getattr(cfg, "chunk_size"))
    if chunk < 1:
        raise ValueError(f"chunk_size musí být >= 1, je {chunk}")
    before = int(getattr(cfg, "n_action_steps"))
    cfg.temporal_ensemble_coeff = coeff
    cfg.n_action_steps = 1
    return (f"Temporal ensembling ZAPNUTO: koeficient {coeff:g}, chunk_size {chunk}, "
            f"n_action_steps {before} -> 1 (politika se pouští v každém ticku)")
