# Deník výzkumné rutiny (větev `orchestration-research`)

Noční rutina, která na téhle větvi dělá **jedno soustředěné vylepšení
orchestračního schématu za noc**. Běží v cloudu **bez GPU, robota, LM Studia
a bez přístupu k `runs/`, `telemetry/`, `images/`, `config.json` a
`projects/`** (jsou v `.gitignore`). Nic z toho, co je tu napsané o chování na
skutečném hardwaru, proto **není naměřený fakt, ale hypotéza k ověření** —
každý záznam takové body vypisuje zvlášť.

Větev se nikdy nemerguje sama; revizi a merge do `main` dělá uživatel ručně.

---

## 2026-09-10 (b) — Adaptivní orchestrace: rozvaha + první krok (kalibrace prahů z telemetrie)

Tenhle záznam nevznikl v noční rutině, ale z rozhovoru s uživatelem, který
přišel s otázkou: **co kdyby se orchestrační schéma měnilo podle úlohy, nebo
bylo nějak adaptivní?** Zapisuju obojí — rozvahu i to, co z ní bylo rovnou
zapracované.

### Rámec: co orchestrace kupuje a za co

Monolitická VLA (text-conditioned policy) drží celou úlohu v jednom spojitém
latentním stavu. Orchestrace ten stav v každém kroku **serializuje do
symbolů** (slug dovednosti, tag, `SUCCESS` / `[unclear]`), čímž propustnost
mezi vrstvami klesne o několik řádů. Na oplátku vznikne **kontrolovatelnost**
(existuje místo, kde se dá zeptat „povedlo se to?") a **zotavení** (existuje
místo, kde se dá rozhodnout jinak).

Adaptivní schéma je pak otázka: *kde v téhle úloze se ta výměna propustnosti
za ověřitelnost vyplatí, a kde ne.* To je obhajitelná výzkumná otázka, ne
inženýrský tuning.

### Čtyři úrovně adaptivity (rozvaha, ne plán prací)

**0 — už existuje.** Katalog nese příznaky `grasp` / `reset` a orchestrátor
podle nich mění ověřovací schéma za běhu (protokol B u úchopu, A u homingu,
u zbytku nic). Všechno níž je zobecnění téhle jedné myšlenky.

**1 — adaptivní parametry, fixní topologie.** Prahy a limity odvozené z dat
místo z ruky. Vědecky nejméně vzrušující, prakticky největší dopad — a je to
to, co se dnes zapracovalo (viz níž).

**2 — CEO nevydá plán, ale *smlouvu*.** Pomalá vrstva by při plánování vydala
i per-step kritérium úspěchu a očekávané režimy selhání; rychlá vrstva je pak
jen vykonává. Čistě dvourychlostní argument: drahý model na **specifikaci**,
levný na **exekuci té specifikace**. Vyřešilo by to i ruční psaní
`verify_hint` v `projects/*.json`. Riziko: ground truth by se přesunula do
nejméně spolehlivé vrstvy — muselo by to být aditivní k fyzickým protokolům,
ne místo nich.

**3 — adaptivní rozpočet ověřování (meta-reasoning).** Orchestrátor si během
běhu drží odhad spolehlivosti každého kanálu a podle toho utrácí pomalou
vrstvu. Formálně bounded rationality / anytime algorithms — citovatelné.
`reflex_retry_decision()` z dnešní noční rutiny je první políčko téhle
úrovně („když levný důkaz nic netvrdí, nevolej drahou vrstvu").

**4 — adaptivní topologie.** Schéma si podle měřitelných vlastností úlohy
(počet fází, je tam úchop, je cíl vizuálně ověřitelný, je selhání vratné)
vybere samo sebe z: monolit / orchestrace bez ověřování / s ověřováním /
s re-plánováním. Nejzajímavější a experimentálně nejnebezpečnější — viz
varování níž.

### Proč je adaptivita zajímavá zrovna u robotiky

Tři věci, které u textového agenta nemají obdobu:

1. **Nevratnost není uniformní.** Minutý nájezd se zopakuje zadarmo; vyražení
   předmětu z dosahu nebo puštění křehkého objektu ne. Ověřování by se mělo
   utrácet úměrně nevratnosti kroku. Fyzikálně nejzřejmější osa adaptivity,
   jakou tahle doména má — a nikdo ji nedělá.
2. **Latence má fyzickou cenu.** Zatímco schéma přemýšlí, svět se hýbe.
   „Kolik ověřování si můžu dovolit" je vlastnost úlohy, ne konfigurace.
3. **Pozorovatelnost je per-krok jiná.** Úchop má silovou signaturu, položení
   vizuální, nájezd kinematickou. Fixní schéma si musí vybrat jednu — přesně
   proto se muselo protokolu A zakázat ukončovat `grasp`.

### Metodické varování (patří do diplomky, ne do kódu)

Adaptivní schéma je **nadmnožina** toho, co se měří. Tvrzení „adaptivní >
fixní" potřebuje napřed změřené fixní varianty, jinak není vůči čemu. Při
reálném počtu běhů, který zvládne jeden člověk s jedním ramenem, přidané
stupně volnosti sežerou statistickou sílu dřív, než z nich něco vyleze.
Doporučení: **současné schéma zůstává fixní jako měřený artefakt**,
adaptivita je navržená-ale-neměřená kapitola.

Zajímavý důsledek: `plan_checks`, `done_checks` a `steps[].outcome` jsou samy
o sobě **měřením toho, kde je fixní schéma špatně** — tedy empirickým
podkladem pro „tady by adaptivita pomohla", aniž by se musela postavit.

### Co se z toho zapracovalo (úroveň 1)

Uživatel souhlasil s kritérii odvozenými z dat a zadal k tomu tři podmínky:
aplikace musí umět **nastavit výchozí hodnoty**, musí **vyžadovat nějaký
počet běhů** pro platný výpočet a musí **zobrazovat naměřené hodnoty proti
nastaveným** kvůli debugování. Všechny tři jsou splněné.

Nový `calibrate_protocols.py` (stdlib, bez LeRobota i bez robota) čte
`telemetry/*.jsonl`, které daemon zapisuje už dnes, a staví tabulku
„naměřeno vs. nastaveno" pro `protocol_a_threshold_rad`,
`protocol_b_stability_slope`, `protocol_b_limit_ma` a `holding_limit_ma`.
Nový endpoint `GET /api/calibration` + panel v Nastavení pod ukončovacími
protokoly. Nový klíč `calibration_min_runs` (default 3).

**Skript ani endpoint nic nezapisují.** Prahy se nesmí měnit uprostřed
měřené série, aniž by o tom experimentátor věděl — aplikovat návrh je pořád
ruční editace v Nastavení. Celá cesta je tím pádem read-only a nemůže rozbít
běžící experimenty.

Jak se odvozuje návrh: u prahů, které mají oddělit dva režimy téže veličiny
(klid vs. pohyb, plató vs. stoupání, prázdné čelisti vs. držení), se vezme
spodní a horní okraj naměřené mezery a návrh je jejich **geometrický
průměr** — střed mezery na logaritmické škále. Není to odhadnutá konstanta:
mezera je naměřená a její střed je jediné místo stejně vzdálené od obou chyb
(práh tak nízko, že protokol nikdy nespustí, × tak vysoko, že spustí
uprostřed pohybu). Aritmetický průměr by u režimů vzdálených o řád skončil
těsně pod horním okrajem, tedy prakticky na hranici pohybu.

`holding_limit_ma` je jediný práh, u kterého jde „drží / nedrží" oštítkovat
bez inspektora: klidové tiky **před prvním krokem běhu** mají prázdné
čelisti, klidové tiky **hned po kroku ukončeném protokolem B** něco drží.
Štítek plyne z pořadí událostí, ne z prahu, který se kalibruje.

`calibration_min_runs` je tvrdá pojistka: dokud krok nemá tolik běhů,
nevydá se u něj **ani verdikt, ani návrh**. Default 3 není doporučená
velikost vzorku, ale nejmenší *n*, při kterém je medián doopravdy prostřední
pozorování a ne průměr dvou krajních — spodní hranice, pod kterou nemá smysl
počítat nic. Reálně si ji uživatel nastaví výš, na to je to pole v UI.

### Co jsem zvažoval a zavrhl

- **Automaticky aplikovat spočtené prahy.** Tiše by to změnilo význam každého
  pozdějšího běhu. Kdyby to uživatel chtěl, správná podoba je přepínač +
  zápis skutečně použitých prahů do `runs/*.json`, ne tichá aplikace.
- **Per-step override prahů (`steps[].protocol_*`, jako už je `timeout_s`).**
  Architektonicky je tohle ta pravá „adaptivní" podoba a katalog na ni má
  strukturu. Zavrženo pro teď: prahy jdou do daemona jako CLI argumenty při
  startu a daemon přežívá celý běh, takže by se per-step hodnoty musely
  posílat protokolem `SET_TASK` — zásah do rozhraní, které drží reálný
  hardware, bez možnosti ho tady ověřit. Až po tomhle měřicím kroku.
- **Navrhovat i `protocol_b_limit_ma`.** Nejde to poctivě: sevření předmětu a
  průjezd proudu při zavírání naprázdno se v naměřených hodnotách překrývají
  (viz komentáře u `PROTOCOL_B_GRACE_S` — potvrzený úchop s nárůstem 108 ležel
  mezi falešnými spuštěními 72 a 139). Z telemetrie samotné je oddělit nelze;
  chybí nezávislý štítek „tenhle úchop se povedl", který zná až inspektor.
  Tabulka proto u téhle veličiny jen popisuje, co se naměřilo.
- **Odvozovat `protocol_b_grace_s` a patience.** Šlo by to (doba do odeznění
  rozjezdového transientu), ale bez okna, které bych si musel vymyslet, to
  nevyjde. Nechávám na příště.

### Co se přidalo navíc (a proč)

`steps[].t_start` / `t_end` v `runs/*.json` — aditivní časové okno každého
pokusu. Nepoužívá to zatím nic, a je to vědomé: bez něj **nejde spárovat
`runs/*.json` s `telemetry/*.jsonl` po krocích**, a právě to párování je
jediná cesta k nezávislému štítku „úchop se povedl", který dnes chybí
kalibraci protokolu B. Data, která se nezaznamenají teď, se zpětně
nedoberou — každý běh bez toho pole je běh, který na lepší kalibraci nepůjde
použít.

### Otevřené otázky

- Sedí návrhy na reálné telemetrii, nebo jsou mezery tak široké, že je návrh
  bezcenný? U prahu protokolu A je mezera podle komentářů v kódu obrovská
  (šum ~0.5 vs. pohyb v desítkách), takže tam je hlavní hodnota v **kontrole,
  že nastavená hodnota vůbec leží v mezeře**, ne v samotném čísle.
- Kolik běhů je doopravdy potřeba, než se rozdělení ustálí? To se dá zjistit
  jen tak, že se `calibration_min_runs` postupně zvedá a kouká se, jestli se
  návrh hýbe.
- Stojí za to párování `runs` × `telemetry` (viz `t_start` / `t_end`), nebo
  je jednodušší nechat uživatele pár úchopů ručně oštítkovat?

### Dodatek: stabilita nastavení mezi běhy

Uživatel k tomu doplnil tvrdý požadavek: **nastavení se nesmí měnit ani mezi
běhy**, protože se v diplomce porovnává baseline s orchestrací a jednotlivé
pokusy orchestrace by jinak měly každý jiné nastavení. To je správně a je to
přesně důvod, proč je celá kalibrační cesta jen čtecí.

Jenže „nastavení jsem neměnil" je **tvrzení o minulosti, které si nikdo
nepamatuje přesně** — a je to táž chyba, na kterou doplatil zavržený
`plan_repeat_conflict()` (2026-09-08): tvrdit něco o skutečnosti z paměti
místo ze záznamu. Naštěstí záznam existuje: `_save_run()` ukládá do každého
`runs/<id>.json` celou konfiguraci běhu i katalog kroků. Jen to nikdo nečetl
zpátky.

Nový `run_consistency.py` (stdlib, jen `runs/*.json`) + `GET
/api/runs/consistency` + panel v Nastavení. Porovná konfigurace napříč běhy a
vypíše, co se lišilo. Klíč je označený jako **rozhodný**, pokud se dostane
(a) na příkazovou řádku daemona, (b) do řídicí smyčky orchestrátoru, nebo
(c) do promptu některého z modelů — seznam je odvozený čtením `Daemon.start()`
a `run()`, ne odhadnutý. Nerozhodné rozdíly (cesty, port robota) se vypíšou
taky, jen stabilitu neshodí; nic se neschovává.

Detaily, které se ukázaly jako podstatné:

- **Per-step `timeout_s` z katalogu se porovnává stejně jako globální prahy**
  (jako `krok[grab].timeout_s`). Je to hodnota, která rozhoduje, kdy se krok
  usekne — její změna mezi běhy je úplně stejně zásadní jako změna prahu, a
  přitom ji zapisuje existující tlačítko „Spustit teď" u časových limitů.
- **Chybějící klíč není shoda s výchozí hodnotou.** Běh z doby před přidáním
  přepínače ho v konfiguraci nemá; hlásí se to jako rozdíl, ne se tiše
  dopočítává default.
- **Běhy bez zaznamenané konfigurace** (starší formát) se počítají zvlášť —
  stabilitu ani nepotvrzují, ani nevyvracejí.
- **`calibration_min_runs` se ignoruje**, protože neovlivňuje jediný řádek
  toho, co dělá robot. Jinak by každé přenastavení kalibrační tabulky
  vypadalo jako změna experimentu.
- Filtr podle `task_slug` je default: běhy jiné úlohy se liší skoro ve všem a
  byl by to šum, ne nález.

V UI je zároveň vypsané, **kudy se nastavení mezi běhy vůbec může změnit**:
ruční editace v Nastavení, přepnutí projektu, tlačítko „Spustit teď" u
časových limitů (píše `timeout_s` do `config.json`) a ruční editace
`config.json`. Kalibrační tabulka ani tenhle přehled mezi nimi nejsou.

Pozn.: `runs/*.json` píše jen `Orchestrator._save_run()`, takže tenhle přehled
pokrývá **orchestrační větev**. Baseline se přes orchestrátor nepouští.

### Co potřebuje ověření na reálném hardwaru (uživatel)

0. **Pusť kontrolu stability na svých dosavadních běhách.** Je to čtecí a
   okamžité, a pokud ti vyhlásí rozdíly, je lepší to vědět teď než při psaní
   výsledků. Čekej, že starší běhy budou mít rozdíly v klíčích, které
   přibyly v posledních nocích (`uncertain_retry`, `plan_state_check`,
   `done_visual_check`) — to je správné chování, ne chyba.
1. **Že tabulka vůbec něco ukáže** — závisí na tom, že daemon telemetrii
   opravdu píše (`--telemetry-log` není `off`) a že `telemetry/` není prázdné.
2. **Jestli verdikty dávají smysl u prahů, o kterých víš, že jsou dobře.**
   Nejlevnější sanity check: podívej se na tabulku po pár běhů, které
   proběhly bez problémů — tam musí být všude „leží v naměřené mezeře".
   Kdyby ne, je špatně kalibrace, ne tvoje nastavení.
3. **Jestli `holding_limit_ma` návrh sedí** s tím, co ti vyšlo z
   `measure_gripper_current.py`. Ty dvě čísla mají spolu souviset a je to
   nezávislá kontrola obojího.
4. Nová pole `t_start` / `t_end` v `runs/*.json` jsou aditivní, ale ověř, že
   ti přes ně analytické skripty neprojdou jinak.

---

## 2026-09-10 — „Nikdo to neviděl" přestává být selhání (OUTCOME_UNCERTAIN)

### Co jsem zkoumal

Navázal jsem na poučení z 2026-09-08: **stav prostředí nelze odvodit z
historie běhu, o světě smí mluvit jen měření.** Hledal jsem, jestli tenhle
princip neporušuje i něco, co v repu už dávno je. Porušuje — v pravdivostní
tabulce `fuse_evidence()` jsou dvě buňky, ve kterých orchestrátor **vyrábí
negativní důkaz z chybějícího důkazu**:

| fyzika | inspektor | dosavadní verdikt |
|---|---|---|
| `NONE` (krok bez protokolu A/B) | `UNCLEAR` | selhání `[unclear]` |
| `UNCLEAR` (zátěž v pásmu nejistoty) | `UNCLEAR` | selhání `[unclear]` |

V obou případech **žádný kanál netvrdí, že krok dopadl špatně**. Fyzika mlčí
(u kroku typu nájezd/přenos/položení žádný ukončovací signál neexistuje),
kamera říká doslova „z tohohle snímku to nepoznám" — a `_verify()` už jednou
zkusil nový snímek. Přesto se to zapsalo jako `success: False` s chybovým
tagem a mělo tři důsledky, které jdou proti sobě:

1. **Zkresluje data diplomky.** V `runs/*.json` je nepozorovaný krok
   nerozeznatelný od kroku, který prokazatelně selhal.
2. **Lže plánovači.** `_build_replan_context()` počítá `failures_here` a při
   >1 přidá větu „This step has now failed 3x — repeating it unchanged is
   unlikely to work." U kroku, který nikdo neviděl, je to **nepravdivé
   tvrzení o světě**, a dostává ho ta nejméně spolehlivá vrstva jako fakt.
   Přitom správná reakce na nepozorovaný krok bývá právě „zopakuj ho" nebo
   „ukliď rameno z výhledu" — tedy pravý opak toho, kam ta věta plánovač tlačí.
3. **Utrácí tu nejdražší věc v systému za nic.** Neprůkazná kontrola dnes
   spustí re-plán, tedy volání pomalého CEO — kterému ale orchestrátor
   nepředává **žádnou novou informaci**: plán nebyl ničím vyvrácen. A protože
   plánovač v takové situaci často vrátí tentýž zbytek plánu (kvůli tomu v
   `run()` vůbec existuje loop-guard na identický plán), robot ten krok
   stejně nakonec zopakuje — jen o jedno pomalé volání LLM později.

To je přesně to místo, kde má dvourychlostní architektura co nabídnout:
**problém pozorování se řeší pozorováním, ne plánováním.**

### Co jsem změnil

**1. `fuse_evidence()` vrací pátou hodnotu `outcome`** ∈
`success` / `failure` / `uncertain` (`OUTCOME_*`). `uncertain` nastane právě
v těch dvou buňkách výše. `success` **zůstává boolean se stejným významem**
(u nepozorovaného kroku `False` — nic se nepotvrdilo), takže žádná existující
analýza nad `steps[].success` se nemění; `outcome` je to, co ty dva případy
teprve odliší.

Dvě hranice jsou vědomé a jsou v testech:

- **`NOIMG` (rozbitá kamera) uncertain NENÍ.** Nejednoznačný snímek se dá
  vyřešit tím, že se člověk podívá znovu; mrtvý kanál ne. Zůstává selháním.
- **`SKIPPED` (ablace „jen fyzika") uncertain NENÍ.** Tam taky nikdo nic
  neviděl, ale v té ablaci je „žádná kontrola" definovaná jako „žádná
  námitka". Jinak by ablace začala kroky přeověřovat, což by ji rozbilo.

**2. Nová čistá funkce `reflex_retry_decision()`** + její zapojení do `run()`.
První neprůkazná kontrola daného kroku → **krok se jednou zopakuje bez volání
CEO** (hot-swap policy je no-op, `set_policy()` na stejnou cestu se vrací
hned, takže cena je jedno spuštění dovednosti). Druhá neprůkazná kontrola
téhož kroku v řadě → bere se jako selhání a jde se na normální re-plán.

**Žádná nová vymyšlená konstanta.** Mez „jedno zopakování, pak eskalace" není
odhadnutý parametr, ale bod, ve kterém „podívej se znovu" prokazatelně
přestalo fungovat — a je to týž idiom, jaký v `run()` už používá loop-guard
na identický plán (jedno opakování se toleruje, druhé běh ukončí).

**3. Re-plán dostává pravdu.** Když se eskaluje, `_build_replan_context()`
dostane `unobserved=True` a napíše, že krok **nebyl vyhodnocen**, že jeho
výsledek je *neznámý, ne negativní*, a nabídne krok obnovující pozorovatelnost
(větu o `[RESET]` přidá jen tehdy, když katalog nějaký `reset` krok opravdu
má — nic task-specific). `failures_here` **nepočítá nepozorované pokusy**,
takže věta o „failed Nx" se objeví jen u skutečně pozorovaných selhání. V
soupisu `PROGRESS THIS RUN` se takový pokus vypíše jako
`NOT VERIFIED [unclear] (outcome unknown, not a failure)`.

**4. Zpětná kompatibilita a ablace.** Nová aditivní pole v `runs/*.json`:
`steps[].outcome` a `steps[].reflex_retry`. Nový přepínač `uncertain_retry`
(default `true`) v `server.py`, `web/config.js`, checkbox v `web/index.html`;
`false` reprodukuje **přesně** dosavadní chování (neprůkazný krok = selhání =
re-plán), takže je to ablatovatelná podmínka, ne tichá změna měřené smyčky.
Testy: `tests/test_fusion.py` rozšířený o `outcome` ve všech 18 kombinacích,
o invariant „`uncertain` nikdy nesmí přijít se `success=True`" a o 7 případů
`reflex_retry_decision()`.

### Co jsem zvažoval a zavrhl

- **Při `uncertain` spustit `[RESET]` dovednost a vyfotit znovu.** Nejsilnější
  nápad večera a věcně správný: nejčastější příčina `[unclear]` je okluze
  vlastním ramenem a homing je přesně ten úkon, co ji odstraní, je levný a
  task-nezávislý (katalogový příznak `reset`). **Zavrhl jsem ho na dnešek:**
  u úchopového kroku by odjezd domů s předmětem v čelistech výrazně změnil
  scénu, kterou se orchestrátor právě chystal posoudit, a tenhle druh
  fyzického chování nemám jak bez robota ověřit. Reflexní zopakování téhož
  kroku je proti tomu bezpečnější v tom, že **nezavádí žádnou novou třídu
  pohybu** — přesně to samé se dnes stane, když plánovač po neprůkazné
  kontrole vrátí tentýž plán. Nechávám to jako kandidáta na příště, ale je to
  rozhodnutí, které chce tvůj názor na to, co robot smí dělat.
- **Nechat `uncertain` proletět jako úspěch a jít na další krok.** To by z
  chybějícího důkazu vyrobilo pozitivní tvrzení — stejná chyba jako dnešní
  stav, jen v opačném směru, a navíc by tiše nafukovala měřené číslo.
- **Vlastní rozpočet nepozorování oddělený od `max_replans`.** Znělo to
  čistě, ale znamenalo by to druhý limit, který musí uživatel ladit. Takhle
  eskalace spotřebuje re-plán jako každé jiné selhání a běh zůstává omezený
  jediným existujícím číslem.
- **Počítat `uncertain` do `success` runu jinak.** Nesahám na hlavní měřenou
  veličinu, stejně jako u `done_checks` (2026-09-09). Data na obě varianty
  vyhodnocení teď existují.

### Otevřené otázky

- **Jak často vůbec `uncertain` nastává?** Teď je to měřitelné
  (`steps[].outcome`). Jestli je to <2 % kroků, je celá změna hlavně
  metodická čistota; jestli je to 20 %, byla dosud pětina „selhání" v datech
  diplomky ve skutečnosti nepozorování.
- **Pomůže reflexní zopakování, nebo jen sežere čas?** `steps[].reflex_retry`
  říká, u kterých pokusů se to stalo — dá se spočítat, kolikrát druhý pokus
  skončil `success` (tedy kolik volání CEO se ušetřilo) a kolikrát zase
  `uncertain`.
- Kdyby vyšlo, že po zopakování je verdikt skoro vždy zase `uncertain`, je to
  silný argument pro tu zavrženou variantu s `[RESET]`: znamenalo by to, že
  příčina je okluze, kterou opakování téhož kroku neodstraní.

### Co potřebuje ověření na reálném hardwaru (uživatel)

1. **Že zopakování nevyhodnotitelného kroku nedělá nic divokého.** Je to
   jediné místo, kde tahle změna mění fyzické chování robota — a mění ho na
   to, co se dnes stane po re-plánu, který vrátí tentýž plán. Nejlevnější
   sanity check: úloha s krokem, kde rameno běžně zaclání kameře.
2. **Že se `[unclear]` u tvých kroků skutečně párují s `outcome: uncertain`**
   a ne s `outcome: failure` — pokud se u tebe `[unclear]` objevuje hlavně u
   úchopových kroků, rozhoduje o nich protokol B a tahle změna se jich
   netýká vůbec.
3. **Že nová pole `outcome` / `reflex_retry` neshodí tvoje analytické
   skripty** (jsou aditivní, ale ověř).
4. Ablace na jeden večer: tatáž úloha 2× s `uncertain_retry: true` a 2× s
   `false`, a porovnat počet volání CEO na běh.

---

## 2026-09-09 — Zavrženo: kontrola opakovaného plánu. Nové: ověření „cíl splněn"

### Zavržená noc 2026-09-08 (revert)

Předchozí noc přidala `plan_repeat_conflict()` — kontrolu, která hlásila
opakovaný re-plán, když se od minulého pokusu „nic nepovedlo" (počet
úspěšných kroků byl stejný). **Uživatel to zamítl a měl pravdu; commit je
revertovaný.** Chyba nebyla v implementaci, ale v premise:

1. **Počet úspěšných kroků není stav světa.** Neúspěšný pokus je pořád
   pohyb — robot mohl předmět posunout, převrátit nebo vystrčit z dosahu.
   „Od té doby se nic nepovedlo, takže je situace stejná" je nepravdivé
   tvrzení o prostředí, odvozené z účetnictví orchestrátoru. Zopakovaný plán
   proto může být zcela legitimní pokus v jiné, změněné scéně.
2. **Plán začínající homingem je nový pokus z výchozí pozice**, ne smyčka.
   Že to moje kontrola většinou nehlásila, byla shoda okolností plynoucí z
   toho, jak se zaznamenával zbytek plánu, ne vlastnost návrhu.

**Poučení, které platí i pro příští noci: stav prostředí nelze odvodit z
historie běhu. O světě smí mluvit jen měření** — čidlo, nebo kamera. Ten
princip už v repu je (`fuse_evidence()` staví na dvou měřicích kanálech);
včerejší kontrola ho porušila a je proto pryč. Zůstává pouze původní
loop-guard `plan == previous_remaining`, který netvrdí nic o prostředí —
jen o tom, že plánovač po selhání zopakoval doslova totéž.

### Co jsem zkoumal dnes

Hledal jsem tvrzení, které **rozhoduje o výsledku běhu a přitom ho nikdo
neověřuje**. Našel jsem jedno, a je to to nejdůležitější v celém schématu:

```python
if plan == [PLAN_DONE]:
    return self._finish(True, started)   # success: True, žádná kontrola
```

Když CEO odpoví `["DONE"]` („cíl je už splněný"), běh se **okamžitě zapíše
do `runs/*.json` jako úspěšný**. Bez snímku, bez inspektora, bez fyzického
důkazu. Přitom:

- každý jednotlivý krok se posuzuje fúzí dvou nezávislých kanálů
  (`fuse_evidence()`), ale tvrzení o **celém cíli** neprochází ničím;
- vydává ho ta vrstva, která odpovídá z promptu, ne z měření, a u které je
  v tomhle projektu zdokumentované, že chybně přečte důkaz doslova napsaný
  ve svém vlastním kontextu;
- halucinované DONE **nafukuje úspěšnost orchestrovaného schématu** přesně v
  tom čísle, které se v diplomce porovnává s baseline.

A přitom vrstva, která na tuhle otázku umí odpovědět, je k dispozici zadarmo:
VLM inspektor je rychlý, volá se po každém kroku a **už dnes tuhle přesnou
otázku zodpovídá** (`GOAL: yes/no`, `parse_goal_flag()`). Jen se ho nikdo
nezeptal v okamžiku, kdy o něčem rozhoduje.

### Co jsem změnil

`Orchestrator._settle_done()` + `_verify_goal()` v `orchestrator.py`, zapojené
na obou místech, kde se DONE dosud rovnalo konci běhu (úvodní plán i re-plán).

Když plánovač řekne DONE, dostane inspektor **snímky, které už jsou po ruce**
(u úvodního plánu tytéž, ze kterých plánoval CEO — dva různé modely, tentýž
obrázek, jedno tvrzení; u re-plánu ty, které právě posuzoval). Žádný nový
snímek se nepořizuje, pokud nějaké existují, takže cena je **jedno volání
rychlého modelu**.

Asymetrie je záměrná v obou směrech:

- **Zastavit smí jen plánovač.** Když inspektor cíl nevidí, ale plánovač na
  DONE po upozornění trvá, běh **skončí jako DONE** a rozpor se jen zapíše.
  Zmatený VLM (špatný úhel, gripper v cestě) nesmí poslat robota manipulovat
  s už hotovou scénou — to by mohlo výsledek reálně rozbít.
- **Pokračovat se smí, jen když se shodnou obě vrstvy:** inspektor cíl nevidí
  **a** plánovač po upozornění DONE odvolá a pojmenuje zbývající kroky.

Inspektorovi je v promptu výslovně řečeno, že „ne" znamená „nepotvrzeno" a je
to bezpečná odpověď. Asymetrie je vědomá: špatné „ano" potichu ukončí
nedokončenou úlohu a zkazí měřené číslo, špatné „ne" stojí jedno volání CEO a
zapíše se.

`success` v záznamu běhu **záměrně neměním**. Nové aditivní pole
`done_checks` v `runs/*.json`: `{replan_index, verdict, inspector_reason,
insisted, plan_after}`, kde `verdict` je `confirmed` / `denied` / `unknown` /
`skipped` / `off`. Viz otevřené otázky — je to metodické rozhodnutí, ne
technické.

Nový přepínač `done_visual_check` (default `true`) v `server.py`,
`web/config.js`, checkbox v `web/index.html`. Nové testy
`tests/test_goal_check.py` (20 případů, bez robota a bez LeRobota) — včetně
prvního pokrytí `parse_goal_flag()`, což je parser, na kterém teď visí
výsledek běhu.

### Co jsem zvažoval a zavrhl

- **Při `denied` přepnout `success` na `False`.** Tím by se hlavní měřená
  veličina diplomky předala do rukou malého VLM: jedna zmatená odpověď by
  shodila prokazatelně povedený běh. Existující kód drží stejnou opatrnost v
  opačném směru (`goal_done` od inspektora se uzná jen tehdy, když i krok
  uspěl — „jedno zmatené GOAL: yes nesmí samo ukončit běh"). Zůstávám u
  zápisu do dat; přepnutí je jednořádková změna, ale je to **tvoje**
  metodické rozhodnutí, ne moje.
- **Ověřovat stejně i `["ABORT"]`.** Není symetrické: „cíl je splněný" je
  otázka, na kterou se dá z fotky odpovědět, „žádná posloupnost dovedností
  odsud nevede k cíli" nikoli. ABORT navíc končí jako neúspěch, tedy v
  konzervativním směru — nenafukuje výsledek.
- **Pořizovat kvůli kontrole nový snímek vždy.** Snímky po ruce jsou čerstvé
  (u re-plánu z právě proběhlé verifikace) a použít u úvodního plánu **tentýž
  obrázek**, ze kterého plánoval CEO, je metodicky lepší: rozpor pak měří
  neshodu modelů, ne rozdíl mezi dvěma fotkami.
- **Startovat kvůli kontrole daemona, když neběží** (úvodní DONE při vypnuté
  `planner_vision`). Znamenalo by to sáhnout na robota a kamery tam, kde se
  dnes nesáhne. Bez snímků se DONE bere jako dnes a zapíše se `unknown`.

### Otevřené otázky

- **Jak často je DONE halucinace?** To je teď měřitelné (`done_checks`) a je
  to samo o sobě výsledek do diplomky: kolik „úspěchů" orchestrace stojí na
  neověřeném tvrzení plánovače.
- Kolikrát plánovač po upozornění DONE **odvolá**, a dokončí pak úloha
  doopravdy? Jestli skoro vždy odvolá a pak stejně selže, je kontrola jen
  drahá.
- Má se `success` u `denied` + `insisted` počítat jinak? Viz výše — návrh:
  vyhodnotit obě varianty nad týmiž daty, když už jsou obě v záznamu.

### Co potřebuje ověření na reálném hardwaru (uživatel)

1. **Jestli inspektor neříká „ne" příliš často.** To je hlavní riziko: každé
   „ne" stojí jedno volání CEO navíc a v případě, kdy plánovač couvne, běh
   pokračuje tam, kde dřív skončil. Pozná se to v `done_checks` —
   `verdict: "denied"` u běhů, které ve skutečnosti hotové byly. Kdyby se to
   dělo, nejjednodušší ústupek je `done_visual_check: false`.
2. **Že se ti při `denied` + odvolaném DONE robot nechová divoce** — je to
   jediný případ, kdy tahle změna mění, co robot fyzicky dělá.
3. **Že nové pole `done_checks` neshodí tvoje analytické skripty** (je
   aditivní, ale ověř).
4. Nejlevnější sanity check: pusť běh na scéně, kde je cíl **evidentně už
   splněný**, a druhý na scéně, kde evidentně není, a jen se podívej, co
   `done_checks` říká. Na to nepotřebuješ ani dokončený trénink.

---

## 2026-09-07 — Kontrola plánu proti čidlu zátěže (grounded plan check)

### Co jsem zkoumal

Přečetl jsem `orchestrator.py` (celý řídicí cyklus, `fuse_evidence()`,
prompty CEO i inspektora), rozhraní `Daemon` k `inference_daemon.py` a
`tests/test_fusion.py`. Hledal jsem místo, kde se **nevyužívá** to, že
schéma má dvě různě rychlé a různě spolehlivé vrstvy.

Nesymetrie, která mi přišla nejzajímavější: **veškerá kontrola v systému míří
odspodu nahoru směrem k výsledku kroku** (fyzika + inspektor se fúzují do
verdiktu o právě provedeném kroku), ale **plán samotný nekontroluje nikdo**.
Přitom plánovač je:

- jediná vrstva, která odpovídá z promptu, ne z měření,
- ta nejméně spolehlivá (malý lokální model, u kterého je zdokumentované, že
  si odporuje s důkazem doslova napsaným v jeho vlastním kontextu),
- a zároveň ta nejdražší na jedno volání.

Nekonzistentní plán se dnes odhalí až tím, že se **celý krok skutečně
vykoná**, zavolá se inspektor a spustí se re-plán. To je nejdražší možná
cesta k informaci, kterou orchestrátor už měl k dispozici dřív, než cokoli
spustil.

### Co jsem změnil

Nová čistá funkce `plan_state_conflict(plan, catalog, holding)` v
`orchestrator.py` + její zapojení přes `Orchestrator._plan_grounded()`, které
nahradilo obě dosavadní dvojice `_create_plan()` + `_resolve_plan()` v
`run()` (úvodní plán i re-plán).

Princip: než se načte jakákoli policy, porovná se **první krok plánu** s
jediným bitem fyzického stavu — drží gripper něco, nebo ne — a to jen proti
metadatům, která katalog kroků už má (`grasp`, `reset`, pořadí):

1. robot něco drží, ale plán začíná úchopovým krokem → chystá se uchopit to,
   co už drží;
2. robot nedrží nic, ale plán začíná krokem zařazeným **za** posledním
   úchopovým krokem (přenos/položení) → plán předpokládá předmět v čelistech,
   který tam není.

Při rozporu se plánovač **jednou** vyzve cílenou opravou (`PLAN_STATE_CORRECTION`)
a odpoví znovu. Pokud na svém plánu trvá, **plán se stejně spustí** — kontrola
nikdy plán nepřepisuje. Důvody:

- deterministické pravidlo, které plán tiše přepíše, umí při špatně
  nastaveném prahu zablokovat celý běh;
- a hlavně: přepsání by zničilo právě to měření, kvůli kterému je tohle
  zajímavé — *jak často malý plánovač odporuje přímému měření a stačí na to
  jedna věta*.

Každý plán (i bezrozporový) se zaznamená do `runs/<id>.json` jako nová
top-level položka `plan_checks`: `{plan, holding, conflict, corrected,
plan_after, conflict_after}`. Přidání je aditivní — existující analýzy nad
`steps` / `success` / `duration_s` se nemění.

Podpůrné: `Orchestrator._holding_state()` je záměrně **přísnější** než text,
který jde do promptu (`_gripper_note()`). Vrací `None` (= netvrdím nic, žádná
kontrola), když čidlo za celý běh nevrátilo nenulovou hodnotu, když nárůst
padne do pásma nejistoty kolem prahu, nebo když je vypnuté
`gripper_state_in_context` (jinak by se plánovač káral za důkaz, který vůbec
nedostal — to by potichu rozbilo tuhle ablaci).

Nový přepínač `plan_state_check` (default `true`) v `server.py`,
`web/config.js` a jako checkbox v `web/index.html`. Nové testy:
`tests/test_plan_check.py` (15 případů, bez robota i bez LeRobota).

**Žádná nová vymyšlená konstanta.** Pásmo nejistoty kolem prahu „něco drží"
používá existující `protocol_b_deadband_frac` — obojí je relativní pásmo
nejistoty nad týmž čidlem zátěže. V kódu je u toho TODO: pokud se z reálných
dat ukáže, že obě hranice potřebují jiné pásmo, má to uživatel rozdělit na
vlastní klíč.

### Co jsem zvažoval a zavrhl

- **Třetí pravidlo: „robot něco drží, a plán začíná krokem PŘED úchopem"**
  (typicky „začni znovu od nájezdu, i když kostku držím"). Vypadá lákavě,
  protože přesně tohle bod 3 systémového promptu zakazuje a model to
  ignoruje. Zavrhl jsem ho: opírá se mnohem víc o to, že pořadí kroků v
  katalogu je sémanticky přesné, a hlavně — při špatně nastaveném
  `holding_limit_ma` (klid se čte jako „drží") by shazoval **každý úvodní
  plán**. Kontrola, která křičí planý poplach, je horší než žádná.
- **Tvrdé přepsání plánu orchestrátorem místo výzvy k opravě.** Viz výše:
  riziko deadlocku při špatném prahu a ztráta měřené veličiny.
- **Kontrola celé sekvence, ne jen prvního kroku.** Vyžadovala by model
  předpokladů/efektů každé dovednosti, který katalog nemá, a musel bych ho
  buď vymyslet, nebo si vynutit novou anotaci v `projects/*.json` (rozbití
  zpětné kompatibility). První krok je jediný, u kterého je fyzický stav
  **skutečně naměřený teď** — u dalších je to už jen predikce.
- **Levný „reflexní" re-try kroku bez volání CEO** (retry / vložení RESETu
  jako reakce na první selhání, bez drahého plánovače). Architektonicky do
  dvourychlostního schématu sedí a je to kandidát na některou příští noc, ale
  je to zásah přímo do měřené smyčky (mění se počet volání CEO na běh, tedy i
  srovnání s baseline). Na jednu noc moc velké sousto a chce to rozmyslet
  metodicky, ne jen implementačně.

### Otevřené otázky

- Přispívá oprava plánu k úspěšnosti, nebo jen přidá jedno volání CEO navíc?
  Data na to teď existují (`plan_checks`), odpověď ne.
- Jak často plánovač po upozornění **trvá na svém**? Jestli skoro vždy, je
  cílená textová oprava u tak malého modelu slepá ulička a příště bude
  zajímavější zkusit kontrolu použít jako filtr při výběru z několika
  navržených plánů (samplovat 2-3 plány, vzít první konzistentní).
- Nemá se stejný „stavový" bit dostat i do promptu inspektora jako tvrdší
  vodítko? Dnes ho dostává jako větu, ne jako závaznou informaci.

### Co potřebuje ověření na reálném hardwaru (uživatel)

1. **Že `holding_limit_ma` je nastavený tak, aby `_holding_state()` nevracelo
   nesmysly.** Celá kontrola stojí a padá s tímhle prahem; pomůže
   `measure_gripper_current.py`. Pokud je práh mimo, projeví se to jako
   opakované hlášky „Plán CEO odporuje čidlu zátěže" hned u úvodních plánů.
2. **Zda pásmo nejistoty odvozené z `protocol_b_deadband_frac` sedí i pro
   práh „něco drží".** Sdílené je z principu úspornosti, ne z měření.
3. **Jestli se `plan_checks` v `runs/*.json` plní tak, jak čekáš**, a jestli
   ti tvoje analytické skripty přes nové pole nespadnou (je aditivní, ale
   ověř).
4. Doporučuju první běh s `plan_state_check: true` pustit na úloze, kde
   *víš*, že robot na začátku nic nedrží — je to nejlevnější sanity check
   celého mechanismu.
