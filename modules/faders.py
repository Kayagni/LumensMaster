"""
Module Faders (submasters).

Chaque fader possède :
    - Un niveau (0-255) contrôlé par un slider
    - Un contenu : ensemble de circuits avec leurs valeurs préenregistrées
    - Un label optionnel

La contribution d'un fader à un circuit = (fader_level / 255) × circuit_value.

Les faders se combinent en HTP (Highest Takes Precedence) :
pour chaque circuit, c'est la valeur la plus haute parmi tous les faders actifs
qui est retenue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from lumensmaster.core.events import EventBus

logger = logging.getLogger(__name__)


@dataclass
class FaderState:
    """État d'un fader individuel."""
    level: int = 0
    label: str = ""
    # Contenu : {numéro_circuit: valeur_enregistrée (0-255)}
    contents: dict[int, int] = field(default_factory=dict)

    def get_output(self, circuit: int) -> int:
        """
        Calcule la sortie de ce fader pour un circuit donné.
        
        Returns:
            Valeur proportionnelle au niveau du fader (0-255).
        """
        if circuit not in self.contents or self.level == 0:
            return 0
        return int(self.contents[circuit] * self.level / 255)


class Faders:
    """
    Gestionnaire de faders (submasters).
    
    Utilisation :
        faders = Faders(bus, count=24)
        
        # Enregistrer un contenu sur le fader 1
        faders.set_contents(1, {1: 255, 2: 128, 5: 200})
        
        # Monter le fader 1 à fond
        faders.set_level(1, 255)
        
        # Calculer la sortie combinée HTP
        output = faders.compute_htp()
        # → {1: 255, 2: 128, 5: 200}
    """

    def __init__(self, bus: EventBus, count: int = 24) -> None:
        self._bus = bus
        self._count = count
        self._faders: dict[int, FaderState] = {
            i: FaderState() for i in range(1, count + 1)
        }

    @property
    def count(self) -> int:
        return self._count

    def get_fader(self, fader_id: int) -> FaderState | None:
        """Retourne l'état d'un fader."""
        return self._faders.get(fader_id)

    def set_level(self, fader_id: int, level: int) -> None:
        """
        Change le niveau d'un fader.
        
        Args:
            fader_id: Identifiant du fader (1-N)
            level: Niveau (0-255)
        """
        fader = self._faders.get(fader_id)
        if fader is None:
            return

        fader.level = max(0, min(255, int(level)))
        self._bus.emit(
            "fader.changed",
            fader_id=fader_id,
            level=fader.level,
        )

    def get_level(self, fader_id: int) -> int:
        """Retourne le niveau d'un fader."""
        fader = self._faders.get(fader_id)
        return fader.level if fader else 0

    def set_label(self, fader_id: int, label: str) -> None:
        """Définit le label d'un fader."""
        fader = self._faders.get(fader_id)
        if fader:
            fader.label = label

    def set_contents(self, fader_id: int, contents: dict[int, int]) -> None:
        """
        Enregistre un contenu (circuits + niveaux) sur un fader.
        
        Args:
            fader_id: Identifiant du fader
            contents: {numéro_circuit: valeur (0-255)}
        """
        fader = self._faders.get(fader_id)
        if fader is None:
            return

        fader.contents = {
            int(k): max(0, min(255, int(v)))
            for k, v in contents.items()
        }
        logger.debug(
            "Fader %d : contenu enregistré (%d circuits)",
            fader_id,
            len(fader.contents),
        )

    def clear_contents(self, fader_id: int) -> None:
        """Vide le contenu d'un fader."""
        fader = self._faders.get(fader_id)
        if fader:
            fader.contents.clear()
            fader.level = 0

    def compute_htp(self) -> dict[int, int]:
        """
        Calcule la sortie combinée de tous les faders en HTP.
        
        Pour chaque circuit, retourne la valeur la plus haute
        parmi tous les faders actifs.
        
        Returns:
            {numéro_circuit: valeur_max (0-255)}
        """
        output: dict[int, int] = {}

        for fader in self._faders.values():
            if fader.level == 0:
                continue

            for circuit in fader.contents:
                value = fader.get_output(circuit)
                if value > output.get(circuit, 0):
                    output[circuit] = value

        return output

    def all_down(self) -> None:
        """Met tous les faders à zéro."""
        for fader_id in self._faders:
            self.set_level(fader_id, 0)

    def to_dict(self) -> dict[str, Any]:
        """Sérialise les faders pour sauvegarde."""
        result = {}
        for fader_id, fader in self._faders.items():
            if fader.contents or fader.label:
                result[str(fader_id)] = {
                    "label": fader.label,
                    "contents": {str(k): v for k, v in fader.contents.items()},
                }
        return result

    def from_dict(self, data: dict[str, Any]) -> None:
        """Restaure les faders depuis des données sauvegardées."""
        # Reset all faders
        for fader in self._faders.values():
            fader.level = 0
            fader.label = ""
            fader.contents.clear()

        for fader_id_str, fader_data in data.items():
            try:
                fader_id = int(fader_id_str)
                fader = self._faders.get(fader_id)
                if fader is None:
                    continue

                fader.label = fader_data.get("label", "")
                contents_raw = fader_data.get("contents", {})
                fader.contents = {
                    int(k): max(0, min(255, int(v)))
                    for k, v in contents_raw.items()
                }
            except (ValueError, TypeError, AttributeError):
                logger.warning("Données de fader invalides ignorées : %s", fader_id_str)