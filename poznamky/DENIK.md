# Deník změn

Chronologický záznam toho, co se v appce dělalo a proč. Staré záznamy se
nemažou ani při zastarání — jen se případně doplní poznámkou, že je něco
nahrazeno novějším řešením, aby zůstal dohledatelný track record.

## 2026-08-04 — první nasazení

Naklonováno `Verlyba/diplomka.git`. Appka: `server.py` (stdlib HTTP server),
`orchestrator.py`, `inference_daemon.py`, `record_with_marks.py`,
`split_dataset.py`, `merge_datasets.py`, dvě stránky (`web/index.html` —
generátor příkazů, `web/orchestrace.html` — živý běh).

## 2026-08-04 — druhá kamera, oprava sidecaru značek

- Přidána podpora druhé kamery (`camera2_*`) napříč `config.js`, `index.html`,
  `setup.js`, `orchestrator.py`, `server.py`.
- **Oprava:** `record_with_marks.py` při `--resume` přepisoval celý sidecar
  `<dataset>.marks.json` značkami jen z aktuální epizody místo sloučení
  s existujícím obsahem — historie starších epizod se ztrácela. Opraveno
  (načte a sloučí existující soubor).
- Zdokumentováno: FFmpeg musí být verze 4–8 (ne 9+, torchcodec je nezná),
  `--dataset.root` je u `--resume=true` povinný, mazání epizod přes
  `lerobot-edit-dataset` (automatická záloha), GPU/CUDA instalace na Windows,
  doporučení k počtu tréninkových kroků na malých datasetech.

## 2026-08-05 — oprava registrace typu robota, oprava camera flagů

- **Oprava:** `lerobot/robots/__init__.py` neimportuje konkrétní robotické
  submoduly (`so_follower` apod.), takže `RobotConfig.get_choice_class(...)`
  padal na `KeyError` a daemon se tiše přepnul do `SIMULATED` módu i
  s připojeným hardwarem. Opraveno explicitním importem submodulů (stejně
  jako to dělají oficiální LeRobot skripty).
- **Oprava (vlastní regrese):** baseline příkaz použil stejný
  `--robot.cameras={ name: {...}}` zápis jako teleop/record (ty jedou přes
  draccus), ale `inference_daemon.py` má vlastní argparse a čeká striktní
  JSON. Přidány `--camera2.*` flagy do daemona, Setup stránka vrácena
  k bezpečným jednotlivým `--camera.*`/`--camera2.*` flagům.

## 2026-08-05 — první živý test na hardwaru, plná orchestrace

Natrénován baseline (`pick_and_place_act`, 5000 kroků) a krokové modely
(`grab_cube`, `pick_cube` @ 5000/3000 kroků). Odzkoušeno na skutečném SO-101
(RTX 4070, `torch==2.11.0+cu128`).

Zásadní sada oprav a implementace celého orchestračního schématu (commit
`bb61bcf`), nalezeno a opraveno ve spolupráci s Gemini:

- Výchozí `python` bez LeRobot/torch/CUDA způsoboval tichý pád do SIMULATED
  módu → v simulaci natvrdo `280 mA` proud gripperu vždy překročil limit
  protokolu B (`250 mA`) → kroky se "dokončovaly" instantně bez pohybu.
  Opraveno nastavením absolutní cesty k `.../envs/lerobot/python.exe`
  v `config.json`/`server.py`/`config.js`.
- Zamčený `COM3` (zombie proces v FTDI ovladači) — řešeno manuálně (kill PID).
- ACT policy běžela na starý `latch_timeout_s=60` bez per-krokového limitu →
  po dokončení přirozeného pohybu pokračovala až 60 s a na konci cyklicky
  otevírala/zavírala gripper. Zaveden `step_timeout_s` a `SET_TASK:krok|timeout=X`.
- `cache_frame` čekala `numpy.ndarray (H,W,3)`, LeRobot 0.6.1 vrací
  `torch.Tensor (3,H,W)` → snímek pro VLM byl vždy `None` → inspektor se
  přeskakoval a krok se automaticky považoval za úspěšný. Opraveno detekcí
  a konverzí tenzoru.
- Univerzální systémový prompt pro plánovač (katalog dovedností s ID, popisem
  akce a očekávaným výsledkem) a re-plánování se zpětnou vazbou o selhání.
- Prioritizace chybových tagů VLM (`[object_missed]`, `[object_slipped]`,
  `[target_moved]`, `[unknown_failure]`) před slovem „success" v odpovědi.
- Záložní re-plán, když LLM vrátí prázdné pole: opakování od selhaného kroku.
- Prodloužený `llm_timeout_s` na 180 s kvůli JIT načítání modelu v LM Studiu.

## 2026-08-08 — oprava měření úspěšnosti, dynamické limity, aktivní držení pozice

Prošel jsem reálné záznamy v `runs/` (8 běhů) a našel čtyři konkrétní,
daty podložené problémy — ne teoreticky, ale přímo v naměřených datech:

1. **Chyba v počítání úspěšnosti.** `all(r["success"] for r in self.results)`
   počítalo přes úplně všechny pokusy včetně těch, které re-plán opravil.
   Běh, který jednou selhal a pak se zotavil a doběhl celý plán, se zapsal
   jako `success: False` (viz `runs/20260805-194945.json`) — přesně ta
   vlastnost, kterou má orchestrace demonstrovat (hypotéza H3), byla ve
   vlastních datech neviditelná. **Opraveno:** dosažení konce smyčky už samo
   o sobě znamená úspěch aktivního plánu; historie pokusů zůstává
   v `self.results` pro diagnostiku.
2. **Chybějící snímek = tichý úspěch.** Stejná větev kódu pro úmyslné
   `skip_inspector` i pro genuinní chybu (kamera/VLM nedostupné). Tři rané
   běhy (`191724`, `192003`, `193627`) prošly všemi kroky s natvrdo danou
   hodnotou `280 mA` ze SIMULATED módu — nikdy se nedotkly hardwaru, ale
   zapsaly se jako plný úspěch. **Opraveno:** chybějící snímek je teď
   skutečné selhání `[no_image]`, ne automatický průchod.
3. **Plochý časový limit kroku (8 s) neodpovídal reálným datům.** Spočteno
   z `marks.json`: `release` běžně 7–10 s, `grab_cube` až 10,9 s. Úplně
   všechny kroky ve všech bězích končily „časovým limitem", nikdy protokolem
   A/B — silný signál systematického uřezávání. **Opraveno:** nový skript
   `compute_step_timeouts.py` spočítá skutečné trvání každého kroku ze
   `marks.json` (max pozorované × rezerva 1,25, zaokrouhleno nahoru) a zapíše
   `timeout_s` do `config.json` (`--apply`). Přidáno i nepovinné pole
   „Časový limit" do editoru kroků na Setup stránce a příkaz do generátoru
   (sekce 6, vedle `split_dataset.py`). Aplikováno na `local/pick_and_place`:
   `grab_cube 14s, pick_cube 5s, move 5.5s, release 12.5s`.
4. **Pád daemona = ztracené měření bez pokusu o zotavení**
   (`runs/20260805-193545.json`, „Daemon neběží"). **Opraveno:** jeden pokus
   o restart daemona a opakování kroku, než se běh vzdá.
5. Nudge v promptu plánovače: zotavení má začít u selhaného kroku, ne
   zbytečně opakovat už úspěšné (pozorováno v `194945.json`, kde selhání
   `release` vedlo k opakování `move`).

**Aktivní držení pozice po konci kroku.** Uživatel pozoroval, že po vypršení
časového limitu kroku robot mezi koncem kroku a odpovědí CEO/VLM „pustí"
už uchopený objekt. Dosavadní chování: konec kroku = daemon prostě přestane
posílat NOVÉ akce (`WAITING` stav), spoléhá se na to, že servo samo drží
poslední zadanou pozici. Problém: pokud krok skončí ČASOVÝM LIMITEM (ne
protokolem A/B), poslední odeslaná akce je cokoliv, co model zrovna
předpovídal v tu milisekundu — může to být pozice uprostřed pohybu (např.
gripper, který se ještě nedozavřel). **Oprava:** `freeze_robot()` — v okamžiku
konce kroku (libovolným způsobem) i na explicitní `STOP` se zachytí aktuální
reálná poloha kloubů a pošle se jako cílová pozice; ve stavu `WAITING` se tahle
pozice odesílá znovu každý tik po celou dobu čekání na další `SET_TASK`, ne
jen jednou. `predict_and_act()` teď vrací i poslední odeslanou akci pro
tento účel.

## Otevřené otázky / co ověřit dál

- Spustit pár testovacích běhů s novými `timeout_s` a zkontrolovat, jestli
  kroky teď občas končí protokolem A/B místo pořád jen časovým limitem — to
  by potvrdilo, že nová čísla (14/5/5.5/12.5 s) sedí líp. Pokud pořád skoro
  vždy padá na timeout, čísla je potřeba posunout ještě výš.
- Ověřit, že aktivní držení pozice (`freeze_robot`) skutečně řeší pozorované
  puštění objektu — sledovat `[TELEMETRY]` mezi koncem kroku a dalším
  `SET_TASK` a fyzicky sledovat gripper.
- `move`/`pick_cube` modely měly jen 3000 tréninkových kroků (úmyslně kvůli
  malému datasetu) — zvážit rollout test na více checkpointech.
- `release_act` mezitím dotrénován (`outputs/training/pick_and_place_release_act`
  existuje) — všechny 4 krokové modely jsou teď kompletní.
