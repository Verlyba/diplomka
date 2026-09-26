# mereni/ — naměřená data: baseline × orchestrace (stav k 2026-09-26)

Tenhle adresář je určený hlavně pro psaní textu práce. Všechna čísla v textu mají pocházet odsud
(nebo z `poznamky/DENIK.md`), nic se nesmí dopočítávat po paměti ani odhadovat. Kdo tu chce něco
tvrdit, musí to umět ukázat na konkrétním souboru níže.

## Co bylo měřeno

Úloha: zelená kostka z bílé desky do červené misky, robot SO-101, kamera shora (`top`) a na gripperu
(`wrist`), 640×480. **16 pevných poloh kostky** (křížky nakreslené tužkou na desce), na každé poloze
jeden záznam na podmínku. Polohy jsou číslované 1–16 a jejich souřadnice jsou v pixelech horní kamery
(`polohy_a_vysledky.csv`).

| podmínka | modely | výcvik | záznam |
|---|---|---|---|
| **orchestrace 120 ep** | CEO `google/gemma-4-e4b`, inspektor `qwen2.5-vl-7b-instruct` (LM Studio, lokálně), kroky `catch_cube`, `carry_cube`, `homing` (`diplomka_2_*_120ep_act`) | ACT, `chunk_size=15`, 120 epizod | `zaznamy_behu/`, řádky 5–20 v sešitě |
| **orchestrace 60 ep** | stejné, kroky `diplomka_2_*_60ep_act` (prvních 60 epizod téhož datasetu) | ACT, `chunk_size=15`, 60 epizod | řádky 26–42 |
| **baseline** | jeden monolitický ACT `diplomka_2_120ep_act` na celou úlohu, bez orchestrátoru a bez ověřování | ACT, `chunk_size=15`, 120 epizod | `telemetrie/`, řádky 5–22 v listu Baseline |

Orchestrace smí re-plánovat (až 5×), baseline je jeden souvislý pokus, který uživatel zastavil, když
skončil nebo neměl šanci. Dále byly ve stejném dni provedeny 5 zkušebních běhů orchestrace `cs100`
(modely `diplomka_1_*`, řádky 21–25, `POCITA SE? = NE`); do porovnání nejdou.

## Soubory

| soubor | co obsahuje |
|---|---|
| `zaznam_behu_2.xlsx` | hlavní sešit: listy Souhrn, Baseline, Orchestrace a **Kontrola** (mapování, kontroly proti snímkům, upozornění). Souhrn jsou vzorce bez uložených hodnot, proto je tentýž souhrn v `souhrn.csv` |
| `souhrn.csv` | úspěšnost, Wilsonův 95% interval, průměrná doba pro tři podmínky |
| `polohy_a_vysledky.csv` | **jedna řádka na polohu**: souřadnice kostky, pokrytí demonstracemi, výsledek orchestrace 120 ep, orchestrace 60 ep a baseline |
| `orchestrace_behy.csv`, `baseline_pokusy.csv` | listy sešitu jako CSV včetně poznámek uživatele (poslední sloupec = číslo řádku v sešitě) |
| `zaznamy_behu/*.json` | záznam každého běhu orchestrace (kroky, verdikty fyziky a inspektora, volání modelů a jejich časy, cesty ke snímkům, konfigurace) |
| `telemetrie/*.jsonl` | telemetrie démona 5×/s (klouby, cíl, zátěž gripperu, události `task_started`/`task_done`) — z ní jsou i všechny baseline pokusy |
| `snimky/<run>/` | snímky obou kamer, na kterých stojí verdikty (`init_*` = úvodní scéna, `a00N_*` = po pokusu N, `call*` = ostatní snímky poslané modelům) |
| `demonstrace/pozice_demonstraci.csv` | poloha kostky a koncová póza ramene každé z 120 demonstrací (prvních 60 řádků = trénink 60ep) |
| `mapa_*.png` | mapy poloh; levá půlka značky = jedna podmínka, pravá = druhá, zelená trefil / červená netrefil |
| `poloha_kostky_start_120ep_baseline.xlsx` | starší export poloh (120 ep × baseline) |
| `../analyza/*.py` | skripty, kterými čísla vznikla (pracovní, s pevnými cestami; slouží jako záznam postupu) |

## Pravidla pro čtení dat (závazná)

1. **Pravdou je úsudek uživatele** (sloupec „VAS USUDEK - USPECH?“), ne verdikt systému (`success` v
   záznamu běhu). Baseline žádný automatický verdikt nemá. Při rozporu má pravdu uživatel; rozdíl je
   chyba systému, typicky inspektora. Do souhrnů jde jen úsudek uživatele a jen řádky s `POCITA SE? = ANO`.
2. Verdikt systému je samostatná metrika **spolehlivosti inspektora**, ne výsledek úlohy. Ve 4 z 21
   běhů z odpoledne 26. 9. se lišil od úsudku uživatele (`165409`, `174723`, `175015`, `181830`),
   u 60ep dále v běhu `220934` (systém ANO, kostka mimo misku) a u pádu démona (`221749`).
3. Úsudek uživatele u všech běhů se snímky souhlasí se závěrečným snímkem (kostka v misce / mimo misku);
   kontrolováno ručně, viz list Kontrola.

## Výsledky (z `souhrn.csv` a `polohy_a_vysledky.csv`)

| podmínka | úspěšných | 95% interval | průměrná doba |
|---|---|---|---|
| orchestrace, 120 ep | 11/16 (69 %) | 44–86 % | 223,5 s |
| orchestrace, 60 ep | 8/16 (50 %) | 28–72 % | 265,4 s (N = 15) |
| baseline, 120 ep | 7/16 (44 %) | 23–67 % | 25,8 s (do zastavení uživatelem) |

- **Rozdíly v úspěšnosti nejsou statisticky významné.** Orchestrace 120 ep × baseline: Fisher p = 0,29;
  párově po polohách McNemar p = 0,29 (obě trefily 5, jen orchestrace 6, jen baseline 2, ani jedna 3).
  Orchestrace 120 ep × 60 ep: Fisher p = 0,47, McNemar p = 0,45.
- **Síla testu je nízká**: při naměřeném efektu má párový test na 16 polohách sílu 19 %; pro 80 % síly
  by bylo potřeba zhruba 70 párů poloh (u dvou nezávislých skupin zhruba 58 na skupinu). Nulový výsledek
  tedy neznamená, že rozdíl neexistuje. Bayesovský odhad bez předchozí informace: orchestrace lepší než
  baseline s pravděpodobností 92 %, mediánový rozdíl +23 procentních bodů, 95% interval −10 až +52 b.
- **Čas je významný**: úspěšné pokusy baseline mají medián 18,5 s, úspěšné běhy orchestrace 140 s;
  baseline byl rychlejší v 77 ze 77 dvojic (přesný Mann–Whitney p = 0,00006, n = 7 a 11).
- **Výhoda orchestrace v úspěšnosti vzniká opakováním**: bez jediného re-plánu uspěla orchestrace 4/16
  (u 60 ep včetně řádku 35, který má jen poznámku „na první pokus“), tedy ne lépe než baseline na jeden pokus.
- **Pokrytí demonstracemi** (průzkumně, práh zvolen až po pohledu na data): u 60ep modelu (učil se z prvních
  60 demonstrací) neuspělo všech 6 poloh s nejvýš 5 demonstracemi do 60 px (6, 7, 8, 9, 13, 16), z 10
  poloh s více demonstracemi jich uspělo 8. Bez pevného prahu: medián počtu demonstrací u úspěšných
  poloh 10, u neúspěšných 4,5 (permutační p = 0,09). U 120 ep je vztah slabší (medián 16 × 11). Polohy jsou
  v každém případě pokryté (nejbližší demonstrace 3–22 px při velikosti kostky ~35 px).
- Všechny další testy a odhady po prvním pohledu na data jsou **průzkumné** a v textu se tak mají označit.

## Jak byly řádky přiřazeny k logům

- **Orchestrace 120 ep**: řádky 5–25 = běhy `20260926-164147` … `181830`. Ověřeno poznámkami uživatele a tím,
  že polohy `cs100` běhů opakují polohy řádků 5, 6, 7, 14, 8 (do 8 px).
- **Orchestrace 60 ep**: řádky 26–34 a 36–42 = běhy `20260926-212027` … `225113` v pořadí (řádek 26 = běh
  zastavený před prvním krokem). Ověřeno vzorem úsudků, poznámkami, závěrečnými snímky a polohami (běhy
  pokrývají polohy 1–4, 6–16).
- **Baseline**: řádky 5–22 = telemetrie `20260926-184956` … `191623` (jeden pokus na soubor). Kotva: řádek 8 je
  jediný pokus, kde se rameno nehnulo (přetížený motor).
- **Polohy baseline nejsou ze snímků** (baseline snímky neukládá). Byly odvozeny z pozice ramene v okamžiku,
  kdy začne zavírat gripper (kNN nad 120 demonstracemi); ověření na 18 skutečných úspěšných úchopech
  orchestrace dává medián chyby 22 px (max. 82 px). Baseline procházel křížky ve stejném pořadí jako
  orchestrace (0 z 20 000 náhodných pořadí sedí stejně dobře). Uvnitř bloku poloh 5–9 je přiřazení nejisté
  (`baseline_prirazeni` v `polohy_a_vysledky.csv`).

## Známé mezery a úpravy dat (nutno uvést v textu)

- **Řádek 35 (orchestrace 60 ep) nemá záznam běhu**; vyloučením patří poloze 5. V datech je jen úsudek
  uživatele (ANO); dobu, re-plány ani počty volání nemá.
- **Řádek 36 (běh `20260926-221749`, poloha 10, 60 ep) skončil pádem démona před homingem**: výměna modelu se
  nepotvrdila (po 180 s restart démona), restartovaný démon spadl na `UnboundLocalError: deltas`, záznam má
  `success: false`, `error: "Daemon neběží."` a uloženou dobu 262,6 s. Kostka je v misce (úsudek ANO).
  **Doba v sešitě (54,9 s) NENÍ naměřená**: je to 46,6 s skutečných do konce ověření `carry_cube` + 8,3 s průměrná
  doba homingu (blok výměna modelu + krok + ověření; průměr z 16 dokončených homingů, 60ep samostatně 8,4 s,
  n = 3). Počty volání CEO/inspektora jsou naměřené bez homingu. Původní doba je v `zaznamy_behu/20260926-221749.json`.
- Řádek 33 (běh `220442`) skončil chybou LM Studia `HTTP Error 400` po 2 re-plánech z 5.
- Baseline: řádek 8 (přetížený motor) a 10 (posunutá základna) se nepočítají; řádek 11 byl uživatelem
  opraven na NE (původní ANO odporovalo poznámce i telemetrii). **Doba baseline pokusu** je čas od zadání úlohy
  do zastavení démona uživatelem, takže u úspěchů zahrnuje stání po dokončení (přesnější indicie: okamžik
  uvolnění gripperu v telemetrii, mediánově ~10 s).
- Souhrn v sešitě jsou vzorce bez uložených hodnot; čísla viz `souhrn.csv`.

## Verze měřicího přístroje — dopad na porovnatelnost

1. **2026-09-25 večer**: prodleva Protokolu A platí pro všechny kroky; ochrana proti zaseknuté sběrnici.
2. **2026-09-25 noc**: krok `carry_cube` má příznak `release` — končí až uvolněním kostky (zátěž gripperu
   klesne a gripper se zastaví), ne dosednutím ramene. Platí pro všechny běhy 26. 9.
3. **2026-09-26, restart serveru ve 20:10**: inspektor se po `[unclear]` už neptá podruhé s novým snímkem a
   `llm_calls` mají pole `image_paths`. **Všechny běhy 120 ep a baseline vznikly před touto změnou, všechny běhy
   60 ep po ní.** Porovnání 60 ep × 120 ep je proto zatížené změnou přístroje. Dopad na výsledek kroku bylo
   možné jen u kroků bez fyzického důkazu (7 z 27 druhých dotazů), na dobu asi 5 s na běh, ale zcela vyloučit se
   nedá. Baseline se změny netýká (orchestrátor nepoužívá).
4. Prompt s pokynem „po úspěšném umístění spusť homing“ platí od běhu `163206`; běhy `cs100` `180826` a
   `181139` ho ještě neměly (proto neskončily homingem, a jsou mimo počty).

Podrobný popis rozhodnutí a důvodů je v `../poznamky/DENIK.md` (sekce z 2026-09-25 a 2026-09-26).

## Co lze a nelze v textu tvrdit

- Lze: popis pilotního měření, úspěšnosti s intervaly, směr efektu (orchestrace s re-plány o ~23 b. lepší, ale
  nevýznamně), významně nižší rychlost orchestrace, příčiny selhání (inspektor, pokrytí demonstracemi, homing),
  potřebná velikost vzorku pro navazující měření.
- Nelze: tvrdit statisticky prokázanou výhodu jedné ze schémat v úspěšnosti; přisuzovat rozdíl 60 ep × 120 ep
  jen počtu epizod (změna přístroje, n = 16, průzkumné testy); používat dobu řádku 36 jako naměřenou;
  vymýšlet hodnoty pro chybějící údaje (řádek 35).
