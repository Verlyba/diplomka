"""Kazdy snimek poslany modelu je ulozeny a volani v llm_calls na nej ukazuje.

Nepotrebuje robota ani LM Studio — model je zastupce, snimky se zapisou do
docasneho adresare.

    python tests/test_llm_call_images.py
"""
import base64
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import orchestrator
from orchestrator import LAYER_INSPECTOR, LAYER_PLANNER, Orchestrator

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "CHYBA"), name, ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


def b64(text: str) -> str:
    return base64.b64encode(b"\xff\xd8\xff\xe0" + text.encode() + b"\xff\xd9").decode("ascii")


class FakeLM:
    def chat_with_images(self, **kw):
        return "ok"


tmp = Path(tempfile.mkdtemp(prefix="diplomka-callimg-"))
real_here = orchestrator.HERE
orchestrator.HERE = tmp
try:
    def make(cfg=None):
        o = Orchestrator({"task_slug": "t", **(cfg or {})}, lambda *a, **k: None)
        o.run_id = "20260926-000000"
        o.lm = FakeLM()
        return o

    A, B, C = b64("kamera-top-1"), b64("kamera-wrist-1"), b64("kamera-top-2")

    # 1) snimky uz ulozene pod pokusem se nezdvojuji, volani na ne jen ukazuje
    o = make()
    saved = o._keep_images("a001", [A, B])
    o._chat(LAYER_INSPECTOR, "verify_step", model="m", user_prompt="p", images_b64=[A, B])
    rec = o.llm_calls[-1]
    check("volani ukazuje na soubory ulozene pod pokusem", rec["image_paths"] == saved, rec["image_paths"])
    check("pocet snimku sedi", rec["images"] == 2, rec["images"])
    files = sorted(p.name for p in (tmp / "images" / "20260926-000000").iterdir())
    check("nic se neulozilo podruhe", files == ["a001_1.jpg", "a001_2.jpg"], files)

    # 2) snimek, ktery ulozeny nebyl, se ulozi pri volani a je dohledatelny
    o._chat(LAYER_INSPECTOR, "scene_change", model="m", user_prompt="p", images_b64=[A, C])
    rec = o.llm_calls[-1]
    check("znamy snimek A ukazuje na puvodni soubor", rec["image_paths"][0] == saved[0], rec["image_paths"])
    check("novy snimek C se ulozil pod call<cislo>_<ucel>", rec["image_paths"][1] == "images/20260926-000000/call002_scene_change_1.jpg", rec["image_paths"])
    check("ulozeny soubor je bajt po bajtu stejny", (tmp / rec["image_paths"][1]).read_bytes() == base64.b64decode(C))

    # 3) stejny snimek podruhe: uz se nesaha na disk
    n_before = len(list((tmp / "images" / "20260926-000000").iterdir()))
    o._chat(LAYER_INSPECTOR, "scene_change", model="m", user_prompt="p", images_b64=[A, C])
    check("opakovany snimek se neuklada znovu",
          len(list((tmp / "images" / "20260926-000000").iterdir())) == n_before)

    # 4) volani bez snimku, planovac, jedna hodnota misto seznamu
    o._chat(LAYER_PLANNER, "initial_plan", model="m", user_prompt="p", images_b64=None)
    check("volani bez snimku ma prazdny seznam cest", o.llm_calls[-1]["image_paths"] == [] and o.llm_calls[-1]["images"] == 0)
    o._chat(LAYER_PLANNER, "replan", model="m", user_prompt="p", images_b64=[B])
    check("planovac: snimek dohledan", o.llm_calls[-1]["image_paths"] == [saved[1]], o.llm_calls[-1]["image_paths"])

    # 5) prazdny prvek v seznamu drzi pozici
    o._chat(LAYER_INSPECTOR, "verify_step", model="m", user_prompt="p", images_b64=[A, "", B])
    check("prazdny snimek drzi svou pozici", o.llm_calls[-1]["image_paths"] == [saved[0], "", saved[1]], o.llm_calls[-1]["image_paths"])

    # 6) vypnute ukladani (save_images=false): nic se nezapisuje a nic nespadne
    tmp2 = Path(tempfile.mkdtemp(prefix="diplomka-callimg2-"))
    orchestrator.HERE = tmp2
    o2 = make({"save_images": False})
    o2._chat(LAYER_INSPECTOR, "verify_step", model="m", user_prompt="p", images_b64=[A, B])
    check("save_images=false: prazdne cesty, zadne soubory",
          o2.llm_calls[-1]["image_paths"] == [] and not (tmp2 / "images").exists(), o2.llm_calls[-1])
    shutil.rmtree(tmp2, ignore_errors=True)
finally:
    orchestrator.HERE = real_here
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    sys.exit(1)
print("OK — snimky poslane modelum jsou ulozene a dohledatelne.")
