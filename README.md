# Porovnání běžného učení LeRobota (ACT) s orchestračním schématem

Minimální aplikace k diplomové práci. Dvě stránky, jeden malý Python server,
žádné závislosti nad rámec standardní knihovny (kromě LeRobota samotného, který
běží ve vlastním prostředí a spouští se jako podproces).

* **Setup** (`web/index.html`) — jen generátor příkazů. Vyplníš porty, ID ramen,
  kameru, kroky úlohy a hyperparametry; stránka z toho poskládá přesné příkazy
  pro kalibraci, teleoperaci, nahrávání, rozdělení datasetu, trénink obou větví
  porovnání a spuštění baseline modelu. Nic sama nespouští — příkazy kopíruješ
  do terminálu.
* **Orchestrační schéma** (`web/orchestrace.html`) — popis schématu a jeho živé
  spuštění: instrukce → plán z LLM → smyčka kroků (výměna modelu, exekuce,
  snímek, verifikace VLM) → záznam běhu.

## Instalace LeRobota

Do vlastního virtuálního prostředí (odkrokováno i na stránce Setup):

```bash
python -m venv .venv-lerobot
.venv-lerobot/Scripts/python.exe -m pip install "lerobot[dataset,training,feetech]"
```

Samotné `pip install lerobot` nestačí — bez extra `dataset` spadne nahrávání na
chybějícím balíku `datasets`, bez `training` spadne trénink na `accelerate`,
bez `feetech` nejdou ovládat motory SO-100/SO-101. Ověřeno na LeRobotu 0.6.1.

## Spuštění

```bash
python server.py
```

Otevři <http://localhost:8000>. Server musí běžet v Pythonu, který má LeRobota —
inferenční daemon si spouští sám podle pole „Python s LeRobotem" v setupu.

Setup funguje i jako holé HTML bez serveru (konfigurace se drží v prohlížeči).
Běh orchestrace server potřebuje.

## Soubory

| Soubor | Co dělá |
| --- | --- |
| `server.py` | HTTP server (stdlib), statické stránky, konfigurace, SSE proud událostí |
| `orchestrator.py` | samotné orchestrační schéma: plánovač, resolver plánu, smyčka kroků, inspektor |
| `inference_daemon.py` | trvalý proces, který drží robota a kamery a přepíná váhy policy (SET_POLICY / SET_TASK / SNAP) |
| `record_with_marks.py` | obal nad `lerobot-record`, který navíc mezerníkem zaznamenává hranice kroků |
| `split_dataset.py` | rozřeže jednu nahrávku na dílčí datasety kroků |
| `merge_datasets.py` | opačný směr — dílčí nahrávky kroků složí do jedné nahrávky celé úlohy |
| `tests/` | ověření `record_with_marks.py` / `split_dataset.py` / `merge_datasets.py` proti skutečnému LeRobotu |
| `config.json` | konfigurace ze setupu (vytvoří se při prvním uložení, negituje se) |
| `runs/` | záznamy běhů orchestrace — plán, kroky, verdikty, časy |

## Metodika porovnání

Baseline i orchestrace se trénují ze **stejných demonstrací** — na stránce
Setup je na výběr, kterým směrem:

* **Rozdělení (doporučeno):** nahraje se jedna sada demonstrací celé úlohy;
  při teleoperaci se mezerníkem označí přechody mezi fázemi
  (`record_with_marks.py`) a `split_dataset.py` podle nich nahrávku rozřeže na
  dílčí datasety kroků. Baseline i kroky orchestrace jsou pak doslova výřezy
  týchž epizod.
* **Sloučení:** každý krok se nahraje zvlášť a `merge_datasets.py` epizody
  kroků poskládá do jedné epizody celé úlohy. Jednodušší na nahrávání, ale
  složená epizoda má na spojích švy (mezi kroky se nahrávání zastavilo) —
  nevýhoda, kterou orchestrace nemá.

V obou případech: **baseline** je jeden model (ACT) trénovaný na plném
datasetu, **orchestrace** jsou malé modely trénované na dílčích datasetech,
řízené LLM plánovačem a ověřované VLM inspektorem. Obě větve jedou stejným
inferenčním daemonem na stejné frekvenci — baseline jen s vypnutými
ukončovacími protokoly a jedním modelem na celou úlohu. Rozdíl ve výsledcích
je rozdílem schémat, ne dat nebo běhového prostředí.

## Bez hardwaru

Když není zadaný port ramene (nebo není dostupný LeRobot/torch), daemon nastartuje
v simulovaném režimu: rameno se dopočítává numericky, ukončovací protokoly fungují,
takže se dá odladit celá smyčka orchestrace včetně plánování a re-plánů.
