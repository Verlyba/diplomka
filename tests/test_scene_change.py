"""Diferencialni vizualni kanal: parser a formatovani (orchestrator).

Meri se jedina vec: zmenilo se ve scene neco, zatimco bezel posledni krok?
Odpoved dava inspektor ze dvou snimku tehoz pohledu, planovac ji sam ziskat
neumi (je bezstavovy a dostava vzdy jen jeden aktualni snimek).

Obe funkce jsou ciste, takze se daji overit bez robota, LeRobota i LM Studia.

    python tests/test_scene_change.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import (SCENE_CHANGED, SCENE_UNCHANGED, format_scene_change,
                          parse_scene_change)

failures: list[tuple] = []


def check(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        failures.append((label, got, want))
    print(f"{'ok  ' if ok else 'CHYBA'} {label}")


# ── parser ──────────────────────────────────────────────────────────────────
# Zadana podoba odpovedi.
PARSE_CASES = [
    ("REASONING: Kostka lezi porad stejne.\nSCENE: unchanged", SCENE_UNCHANGED),
    ("REASONING: Kostka je jinde.\nSCENE: changed", SCENE_CHANGED),
    # 'unsure' NENI mereni — nesmi se prelozit na ani jednu ze stran.
    ("REASONING: Rameno cloni.\nSCENE: unsure", None),

    # Male modely format nedodrzi presne; tohle je to, co se da precist bez
    # domysleni. Interpunkce, markdown, velikost pismen.
    ("SCENE: UNCHANGED.", SCENE_UNCHANGED),
    ("**SCENE: changed**", SCENE_CHANGED),
    ("  scene:   unchanged  ", SCENE_UNCHANGED),
    ("SCENE: no change", SCENE_UNCHANGED),
    ("SCENE: not changed", SCENE_UNCHANGED),
    ("SCENE: same as before", SCENE_UNCHANGED),
    ("SCENE: changed slightly", SCENE_CHANGED),

    # Za hranici ctenosti — radsi zadne tvrzeni nez uhodnute.
    ("SCENE: maybe", None),
    ("SCENE:", None),
    ("SCENE: the cube", None),
    ("Rekl bych, ze se nic nezmenilo.", None),
    ("", None),
    # Radek o cili nesmi tenhle parser splest a naopak.
    ("GOAL: yes\nSCENE: unchanged", SCENE_UNCHANGED),
    ("GOAL: no", None),
]
for text, want in PARSE_CASES:
    check(f"parse {text.splitlines()[-1] if text else '(prazdne)':<28} -> {want}",
          parse_scene_change(text), want)

# Slovo 'unchanged' zacina na 'un', ne na 'chang' — poradi kontrol v parseru
# je proto rozhodne a tenhle pripad ho hlida.
check("'unchanged' se necte jako 'changed'",
      parse_scene_change("SCENE: unchanged"), SCENE_UNCHANGED)

print()

# ── formatovani bloku do kontextu re-planu ──────────────────────────────────
STEP = "grab"
REASON = "kostka lezi na stejnem miste"

# Bez mereni se do kontextu neprida nic. Tim padem se planovaci pri 'unsure'
# ani pri selhani volani nic nemeni oproti chovani pred touhle zmenou.
for verdict in (None, "unsure", "unknown", "off", "skipped", "no_frames", ""):
    check(f"format({verdict!r}) je prazdne", format_scene_change(verdict, REASON, STEP), "")

unchanged = format_scene_change(SCENE_UNCHANGED, REASON, STEP)
changed = format_scene_change(SCENE_CHANGED, REASON, STEP)

check("unchanged neco vraci", bool(unchanged), True)
check("changed neco vraci", bool(changed), True)
check("oba bloky se lisi", unchanged != changed, True)

for label, block in (("unchanged", unchanged), ("changed", changed)):
    check(f"{label}: jmenuje krok", STEP in block, True)
    check(f"{label}: nese oduvodneni inspektora", REASON in block, True)
    check(f"{label}: je oznaceny jako mereni", "MEASURED" in block, True)
    # Blok jde do promptu planovace, ktery je cely anglicky.
    check(f"{label}: je na jeden radek", "\n" not in block, True)

# Klicova vlastnost: nic neprikazuje. Merena veta smi planovac informovat, ale
# nesmi za nej rozhodnout — to je presne chyba, kvuli ktere byl 2026-09-08
# zavrzen plan_repeat_conflict().
for label, block in (("unchanged", unchanged), ("changed", changed)):
    low = block.lower()
    for forbidden in ("do not repeat", "must ", "you have to", "never repeat"):
        check(f"{label}: neobsahuje prikaz {forbidden!r}", forbidden in low, False)

# 'unchanged' nesmi tvrdit, ze je stejny i stav robota — rameno je po kroku
# jinde a pro ACT policy je to jina pocatecni podminka.
check("unchanged: upozornuje, ze poloha ramene se mohla zmenit",
      "arm" in unchanged.lower(), True)
# 'changed' musi obhajit legitimni zopakovani kroku ve zmenene scene.
check("changed: rika, ze dalsi pokus zacina z jine sceny",
      "not the scene" in changed.lower(), True)

# Oduvodneni inspektora je nepovinne — chybejici veta nesmi rozbit blok.
no_reason = format_scene_change(SCENE_UNCHANGED, "", STEP)
check("bez oduvodneni blok porad vznikne", bool(no_reason), True)
check("bez oduvodneni nezustanou prazdne zavorky", "()" in no_reason, False)

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print(f"OK — vsech {len(PARSE_CASES)} pripadu parseru a formatovani bloku sedi.")
