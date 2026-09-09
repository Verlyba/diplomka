"""Pravdivostni tabulka fuze dukazu (orchestrator.fuse_evidence).

Na rozdil od ostatnich testu v teto slozce nepotrebuje ani robota, ani
LeRobota — fuze je cista funkce, takze se da overit cela, vcetne kombinaci,
ktere se na skutecnem robotu trefi jen zridka.

    python tests/test_fusion.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import (OUTCOME_FAILURE, OUTCOME_SUCCESS, OUTCOME_UNCERTAIN,
                          PHYS_CONFIRM, PHYS_DENY, PHYS_NONE, PHYS_UNCLEAR,
                          fuse_evidence, reflex_retry_decision)

NOTE = "protokol B: cclisti registruji sevreni"
FAIL_TAG = "[object_missed]"
FAIL_REASON = "gripper je prazdny"
OK_REASON = "kostka je v celistech"

# (phys, vis, v_tag, v_reason) -> (success, conflict_expected, outcome)
CASES = [
    # -- oba kanaly se shodnou -------------------------------------------
    ((PHYS_CONFIRM, "SUCCESS", "SUCCESS", OK_REASON), (True, False, OUTCOME_SUCCESS)),
    ((PHYS_DENY, "FAIL", FAIL_TAG, FAIL_REASON), (False, False, OUTCOME_FAILURE)),

    # -- jeden kanal nese druhy (bez rozporu) ----------------------------
    # Inspektor nerozhodne -> nese fyzika. Tohle je ta oprava, kvuli ktere
    # driv `[unclear]` shazoval jinak v poradku dokonceny reset krok.
    # Fyzika tady neco zmerila, takze to NENI "nikdo nic nevidel".
    ((PHYS_CONFIRM, "UNCLEAR", "[unclear]", "nevidim za gripper"), (True, False, OUTCOME_SUCCESS)),
    ((PHYS_DENY, "UNCLEAR", "[unclear]", "nevidim za gripper"), (False, False, OUTCOME_FAILURE)),

    # -- skutecne rozpory: oba se musi ohlasit, ne zahodit ---------------
    # Fyzika potvrzuje, ale inspektor vidi konkretni problem (napr. drzi
    # spatny predmet) -> vyhrava inspektor, protoze prave tohle je to, co
    # protokol B principialne nemuze poznat.
    ((PHYS_CONFIRM, "FAIL", FAIL_TAG, FAIL_REASON), (False, True, OUTCOME_FAILURE)),
    # Fyzika nepotvrdila, ale inspektor jasne vidi splneny vysledek ->
    # zachrana pozde/plane vyhodnoceneho protokolu.
    ((PHYS_DENY, "SUCCESS", "SUCCESS", OK_REASON), (True, True, OUTCOME_SUCCESS)),

    # -- bez fyzickeho dukazu rozhoduje inspektor sam --------------------
    ((PHYS_NONE, "SUCCESS", "SUCCESS", OK_REASON), (True, False, OUTCOME_SUCCESS)),
    ((PHYS_NONE, "FAIL", FAIL_TAG, FAIL_REASON), (False, False, OUTCOME_FAILURE)),
    # Jedina bunka tabulky, kde krok neposoudil nikdo: success zustava False
    # (nic se nepotvrdilo), ale outcome to odlisuje od skutecneho selhani.
    ((PHYS_NONE, "UNCLEAR", "[unclear]", "rozmazane"), (False, False, OUTCOME_UNCERTAIN)),

    # -- fyzika zmerena, ale prilis blizko prahu (pasmo nejistoty) -------
    # Musi se chovat jako "zadny fyzicky dukaz": rozhodne snimek. Jinak by
    # o verdiktu rozhodoval prah, ktery je sam nejistY.
    ((PHYS_UNCLEAR, "SUCCESS", "SUCCESS", OK_REASON), (True, False, OUTCOME_SUCCESS)),
    ((PHYS_UNCLEAR, "FAIL", FAIL_TAG, FAIL_REASON), (False, False, OUTCOME_FAILURE)),
    ((PHYS_UNCLEAR, "UNCLEAR", "[unclear]", "rozmazane"), (False, False, OUTCOME_UNCERTAIN)),

    # -- inspektor vypnuty (ablace "jen fyzika") -------------------------
    # Taky nikdo nic nevidel, ale tady je "zadna kontrola" = "zadna namitka":
    # ablace skip_inspector nesmi zacit kroky preverovat.
    ((PHYS_CONFIRM, "SKIPPED", "", ""), (True, False, OUTCOME_SUCCESS)),
    ((PHYS_DENY, "SKIPPED", "", ""), (False, False, OUTCOME_FAILURE)),
    ((PHYS_NONE, "SKIPPED", "", ""), (True, False, OUTCOME_SUCCESS)),

    # -- rozbita kamera --------------------------------------------------
    # NOIMG je porucha kanalu, ne nejednoznacny snimek: opakovanim se to
    # nevyresi, takze zustava selhanim a nikoli OUTCOME_UNCERTAIN.
    ((PHYS_CONFIRM, "NOIMG", "", ""), (True, False, OUTCOME_SUCCESS)),
    ((PHYS_DENY, "NOIMG", "", ""), (False, False, OUTCOME_FAILURE)),
    ((PHYS_NONE, "NOIMG", "", ""), (False, False, OUTCOME_FAILURE)),
]

failures = []
for (phys, vis, v_tag, v_reason), want in CASES:
    success, tag, reason, conflict, outcome = fuse_evidence(phys, NOTE, vis, v_tag, v_reason)
    got = (success, bool(conflict), outcome)
    status = "ok  " if got == want else "CHYBA"
    if got != want:
        failures.append((phys, vis, got, want))
    print(f"{status} phys={phys:<7} vis={vis:<8} -> success={success!s:<5} "
          f"tag={tag:<17} conflict={'ano' if conflict else 'ne':<3} outcome={outcome}")

# Rozpor nesmi nikdy zmizet potichu: kdykoli se kanaly jiste neshodnou,
# musi fuze vratit neprazdny `conflict`, aby se dostal do runs/*.json
# i do kontextu re-planu.
for phys, vis in ((PHYS_CONFIRM, "FAIL"), (PHYS_DENY, "SUCCESS")):
    *_, conflict, _ = fuse_evidence(phys, NOTE, vis, FAIL_TAG, FAIL_REASON)
    if not conflict:
        failures.append((phys, vis, "bez conflict textu", "neprazdny conflict"))

# Nerozhodny inspektor nesmi nikdy prebit fyzicky dukaz — jinak by slaby
# VLM vyrabel nove falesne pady z nejednoznacneho uhlu kamery.
for phys, expected in ((PHYS_CONFIRM, True), (PHYS_DENY, False)):
    success, _, _, conflict, _ = fuse_evidence(phys, NOTE, "UNCLEAR", "[unclear]", "nevidim")
    if success is not expected or conflict:
        failures.append((phys, "UNCLEAR", success, expected))

# OUTCOME_UNCERTAIN nesmi nikdy prijit s success=True: je to "nikdo nic
# netvrdi", ne "proslo to". Kdyby proslo, tichy uspech by se dostal do
# runs/*.json jako splneny krok bez jedineho pozorovani.
for (phys, vis, v_tag, v_reason), _want in CASES:
    success, _, _, _, outcome = fuse_evidence(phys, NOTE, vis, v_tag, v_reason)
    if outcome == OUTCOME_UNCERTAIN and success:
        failures.append((phys, vis, "uncertain + success=True", "uncertain => success=False"))

# ── Reflexni opakovani nevyhodnotitelneho kroku ─────────────────────────
# (action, streak_step, streak_count) pro reflex_retry_decision().
STREAK_CASES = [
    # Pozorovany vysledek streak vzdy vynuluje — i uspech po neprukaznem pokusu.
    ((OUTCOME_SUCCESS, "grab", "grab", 1, True), ("none", "", 0)),
    ((OUTCOME_FAILURE, "grab", "grab", 1, True), ("none", "", 0)),
    # Prvni neprukazna kontrola kroku: opakuj bez volani CEO.
    ((OUTCOME_UNCERTAIN, "grab", "", 0, True), ("retry", "grab", 1)),
    # Druha v rade u tehoz kroku: uz to reseni neni, jde se na re-plan.
    ((OUTCOME_UNCERTAIN, "grab", "grab", 1, True), ("escalate", "grab", 2)),
    ((OUTCOME_UNCERTAIN, "grab", "grab", 2, True), ("escalate", "grab", 3)),
    # Jiny krok = novy streak, ne pokracovani predchoziho.
    ((OUTCOME_UNCERTAIN, "place", "grab", 1, True), ("retry", "place", 1)),
    # Vypnuto (uncertain_retry=false) = chovani pred touhle zmenou: selhani.
    ((OUTCOME_UNCERTAIN, "grab", "", 0, False), ("escalate", "grab", 1)),
]
for args, want in STREAK_CASES:
    got = reflex_retry_decision(*args)
    status = "ok  " if got == want else "CHYBA"
    if got != want:
        failures.append((args, got, want))
    print(f"{status} outcome={args[0]:<9} step={args[1]:<6} streak=({args[2]!r},{args[3]}) "
          f"enabled={args[4]!s:<5} -> {got}")

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print(f"OK — vsech {len(CASES)} kombinaci pravdivostni tabulky a "
      f"{len(STREAK_CASES)} pripadu reflexniho opakovani sedi.")
