"""Obnova zaseknuté sběrnice servo motorů (Feetech SDK "Port is in use!").

Feetech SDK při odeslání paketu nastaví `PortHandler.is_using = True` a smaže
příznak až na konci `rxPacket()`. Výjimka, která vyskočí mezi tím — typicky
`SerialException` z USB adaptéru na Windows — nechá příznak viset navždy a od
té chvíle každé volání sběrnice okamžitě vrátí COMM_PORT_BUSY:

    Failed to sync read 'Present_Position' ... [TxRxResult] Port is in use!

až do konce procesu. Zaznamenáno 2026-09-25 (běh s diplomka_2): od 20:24:27
zůstaly klouby v telemetrii bit po bitu zmrzlé (poslední staré čtení), krok
doběhl do timeoutu a LLM/VLM pak usuzovaly nad během, který už nic neměřil.
LeRobot sám uvolňuje ten samý příznak stejným způsobem v
`FeetechMotorsBus.disconnect()` (`clearPort()` + `is_using = False`).

Modul je záměrně bez závislostí, ať jde otestovat bez robota i bez LeRobotu.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

BUS_FAULT_MARKERS = (
    "Port is in use",                                # scservo_sdk COMM_PORT_BUSY
    "Failed to sync read", "Failed to sync write",   # obálky v lerobot FeetechMotorsBus
    "TxRxResult",
    "SerialException", "ClearCommError", "WriteFile failed", "ReadFile failed",
    "PermissionError", "could not open port",
)


class BusGuard:
    """Pozná zaseknutou sběrnici, uvolní ji a omezí záplavu stejných hlášek.

    fault(exc): volat z každého `except` kolem I/O robota. Chyby, které nevypadají
        jako chyba sběrnice, ignoruje (vrací False).
    ok(): volat po každém úspěšném I/O — ukončí aktuální epizodu poruchy.

    Postup obnovy (od nejlevnějšího):
      1. uvolnit `is_using` + vyprázdnit port — až od DRUHÉ chyby v řadě: jediné
         "Port is in use" je nejspíš běžný souběh dvou vláken (jeden přeběhl
         druhého), který se vyřeší sám, a zásah do cizí rozběhnuté transakce by
         ji poškodil. Zaseknutý příznak naopak hlásí chybu při každém ticku.
      2. po `reopen_every` marných uvolněních port zavřít a znovu otevřít.
      3. trvá-li porucha `give_up_after_s`, nastaví `gave_up` — volající ukončí
         daemon a orchestrátor ho restartuje čerstvý (nová instance PortHandleru).
    """

    def __init__(self,
                 get_port_handler: Callable[[], Any],
                 log_fn: Callable[[str], None],
                 event_fn: Callable[..., None],
                 *,
                 now: Callable[[], float] = time.monotonic,
                 reopen_every: int = 5,
                 give_up_after_s: float = 8.0,
                 min_interval_s: float = 0.2,
                 summary_every_s: float = 2.0) -> None:
        self._get_ph = get_port_handler
        self._log = log_fn
        self._event = event_fn
        self._now = now
        self._reopen_every = max(1, reopen_every)
        self._give_up_after = give_up_after_s
        self._min_interval = min_interval_s
        self._summary_every = summary_every_s
        self._lock = threading.Lock()
        self._in_fault = False
        self._t0 = 0.0
        self._n = 0
        self._resets = 0
        self._last_action = -1e9
        self._last_summary = 0.0
        self.gave_up = False

    @staticmethod
    def is_bus_fault(exc: BaseException) -> bool:
        text = f"{type(exc).__name__}: {exc}"
        return any(m in text for m in BUS_FAULT_MARKERS)

    @property
    def in_fault(self) -> bool:
        return self._in_fault

    def fault(self, exc: BaseException) -> bool:
        if not self.is_bus_fault(exc):
            return False
        text = f"{type(exc).__name__}: {exc}"
        now = self._now()
        messages: list[str] = []
        events: list[tuple[str, dict]] = []
        with self._lock:
            if not self._in_fault:
                self._in_fault, self._t0, self._n, self._resets = True, now, 0, 0
                self._last_summary = now
                self.gave_up = False
                messages.append(f"Chyba sběrnice servo motorů — první výjimka: {text}")
                events.append(("bus_fault", {"error": text[:400]}))
            self._n += 1
            if self._n >= 2 and now - self._last_action >= self._min_interval:
                self._last_action = now
                messages.extend(self._recover_locked())
            if now - self._last_summary >= self._summary_every:
                self._last_summary = now
                messages.append(
                    f"Sběrnice servo motorů stále nefunguje ({now - self._t0:.1f} s, "
                    f"{self._n} chyb, {self._resets} pokusů o obnovu): {text}")
            if now - self._t0 >= self._give_up_after and not self.gave_up:
                self.gave_up = True
                events.append(("bus_lost", {"after_s": round(now - self._t0, 2),
                                            "errors": self._n, "resets": self._resets,
                                            "error": text[:400]}))
        for m in messages:
            self._safe(self._log, m)
        for name, fields in events:
            self._safe(self._event, name, **fields)
        return True

    def ok(self) -> None:
        if not self._in_fault:          # rychlá cesta — volá se v každém ticku
            return
        with self._lock:
            if not self._in_fault:
                return
            dur = self._now() - self._t0
            n, resets = self._n, self._resets
            self._in_fault = False
        self._safe(self._log, f"Sběrnice servo motorů obnovena po {dur:.2f} s "
                              f"({n} chyb, {resets} pokusů o obnovu).")
        self._safe(self._event, "bus_recovered", downtime_s=round(dur, 3), errors=n, resets=resets)

    # -- vnitřek ---------------------------------------------------------------
    def _recover_locked(self) -> list[str]:
        msgs: list[str] = []
        ph = self._safe(self._get_ph)
        if ph is None:
            return msgs
        self._resets += 1
        try:
            ph.is_using = False
        except Exception:
            pass
        self._safe(lambda: ph.clearPort())
        if self._resets % self._reopen_every == 0:
            try:
                ph.closePort()
            except Exception:
                # ser.close() může sám selhat; bez tohohle by openPort() -> setupPort()
                # zkusilo zavřít znovu a nikdy se nedostalo k otevření.
                try:
                    ph.is_open = False
                except Exception:
                    pass
            try:
                ph.openPort()
                msgs.append(f"Port sběrnice znovu otevřen (pokus o obnovu č. {self._resets}).")
            except Exception as e:  # noqa: BLE001 — port může být fyzicky pryč
                msgs.append(f"Znovuotevření portu sběrnice selhalo: {type(e).__name__}: {e}")
            try:
                ph.is_using = False
            except Exception:
                pass
        return msgs

    @staticmethod
    def _safe(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None
