"""Ukladani snimku k jednotlivym pokusum (orchestrator.save_snapshot_files).

Nepotrebuje robota ani LeRobota — snimky se vyrobi v pameti a zapisou do
docasneho adresare.

    python tests/test_images.py
"""
import base64
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import save_snapshot_files

failures = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'CHYBA'} {name}: {got!r}" + ("" if ok else f"  (cekano {want!r})"))
    if not ok:
        failures.append((name, got, want))


# Minimalni "JPEG" — obsah se nekontroluje, jde o to, ze se zapise beze zmeny.
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"obsah snimku" + bytes([0xFF, 0xD9])
B64 = base64.b64encode(JPEG).decode("ascii")

root = Path(tempfile.mkdtemp(prefix="diplomka-images-"))
try:
    # ── Zakladni zapis ──────────────────────────────────────────────────────
    paths = save_snapshot_files(root, "20260911-101500", "a003", [B64, B64])
    check("dve kamery = dva soubory", paths,
          ["images/20260911-101500/a003_1.jpg", "images/20260911-101500/a003_2.jpg"])
    check("soubor opravdu vznikl", (root / paths[0]).exists(), True)
    check("obsah sedi bajt po bajtu", (root / paths[0]).read_bytes(), JPEG)
    # Cesty v zaznamu musi byt relativni ke korenu projektu, aby sel run
    # prenest jinam i se slozkou snimku — absolutni cesta by na jinem stroji
    # neplatila.
    check("cesta je relativni", paths[0].startswith("images/"), True)
    check("cesta ma lomitka i na Windows", "\\" in paths[0], False)

    # ── Navaznost na pokus ──────────────────────────────────────────────────
    # Tag je to jedine, co snimek vaze na konkretni pokus, takze se musi
    # promitnout do nazvu souboru.
    p2 = save_snapshot_files(root, "20260911-101500", "a004", [B64])
    check("jiny pokus = jiny soubor", p2, ["images/20260911-101500/a004_1.jpg"])
    p3 = save_snapshot_files(root, "20260911-101500", "init", [B64])
    check("vychozi scena ma svuj tag", p3, ["images/20260911-101500/init_1.jpg"])
    p4 = save_snapshot_files(root, "20260911-101500", "done0", [B64])
    check("kontrola cile ma svuj tag", p4, ["images/20260911-101500/done0_1.jpg"])

    # Vsechno z jednoho behu lezi v jedne slozce pojmenovane jako zaznam behu.
    files = sorted(f.name for f in (root / "images" / "20260911-101500").iterdir())
    check("vse v jedne slozce behu", files,
          ["a003_1.jpg", "a003_2.jpg", "a004_1.jpg", "done0_1.jpg", "init_1.jpg"])

    # Jiny beh = jina slozka, nic se nemicha dohromady.
    other = save_snapshot_files(root, "20260911-110000", "a001", [B64])
    check("jiny beh ma vlastni slozku", other, ["images/20260911-110000/a001_1.jpg"])

    # ── Degenerovane vstupy — nic z toho nesmi vyhodit vyjimku ──────────────
    check("zadne snimky", save_snapshot_files(root, "r", "a001", []), [])
    check("None misto seznamu", save_snapshot_files(root, "r", "a001", None), [])
    check("prazdny retezec se preskoci", save_snapshot_files(root, "r", "a001", [""]), [])
    # Poskozeny base64 nesmi shodit beh — jen se ten snimek neulozi.
    check("nesmyslny base64", save_snapshot_files(root, "r", "a001", ["???nen&i base64"]), [])
    # Prazdny snimek (validni base64, nulova delka) taky nema co delat na disku.
    check("prazdny snimek", save_snapshot_files(root, "r", "a001", [""]), [])

    # Jeden poskozeny mezi platnymi nesmi shodit ty ostatni — vysledek je
    # kratsi seznam, ne vyjimka.
    mixed = save_snapshot_files(root, "20260911-120000", "a001", [B64, "", B64])
    check("poskozeny snimek nezhodi ostatni", len(mixed), 2)
    check("ocislovani nechava mezeru po vynechanem", mixed,
          ["images/20260911-120000/a001_1.jpg", "images/20260911-120000/a001_3.jpg"])

    # ── Prepis tehoz tagu ───────────────────────────────────────────────────
    # Reflexni zopakovani kroku vyrobi novy pokus s novym cislem, takze ke
    # kolizi by dojit nemelo — ale kdyby, musi zapis projit a ne spadnout.
    again = save_snapshot_files(root, "20260911-101500", "a003", [B64])
    check("prepis tehoz tagu projde", again, ["images/20260911-101500/a003_1.jpg"])

    # ── Po sobe nezustavaji docasne soubory ────────────────────────────────
    leftovers = [f.name for f in (root / "images" / "20260911-101500").iterdir()
                 if f.name.startswith(".") or f.name.endswith(".tmp")]
    check("zadne docasne soubory po atomickem zapisu", leftovers, [])
finally:
    shutil.rmtree(root, ignore_errors=True)

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print("OK — ukladani snimku k pokusum sedi.")
