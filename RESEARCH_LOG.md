# Deník výzkumné rutiny (větev `orchestration-research`)

Noční rutina, která na téhle větvi dělá **jedno soustředěné vylepšení
orchestračního schématu za noc**. Běží v cloudu **bez GPU, robota, LM Studia
a bez přístupu k `runs/`, `telemetry/`, `images/`, `config.json` a
`projects/`** (jsou v `.gitignore`). Nic z toho, co je tu napsané o chování na
skutečném hardwaru, proto **není naměřený fakt, ale hypotéza k ověření** —
každý záznam takové body vypisuje zvlášť.

Větev se nikdy nemerguje sama; revizi a merge do `main` dělá uživatel ručně.

---

## 2026-09-08 — Kontrola opakovaného plánu (anti-loop audit re-plánu)

### Co jsem zkoumal

Navázal jsem na včerejší kontrolu plánu a prošel řídicí smyčku `run()` z
pohledu **re-plánování**: co se stane, když krok selže a plánovač dostane
slovo podruhé, potřetí, počtvrté. Zajímal mě existující „loop-guard“
(`plan == previous_remaining`).

Má dvě slabiny:

1. **Porovnává jen s bezprostředně předchozím pokusem.** Když plánovač
   střídá dva plány (A → B → A → B), *každý* re-plán se od svého předchůdce
   liší, guard nikdy nesepne dvakrát po sobě a běh spotřebuje celý rozpočet
   re-plánů, aniž by se cokoli změnilo. Přitom právě tohle je typické chování
   malého modelu, který si mezi voláními nic nepamatuje a odpovídá z
   podobného kontextu podobně.
2. **Když sepne, jediná reakce je ukončit běh.** Plánovač se nikdy nedozví,
   že se opakuje — informaci, kterou orchestrátor má a on ne (jeho kontext
   obsahuje výsledky *kroků*, ne seznam už vyzkoušených *plánů*).

To je přesně asymetrie, kterou má orchestrace využívat: levná deterministická
vrstva ví něco, co drahá pomalá vrstva neví, a může jí to říct dřív, než se
znovu spustí celý krok.

### Co jsem změnil

Nová čistá funkce `plan_repeat_conflict(plan, failed_plans, progress)` v
`orchestrator.py`, zapojená stejným způsobem jako včerejší státní kontrola.

`failed_plans` je historie běhu: jeden záznam `(progress, kroky)` za každý
plán, který se skutečně vykonal a selhal. `progress` = kolik kroků se v běhu
do té chvíle povedlo. **Shoda se hlásí jen při stejném `progress`** — to je
klíč stavu. Pokud se mezitím cokoli povedlo (i RESET), robot prokazatelně
není ve stejné situaci a zopakovat dřívější plán je legitimní zotavení, ne
smyčka. Žádná nová konstanta: `progress` je odvozený z `self.results`.

Zapojení:

- `_plan_grounded()` teď spouští **obě** kontroly (`_audit_plan()`) a při
  jakémkoli nálezu pošle plánovači **jednu** společnou opravnou výzvu
  (`PLAN_CORRECTION` nahradila `PLAN_STATE_CORRECTION`). Ať sepne jedna
  kontrola nebo obě, volání CEO je nejvýš 2 na plán — audit nikdy nenafoukne
  jedno volání na tři.
- Kód-level guard v `run()` porovnává nově přes `plan_repeat_conflict()`
  místo `plan == previous_remaining`. Nová podmínka tu starou **obsahuje**
  (plán identický s tím, co právě selhalo, má stejný `progress`) a navíc
  chytá střídání A/B.
- Guard běží **i při vypnutém** `plan_repeat_check` — přepínač řídí jen tu
  drahou část (výzvu plánovači), pojistka proti donekonečna opakovanému
  plánu je bezpečnostní prvek, ne experimentální podmínka. Zároveň tak
  pokrývá i záložní plán, který sestavuje kód a žádný plánovač ho neviděl.
- `plan_checks` v `runs/<id>.json` má nová aditivní pole `repeat` a
  `repeat_after` vedle stávajících `conflict` / `conflict_after`.

Nový přepínač `plan_repeat_check` (default `true`) v `server.py`,
`web/config.js` a jako checkbox v `web/index.html`. Testy rozšířeny:
`tests/test_plan_check.py` má nově 28 případů (13 nových pro opakování).

**Pozor na jednu změnu v datech:** `_plan_grounded()` už se nevrací brzy,
když je `plan_state_check` vypnutá — záznam do `plan_checks` se proto zapíše
u každého plánu (s prázdnými nálezy), i v ablaci. Je to aditivní a pro
analýzu spíš lepší (je vidět i `holding` v ablaci), ale je to rozdíl oproti
včerejšku.

### Co jsem zvažoval a zavrhl

- **Nepočítat úspěšný RESET jako `progress`.** Chytlo by to i „reset → týž
  plán → selhání → reset → …" cyklus, který dnešní kontrola mine (mezi
  opakováními je vždy jeden úspěch). Zavrhl jsem to: RESET je v systémovém
  promptu doporučené zotavení, takže první „resetuj a zkus to znovu" by se
  okamžitě hlásilo jako smyčka. Kontrola, která shazuje právě to chování,
  které sama doporučuje, je horší než slepé místo — a ten cyklus pořád
  omezuje `max_replans`. Zapsáno i v docstringu funkce.
- **Podobnostní míra místo přesné shody** (např. „plán se shoduje z 80 %").
  Vyžadovalo by to vymyšlený práh bez opory v datech, přesně to, co v tomhle
  repu nechceme. Přesná shoda je deterministická a vysvětlitelná v textu
  diplomky jednou větou.
- **Vypsat plánovači do kontextu seznam všech už vyzkoušených plánů** místo
  jedné cílené výzvy až při shodě. Prodlužuje to *každý* re-plánovací prompt
  (nejdražší volání ve schématu) kvůli případu, který nastane jen někdy —
  a u malého modelu delší kontext spíš škodí. Cílená výzva až při skutečném
  nálezu je levnější a měřitelnější.
- **Tvrdě přepsat opakovaný plán** (např. vynutit RESET). Stejné důvody jako
  včera: riziko zablokování běhu a ztráta měřené veličiny.

### Otevřené otázky

- Rozliší se v datech dvě různé příčiny opakování — „plánovač nemá jinou
  strategii" vs. „plánovač si nepamatuje, co už zkusil"? Pokud po výzvě
  skoro vždy navrhne něco jiného, jde o druhý případ a stálo by za to dát mu
  historii plánů do kontextu natrvalo (viz zavržený bod výše, byla by to pak
  informovaná změna, ne dohad).
- Je `progress` (počet úspěšných kroků) dost dobrý klíč stavu? Alternativa
  by byla dvojice (počet úspěchů, poslední úspěšný krok) — jemnější, ale
  chytřejší jen tehdy, když se katalog vrací k témuž kroku vícekrát.
- Pořád platí včerejší otázka: nemá stavový bit „drží/nedrží" jít i do
  promptu inspektora jako tvrdší vodítko?

### Co potřebuje ověření na reálném hardwaru (uživatel)

1. **Jestli guard nezačne ukončovat běhy, které dřív doběhly.** Nová
   podmínka je striktně širší než stará, takže teoreticky může běh skončit
   dřív než dosud (dvě opakování „ze stejného stavu" místo dvou identických
   po sobě). Projeví se hláškou „…podruhé za sebou navrhl plán, který v tomto
   běhu ze stejného stavu už selhal". Pokud by to přišlo příliš brzy,
   nejjednodušší ústupek je tolerovat dvě opakování místo jednoho — je to
   jedna podmínka v `run()`.
2. **Jak často výzva k jiné strategii zabere** (`plan_checks[*].repeat` vs.
   `repeat_after` v `runs/*.json`). To je vlastní měřená veličina téhle noci
   a nemám k ní žádná data.
3. **Že nové pole `repeat` v `plan_checks` neshodí tvoje analytické
   skripty** — je aditivní, ale ověř.
4. Zvaž jeden běh s `plan_repeat_check: false` jako ablaci (výzva vypnutá,
   pojistka pořád funguje) proti běhu s `true`, na stejné úloze.

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
