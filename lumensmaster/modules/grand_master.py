"""
Grand Master : contrôle global du niveau de sortie.

Le Grand Master est un multiplicateur appliqué à l'ensemble de la sortie DMX.
Valeur 255 = 100% (pas d'atténuation), valeur 0 = blackout total.
"""

from __future__ import annotations

import logging

from lumensmaster.core.events import EventBus

logger = logging.getLogger(__name__)


class GrandMaster:
    """
    Grand Master de la console.
    
    Utilisation :
        gm = GrandMaster(bus)
        gm.level = 200       # ~78%
        gm.apply(128)        # → 128 * 200/255 ≈ 100
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._level: int = 255

    @property
    def level(self) -> int:
        return self._level

    @level.setter
    def level(self, value: int) -> None:
        self._level = max(0, min(255, int(value)))
        self._bus.emit("grandmaster.changed", level=self._level)
        logger.debug("Grand Master : %d (%.0f%%)", self._level, self._level / 255 * 100)

    @property
    def ratio(self) -> float:
        """Retourne le ratio Grand Master entre 0.0 et 1.0."""
        return self._level / 255.0

    def apply(self, value: int) -> int:
        """Applique le Grand Master à une valeur DMX."""
        return int(value * self._level / 255)

    def blackout(self) -> None:
        """Met le Grand Master à zéro."""
        self.level = 0

    def full(self) -> None:
        """Met le Grand Master à fond."""
        self.level = 255