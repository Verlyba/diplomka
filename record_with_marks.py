#!/usr/bin/env python3
"""
Nahrávání demonstrací se značkami hranic kroků.

Spouští se úplně stejně jako `lerobot-record` — všechny argumenty jdou beze
změny do LeRobota. Navíc tenhle skript poslouchá klávesu **mezerník**: každý
stisk zapíše hranici mezi fázemi úlohy. Značky se ukládají do sidecar souboru
vedle datasetu (`<dataset>.marks.json`), který pak čte `split_dataset.py`.

    python record_with_marks.py --robot.type=so101_follower --robot.port=COM3 ...

Klávesy navíc (LeRobotu zůstávají jeho vlastní → / ← / Esc, resp. n / r / q):

    mezerník nebo m   označ hranici kroku (kolikrát, tolik hranic)
    u                 vezmi zpět poslední značku v této epizodě

Do LeRobota se nijak nezasahuje. Skript si naimportuje
`lerobot.scripts.lerobot_record` a obalí dvě věci:

* `record_loop()` — LeRobot ji volá jednou s datasetem (nahrávání epizody)
  a jednou bez něj (reset scény), takže z ní jde poznat, kde epizoda začíná
  a končí, aniž by se cokoli přepisovalo;
* `dataset.add_frame()` — počítá snímky opravdu zapsané do epizody. Čas značky
  se pak počítá jako `snímky / fps`, tedy ve stejné časové ose, jakou má sloupec
  `timestamp` v datasetu. Kdyby řídicí smyčka jela pomaleji než cílové FPS,
  značka podle hodin by se rozešla s daty; podle snímků ne.

Když se epizoda zahodí a nahrává znovu (←/r), LeRobot pro ni použije stejný
index — značky starého pokusu se proto při začátku epizody smažou.

Formát výstupu (sekundy od začátku epizody):

    {"episodes": {"0": [4.12, 9.30], "1": [3.87, 8.94]}}
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

# Skript píše do terminálu, kde běží nahrávání. Kódování konzole se nechává
# (aby byla čeština čitelná), ale znak, který v něm není, nesmí shodit
# nahrávání uprostřed sběru dat — proto errors="replace".
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

# Názvy kláves, jak je hlásí posluchač LeRobotu (mezerník = "space")
MARK_KEYS = {"space", " ", "m"}   # označ hranici kroku
UNDO_KEYS = {"u"}                 # vezmi zpět poslední značku

_LOCK = threading.Lock()
_STATE = {
    "episode": -1,      # index právě nahrávané epizody
    "frames": 0,        # snímků zapsaných do aktuální epizody
    "fps": 0.0,
    "t0": 0.0,          # záložní časová základna (když selže počítání snímků)
    "counted": False,   # daří se počítat snímky?
    "recording": False,
    "marks": {},        # {"0": [4.12, 9.30], ...}
    "path": None,       # kam se sidecar zapisuje
}


def _say(message: str) -> None:
    print(f"[MARKS] {message}", flush=True)


# ── Zápis sidecar souboru ───────────────────────────────────────────────────

def _persist() -> None:
    """Uloží značky po každé změně — přežijí i pád nahrávání uprostřed."""
    path = _STATE["path"]
    if not path:
        return
    payload = {"episodes": {ep: sorted(times) for ep, times in _STATE["marks"].items() if times}}
    try:
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        _say(f"soubor se značkami se nepodařilo zapsat: {e}")


def _mark() -> None:
    with _LOCK:
        if not _STATE["recording"]:
            _say("mimo epizodu — značka ignorována")
            return
        if _STATE["counted"] and _STATE["fps"] > 0:
            t = _STATE["frames"] / _STATE["fps"]
        else:
            t = time.perf_counter() - _STATE["t0"]
        episode = str(_STATE["episode"])
        _STATE["marks"].setdefault(episode, []).append(round(t, 3))
        count = len(_STATE["marks"][episode])
        _persist()
    _say(f"epizoda {episode}: hranice #{count} v čase {t:.2f} s")


def _undo() -> None:
    with _LOCK:
        episode = str(_STATE["episode"])
        times = _STATE["marks"].get(episode) or []
        if not times:
            _say(f"epizoda {episode}: není co vzít zpět")
            return
        removed = times.pop()
        _persist()
    _say(f"epizoda {episode}: značka {removed:.2f} s odebrána")


def _drop_episode_marks(reason: str) -> None:
    """Zahodí značky právě nahrávané epizody (uživatel ji zahodil klávesou)."""
    with _LOCK:
        episode = str(_STATE["episode"])
        dropped = _STATE["marks"].pop(episode, None)
        if dropped:
            _persist()
    if dropped:
        _say(f"epizoda {episode}: {len(dropped)} značek zahozeno ({reason})")


# ── Poslech klávesnice ──────────────────────────────────────────────────────

def make_dispatch(keys, events: dict):
    """Obsluha jednoho stisku klávesy — naše značky i vlastní ovládání LeRobotu.

    Klíčové je, že se klávesy čtou **jednou**. Vlastní paralelní posluchač by
    LeRobotu bral stisky n/r/q z terminálu (a naopak), protože bez pynputu oba
    čtou tentýž vstup konzole. Proto se sem posílají všechny klávesy a ty,
    které patří LeRobotu, se předají jeho vlastní obsluze.
    """
    def dispatch(name: str) -> None:
        key = (name or "").lower()
        if key in MARK_KEYS:
            _mark()
        elif key in UNDO_KEYS:
            _undo()
        elif key in ("right", "n"):
            keys.apply_recording_control("right", events)
        elif key in ("left", "r"):
            # Zahozená epizoda se nahraje znovu pod stejným indexem —
            # značky starého pokusu musí zmizet s ním.
            _drop_episode_marks("epizoda se nahrava znovu")
            keys.apply_recording_control("left", events)
        elif key in ("esc", "q"):
            keys.apply_recording_control("esc", events)

    return dispatch


def _make_keyboard_listener(keys):
    """Náhrada za `init_keyboard_listener()` LeRobotu — jeden posluchač pro obojí.

    Backend (pynput / terminál / žádný) vybírá `create_key_listener` LeRobotu,
    takže se značkování chová stejně jako jeho vlastní ovládání nahrávání.
    """
    def init_keyboard_listener():
        events = {"exit_early": False, "rerecord_episode": False, "stop_recording": False}
        listener = keys.create_key_listener(
            make_dispatch(keys, events),
            controls_help="mezernik = hranice kroku, u = zpet")
        _say("klavesy: mezernik/m = hranice kroku, u = zpet; n/r/q (sipky, Esc) = ovladani LeRobotu")
        return listener, events

    return init_keyboard_listener


# ── Napojení na lerobot-record ──────────────────────────────────────────────

def _install_frame_counter(dataset) -> bool:
    """Počítá snímky zapsané do epizody — časová základna značek."""
    if getattr(dataset, "_marks_counted", False):
        return True
    try:
        original = dataset.add_frame

        def add_frame(frame, *args, **kwargs):
            with _LOCK:
                _STATE["frames"] += 1
            return original(frame, *args, **kwargs)

        dataset.add_frame = add_frame
        dataset._marks_counted = True
        return True
    except Exception as e:
        _say(f"snímky nelze počítat ({e}) — značky poběží podle hodin")
        return False


def main() -> int:
    try:
        import lerobot.scripts.lerobot_record as rec
    except ImportError as e:
        print(f"LeRobot se nepodařilo naimportovat: {e}", file=sys.stderr)
        return 2

    # Bez těchhle by skript tiše nahrál data bez značek — to je horší než se
    # nespustit vůbec, protože se to pozná až při rozdělování datasetu.
    missing = [name for name in ("record_loop", "main", "init_keyboard_listener")
               if not callable(getattr(rec, name, None))]
    if missing:
        print(f"Tahle verze LeRobotu nemá {', '.join(missing)} v lerobot.scripts."
              f"lerobot_record — wrapper by značky nezaznamenal. Nahrávání se nespustí.",
              file=sys.stderr)
        return 3

    try:
        import lerobot.utils.keyboard_input as keys
        assert callable(keys.create_key_listener) and callable(keys.apply_recording_control)
    except (ImportError, AttributeError, AssertionError) as e:
        print(f"Tahle verze LeRobotu nemá lerobot.utils.keyboard_input.create_key_listener "
              f"({e}) — značky by si s ovládáním nahrávání kradly klávesy. Nahrávání se nespustí.",
              file=sys.stderr)
        return 3

    original_record_loop = rec.record_loop

    def record_loop(*args, **kwargs):
        dataset = kwargs.get("dataset")
        if dataset is None:
            # Volání bez datasetu = fáze resetu scény mezi epizodami
            with _LOCK:
                _STATE["recording"] = False
        else:
            counted = _install_frame_counter(dataset)
            with _LOCK:
                episode = int(getattr(dataset, "num_episodes", 0) or 0)
                _STATE["episode"] = episode
                _STATE["fps"] = float(getattr(dataset, "fps", 0) or kwargs.get("fps") or 0)
                _STATE["frames"] = 0
                _STATE["t0"] = time.perf_counter()
                _STATE["counted"] = counted
                _STATE["recording"] = True
                # Opakovaná epizoda dostane stejný index — značky zahozeného
                # pokusu musí zmizet s ním.
                if _STATE["marks"].pop(str(episode), None):
                    _persist()
                if _STATE["path"] is None:
                    root = Path(str(getattr(dataset, "root", "") or ""))
                    _STATE["path"] = str(root.parent / f"{root.name}.marks.json")
                path = _STATE["path"]
            _say(f"epizoda {episode} — nahrávám (fps {_STATE['fps']:.0f}, značky do {path})")

        try:
            return original_record_loop(*args, **kwargs)
        finally:
            if dataset is not None:
                with _LOCK:
                    _STATE["recording"] = False
                    episode, frames = _STATE["episode"], _STATE["frames"]
                    marks = len(_STATE["marks"].get(str(episode), []))
                _say(f"epizoda {episode} hotová — {frames} snímků, {marks} značek")

    rec.record_loop = record_loop
    rec.init_keyboard_listener = _make_keyboard_listener(keys)

    try:
        rec.main()
    finally:
        _persist()
        path = _STATE["path"]
        total = sum(len(v) for v in _STATE["marks"].values())
        if path:
            _say(f"uloženo {total} značek v {len(_STATE['marks'])} epizodách → {path}")
        else:
            _say("žádné značky se nezapsaly (nahrávání se nerozeběhlo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
