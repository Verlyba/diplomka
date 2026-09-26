# Poznámky k textové části práce + návrh metodiky (dvě kostky, cloudové modely)

*Pracovní poznámky, ne hotový text. Cíl: dát dohromady, co se za tu dobu vývoje
a ladění zjistilo, a navrhnout měřitelnou, obhajitelnou metodiku pro dvě další
rozšíření experimentu. Všechno se dá citovat/parafrázovat přímo do kapitol —
u každého bodu je poznámka, do které části práce (Metodika / Výsledky /
Diskuze / Limitace) by patřil.*

---

## 1. Poznámky k textové části práce

### 1.1 Iterativní, na datech založený vývoj ukončovacích protokolů — patří do Metodiky

Protokol A (klouby přestaly hýbat) a Protokol B (zátěž gripperu) prošly
několika kola přeladění, a to je samo o sobě metodologicky zajímavý příběh,
ne jen "bug fixing":

- Protokol A byl původně založený na chybě sledování cíle (target-tracking
  error), což u kroku typu *reset* nikdy nefirovalo (šum predikce policy byl
  systematicky větší než práh). Přechod na měření skutečné rychlosti
  (tick-to-tick) fixnul reset, ale zavedl nový problém: u úchopového kroku
  se skutečná rychlost brzy po startu čte jako "stojí", i když robot teprve
  začíná najíždět — krok se ukončil po **0,5–0,9 s**, dřív než se čelisti
  vůbec stihly zavřít.
- Řešení nebylo "posunout práh o kousek" — bylo architektonické: `grasp`
  krok už dnes **nesmí být ukončen Protokolem A vůbec**, protože "klouby se
  přestaly hýbat" u úchopu neznamená totéž co u homingu (u homingu je stání
  = cíl; u úchopu je to jen "policy se zastavila", což nerozliší dokončené
  sevření od zastavení s prázdným gripperem).
- Protokol B prošel obdobnou proměnou: z prahové hodnoty na **plató, ne
  špičku** (`PROTOCOL_B_STABILITY_SLOPE`) — reálná telemetrie ukázala, že
  falešné spuštění (72, 139) a potvrzený úchop (108) se v hodnotě nárůstu
  proudu **překrývají**; odlišuje je až to, jestli nárůst je stabilní přes
  víc snímků, nebo je to přechodová špička pohybu.

**Poznámka pro Metodiku:** tohle je dobrý příklad na formulaci typu "ladění
prahů nebylo hádání jednotlivých čísel, ale opakované vyvrácení hypotézy o
tom, jaký signál se vůbec dá použít" — a dá se to doložit konkrétními čísly
z `telemetry/*.jsonl`, ne jen tvrzením.

### 1.2 Fúze fyzického a vizuálního důkazu — patří do Metodiky (architektura)

`fuse_evidence()` kombinuje dva nezávislé kanály (senzor zátěže gripperu +
VLM inspektor) tak, že žádný z nich nesmí druhý přebít tiše:

- VLM smí přebít fyzický `DENY` jen **jistým** tagem (`SUCCESS`/`FAIL`),
  nikdy `[unclear]`.
- Fyzika s nejednoznačným čtením (v pásmu nejistoty kolem prahu) se
  označuje `UNCLEAR`, ne `DENY` — rozhoduje pak inspektor, ale rozpor mezi
  kanály se vždy zaznamená (`conflict` pole v `runs/*.json`), i když se
  jeden kanál nakonec prosadí.

**Poznámka pro Výsledky:** kolikrát se kanály neshodly a kdo měl nakonec
pravdu (podle skutečného výsledku pokusu) je samo o sobě měřitelná
veličina — kolik z těch rozporů skončilo úspěchem díky tomu, že se
nepoužil jen jeden kanál. Data na to jsou v `runs/*.json` (`conflict` pole
u každého kroku).

### 1.3 Zdokumentovaný příklad selhání malého lokálního LLM jako plánovače — patří do Diskuze/Limitací

Konkrétní, doložitelný incident: CEO (`gemma-4-e4b`) v re-plánu napsal
*"the cube was dropped outside the bowl"*, přestože inspektor o krok dřív
doslova napsal *"The green cube appears to be **inside** the red
bowl..."* — CEO dostal fotku i přesnou citaci inspektora a přesto si
vymyslel opak, i když prompt už tehdy explicitně říkal "FIRST: check if
the goal is ALREADY satisfied in the photo". Tohle není chyba v zapojení
(prompt byl správně), je to limit schopností 4B modelu na tenhle typ
grounded reasoningu.

**Poznámka pro Diskuzi:** tohle je přesně ten typ dokladu, který stojí za
kvantifikaci — kolikrát v celé sadě běhů CEO navrhl re-plán, který
prokazatelně odporoval textu, jenž měl v kontextu (dá se to zpětně spočítat
z `runs/*.json`, pole `ceo_reasoning` vs. `insp_reason` předchozího kroku,
pokud logování běželo). Přímo to motivuje sekci 3 (cloudové modely) níž.

### 1.4 Metodická poznámka k retry/re-plánům — patří do Metodiky (srovnání)

Orchestrace smí re-plánovat (až `max_replans`), baseline běží jako jeden
souvislý pokus. To **není** nefér výhoda k zamlčení — je to vlastnost
architektury, kterou práce testuje. Ale aby srovnání neslilo "orchestrace
je lepší" s "orchestrace dostala víc pokusů", je třeba reportovat:

- hlavní srovnání: orchestrace (s re-plány) vs. baseline (jeden pokus),
- doplňkově: **ablace `max_replans=0`** — orchestrace bez re-plánů, čistě
  napoprvé, pro přímé srovnání jednoho pokusu proti jednomu,
- **počet použitých re-plánů/čas do úspěchu** jako vlastní metrika vedle
  úspěšnosti — systém, co uspěje na 90 % ale v průměru se 3 re-plány, není
  totéž jako 90 % napoprvé.

### 1.5 Velikost vzorku — patří do Metodiky

Doporučené minimum **15–20 běhů na testovanou podmínku** (baseline,
orchestrace, případně ablace bez re-plánů) — při menším N je interval
spolehlivosti na úspěšnost tak široký (klidně ±25–30 % při n≈8–10), že se
rozdíl nedá obhájit jako signifikantní. Počítat **Wilsonův interval
spolehlivosti**, ne normální aproximaci — je přesnější právě pro malá n a
krajní podíly (blízko 0 % nebo 100 %), což je u úspěšnosti pick-and-place
běžné.

---

## 2. Návrh metodiky: dvě barevné kostky

### 2.1 Proč to není triviální rozšíření — patří do Metodiky (zdůvodnění designu)

ACT (na rozdíl od SmolVLA a dalších VLA) **nemá žádnou jazykovou/textovou
kondicionaci** — vstup je jen aktuální snímek + stav kloubů, výstup je
akční sekvence. To znamená, že **nejde** natrénovat jeden monolitický
model a za běhu mu říct "tentokrát vezmi zelenou, příště černou" — model
nemá žádný vstupní kanál, kam by se taková instrukce dala vložit.

Důsledek pro design experimentu: úloha musí být formulovaná tak, aby šla
splnit **bez** potřeby za běhu vybírat cíl slovně. Dvě smysluplné varianty:

**Varianta A — "ukliď obě" (clear-both):** cílem je dostat OBĚ kostky do
misky, na pořadí nezáleží. Nevyžaduje žádnou volbu "kterou teď" předem —
řeší se to průběžně podle toho, co je vidět na stole.

**Varianta B — "ukliď jen zelenou" (selective, s rušivým objektem):**
cílem je odstranit jen kostku jedné barvy, druhá (rušivá) musí zůstat na
stole. Model se musí naučit **rozlišovat cíl od rušivého objektu** čistě
vizuálně, v rámci JEDNOHO natrénovaného chování (žádná volba za běhu —
"vždy zelená, černá je translator" je fixní úkol, na který se trénuje).

Tyhle dvě varianty testují different things a obě stojí za měření —
varianta A je tam, kde by orchestrace měla mít reálnou výhodu (viz 2.3),
varianta B je tvrdší test čisté vizuální diskriminace.

### 2.2 Proč právě varianta A ukazuje výhodu orchestrace — patří do Diskuze

U varianty "ukliď obě" má orchestrace mechanismus, který baseline **nemá
čím nahradit**: VLM inspektor po každém kroku vidí, která kostka na stole
ještě zbývá, a CEO podle toho **adaptivně přeplánuje**, kterou z nich zkusit
příště. Tohle je přesně ta jazyková/rozhodovací vrstva, kterou ACT
architektura sama o sobě nemá — orchestrace ji dodává zvenčí, přes dvojici
pomalý-plánovač + rychlý-inspektor, aniž by musela měnit samotnou policy
architekturu.

Baseline monolitický model musí tohle "co ještě zbývá" řešit čistě uvnitř
své vlastní, netransparentní politiky — bez jakékoli externí korekční
smyčky. To je přímo měřitelný rozdíl v architektuře, ne jen v datech.

### 2.3 Fér rozpočet dat — patří do Metodiky

Návrh z diskuze v rámci vývoje (přímo doložitelný, ne dodatečně
vymyšlený): **120 epizod celkem pro obě větve**, jen jinak rozdělených.

**Baseline (jeden monolitický model, varianta "ukliď obě"):**
- 50 epizod: na stole jen zelená kostka, cíl zelená → miska
- 50 epizod: na stole jen černá kostka, cíl černá → miska
- 10 epizod: obě kostky na stole, cíl uklidit zelenou (černá zůstává)
- 10 epizod: obě kostky na stole, cíl uklidit černou (zelená zůstává)
- **Trénink na sjednocení všech 120 epizod najednou** (joint training, ne
  sekvenční dotrénování — u sekvenčního hrozí katastrofické zapomínání
  prvního objektu při učení druhého; joint trénink na to není náchylný).

**Orchestrace (dva specialisté, po jednom na barvu):**
- Každý specialista (zelená-specialista, černá-specialista) dostane
  **60 epizod** svého vlastního catch_cube/carry_cube — stejný celkový
  rozpočet dat (120) jako baseline, jen rozdělený mezi dva modely místo
  jednoho.
- CEO mezi nimi přepíná podle toho, co vidí na fotce (přes existující
  mechanismus hot-swapu policy) — orchestrace dostává ROVNOU informaci "co
  na stole zbývá" z VLM, baseline si ji musí odvodit sama, implicitně.

**Otázka k rozhodnutí (na tebe): zahrnout do baseline i disruptor epizody
(10+10 výše), nebo trénovat baseline jen na variantě "ukliď obě, kostky
nejsou nikdy dvě najednou v tréninku"?** Bez disruptor epizod je varianta A
snazší pro baseline (nikdy neviděl dvě kostky najednou v datech, i když je
v testu uvidí) — s nimi je to fér srovnání s tím, co orchestrace taky vidí
(CEO/VLM vidí obě kostky vždy, přes fotku). Doporučuju disruptor epizody
zahrnout, jinak baseline dostává systematicky slabší trénovací podmínky.

### 2.4 Protokol měření — patří do Metodiky

Pro **každou** ze dvou variant (A: ukliď obě, B: selektivní) a **každou**
ze dvou větví (baseline, orchestrace):

1. **N = 15–20 běhů**, kostky umístěné na náhodné, ale zaznamenané
   (fotograficky) pozice — stejná sada startovních pozic pro obě větve v
   dané variantě, aby rozdíl nebyl jen "orchestrace měla lehčí startovní
   pozice".
2. Metriky k zaznamenání (dá se přímo číst z `runs/*.json` po zapojení
   image loggingu, který teď existuje):
   - úspěšnost (obě kostky v misce / jen správná kostka v misce a druhá
     nedotčená),
   - **chyba diskriminace** u varianty B: kolikrát model sáhl po ŠPATNÉ
     (rušivé) kostce — to je jiná chyba než "minul obě", stojí za
     samostatné počítání,
   - u orchestrace: počet re-plánů, celkový čas, kolikrát VLM správně/
     špatně určil "která kostka zbývá",
   - u baseline: celkový čas, počet kroků do dokončení (pokud je
     pozorovatelný z policy chování, např. přes délku běhu).
3. **Ablace k úvaze**: baseline dotrénovaný sekvenčně (nejdřív zelená,
   pak přidat černou) vs. joint — přímo testuje otázku katastrofického
   zapomínání, kterou jsi nadhodil v diskuzi dřív. Není nutná pro hlavní
   srovnání, ale je to laciné (žádná nová data, jen jiný trénovací běh) a
   dobře publikovatelné zjištění buď směr.

---

## 3. Návrh metodiky: připojení ke cloudovým modelům

### 3.1 Motivace — patří do Úvodu/Diskuze

Přímo navazuje na 1.3: pokud je zdokumentováno, že malý lokální model
(4B/7B) dělá reasoning chyby, které se dají přičíst kapacitě modelu, ne
zapojení, je přirozeným dalším krokem změřit, **kolik z pozorovaných
selhání orchestrace zmizí s výkonnějším plánovačem/inspektorem** — a
kolik zbyde jako architektonický limit, ne limit modelu.

### 3.2 Co přesně měnit, a co ne — patří do Metodiky

`orchestrator.py`'s `LMStudio` třída mluví na OpenAI-kompatibilní HTTP
endpoint (`chat`, `chat_with_images`). Většina cloudových poskytovatelů
(OpenAI přímo, Google Gemini má OpenAI-kompatibilní vrstvu, Anthropic má
vlastní formát ale podobnou strukturu) jde připojit buď přímo (stačí
změnit `lm_url` + `llm_model`/`vlm_model` v configu), nebo přes tenký
adaptér, pokud formát odpovědi/požadavku není 1:1 kompatibilní. **ACT
policy, protokoly A/B, robot samotný se nemění vůbec** — tohle je čistě
výměna CEO/VLM vrstvy, fyzické chování robota zůstává úplně stejné jako u
lokální varianty.

Doporučený postup zapojení (bezpečný, revertovatelný):
1. Nová konfigurační sada (`lm_url`, `llm_model`, `vlm_model` ukazující na
   cloud endpoint) — přepínatelná v Nastavení, ne natvrdo v kódu, přesně
   jako dnešní lokální nastavení.
2. Ověřit, že cílový cloud model **přijímá obrázky** (multimodální vstup)
   — pro CEO (potřebuje vidět scénu při plánování) i pro VLM inspektor
   (potřebuje vidět scénu po kroku). Většina současných velkých modelů
   (GPT-4o třída, Gemini, Claude) multimodální vstup má, ale ověřit u
   konkrétního vybraného modelu/tarify přímo, ne předpokládat.
3. **Nezasahovat do zbytku pipeline** — stejné prompty
   (`PLANNER_SYSTEM_PROMPT`, `VERIFY_PROMPT_RULES`, `GOAL_CHECK_RULES`),
   stejná fúze důkazů, stejné ukončovací protokoly. Mění se jen to, KDO
   odpovídá na dotaz, ne CO se ptá.

### 3.3 Návrh srovnávacích podmínek (izolace, co konkrétně zlepšilo výsledek) — patří do Metodiky

Ne jen "lokální vs. cloud", ale rozbité na dvě otázky zvlášť (protože jsou
to nezávisle vyměnitelné vrstvy):

| podmínka | CEO (plánovač) | VLM (inspektor) | co izoluje |
|---|---|---|---|
| C0 (dnešní baseline) | lokální (gemma-4-e4b) | lokální (qwen2.5-vl-7b) | referenční |
| C1 | **cloud** | lokální | čistě schopnost plánování/reasoningu |
| C2 | lokální | **cloud** | čistě kvalita vizuálního čtení scény |
| C3 | **cloud** | **cloud** | horní hranice schopností "pomalé vrstvy" |

C1 přímo testuje, jestli zdokumentovaná halucinace CEO (1.3) zmizí se
silnějším modelem. C2 testuje, jestli je problém spíš v kvalitě
vizuálního odečtu (VLM) než v plánování. C3 dává horní odhad, kolik z
dnešní mezery mezi orchestrací a jejím teoretickým stropem je způsobené
modelem, ne architekturou.

### 3.4 Co je nutné zaznamenat navíc a proč — patří do Metodiky

- **Síťová latence CEO/VLM volání zvlášť od doby běhu robota** — cloud
  volání přidává reálný, proměnlivý čas (řádově sekundy), který se nesmí
  míchat s dobou fyzického pohybu při reportování "jak dlouho běh trval".
  `runs/*.json` už má `duration_s` na úrovni běhu; bylo by třeba přidat
  časování jednotlivých LLM/VLM volání zvlášť (dá se odvodit z
  `t_start`/`t_end` polí, která teď existují na úrovni kroku, jen pro to
  přidat totéž na úrovni volání plánovače/inspektora).
- **Náklady** — cloud API volání stojí peníze; i orientační počet
  volání × cena za běh je hodnota, kterou práce může (a asi má) reportovat
  jako praktickou nevýhodu cloud varianty, i kdyby přesnost byla lepší.
- **Soukromí/etika** — fotky pracovního prostoru (stůl, kostka, miska) se
  posílají třetí straně. U kontrolovaného laboratorního snímku bez
  citlivého obsahu to typicky není problém, ale patří to do metodické
  poznámky/etické sekce práce jako vědomé rozhodnutí, ne opomenutí.
- **Reprodukovatelnost** — cloud modely se v čase mění (verze API se
  aktualizují bez oznámení). Zaznamenat přesnou verzi/model ID použitou
  v době měření (`llm_model` pole se do `runs/*.json` už ukládá) a
  v textu práce explicitně napsat, že jde o snímek v čase, ne o
  reprodukovatelný benchmark v přísném slova smyslu.

### 3.5 Očekávaný přínos pro práci — patří do Diskuze/Závěru

Tahle sada experimentů dovoluje v práci tvrdit přesněji, **co orchestrace
skutečně přináší** — pokud C1–C3 ukážou výrazné zlepšení oproti C0, jde o
silný argument, že orchestrační schéma je **škálovatelné s budoucím
zlepšováním jazykových/vizuálních modelů** nezávisle na tom, jak se zlepší
samotná manipulace (ACT policy) — architektonická výhoda, která má prostor
růst. Pokud C1–C3 zlepšení neukážou, je to taky validní a zajímavý závěr:
problém je v architektuře fúze/promptů, ne v kapacitě modelu, a stojí za
to to v práci takhle explicitně napsat, ne to obcházet.
