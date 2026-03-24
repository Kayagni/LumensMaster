"""
Module de patch : correspondance entre circuits logiques et canaux DMX.

Un circuit est un canal logique manipulé par l'utilisateur (faders, cues, etc.).
Le patch définit sur quel(s) canal(aux) DMX physique(s) chaque circuit agit.

Par défaut, le patch est en mode 1:1 : circuit N → canal DMX N.
"""

from __future__ import annotations

import logging
from typing import Any

from lumensmaster.core.events import EventBus

logger = logging.getLogger(__name__)

MAX_CIRCUITS = 512


class Patch:
    """
    Gestion du patch circuits → canaux DMX.
    
    Utilisation :
        patch = Patch(bus)
        
        # Par défaut, circuit 1 → DMX 1 (1:1)
        patch.get_dmx_channel(1)  # → 1
        
        # Remapper circuit 5 vers DMX 20
        patch.map(5, 20)
        patch.get_dmx_channel(5)  # → 20
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        # Par défaut : mapping 1:1 (circuit N → DMX N)
        self._mapping: dict[int, int] = {}

    def map(self, circuit: int, dmx_channel: int) -> None:
        """
        Assigne un circuit à un canal DMX.
        
        Args:
            circuit: Numéro de circuit logique (1-512)
            dmx_channel: Numéro de canal DMX physique (1-512)
        """
        if not (1 <= circuit <= MAX_CIRCUITS and 1 <= dmx_channel <= MAX_CIRCUITS):
            logger.warning("Patch invalide : circuit %d → DMX %d", circuit, dmx_channel)
            return

        self._mapping[circuit] = dmx_channel
        self._bus.emit("patch.updated", circuit=circuit, dmx_channel=dmx_channel)
        logger.debug("Patch : circuit %d → DMX %d", circuit, dmx_channel)

    def unmap(self, circuit: int) -> None:
        """Supprime le mapping d'un circuit (revient au 1:1)."""
        self._mapping.pop(circuit, None)
        self._bus.emit("patch.updated", circuit=circuit, dmx_channel=circuit)

    def get_dmx_channel(self, circuit: int) -> int:
        """
        Retourne le canal DMX associé à un circuit.
        Si pas de mapping explicite, retourne le circuit lui-même (1:1).
        """
        return self._mapping.get(circuit, circuit)

    def get_all_mappings(self) -> dict[int, int]:
        """Retourne uniquement les mappings explicites (non-1:1)."""
        return dict(self._mapping)

    def clear(self) -> None:
        """Réinitialise le patch en mode 1:1."""
        self._mapping.clear()
        self._bus.emit("patch.updated", circuit=0, dmx_channel=0)
        logger.info("Patch réinitialisé (mode 1:1)")

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le patch pour sauvegarde."""
        return {str(k): v for k, v in self._mapping.items()}

    def from_dict(self, data: dict[str, Any]) -> None:
        """Restaure le patch depuis des données sauvegardées."""
        self._mapping.clear()
        for circuit_str, dmx_channel in data.items():
            try:
                self._mapping[int(circuit_str)] = int(dmx_channel)
            except (ValueError, TypeError):
                logger.warning("Entrée de patch invalide ignorée : %s → %s", circuit_str, dmx_channel)