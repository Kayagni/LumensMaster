"""
Module Circuits : gestion directe des niveaux par circuit et groupes.

Permet de piloter individuellement chaque circuit depuis la vue circuits.
Les niveaux définis ici participent au calcul HTP avec les faders et
le séquenceur.

Groupes :
    - Un groupe est un ensemble nommé de circuits
    - Un circuit peut appartenir à plusieurs groupes
    - Les groupes ont un ordre d'affichage modifiable
    - Les groupes peuvent être réduits (collapsed) ou développés
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from lumensmaster.core.events import EventBus

logger = logging.getLogger(__name__)

MAX_CIRCUITS = 512


@dataclass
class CircuitGroup:
    """Un groupe nommé de circuits."""
    name: str
    circuits: list[int] = field(default_factory=list)
    collapsed: bool = False

    def add_circuits(self, new_circuits: list[int]) -> None:
        """Ajoute des circuits au groupe (sans doublons)."""
        for c in new_circuits:
            if c not in self.circuits and 1 <= c <= MAX_CIRCUITS:
                self.circuits.append(c)
        self.circuits.sort()

    def remove_circuits(self, to_remove: list[int]) -> None:
        """Retire des circuits du groupe."""
        self.circuits = [c for c in self.circuits if c not in to_remove]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "circuits": self.circuits,
            "collapsed": self.collapsed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CircuitGroup":
        return cls(
            name=data.get("name", "Sans nom"),
            circuits=data.get("circuits", []),
            collapsed=data.get("collapsed", False),
        )


class Circuits:
    """
    Gestionnaire de niveaux directs par circuit et de groupes.

    Les niveaux circuits sont indépendants des faders. Au moment du calcul
    DMX, les deux sont combinés en HTP (Highest Takes Precedence).
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._levels: dict[int, int] = {}
        self._selection: set[int] = set()
        self._last_selected: int = 0
        self._display_percent: bool = False

        # Groupes : liste ordonnée (l'ordre = ordre d'affichage)
        self._groups: list[CircuitGroup] = []

    @property
    def display_percent(self) -> bool:
        return self._display_percent

    @display_percent.setter
    def display_percent(self, value: bool) -> None:
        self._display_percent = value
        self._bus.emit("circuits.display_mode_changed", percent=value)

    @property
    def selection(self) -> set[int]:
        return set(self._selection)

    @property
    def groups(self) -> list[CircuitGroup]:
        return self._groups

    # --- Niveaux ---

    def get_level(self, circuit: int) -> int:
        return self._levels.get(circuit, 0)

    def set_level(self, circuit: int, value: int) -> None:
        if not 1 <= circuit <= MAX_CIRCUITS:
            return
        value = max(0, min(255, int(value)))
        if value == 0:
            self._levels.pop(circuit, None)
        else:
            self._levels[circuit] = value
        self._bus.emit("circuit.changed", circuit=circuit, level=value)

    def set_selected_level(self, value: int) -> None:
        value = max(0, min(255, int(value)))
        for circuit in self._selection:
            if value == 0:
                self._levels.pop(circuit, None)
            else:
                self._levels[circuit] = value
            self._bus.emit("circuit.changed", circuit=circuit, level=value)

    def nudge_selected(self, delta: int) -> None:
        for circuit in self._selection:
            current = self._levels.get(circuit, 0)
            new_value = max(0, min(255, current + delta))
            if new_value == 0:
                self._levels.pop(circuit, None)
            else:
                self._levels[circuit] = new_value
            self._bus.emit("circuit.changed", circuit=circuit, level=new_value)

    def clear_all(self) -> None:
        for circuit in list(self._levels.keys()):
            self._bus.emit("circuit.changed", circuit=circuit, level=0)
        self._levels.clear()

    def clear_selected(self) -> None:
        for circuit in self._selection:
            self._levels.pop(circuit, None)
            self._bus.emit("circuit.changed", circuit=circuit, level=0)

    # --- Sélection ---

    def select(self, circuit: int) -> None:
        if not 1 <= circuit <= MAX_CIRCUITS:
            return
        self._selection = {circuit}
        self._last_selected = circuit
        self._bus.emit("circuits.selection_changed", selection=self.selection)

    def select_add(self, circuit: int) -> None:
        if not 1 <= circuit <= MAX_CIRCUITS:
            return
        if circuit in self._selection:
            self._selection.discard(circuit)
        else:
            self._selection.add(circuit)
        self._last_selected = circuit
        self._bus.emit("circuits.selection_changed", selection=self.selection)

    def select_range(self, circuit: int) -> None:
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
        self._selection.clear()
        self._last_selected = 0
        self._bus.emit("circuits.selection_changed", selection=self.selection)

    def is_selected(self, circuit: int) -> bool:
        return circuit in self._selection

    # --- Groupes ---

    def create_group(self, name: str, circuits: list[int] | None = None) -> CircuitGroup:
        """
        Crée un nouveau groupe.

        Args:
            name: Nom du groupe.
            circuits: Liste de circuits initiaux. Si None, utilise la sélection.
        """
        if circuits is None:
            circuits = sorted(self._selection)

        group = CircuitGroup(name=name)
        group.add_circuits(circuits)
        self._groups.append(group)

        self._bus.emit("groups.changed")
        logger.info("Groupe créé : '%s' (%d circuits)", name, len(group.circuits))
        return group

    def delete_group(self, group_name: str) -> bool:
        """Supprime un groupe par son nom."""
        for i, group in enumerate(self._groups):
            if group.name == group_name:
                self._groups.pop(i)
                self._bus.emit("groups.changed")
                logger.info("Groupe supprimé : '%s'", group_name)
                return True
        return False

    def get_group(self, group_name: str) -> CircuitGroup | None:
        """Retourne un groupe par son nom."""
        for group in self._groups:
            if group.name == group_name:
                return group
        return None

    def get_group_names(self) -> list[str]:
        """Retourne la liste des noms de groupes dans l'ordre d'affichage."""
        return [g.name for g in self._groups]

    def add_to_group(self, group_name: str, circuits: list[int] | None = None) -> bool:
        """
        Ajoute des circuits à un groupe existant.

        Args:
            group_name: Nom du groupe cible.
            circuits: Circuits à ajouter. Si None, utilise la sélection.
        """
        group = self.get_group(group_name)
        if group is None:
            return False

        if circuits is None:
            circuits = sorted(self._selection)

        group.add_circuits(circuits)
        self._bus.emit("groups.changed")
        logger.info(
            "Ajout de %d circuits au groupe '%s' (total: %d)",
            len(circuits), group_name, len(group.circuits),
        )
        return True

    def remove_from_group(self, group_name: str, circuits: list[int] | None = None) -> bool:
        """
        Retire des circuits d'un groupe.

        Args:
            group_name: Nom du groupe cible.
            circuits: Circuits à retirer. Si None, utilise la sélection.
        """
        group = self.get_group(group_name)
        if group is None:
            return False

        if circuits is None:
            circuits = sorted(self._selection)

        group.remove_circuits(circuits)
        self._bus.emit("groups.changed")
        logger.info(
            "Retrait de %d circuits du groupe '%s' (restant: %d)",
            len(circuits), group_name, len(group.circuits),
        )
        return True

    def move_group(self, group_name: str, direction: int) -> bool:
        """
        Déplace un groupe dans l'ordre d'affichage.

        Args:
            group_name: Nom du groupe à déplacer.
            direction: -1 pour monter, +1 pour descendre.
        """
        for i, group in enumerate(self._groups):
            if group.name == group_name:
                new_index = i + direction
                if 0 <= new_index < len(self._groups):
                    self._groups.pop(i)
                    self._groups.insert(new_index, group)
                    self._bus.emit("groups.changed")
                    return True
                return False
        return False

    def toggle_group_collapsed(self, group_name: str) -> bool:
        """Bascule l'état réduit/développé d'un groupe."""
        group = self.get_group(group_name)
        if group is None:
            return False
        group.collapsed = not group.collapsed
        self._bus.emit("groups.changed")
        return True

    # --- Snapshot / Enregistrement ---

    def get_active_snapshot(self) -> dict[int, int]:
        return dict(self._levels)

    def get_output(self) -> dict[int, int]:
        return dict(self._levels)

    # --- Conversion affichage ---

    @staticmethod
    def dmx_to_percent(value: int) -> int:
        return round(value * 100 / 255)

    @staticmethod
    def percent_to_dmx(percent: int) -> int:
        return round(percent * 255 / 100)

    def format_value(self, value: int) -> str:
        if self._display_percent:
            return f"{self.dmx_to_percent(value)}%"
        return str(value)

    def parse_input(self, text: str) -> int | None:
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
        return {
            "levels": {str(k): v for k, v in self._levels.items()},
            "groups": [g.to_dict() for g in self._groups],
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        self._levels.clear()
        self._selection.clear()
        self._groups.clear()

        # Charger les niveaux
        levels_data = data.get("levels", data)
        if isinstance(levels_data, dict):
            for circuit_str, value in levels_data.items():
                if circuit_str == "groups":
                    continue
                try:
                    circuit = int(circuit_str)
                    if 1 <= circuit <= MAX_CIRCUITS:
                        self._levels[circuit] = max(0, min(255, int(value)))
                except (ValueError, TypeError):
                    pass

        # Charger les groupes
        for group_data in data.get("groups", []):
            try:
                self._groups.append(CircuitGroup.from_dict(group_data))
            except Exception:
                logger.warning("Données de groupe invalides ignorées")