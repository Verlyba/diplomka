"""Pustil gripper předmět? Fyzický signál pro krok s příznakem |release.

Proč to existuje. U kroku, který předmět přenáší a pouští (u nás carry_cube), rameno
dojede nad cíl DŘÍV, než se gripper otevře — stejně jako u úchopu, kde se rameno zastaví
dřív, než se gripper zavře. Protokol A měří jen rameno (vzdálenost kloubů od predikce),
takže krok ukončil ve chvíli, kdy rameno dosedlo, a `freeze_robot()` pak zmrazil pózu
s kostkou stále sevřenou v čelistech. Z telemetrie 2026-09-25 (17 kroků carry_cube
ukončených protokolem A): 7 skončilo s kostkou v čelistech (zátěž 280–488 na konci, gripper
17–19°), 1 přesně ve chvíli, kdy se gripper začal otevírat (cíl 28°, poloha 20°, zátěž už
klesala), 8 až po puštění a 1 bez kostky. Když se krok po předčasném konci zopakoval, gripper
se otevřel za 1,0 s. Starý běh s časovým limitem 20 s (kdy protokol A nikdy nevystřelil)
kostku pustil ve 13 ze 14 pokusů (zbylý nikdy nic nedržel); zátěž při tom spadla z 213–500
na 0–101.

Pravidlo, odvozené z těch křivek (obě hranice jako podíl limitu protokolu B, viz daemon):

  1. "Drží" — zátěž nad `hold_rise` na PLATÓ (ne rostoucí hrana), po `hold_patience` po sobě
     jdoucích tiků. Plató je nutné z téhož důvodu jako u protokolu B: náběh zavírání gripperu
     na začátku kroku (208 při 39,9° -> 18,4°, run 20260906-130647) přejde přes každou hodnotu
     a bez plató by se armoval a o 0,6 s později "pustil".
  2. "Pustil" — poté zátěž POD `free_rise` a zároveň se gripper přestal hýbat, po `settle`
     po sobě jdoucích tiků. Sama klesající zátěž nestačí: klesá hned na začátku otvírání,
     kdy čelisti ještě kostku obklopují (stejně jako u úchopu: krok není hotový, dokud
     se gripper nezastaví). Otevření gripperu o nějaký minimální úhel se NEPOŽADUJE: v běhu
     20260925-215320 se kostka pustila při posunu gripperu o 1,2° (držená přes diagonálu
     při 30,7°, zátěž 500 -> 72) a snímek z kamery na gripperu ji ukazuje v misce.

Dokud předmět "drží" a ještě ho nepustil, nesmí krok skončit protokolem A. Pokud v kroku
nebylo nic sevřeno (předchozí úchop selhal), `holding` zůstane False a protokol A funguje
jako dřív — nemá co pouštět.

Modul je záměrně bez závislostí, ať jde otestovat bez robota i bez LeRobotu.
"""
from __future__ import annotations

# Obě hranice zátěže jako podíl PROTOCOL_B_LOAD_LIMIT (jediná škála zátěže, kterou tenhle
# projekt kalibroval). Leží uvnitř naměřené mezery: plató sevření nikdy nespadlo pod 213,
# usazený uvolněný gripper nikdy nestál nad 101 (143–165 bylo vidět jen při pohybu čelistí,
# což podmínka "gripper stojí" vylučuje). Při limitu 300 vychází 198 a 150.
HOLD_FRAC = 0.66
FREE_FRAC = 0.5


class ReleaseTracker:
    """Stavový automat "nic -> drží -> pustil", krmený jednou hodnotou zátěže za tik."""

    def __init__(self, hold_rise: float, free_rise: float, hold_patience: int,
                 settle_ticks: int, still_delta: float) -> None:
        if not free_rise < hold_rise:
            raise ValueError(f"free_rise ({free_rise}) musí být pod hold_rise ({hold_rise}) — "
                             "jinak by nevznikl hysterezní pás mezi 'drží' a 'pustil'")
        if hold_patience < 1 or settle_ticks < 1:
            raise ValueError("hold_patience i settle_ticks musí být >= 1")
        self.hold_rise = float(hold_rise)
        self.free_rise = float(free_rise)
        self.hold_patience = int(hold_patience)
        self.settle_ticks = int(settle_ticks)
        self.still_delta = float(still_delta)
        self.reset()

    def reset(self) -> None:
        self.gripped = False      # v tomto kroku už bylo vidět sevření předmětu
        self.released = False     # a poté pustění (zátěž pryč + gripper stojí)
        self._hi = 0
        self._free = 0

    @property
    def holding(self) -> bool:
        """Předmět je (nebo byl a ještě nebyl puštěn) sevřen — konec protokolem A je zakázán."""
        return self.gripped and not self.released

    def update(self, rise: float, gripper_step: float, plateau: bool = True) -> None:
        """`rise` = zátěž nad klidovou hodnotou; `gripper_step` = |posun gripperu| od minulého tiku
        (inf, když ho nejde změřit — pak se gripper nepočítá za stojící); `plateau` = zátěž
        se přestala měnit (rostoucí hrana se za sevření nepočítá)."""
        if self.released:
            return
        if not self.gripped:
            self._hi = self._hi + 1 if (rise >= self.hold_rise and plateau) else 0
            if self._hi >= self.hold_patience:
                self.gripped = True
                self._free = 0
            return
        free = rise <= self.free_rise and gripper_step < self.still_delta
        self._free = self._free + 1 if free else 0
        if self._free >= self.settle_ticks:
            self.released = True
