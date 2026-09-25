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

## 2026-08-08 — proč tři modelové role, a proč re-plán musí vidět snímek

Vznikla otázka, jestli tři "modely" (LLM plánovač / VLM inspektor / krokové
ACT politiky) nejsou zbytečné — nešlo by to na dva?

**Rozhodnutí: necháváme tři role.** Krokové ACT politiky jsou samostatná
kategorie (řízení motorů, ne uvažování), takže reálná otázka byla LLM
plánovač vs. VLM inspektor. Důvody proti sloučení do jednoho vision modelu:

- **Cena/frekvence.** VLM inspektor se volá po **každém** kroku (časté,
  levné, úzký úkol — jedno slovo z pevné množiny tagů). Plánovač se volá jen
  na začátku běhu a při selhání (vzácné). Sloučení by znamenalo, že i ten
  časný happy-path check nese těžší kontext (celý katalog dovedností +
  instrukce k plánování) — zbytečná latence na cestě, která se používá
  nejčastěji.
- **Specializace modelu.** Appka už teď běžně používá dva různé modely
  v LM Studiu (`google/gemma-4-e4b` jako plánovač, `qwen2.5-vl-7b-instruct`
  jako inspektor) — malý vision model dobrý na úzkou vizuální klasifikaci
  nemusí být stejně dobrý na spolehlivé generování JSON polí přes otevřený
  katalog dovedností, a naopak. Rozdělení rolí umožňuje vybrat pro každou
  vhodný model nezávisle.

**Ale skutečná díra byla jinde:** re-plán CEO dostával jen **text** — jméno
selhaného kroku a tag (`[target_moved]` apod.), nikdy ne fotku, kterou VLM
zrovna vyhodnocovalo. Tag je jen nejbližší kategorie z pevné čtveřice
možností — pokud se scéna změnila způsobem, co se do žádného tagu nevejde
(např. v záběru přibyl další objekt), CEO to nemá šanci poznat, protože fotku
nikdy neviděl. Bez obrázku je re-plán ve skutečnosti jen "uhodni opravu
z jednoho slova".

**Oprava:** `_create_plan()` teď bere nepovinný `image_b64` a při re-plánu
(ne při úvodním plánu — na začátku běhu žádný snímek ještě neexistuje) se
posílá **stejná fotka**, kterou právě vyhodnotil VLM inspektor. Prompt
plánovače byl doplněn o instrukci: věř fotce víc než textu tagu. Pokud
nakonfigurovaný model plánovače neumí obrázky (LM Studio na to typicky
odpoví HTTP chybou), kód to odchytí a zkusí re-plán znovu jen s textem —
model plánovače tedy musí být vision-capable, aby z týhle opravy byl plný
užitek, jinak appka jen tiše spadne zpátky na starší (slepé) chování.

## 2026-08-08 — přepracování promptů obou modelů, konfigurovatelné protokoly

Vzniklo z brainstormu nad tím, proč plánovač vždycky vygeneruje stejnou
posloupnost kroků, i když by nebyla potřeba nebo byla špatná. Diagnóza měla
čtyři příčiny, tři z nich se opravily, jedna je v pořádku a nechává se:

1. **Počáteční plán byl deterministický** — a to je správně. Pro měření je
   výhoda, že plánovač do výsledků nevnáší vlastní rozptyl. Problém byl jinde.
2. **Re-plán dostával přímo příkaz „starting from '{step}'"** — takže poslušně
   vracel ocas katalogu bez ohledu na to, co se na stole skutečně stalo.
   **Opraveno:** ten pokyn je pryč. Místo něj dostane fakta (co proběhlo, co
   uspělo, kolikrát tento krok už selhal, co hlásí gripper, fotka) a rozhodne
   se sám.
3. **Nesměl uvažovat** (`Return ONLY a valid JSON array`), což je pro malý
   4B model na vizuální úloze hodně. **Opraveno:** volitelné „uvažování
   nahlas“ — jedna věta o tom, co vidí, teprve pak plán na posledním řádku.
4. **Neměl jak říct „nic není potřeba" ani „tohle nejde".** Prázdné pole
   spadlo do fallbacku, který ho stejně naplnil. **Opraveno:** sentinely
   `["DONE"]` a `["ABORT"]`, které se propisují až do výsledku běhu.

**Blokátor, který to celé podmiňoval:** `parse_json_array()` bral vše mezi
PRVNÍ `[` a POSLEDNÍ `]`. Jakmile by model směl psát prózu, věta
„tag `[object_missed]` byl nepřesný, plán je `["grab_cube"]`" by se parsovala
od `[object_missed]`, spadla na `JSONDecodeError` a shodila celý běh.
**Opraveno:** parser hledá všechny vyvážené `[...]` úseky a zkouší je od
posledního zpět — próza včetně chybových tagů v hranatých závorkách je tím
neškodná a plán se čte z finálního výroku modelu. Otestováno na 9 případech
včetně těch, co starou verzi shazovaly.

**Počáteční snímek pro plánovač.** Daemon se teď startuje PŘED plánováním
(s policy prvního kroku z katalogu — je to jedno, `SET_POLICY` ji stejně
prohodí) a pořídí snímek výchozí scény. Plánovač tak nevidí učebnicový stav,
ale skutečný. Když se snímek nepodaří pořídit, plánuje se bez něj.

**Fúze s proudem gripperu.** `Daemon` si teď pamatuje poslední hodnotu `load`
z telemetrie a plánovač dostává větu typu „gripper current 281 mA — something
appears to be held". Je to podstatně spolehlivější než odhad z fotky shora,
kde je malá kostka v čelistech sotva vidět — plánovač pak neopakuje úchop nad
předmětem, který už drží. Vypínatelné (`gripper_state_in_context`).

**Konfigurovatelné ukončovací protokoly.** Dřív byly prahy natvrdo v kódu.
Robot je ale univerzální a kroky si autor navrhuje sám — úloha bez úchopu
protokol B nepotřebuje, jiný gripper nebo předmět potřebuje jiný limit
proudu. Přidáno do konfigurace i do UI s vysvětlením, co která metoda dělá
a kdy ji vypnout: `protocol_a_enabled`, `protocol_a_threshold_rad`,
`protocol_a_patience`, `protocol_b_enabled`, `protocol_b_limit_ma`.
Daemon dostal odpovídající přepínače (`--no-protocol-a`, `--no-protocol-b`,
`--protocol-a.threshold`, `--protocol-a.patience`, `--protocol-b.limit`);
`--no-triggers` zůstává jako hlavní vypínač pro baseline.

**VLM inspektor:**
- **Asymetrie chyb v promptu.** Špatné `SUCCESS` posune plán ze stavu, ve
  kterém robot není, a chyba se kumuluje; špatné selhání stojí jeden retry.
  Prompt teď explicitně říká: nejsi-li si jistý, hlas selhání.
- **Nalezena a opravena past v `_verify`:** kontrola `if "SUCCESS" in upper`
  byla substringová, takže odpověď „the step was not successful“ obsahuje
  `SUCCESSFUL` → `SUCCESS` a **vyhodnotila by se jako úspěch**. Nově se
  SUCCESS matchuje na celou odpověď (po odstranění interpunkce); cokoli
  nečitelného je selhání, nikdy ne postup dál.
- **Nový tag `[unclear]`** pro zakrytý/rozmazaný snímek. Neznamená selhání,
  ale „nedokážu z tohohle rozhodnout" — udělá se nový snímek a zeptá se
  ještě jednou, což je mnohem levnější než re-plán.
- **Nepovinné pole `verify_hint`** u kroku. `description` je anotace datasetu
  a popisuje AKCI („nájezd nad kostku“), jenže inspektor potřebuje
  pozorovatelný VÝSLEDEK („gripper je přímo nad kostkou, čelisti otevřené“).
  Bez vyplnění se použije popis jako dřív.

**Neimplementováno záměrně — učení napříč běhy.** Zvažovalo se dávat
plánovači statistiku z minulých běhů („`pick_cube` selhává ve 40 %"). Pro
diplomku je to past: orchestrace by se během měření učila a baseline ne, čímž
padá zásada „stejná data, liší se jen schéma" a jednotlivé pokusy přestanou
být nezávislé (rozbité intervaly spolehlivosti). Čistá cesta by byla nechat
systém sbírat zkušenost na ladicích bězích, poučky **zamrznout** a teprve pak
měřit — nebo z toho udělat samostatný experiment C měřený odděleně.
Paměť v rámci jednoho běhu (historie pokusů v kontextu) tímhle problémem
netrpí a implementovaná je.

## 2026-08-09 — appka nekontrolovala, co je natrénované; oprava verify_hint

Vzniklo z konkrétní stížnosti nad screenshotem editoru kroků: appka neříká,
jestli je pro daný krok checkpoint vůbec hotový, a formulář nejasně
komunikuje, které pole čte který model.

**Nalezena a opravená chyba:** `step_catalog()` nikdy nekopírovala
`verify_hint` z konfigurace do katalogu, kterým se krmí `_verify()`. Pole ve
formuláři šlo vyplnit, uložilo se do `config.json`, ale inspektor ho **nikdy
neviděl** — vždycky dostal jen `description` jako fallback. Vyplněná
nápověda u `grab_cube` z minulé iterace tedy celou dobu nedělala nic.

**Kontrola natrénovanosti.** Nová `orchestrator.checkpoint_status(path)` dělá
přesně tu samou kontrolu, kterou `inference_daemon.resolve_policy_dir()` dělá
při skutečném načítání (`<path>/checkpoints/last/pretrained_model/config.json`
existuje?) — takže appka a live běh se nemůžou rozejít v tom, co je „hotové".
`model_status(cfg)` to spočítá pro baseline i všechny nakonfigurované kroky,
nový endpoint `GET /api/models` to vystavuje. Setup stránka u každého kroku
(a u baseline ve 2 sekcích) ukazuje kolečko: zeleně „● N kroků", žlutě
„○ netrénováno" (title má plnou cestu). Zjištěno při ověřování: `release_act`
má jen **1000 kroků** (ostatní 5000/5000/3000/3000) — silně nedotrénováno,
řešit před měřením.

**Zpřehlednění formuláře.** Editor kroků teď u každého pole píše, kam jde
(do tooltipu i do statického vysvětlení nad tabulkou): slug → ID dovednosti
pro LLM + jméno datasetu/checkpointu; popis → anotace + katalog pro LLM +
záložní text pro VLM; úchop → LLM popis dovednosti + daemon protokol B;
časový limit → jen daemon; cílový stav (hint) → jen VLM.

## 2026-08-09 — počet tréninkových kroků per krok, badge zjednodušený na fajfku

Předchozí iterace (badge s cestou a počtem kroků) byla vedle — autor chtěl
jednodušší věc: nastavit si u kroku, na kolik kroků se má trénovat, aby se
podle toho přepsal generovaný `lerobot_train` příkaz, a mít jen fajfku/křížek,
jestli je to hotové.

- Nové nepovinné pole **„Kroků tréninku"** u každého kroku (`train_steps`),
  stejný vzor jako `timeout_s` — prázdné pole = použije se globální „Kroků
  tréninku". Generovaný příkaz v sekci 7 teď použije tohle číslo místo
  globálního a `save_freq` se automaticky omezí na `min(save_freq, steps)` —
  vyšší `save_freq` než cílové kroky by neuložilo žádný checkpoint vůbec,
  což by se zjistilo až z chybové hlášky `lerobot-train`.
- Badge zjednodušený na **✓ / ✗**. ✓ = checkpoint na disku má aspoň tolik
  kroků, kolik je aktuálně nastavený cíl (per-krokové pole, nebo globální
  default). ✗ = buď žádný checkpoint, nebo existuje, ale má míň kroků, než
  je teď nastaveno — typicky proto, že se cíl zvedl a ještě se nedotrénovalo.
  Backend to počítá v `checkpoint_status(path, target_steps)` /
  `model_status(cfg)`, cesta a přesný počet kroků jsou v title po najetí myší.
- Opraven i skutečný stav věci: globální `train_steps` v `config.json` byl
  pořád `20000`, ale nikdy se na to nic netrénovalo — všechny čtyři modely
  vznikly ručně spuštěnými příkazy s jinými čísly (5000/3000/3000/1000).
  Nastaveno tak, aby fajfky odpovídaly realitě: `grab_cube` (5000),
  `pick_cube` (3000), `move` (3000) mají teď per-krokový `train_steps`
  rovný tomu, na co byly úmyslně natrénované (✓); `release` žádný override
  nemá, takže spadá do (opraveného) globálního defaultu 5000 — a protože má
  na disku jen 1000, správně to teď appka hlásí křížkem.

## 2026-08-09 — revize cizích změn (více kamer, povinné ověření úchopu)

Kontrola rozpracovaných změn (více kamer do obou modelů, `extract_gripper_load`,
povinné ověření úchopu protokolem B, přepsané prompty, průběh běhu v UI).
Funkčnost ověřena spuštěním daemona v simulovaném režimu a celého
orchestračního cyklu s falešným LM Studiem.

**Opravené chyby:**

1. **`active_task = task` bylo smazáno** (`inference_daemon.py`, `SET_TASK`).
   `active_task` tím zůstal prázdný řetězec po celou dobu běhu, takže se
   politika podmiňovala na `""` místo na jméno kroku a `[STATUS] TASK_DONE`
   hlásil krok bez jména. Ověřeno empiricky (`TASK_DONE:  | Protokol B…`),
   po opravě `TASK_DONE: pick_cube | …`. U ACT to je bez následku, u jazykově
   podmíněné politiky (SmolVLA je v nabídce) by to bylo zásadní.
2. **`protocol_b_enabled` zmizelo ze `server.py` DEFAULT_CONFIG**, přestože ho
   kód čte a UI má pro něj zaškrtávátko. Doplněno zpět.
3. **`holding_limit_ma` nešlo nastavit z UI** — nový klíč se používal, ale pole
   chybělo. Doplněno vedle limitu protokolu B, s vysvětlením, proč jsou to dva
   *různé* prahy (ukončení kroku vs. odečet „drží/nedrží").
4. **`_verify` volal VLM podruhé se stejným snímkem**, když se nový snímek
   nepodařilo pořídit — zaručeně stejná odpověď za cenu dalšího volání.
5. **`case 'snapshot'` v `run.js`** deklaroval `const` bez bloku (scope celého
   switche). Zabaleno do `{}`.
6. **Mrtvé klíče `latch_timeout_s` a `step_timeout_s`** zůstaly v `config.json`
   po commitu `2f5e62a`, ačkoli je už nikdo nečte. Odstraněny; docstring
   `compute_step_timeouts.py` na ně přestal odkazovat.
7. **`GRASP_WORDS`** zůstalo jako mrtvý kód po zrušení heuristiky podle názvu
   kroku. Odstraněno (i s vysvětlením, proč se hádat nemá).

**Zásadní pojistka — povinné ověření úchopu mohlo utopit každý běh.**
Nová logika bere u úchopových kroků neaktivování protokolu B jako tvrdé
selhání `[object_missed]`, bez ohledu na inspektora. To dává smysl, *pokud
čidlo proudu funguje*. Jenže ve skutečných bězích v `runs/` telemetrie hlásila
`load:0` pořád — proud se nikdy nepřečetl. Ověřeno testem: při nulovém čidle
každý úchop selže, spotřebuje se celý rozpočet re-plánů a běh skončí chybou —
**pokaždé, bez šance uspět**. Doplněno: `Daemon.load_ever_nonzero` sleduje,
jestli čidlo za celý běh vůbec někdy vrátilo nenulovou hodnotu; když ne,
kontrola se přeskočí, krok posoudí inspektor a do logu jde hlasitá chyba.
Když čidlo funguje, kontrola platí beze změny (ověřeno oběma scénáři).

**Čtení proudu přepsáno.** `get_observation()` u SO-101 vrací jen `<motor>.pos`
a snímky — proud tam **není vůbec**, takže větev parsující observation je pro
tenhle robot mrtvá a jedinou funkční cestou je přímé čtení registru ze
sběrnice. `Present_Current` (reg. 69) v tabulce `sts3215` je a nenormalizuje se.
Původní implementace ale zkoušela až 4 názvy registrů **v každém snímku**
(30×/s) a při nulovém proudu udělala dvě sériové transakce navíc na sběrnici,
která už při nahrávání padala na 19 Hz. Přepsáno: funkční registr se zjistí
jednou a pak se používá; když neprojde žádný, zkoušení se vypne.

**Co zůstává k ověření na hardwaru (nelze rozhodnout od stolu):**

- **Jednotky prahů.** `Present_Current` je surová hodnota registru, u STS3215
  má LSB řádově jednotky mA (datasheet uvádí 6,5 mA). Prahy `protocol_b_limit_ma
  = 100` a `holding_limit_ma = 20` jsou ale pojmenované i použité jako
  miliampéry. Dokud se skutečná hodnota nezměří, nelze říct, jestli 100
  znamená 100 mA nebo ~650 mA. **Změřit a prahy podle toho nastavit.**
- Jestli `bus.read("Present_Current", "gripper")` na daném kusu hardwaru vůbec
  projde — daemon to teď loguje hned po připojení.
- Jestli přidané čtení nezpůsobí propady pod 30 Hz.

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
- **Ověřit, že `google/gemma-4-e4b` v LM Studiu skutečně přijímá obrázky.**
  Pokud ne, uvidíš v logu „Plánovač se snímkem selhal … zkouším bez snímku“
  a celá práce s fotkou (počáteční i re-plán) vyjde naprázdno. Alternativa:
  použít na plánování taky `qwen2.5-vl-7b-instruct`.
- Ověřit, jestli `planner_reasoning` malému modelu pomáhá, nebo škodí —
  porovnat pár běhů zapnuto/vypnuto, nebrat zlepšení jako samozřejmost.
- Vyplnit `verify_hint` u zbylých tří kroků (u `grab_cube` je vyplněný jako
  ukázka) — inspektor tím dostane pozorovatelný cíl místo popisu akce. Teď už
  se skutečně použije (oprava `step_catalog()` 2026-08-09).
- **`release_act` má jen 1000 tréninkových kroků** — dotrénovat před měřením,
  appka to teď hlásí žlutě/zeleně přímo u kroku na Setup stránce.
- Zvážit posílání dvojice snímků před/po místo jednoho — otázka „změnilo se
  něco?“ je pro VLM výrazně snazší než „je tohle správný koncový stav?“.
  Daemon snímky cachuje, takže je to levné.
- `release_act` mezitím dotrénován (`outputs/training/pick_and_place_release_act`
  existuje) — všechny 4 krokové modely jsou teď kompletní.

## 2026-09-19 — první živý test na aktuálním kódu: tři nálezy k `catch_cube`

První skutečný test po sloučení fúze důkazů a rozdělení protokolu A na
grasp/reset threshold (viz commit `61812aa` a okolí). Dva běhy
(`runs/20260919-204911.json`, `runs/20260919-212218.json`) + navazující
`210802`. Tři samostatné věci, každá potvrzená z telemetrie/fotek, ne odhad.

**1. Oprava (kód): inspektor si u RESET kroku pletl vlastní nálepku se
skutečným cílem.** `homing` fyzicky sedlo správně (protokol A, `drženo 7/7`,
top kamera potvrzuje domovskou pozici — `images/20260919-204911/a002_1.jpg`),
ale fúze důkazů to i tak shodila jako `[unknown_failure]`, protože VLM
inspektor u zápěstní kamery ([a002_2.jpg](../images/20260919-204911/a002_2.jpg),
náhodou zabírá misku i z domovské pozice) vygeneroval odůvodnění mluvící o
„positioned for approach" — frázi, kterou nemá odkud znát kromě nálepky
`[positioning/approach, no grasp]`, co `_verify()` lepí ke KAŽDÉMU
negraspovému kroku včetně resetu. Opraveno v `orchestrator.py` `_verify()`:
RESET kroky mají teď vlastní nálepku bez slova „approach" a explicitní větu,
že okolní předměty v záběru nejsou pro reset relevantní.

**2. Oprava (kód): `catch_cube` startoval z pozice, kterou nikdy neviděl v
tréninku.** Audit `diplomka_1_catch_cube` (120 epizod) ukázal, že hlavní
klouby ramene mají při startu epizody směrodatnou odchylku jen 1,6–4° — každá
tréninková epizoda fakticky začíná z domovské pozice, přestože popis
dovednosti tvrdí „z libovolné pozice". V živém běhu ale orchestrátor občas
pustí `catch_cube` znovu bez mezikroku `homing` — rameno pak startuje tam, kde
skončil předchozí neúspěšný krok, naměřeno až 150–220° od tréninkové pozice na
rameni/lokti (`runs/20260919-212218.json`, pokusy 7 a 8 — identické selhání
dvakrát po sobě, gripper míří k misce místo ke kostce). Přidána
`plan_pose_conflict()` (stejný princip jako existující `plan_state_conflict()`
— nikdy tichý přepis plánu, jen upozornění a re-ask CEO), testy v
`tests/test_plan_check.py`.

**3. Nález pro diplomku (NEOPRAVENO, netýká se kódu appky): rozdělení
nahrávky na dovednosti systematicky poškozuje přesnost úchopu přes ACT
`chunk_size`.**

I po opravě bodu 2 (start ze správné pozice) `catch_cube` míjí jinak, než
baseline — kostka skončí před nebo za čelistmi, ne uprostřed
(`images/20260919-210802/a004_1.jpg` + `a004_2.jpg`, start ~5° od tréninkové
pozice, přesto minutí). Baseline (`diplomka_1_120ep_act`) naproti tomu najíždí
přesně nad kostku a v úrovni s ní — když selže, je to spíš špatné načasování
sevření gripperu, ne špatná pozice.

Mechanismus (ověřeno přímo ve zdrojáku LeRobotu, ne odhad):
`lerobot/datasets/dataset_reader.py:215-232`, `_get_query_indices()` — cílová
budoucí akce se u KAŽDÉHO snímku ořízne na `min(ep_end - 1, idx + delta)` a
zbytek označí jako `is_pad`. ACT má `chunk_size = 100` výchozí
(`lerobot/policies/act/configuration_act.py:85`) — posledních 100 snímků
KAŽDÉ epizody tedy dostává čím dál víc ořezaný/opakovaný cíl místo
skutečného pokračování pohybu.

Změřené délky epizod (`meta/episodes/*.parquet`, `length`):

| dataset | průměr snímků/epizoda | % epizod < 200 snímků |
| --- | --- | --- |
| `diplomka_1` (baseline, celá úloha) | 598 | 0 % |
| `diplomka_1_catch_cube` | 180 | 81 % |
| `diplomka_1_carry_cube` | 117 | 100 % (12 % celých epizod < 100) |
| `diplomka_1_homing` | 302 | 1 % |

U `catch_cube` leží finální přiblížení a úchop (druhá půlka epizody) přesně v
tom 100-snímkovém poškozeném okně. U baseline sedí ten samý moment úchopu
(kolem snímku 180 z 598) skoro 400 snímků od konce epizody — mimo okno,
čistý signál. `carry_cube` je na tom nejhůř (celé epizody kratší než okno).

**Proč se to nemění teď:** rozdělení nahrávky na `catch_cube`/`carry_cube`/
`homing` existuje výhradně proto, aby orchestrace i baseline trénovaly ze
stejných dat stejné kvality — jinak by šlo o srovnání architektury
zamotané se srovnáním datasetů, což by znehodnotilo celé měření. Kdyby tahle
podmínka neplatila, řešením by bylo buď nahrávat samostatné epizody na
každou dovednost s vlastním „doběhem" po cíli (padding by pak padl na
nezajímavé snímky, ne na přiblížení), nebo nastavit `chunk_size` zvlášť podle
délky epizody místo univerzálního defaultu 100 — což `web/setup.js`
`trainFlags()` nikdy nedělal (`--policy.chunk_size` se nikdy explicitně
neposílá, jede se na defaultu ACT configu stejně pro baseline i pro všechny
tři krokové modely, bez ohledu na to, že mají řádově kratší epizody). Obojí
je ale retrénink, ne oprava kódu appky — sem patří jen jako doložený nález
pro diskuzi v diplomce (proč orchestrovaný úchop typicky míří hůř než
monolitický baseline i po opravě startovní pozice), ne jako TODO na dnešek.

## 2026-09-25 — první živý běh s `diplomka_2` (retrénované modely, `chunk_size=15`): dvě chyby

Nahrání retrénovaných modelů do projektu `diplomka_2` (12 checkpointů pod
standardními názvy bez `_cs15`, ověřené sha256 vah, všech 12 mělo poslední krok
= cílový a váhy bez NaN/inf) a první orchestrovaný běh na nich
(`runs/20260925-202316.json`, telemetrie `20260925-202327.jsonl`). Běh selhal a
oba důvody dohledány z reálné telemetrie, ne odhadem.

**1. Zaseknutá sběrnice servo motorů — oprava (`bus_guard.py`).** V 20:24:27 přestal
daemon dostávat polohy kloubů a od té chvíle hlásil jen
`Failed to sync read 'Present_Position' … [TxRxResult] Port is in use!`.
V telemetrii jsou od 20:24:27.208 klouby zmrzlé bit po bitu (232 tiků po sobě
identické, `load` 0) — tik jen opakoval poslední úspěšné čtení, krok doběhl do
timeoutu a LLM/VLM pak usuzovaly nad během, který už nic neměřil (`unclear`,
`no_image`, re-plán).

Mechanismus (ověřen ve zdrojáku `scservo_sdk/protocol_packet_handler.py`):
`txPacket()` nastaví `port.is_using = True` (řádek 75) a smaže ho až na konci
`rxPacket()` (řádek 171). Výjimka mezi tím — typicky `SerialException` z USB
adaptéru na Windows — nechá příznak viset do konce procesu a každé další volání
okamžitě vrátí `COMM_PORT_BUSY`. Původní (první) výjimka se z logu dohledat
nedala: orchestrátor propouští každý řádek stderr daemona jako událost a
záplava (383× stejná hláška) vytlačila z 500místné historie serveru všechno
starší. Příčina samotného prvního výpadku (pravděpodobně USB/napájení při rychlém
švihu ramene se zátěží gripperu ~480) tak zůstává neprokázaná.

Řešení: `BusGuard` (samostatný modul bez závislostí, ať jde testovat bez robota)
— (a) uvolní `is_using` a vyprázdní port, ale až od druhé chyby v řadě, protože
jediné „Port is in use“ bývá běžný souběh dvou vláken, do jehož rozběhnuté
transakce se zasahovat nemá; (b) po pěti marných uvolněních port zavře a znovu
otevře; (c) trvá-li porucha 8 s, daemon skončí a orchestrátor ho spustí znovu
(stávající větev „Daemon selhal — restartuji“, čerstvý proces = nový
PortHandler); (d) opakující se hlášky omezí, aby první výjimka zůstala vidět;
(e) do telemetrie zapíše `bus_fault`, `bus_recovered` (s dobou výpadku) a
`bus_lost`, aby bylo u každého běhu poznat, že proběhl s výpadkem. Zamítnuté
alternativy: záplata přímo v balíčku SDK (zásah do cizího kódu, zmizí při
přeinstalaci) a zámek kolem všech volání sběrnice (řeší souběh vláken, ale ne
příznak zanechaný výjimkou). Souvisí s tím druhá oprava: tik, jehož čtení
selhalo, se už nezapočítává do „klid drženo 7×“ Protokolu A (zmrzlé klouby
dřív mohly krok „dosednutím“ ukončit i na mrtvé sběrnici). Testy:
`tests/test_bus_guard.py` (33 případů).

Dodatečně zjištěno, že to není ojedinělé a není to způsobené novými modely:
stejný podpis (klouby v `RUNNING` identické bit po bitu, `load` konstantní,
rameno fyzicky nehybné na fotkách ze všech kroků) je už v posledním běhu z
2026-09-19 (`runs/20260919-214945.json`, telemetrie `20260919-214956.jsonl`,
stará data a projekt `diplomka_1`): od 21:51:47, necelou sekundu po startu
`catch_cube`, zůstaly klouby zmrzlé 135 s přes tři kroky a `homing` skončil za
1,02 s s „pohyb 0,00000/tik“. Tehdy si toho nikdo nevšiml. V telemetriích
z hardwaru (celkem jen ~1,6 h běhu daemona od 2026-08-27) není podpis nikde
jinde než 19. a 25. 9., tj. v obou posledních sezeních a v žádném dřívějším.
Kód daemona se mezi nimi neměnil (poslední commit 2026-09-13), rychlost kloubů
(61–107 °/s) ani proud gripperu v `carry_cube` se u nových modelů nijak
neliší od starých. Vysvětlení tedy leží pravděpodobně mimo software (USB
adaptér, kabel, port, napájení, stav ovladače po restartu PC) — v deníku z
2026-08-05 je navíc zaznamenaný zamčený COM3 kvůli zombie procesu v FTDI
ovladači, takže tenhle adaptér s ovladačem už jednou nestabilní byl. Kroky
běhů měřené po výpadku (od 21:51:47 19. 9. a od 20:24:27 25. 9.) jsou neplatná
měření, ne selhání policy.

**2. `carry_cube` končil Protokolem A za 0,22 s, aniž se rameno pohnulo — oprava
(ochranná doba pro všechny kroky).** Uživatel si všiml, že se `carry_cube` vůbec
nepohnul a už ho to zastavilo a zavolalo re-plán. Ze všech `telemetry/*.jsonl`
(každý běžný krok ukončený Protokolem A v režimu „dosedly na predikci“, což je
jen `carry_cube`, 11 případů): 9 skončilo za 0,22–0,25 s s pohybem ramene
0,8–3,2°, 2 skončily po 2,28 s a 2,89 s s pohybem 58° a 88° (skutečná
dosednutí). Mezi 0,25 s a 2,28 s nic neleží, takže jediná už existující hodnota
`protocol_a_grace_s = 1,0` obě skupiny čistě rozdělí (ochranná doba kdekoliv
mezi 0,5 a 2,0 s zablokuje právě těch 9 a žádné z 2 dobrých).

Příčina: první predikovaná akce nového bloku leží těsně u aktuální pozice, takže
vzdálenost od predikce je pod `protocol_a_target_threshold_rad` (5,25) po celých
7 tiků dřív, než se cokoliv pohne. Ochranná doba přitom platila jen pro kroky
s měřením rychlosti (`|reset`, `|grasp`) a komentář v kódu výslovně tvrdil, že
běžné kroky tímto selháním „nejsou dotčené“ — data říkají opak. Chyba nesouvisí
s novými modely: vznikla rozdělením prahu (commit `61812aa`, 2026-09-13), před
kterým `carry_cube` Protokolem A nikdy neskončil (kalibrace z 2026-09-13:
19 historických běhů, vždy timeout 20 s, protože práh 0,5 ležel pod naměřeným
rozsahem 2,57–10,72) a po kterém se začal ukončovat hned na startu. Kalibrace prahu z 2026-09-13 tak správně opravila, že Protokol A vůbec
funguje, ale přehlédla, že „vzdálenost od predikce“ je malá i tehdy, když se
rameno ještě nerozjelo.

Oprava: `grace_elapsed_a` teď platí pro každý krok, který smí Protokol A ukončit.
Ověřeno na skutečném procesu daemona v simulovaném režimu (start je tam stejný —
predikce hned u aktuální pozice): s ochrannou dobou 0 skončí běžný krok za
0,17 s, s 1,0 s za 1,01 s. Řádky v `runs/` z běhů před touto opravou, u kterých
`carry_cube` skončil za ~0,2 s, jsou tedy neplatná měření kroku, ne selhání
policy.

## 2026-09-25 (večer) — pokusný přepínač temporal ensemblingu

**Proč.** Při ručních zkouškách s `diplomka_2` (`chunk_size=15`) rameno u `catch_cube`
dojede ke kostce a po minutém úchopu pak pokračuje dál místo aby zůstalo stát. Z telemetrie
(minutý úchop = krok doběhl do timeoutu, chování od 5. s do konce): nové modely se hýbou
v mediánu 2,95 °/s (n = 6), staré (`chunk_size=100`) 1,70 °/s (n = 26, bez simulovaných
běhů); permutační test p = 0,046 pro rychlost, p = 0,20 pro vzdálenost posunu. Staré modely
bloudily taky (6 z 26 případů nad 3 °/s), rozdíl je tedy jen v míře a vzorek nových je malý.
Hypotéza: nový model přeplánovává z obrazu každých 0,5 s, starý přehrával jeden plán 3,3 s
naslepo a po jeho dohrání stál. Hypotéza je konzistentní s daty, ne prokázaná. Zároveň je
poctivé přiznat, že `chunk_size=15` byl zvolen jen podle podílu paddingu v konci epizody
a druhá strana kompromisu (kratší horizont = menší časový „závazek“ k rozhodnutí, např.
zavřít gripper) při volbě zvažována nebyla.

**Co bylo přidáno.** Přepínač `temporal_ensemble` (+ `temporal_ensemble_coeff`, výchozí 0,01
jako v původní práci o ACT) v konfiguraci projektu, v Setupu v sekci „Inference politiky“.
Jen inference, váhy se nemění. Při načtení politiky se nastaví `temporal_ensemble_coeff` a
`n_action_steps = 1` (LeRobot vyžaduje obojí spolu; kontrola v `ACTConfig.__post_init__` se
při změně po načtení z checkpointu neprovede, proto ji dělá `temporal_ensemble.py` sám). Model
se pak pouští v každém ticku a do robota jde vážený průměr všech dosavadních předpovědí pro
daný okamžik s váhou `exp(-coeff * i)`, `i = 0` nejstarší (`ACTTemporalEnsembler`,
Algorithm 2 v arXiv 2304.13705). Vypnuto (výchozí) = politika se načítá bit po bitu jako
dřív a orchestrátor nepředá daemonovi ani jeden argument navíc.

**Ověření.** Na skutečném checkpointu (`diplomka_2_catch_cube_120ep_act`): bez přepínače
ensembler neexistuje a `n_action_steps = 15`; s přepínačem existuje, `n_action_steps = 1`,
a výstup v každém z prvních šesti ticků odpovídá váženému průměru podle dokumentace
s odchylkou do 1,2·10⁻⁷. Jeden průchod modelu na RTX 4070 trvá 10,5 ms (p95 11,1 ms), tik
s ensemblingem 10,8 ms z rozpočtu 33,3 ms při 30 FPS — zbytek smyčky (čtení kamer, sběrnice,
předzpracování) tím nezměřen, proto daemon nově zapisuje do `task_done` telemetrie
`loop_period_ms`, `loop_ms_median`, `loop_ms_p95` a `loop_overruns` (kolik tiků přesáhlo
periodu o víc než 5 %). Přepínač i koeficient jsou v záznamu běhu, `run_consistency.py` je
bere jako rozhodné (neznámý klíč je rozhodný z definice).

**Rizika, na která se dívat.** (1) Smyčka nemusí stíhat 30 Hz — pak se rameno hýbe pomaleji,
než jak bylo trénováno (viz `loop_overruns`). (2) Průměr je hladší a za pohybem zaostává,
takže cílová hodnota, se kterou Protokol A u běžných kroků porovnává klouby, se chová jinak
než ta, na které se kalibroval práh; totéž platí o načasování zavření gripperu — náhlé
„zavři“ se zprůměruje se staršími „ještě ne“. (3) Zapnuto je pro všechny kroky v daemonu
(catch_cube, carry_cube i homing).

**Dodatek 2026-09-25 (večer) k nálezu č. 3 z 2026-09-19 (padding a `chunk_size`): mechanismus
ověřen, účinek na výkon robota NEprokázán.** Že se posledních `chunk_size` snímků každé epizody
učí s ořezaným cílem (`dataset_reader.py`, `_get_query_indices`) a že to u krátkých
rozdělených epizod zasahuje velkou část trajektorie, je ověřené v kódu i na délkách epizod.
Tvrzení, že to „systematicky poškozuje přesnost úchopu“ a že `chunk_size=15` to opraví, ale
první porovnání na robotu nepodporují: `catch_cube` s `chunk_size=15` bez ensemblingu chytil 6
z 12 pokusů, se zapnutým ensemblingem 1 z 8, staré modely (`chunk_size=100`, `diplomka_1`)
zatím 2 ze 3 (chycení = Protokol B). Na skoro stejném místě desky (střed kostky ~(285, 99) px)
starý model kostku chytl, nový s ensemblingem ji minul 6× po sobě v okolí (266–283, 101–116);
model je deterministický (4 pokusy z domova skončily do 7° stejně), takže jeden pokus na
kombinaci model × místo je reprezentativní. Nový model dojíždí za okraj desky, zápěstní kamera
pak vidí jen koberec (fotky `runs/20260925-212658`), což je vstup mimo trénovací data.
Vzorky jsou malé a podmínky nebyly řízené (poloha kostky se mezi běhy měnila), takže z toho
plyne jen: účinek přeškolení na `chunk_size=15` je nejasný, možná záporný. Do práce patří jako
hypotéza a otevřená otázka, ne jako prokázaná příčina. Možné vysvětlení, které se nabízí a
není ověřené: starý model naplánuje celý dojezd naráz z prvního čistého snímku z domovské pozice
a odjede ho naslepo, kdežto s krátkým chunkem se přeplánovává z mezisnímků za rychlého pohybu
(zakrytí kostky ramenem, neobvyklé úhly zápěstní kamery), a chyba se tak kumuluje. `chunk_size=15`
byl navíc zvolen jen podle podílu paddingu, druhá strana kompromisu se nezvažovala.
