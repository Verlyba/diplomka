# analyza/ — skripty k číslům v `mereni/`

Pracovní skripty, jimiž vznikly čísla, sešit a mapy. Mají pevně zadané cesty (scratchpad, `D:\_WinZakladniSlozky\`,
`C:\Users\green\diplomka\`) a nejsou psané pro opakované spouštění jinde; slouží jako záznam postupu
(co přesně bylo spočítáno a jak). Výsledky jsou v `../mereni/`.

| skript | co dělá |
|---|---|
| `orch_summary.py`, `list_new.py`, `list_base.py` | výpis běhů orchestrace (`runs/`) a baseline pokusů (telemetrie), přiřazení řádků sešitu |
| `cube_pos.py` | poloha zelené kostky na úvodním snímku každého běhu (maska zelené barvy) |
| `coverage.py` | pokrytí poloh demonstracemi (nejbližší demonstrace, počet do 60 px) |
| `pose2pos.py`, `base_pos.py` | odhad polohy kostky z pozice ramene (kalibrace na 120 demonstracích, ověření na orchestraci) a přiřazení baseline pokusů k polohám |
| `base_check.py` | indicie z telemetrie zátěže gripperu u baseline (sevřel + pustil) proti úsudku uživatele |
| `replay_release.py`, `carry_end.py`, `carry_traces.py` | přehrání pravidla `release` přes telemetrii `carry_cube` a rozbor toho, jak kroky končily |
| `fill_xlsx.py`, `fill_60ep_v2.py` | doplnění automatických sloupců sešitu a listu Kontrola (120 ep, 60 ep) |
| `export_map.py` | mapa poloh a export polohy kostky na začátku |
| `montage.py`, `finals.py` | přehledy snímků pro ruční kontrolu úsudků proti závěrečným snímkům |
| `build_mereni.py` | sestavení adresáře `mereni/` |
