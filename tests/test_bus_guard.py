"""Obnova zaseknuté sběrnice servo motorů (bus_guard.BusGuard).

Nepotřebuje robota ani LeRobota — falešný PortHandler a simulované hodiny.
Odpovídá skutečné poruše z 2026-09-25: po jedné výjimce sériové linky zůstal
příznak `is_using` viset a každé další čtení vrátilo "Port is in use!".

    python tests/test_bus_guard.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bus_guard import BusGuard

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "CHYBA"), name, ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


class FakePort:
    def __init__(self, close_raises=False, clear_raises=False, open_raises=False):
        self.is_using = True          # zaseknutý příznak, jako v reálné poruše
        self.is_open = True
        self.calls = []
        self.close_raises, self.clear_raises, self.open_raises = close_raises, clear_raises, open_raises

    def clearPort(self):
        self.calls.append("clear")
        if self.clear_raises:
            raise OSError("flush selhal")

    def closePort(self):
        self.calls.append("close")
        if self.close_raises:
            raise OSError("close selhal")
        self.is_open = False

    def openPort(self):
        self.calls.append("open")
        if self.open_raises:
            raise OSError("COM3 zmizel")
        self.is_open = True
        return True


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(port, **kw):
    clk = Clock()
    logs, events = [], []
    g = BusGuard(lambda: port, logs.append, lambda name, **f: events.append((name, f)), now=clk, **kw)
    return g, clk, logs, events


PIU = RuntimeError("Failed to sync read 'Present_Position' on ids=[1, 2, 3, 4, 5, 6] after 3 tries. "
                   "[TxRxResult] Port is in use!")

# ── rozpoznání chyby sběrnice ───────────────────────────────────────────────
check("Port is in use je chyba sběrnice", BusGuard.is_bus_fault(PIU))
check("SerialException je chyba sběrnice", BusGuard.is_bus_fault(Exception("ClearCommError failed (PermissionError(13,))")))
check("obyčejná ValueError není chyba sběrnice", not BusGuard.is_bus_fault(ValueError("tvar snímku nesedí")))
check("timeout kamery není chyba sběrnice", not BusGuard.is_bus_fault(RuntimeError("Timed out waiting for frame from camera")))

p = FakePort(); g, clk, logs, events = make(p)
check("cizí chyba: fault() vrací False", g.fault(ValueError("x")) is False)
check("cizí chyba: příznak nedotčen", p.is_using is True and not p.calls)
check("cizí chyba: nic v logu ani v událostech", not logs and not events)

# ── jediné "Port is in use" = souběh vláken, do cizí transakce nesahat ─────
p = FakePort(); g, clk, logs, events = make(p)
g.fault(PIU)
check("první chyba: příznak zatím netknutý", p.is_using is True and "clear" not in p.calls)
check("první chyba: událost bus_fault", [e[0] for e in events] == ["bus_fault"])
check("první chyba: v logu celá výjimka", len(logs) == 1 and "Port is in use" in logs[0])

# ── trvalá chyba = zaseknutý příznak -> uvolnit ────────────────────────────
clk.t += 0.033
g.fault(PIU)
check("druhá chyba v řadě: příznak uvolněn", p.is_using is False)
check("druhá chyba v řadě: port vyprázdněn", "clear" in p.calls)
check("druhá chyba: bus_fault se neopakuje", [e[0] for e in events].count("bus_fault") == 1)

# ── po úspěchu konec epizody ───────────────────────────────────────────────
clk.t += 0.033
g.ok()
names = [e[0] for e in events]
check("ok() po chybě: událost bus_recovered", names == ["bus_fault", "bus_recovered"], names)
rec = events[-1][1]
check("bus_recovered nese dobu výpadku a počty", rec["errors"] == 2 and rec["resets"] == 1 and 0.05 < rec["downtime_s"] < 0.08, rec)
n_before = len(events)
g.ok(); g.ok()
check("další ok() už nic nehlásí", len(events) == n_before)

# ── záplava se nesmí dostat do logu ────────────────────────────────────────
p = FakePort(); g, clk, logs, events = make(p, give_up_after_s=1e9)
for _ in range(300):               # 300 chyb za ~10 s při 30 Hz
    g.fault(PIU)
    clk.t += 0.033
check("300 chyb za 10 s: log má jednotky řádků, ne stovky", len(logs) < 20, len(logs))
check("záplava: první řádek je pořád ta první výjimka", "první výjimka" in logs[0])

# ── minimální rozestup pokusů o obnovu ─────────────────────────────────────
p = FakePort(); g, clk, logs, events = make(p, min_interval_s=0.2, reopen_every=99, give_up_after_s=1e9)
g.fault(PIU)
g.fault(PIU)                        # 1. obnova
p.is_using = True
g.fault(PIU)                        # ihned -> pod min_interval, žádný zásah
check("pokusy o obnovu se nekonají častěji než min_interval", p.is_using is True and p.calls.count("clear") == 1, p.calls)
clk.t += 0.25
g.fault(PIU)
check("po uplynutí min_interval se zasáhne znovu", p.is_using is False and p.calls.count("clear") == 2, p.calls)

# ── po několika marných uvolněních znovu otevřít port ──────────────────────
p = FakePort(); g, clk, logs, events = make(p, reopen_every=3, give_up_after_s=1e9)
for _ in range(7):
    p.is_using = True
    clk.t += 0.3
    g.fault(PIU)
check("port se znovu otevřel (close+open)", "close" in p.calls and "open" in p.calls, p.calls)
check("po znovuotevření je příznak volný", p.is_using is False)
check("znovuotevření je v logu", any("znovu otevřen" in m for m in logs), logs)

p = FakePort(close_raises=True); g, clk, logs, events = make(p, reopen_every=1, give_up_after_s=1e9)
for _ in range(3):
    clk.t += 0.3
    g.fault(PIU)
check("close() selže -> přesto se zkusí open()", "open" in p.calls, p.calls)
check("close() selže -> is_open se srovná ručně", p.is_open is True)

p = FakePort(open_raises=True); g, clk, logs, events = make(p, reopen_every=1, give_up_after_s=1e9)
for _ in range(3):
    clk.t += 0.3
    g.fault(PIU)
check("open() selže (COM zmizel) -> výjimka neprosákne, jde do logu", any("selhalo" in m for m in logs), logs)

p = FakePort(clear_raises=True); g, clk, logs, events = make(p)
try:
    g.fault(PIU); clk.t += 0.3; g.fault(PIU)
    check("clearPort() vyhodí výjimku -> guard ji spolkne", p.is_using is False)
except Exception as e:  # noqa: BLE001
    check("clearPort() vyhodí výjimku -> guard ji spolkne", False, repr(e))

# ── vzdání se ───────────────────────────────────────────────────────────────
p = FakePort(); g, clk, logs, events = make(p, give_up_after_s=8.0)
g.fault(PIU)
clk.t += 7.9
g.fault(PIU)
check("před limitem se guard nevzdá", g.gave_up is False)
clk.t += 0.2
g.fault(PIU)
check("po give_up_after_s se guard vzdá", g.gave_up is True)
check("bus_lost se ohlásí právě jednou", [e[0] for e in events].count("bus_lost") == 1)
clk.t += 1; g.fault(PIU)
check("další chyby bus_lost neopakují", [e[0] for e in events].count("bus_lost") == 1)

# úspěšné čtení uprostřed vzdání se neruší — nová epizoda začíná od nuly
g.ok()
g.fault(PIU)
check("po ok() nová epizoda nezačíná vzdaná", g.gave_up is False)

# ── simulovaný režim (žádný PortHandler) ───────────────────────────────────
g, clk, logs, events = make(None)
try:
    g.fault(PIU); clk.t += 0.3; g.fault(PIU); g.ok()
    check("bez PortHandleru (simulace) nic nespadne", True)
except Exception as e:  # noqa: BLE001
    check("bez PortHandleru (simulace) nic nespadne", False, repr(e))

print()
if failures:
    print(f"NEPROSLO: {len(failures)} — {failures}")
    sys.exit(1)
print("OK — obnova zaseknuté sběrnice sedí ve všech kontrolovaných případech.")
