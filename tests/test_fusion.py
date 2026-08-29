"""Pravdivostni tabulka fuze dukazu (orchestrator.fuse_evidence).

Na rozdil od ostatnich testu v teto slozce nepotrebuje ani robota, ani
LeRobota — fuze je cista funkce, takze se da overit cela, vcetne kombinaci,
ktere se na skutecnem robotu trefi jen zridka.

    python tests/test_fusion.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import (PHYS_CONFIRM, PHYS_DENY, PHYS_NONE, PHYS_UNCLEAR,
                          fuse_evidence)

NOTE = "protokol B: cclisti registruji sevreni"
FAIL_TAG = "[object_missed]"
FAIL_REASON = "gripper je prazdny"
OK_REASON = "kostka je v celistech"

# (phys, vis, v_tag, v_reason) -> (success, conflict_expected)
CASES = [
    # -- oba kanaly se shodnou -------------------------------------------
    ((PHYS_CONFIRM, "SUCCESS", "SUCCESS", OK_REASON), (True, False)),
    ((PHYS_DENY, "FAIL", FAIL_TAG, FAIL_REASON), (False, False)),

    # -- jeden kanal nese druhy (bez rozporu) ----------------------------
    # Inspektor nerozhodne -> nese fyzika. Tohle je ta oprava, kvuli ktere
    # driv `[unclear]` shazoval jinak v poradku dokonceny reset krok.
    ((PHYS_CONFIRM, "UNCLEAR", "[unclear]", "nevidim za gripper"), (True, False)),
    ((PHYS_DENY, "UNCLEAR", "[unclear]", "nevidim za gripper"), (False, False)),

    # -- skutecne rozpory: oba se musi ohlasit, ne zahodit ---------------
    # Fyzika potvrzuje, ale inspektor vidi konkretni problem (napr. drzi
    # spatny predmet) -> vyhrava inspektor, protoze prave tohle je to, co
    # protokol B principialne nemuze poznat.
    ((PHYS_CONFIRM, "FAIL", FAIL_TAG, FAIL_REASON), (False, True)),
    # Fyzika nepotvrdila, ale inspektor jasne vidi splneny vysledek ->
    # zachrana pozde/plane vyhodnoceneho protokolu.
    ((PHYS_DENY, "SUCCESS", "SUCCESS", OK_REASON), (True, True)),

    # -- bez fyzickeho dukazu rozhoduje inspektor sam --------------------
    ((PHYS_NONE, "SUCCESS", "SUCCESS", OK_REASON), (True, False)),
    ((PHYS_NONE, "FAIL", FAIL_TAG, FAIL_REASON), (False, False)),
    ((PHYS_NONE, "UNCLEAR", "[unclear]", "rozmazane"), (False, False)),

    # -- fyzika zmerena, ale prilis blizko prahu (pasmo nejistoty) -------
    # Musi se chovat jako "zadny fyzicky dukaz": rozhodne snimek. Jinak by
    # o verdiktu rozhodoval prah, ktery je sam nejistY.
    ((PHYS_UNCLEAR, "SUCCESS", "SUCCESS", OK_REASON), (True, False)),
    ((PHYS_UNCLEAR, "FAIL", FAIL_TAG, FAIL_REASON), (False, False)),
    ((PHYS_UNCLEAR, "UNCLEAR", "[unclear]", "rozmazane"), (False, False)),

    # -- inspektor vypnuty (ablace "jen fyzika") -------------------------
    ((PHYS_CONFIRM, "SKIPPED", "", ""), (True, False)),
    ((PHYS_DENY, "SKIPPED", "", ""), (False, False)),
    ((PHYS_NONE, "SKIPPED", "", ""), (True, False)),

    # -- rozbita kamera --------------------------------------------------
    ((PHYS_CONFIRM, "NOIMG", "", ""), (True, False)),
    ((PHYS_DENY, "NOIMG", "", ""), (False, False)),
    ((PHYS_NONE, "NOIMG", "", ""), (False, False)),
]

failures = []
for (phys, vis, v_tag, v_reason), (want_success, want_conflict) in CASES:
    success, tag, reason, conflict = fuse_evidence(phys, NOTE, vis, v_tag, v_reason)
    got = (success, bool(conflict))
    status = "ok  " if got == (want_success, want_conflict) else "CHYBA"
    if got != (want_success, want_conflict):
        failures.append((phys, vis, got, (want_success, want_conflict)))
    print(f"{status} phys={phys:<7} vis={vis:<8} -> success={success!s:<5} "
          f"tag={tag:<17} conflict={'ano' if conflict else 'ne'}")

# Rozpor nesmi nikdy zmizet potichu: kdykoli se kanaly jiste neshodnou,
# musi fuze vratit neprazdny `conflict`, aby se dostal do runs/*.json
# i do kontextu re-planu.
for phys, vis in ((PHYS_CONFIRM, "FAIL"), (PHYS_DENY, "SUCCESS")):
    _, _, _, conflict = fuse_evidence(phys, NOTE, vis, FAIL_TAG, FAIL_REASON)
    if not conflict:
        failures.append((phys, vis, "bez conflict textu", "neprazdny conflict"))

# Nerozhodny inspektor nesmi nikdy prebit fyzicky dukaz — jinak by slaby
# VLM vyrabel nove falesne pady z nejednoznacneho uhlu kamery.
for phys, expected in ((PHYS_CONFIRM, True), (PHYS_DENY, False)):
    success, _, _, conflict = fuse_evidence(phys, NOTE, "UNCLEAR", "[unclear]", "nevidim")
    if success is not expected or conflict:
        failures.append((phys, "UNCLEAR", success, expected))

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print(f"OK — vsech {len(CASES)} kombinaci pravdivostni tabulky sedi.")
