"""
Module Circuits : gestion directe des niveaux par circuit.

Permet de piloter individuellement chaque circuit depuis la vue circuits.
Les niveaux définis ici participent au calcul HTP avec les faders et
le séquenceur.

Fonctionnalités :
    - Sélection simple, multiple (Ctrl), plage (Shift)
    - Saisie de valeur au clavier (DMX 0-255 ou pourcentage 0-100%)
    - Pilotage à la molette (+1/-1)
    - Enregistrement de l'état courant sur un fader
"""

from __future__ import annotations

import logging
from typing import Any

from lumensmaster.core.events import EventBus

logger = logging.getLogger(__name__)

MAX_CIRCUITS = 512


class Circuits:
    """
    Gestionnaire de niveaux directs par circuit.

    Les niveaux circuits sont indépendants des faders. Au moment du calcul
    DMX, les deux sont combinés en HTP (Highest Takes Precedence).

    Utilisation :
        circuits = Circuits(bus)

        # Sélectionner des circuits
        circuits.select(1)
        circuits.select_add(5)
        circuits.select_range(1, 10)

        # Piloter
        circuits.set_level(1, 200)
        circuits.set_selected_level(255)
        circuits.nudge_selected(5)      # +5
        circuits.nudge_selected(-1)     # -1

        # Enregistrer l'état
        snapshot = circuits.get_active_snapshot()
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        # Niveaux directs : {circuit: valeur 0-255}
        self._levels: dict[int, int] = {}
        # Sélection : ensemble de circuits sélectionnés
        self._selection: set[int] = set()
        # Dernier circuit sélectionné (pour shift+clic)
        self._last_selected: int = 0
        # Mode d'affichage : True = pourcentage, False = DMX brut
        self._display_percent: bool = False

    @property
    def display_percent(self) -> bool:
        return self._display_percent

    @display_percent.setter
    def display_percent(self, value: bool) -> None:
        self._display_percent = value
        self._bus.emit("circuits.display_mode_changed", percent=value)

    @property
    def selection(self) -> set[int]:
        """Retourne une copie de la sélection courante."""
        return set(self._selection)

    # --- Niveaux ---

    def get_level(self, circuit: int) -> int:
        """Retourne le niveau direct d'un circuit (0-255)."""
        return self._levels.get(circuit, 0)

    def set_level(self, circuit: int, value: int) -> None:
        """Définit le niveau direct d'un circuit."""
        if not 1 <= circuit <= MAX_CIRCUITS:
            return
        value = max(0, min(255, int(value)))
        if value == 0:
            self._levels.pop(circuit, None)
        else:
            self._levels[circuit] = value
        self._bus.emit("circuit.changed", circuit=circuit, level=value)

    def set_selected_level(self, value: int) -> None:
        """Définit le niveau de tous les circuits sélectionnés."""
        value = max(0, min(255, int(value)))
        for circuit in self._selection:
            if value == 0:
                self._levels.pop(circuit, None)
            else:
                self._levels[circuit] = value
            self._bus.emit("circuit.changed", circuit=circuit, level=value)

    def nudge_selected(self, delta: int) -> None:
        """
        Incrémente/décrémente les circuits sélectionnés.

        Args:
            delta: Valeur à ajouter (positive ou négative).
        """
        for circuit in self._selection:
            current = self._levels.get(circuit, 0)
            new_value = max(0, min(255, current + delta))
            if new_value == 0:
                self._levels.pop(circuit, None)
            else:
                self._levels[circuit] = new_value
            self._bus.emit("circuit.changed", circuit=circuit, level=new_value)

    def clear_all(self) -> None:
        """Remet tous les circuits à zéro."""
        for circuit in list(self._levels.keys()):
            self._bus.emit("circuit.changed", circuit=circuit, level=0)
        self._levels.clear()

    def clear_selected(self) -> None:
        """Remet les circuits sélectionnés à zéro."""
        for circuit in self._selection:
            self._levels.pop(circuit, None)
            self._bus.emit("circuit.changed", circuit=circuit, level=0)

    # --- Sélection ---

    def select(self, circuit: int) -> None:
        """Sélectionne un seul circuit (désélectionne les autres)."""
        if not 1 <= circuit <= MAX_CIRCUITS:
            return
        self._selection = {circuit}
        self._last_selected = circuit
        self._bus.emit("circuits.selection_changed", selection=self.selection)

    def select_add(self, circuit: int) -> None:
        """Ajoute ou retire un circuit de la sélection (Ctrl+clic)."""
        if not 1 <= circuit <= MAX_CIRCUITS:
            return
        if circuit in self._selection:
            self._selection.discard(circuit)
        else:
            self._selection.add(circuit)
        self._last_selected = circuit
        self._bus.emit("circuits.selection_changed", selection=self.selection)

    def select_range(self, circuit: int) -> None:
        """Sélectionne une plage depuis le dernier circuit sélectionné (Shift+clic)."""
        if not 1 <= circuit <= MAX_CIRCUITS:
            return
        if self._last_selected == 0:
            self.select(circuit)
            return
        start = min(self._last_selected, circuit)
        end = max(self._last_selected, circuit)
        for c in range(start, end + 1):
            self._selection.add(c)
        self._bus.emit("circuits.selection_changed", selection=self.selection)

    def select_none(self) -> None:
        """Désélectionne tous les circuits."""
        self._selection.clear()
        self._last_selected = 0
        self._bus.emit("circuits.selection_changed", selection=self.selection)

    def is_selected(self, circuit: int) -> bool:
        """Vérifie si un circuit est sélectionné."""
        return circuit in self._selection

    # --- Snapshot / Enregistrement ---

    def get_active_snapshot(self) -> dict[int, int]:
        """
        Retourne un snapshot de tous les circuits ayant une valeur > 0.
        Utilisé pour enregistrer l'état courant sur un fader ou dans le séquenceur.
        """
        return dict(self._levels)

    def get_output(self) -> dict[int, int]:
        """
        Retourne les niveaux directs pour le calcul HTP.
        Identique à get_active_snapshot mais sémantiquement distinct.
        """
        return dict(self._levels)

    # --- Conversion affichage ---

    @staticmethod
    def dmx_to_percent(value: int) -> int:
        """Convertit une valeur DMX (0-255) en pourcentage (0-100)."""
        return round(value * 100 / 255)

    @staticmethod
    def percent_to_dmx(percent: int) -> int:
        """Convertit un pourcentage (0-100) en valeur DMX (0-255)."""
        return round(percent * 255 / 100)

    def format_value(self, value: int) -> str:
        """Formate une valeur selon le mode d'affichage courant."""
        if self._display_percent:
            return f"{self.dmx_to_percent(value)}%"
        return str(value)

    def parse_input(self, text: str) -> int | None:
        """
        Parse une saisie utilisateur en valeur DMX.
        Accepte "50%" ou "128" selon le mode.

        Returns:
            Valeur DMX (0-255) ou None si invalide.
        """
        text = text.strip()
        if not text:
            return None

        try:
            if text.endswith("%"):
                percent = int(text[:-1])
                if 0 <= percent <= 100:
                    return self.percent_to_dmx(percent)
                return None
            else:
                value = int(text)
                if self._display_percent:
                    # En mode %, une saisie sans % est interprétée comme %
                    if 0 <= value <= 100:
                        return self.percent_to_dmx(value)
                    return None
                else:
                    if 0 <= value <= 255:
                        return value
                    return None
        except ValueError:
            return None

    # --- Sérialisation ---

    def to_dict(self) -> dict[str, Any]:
        """Sérialise pour sauvegarde."""
        return {str(k): v for k, v in self._levels.items()}

    def from_dict(self, data: dict[str, Any]) -> None:
        """Restaure depuis des données sauvegardées."""
        self._levels.clear()
        self._selection.clear()
        for circuit_str, value in data.items():
            try:
                circuit = int(circuit_str)
                if 1 <= circuit <= MAX_CIRCUITS:
                    self._levels[circuit] = max(0, min(255, int(value)))
            except (ValueError, TypeError):
                pass