"""
lumensmaster/modules/trichromie.py
Gestion de la trichromie et quadrichromie (RGB / RGBA).

Concepts :
    - ColorGroup : lie des circuits DMX à des rôles couleur (R, G, B, A)
      Exemple : PAR LED jardin → R=circuit 1, G=circuit 2, B=circuit 3, A=circuit 4
    - ColorPreset : couleur nommée réutilisable (ex: "Bleu nuit", "Ambre chaud")
    - ColorManager : gestionnaire central des groupes et presets

Utilisation :
    manager = ColorManager(bus)
    manager.set_circuit_callback(engine._set_circuit_for_color)

    # Créer un groupe couleur
    group = ColorGroup(name="PAR LED 1", red=1, green=2, blue=3, amber=4)
    manager.add_group(group)

    # Appliquer une couleur
    manager.apply_color("PAR LED 1", rgba=(0, 100, 255, 0))

    # Presets
    manager.add_preset(ColorPreset("Bleu nuit", 0, 40, 200, 0))
    manager.apply_preset("PAR LED 1", "Bleu nuit")
"""

from __future__ import annotations

import colorsys
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from lumensmaster.core.events import EventBus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Presets par défaut chargés à chaque démarrage
# ---------------------------------------------------------------------------
DEFAULT_PRESETS: list[tuple[str, int, int, int, int]] = [
    ("Rouge",       255,   0,   0,   0),
    ("Vert",          0, 255,   0,   0),
    ("Bleu",          0,   0, 255,   0),
    ("Cyan",          0, 255, 255,   0),
    ("Magenta",     255,   0, 255,   0),
    ("Jaune",       255, 255,   0,   0),
    ("Blanc",       255, 255, 255,   0),
    ("Ambre chaud", 255, 160,  20, 200),
    ("Blanc chaud", 255, 200, 140, 180),
    ("Bleu nuit",     0,  20, 120,   0),
    ("Lavande",     140,  80, 255,   0),
    ("Orange",      255, 100,   0,  80),
    ("Noir",          0,   0,   0,   0),
]


# ---------------------------------------------------------------------------
# ColorGroup
# ---------------------------------------------------------------------------

@dataclass
class ColorGroup:
    """
    Association entre des circuits DMX et des rôles couleur.

    Chaque rôle (red, green, blue, amber) pointe vers un numéro de circuit.
    Un circuit à 0 signifie que le rôle n'est pas assigné.
    """
    name:  str = ""
    red:   int = 0   # Numéro de circuit (0 = non assigné)
    green: int = 0
    blue:  int = 0
    amber: int = 0   # 0 = mode RGB uniquement
    auto_amber: bool = True   # ambre calculé automatiquement depuis RGB

    @property
    def mode(self) -> str:
        """'rgba' si l'ambre est assigné, sinon 'rgb'."""
        return "rgba" if self.amber > 0 else "rgb"

    @property
    def is_valid(self) -> bool:
        """Un groupe est valide s'il a au moins R, G et B assignés."""
        return self.red > 0 and self.green > 0 and self.blue > 0

    @property
    def circuits(self) -> list[int]:
        """Liste des numéros de circuits assignés (non nuls)."""
        return [c for c in (self.red, self.green, self.blue, self.amber) if c > 0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":  self.name,
            "red":   self.red,
            "green": self.green,
            "blue":  self.blue,
            "amber": self.amber,
            "auto_amber": self.auto_amber,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ColorGroup":
        return cls(
            name=data.get("name", ""),
            red=data.get("red",   0),
            green=data.get("green", 0),
            blue=data.get("blue",  0),
            amber=data.get("amber", 0),
            auto_amber=data.get("auto_amber", True),
        )


# ---------------------------------------------------------------------------
# ColorPreset
# ---------------------------------------------------------------------------

@dataclass
class ColorPreset:
    """Couleur nommée réutilisable."""
    name: str = ""
    r:    int = 0
    g:    int = 0
    b:    int = 0
    a:    int = 0   # Ambre

    @property
    def rgba(self) -> tuple[int, int, int, int]:
        return (self.r, self.g, self.b, self.a)

    @rgba.setter
    def rgba(self, value: tuple[int, int, int, int]) -> None:
        self.r, self.g, self.b, self.a = value

    @property
    def rgb_normalized(self) -> tuple[float, float, float]:
        """RGB normalisé 0.0-1.0 pour Dear PyGui (couleur de bouton)."""
        return (self.r / 255.0, self.g / 255.0, self.b / 255.0)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "r": self.r, "g": self.g, "b": self.b, "a": self.a}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ColorPreset":
        return cls(
            name=data.get("name", ""),
            r=data.get("r", 0),
            g=data.get("g", 0),
            b=data.get("b", 0),
            a=data.get("a", 0),
        )


# ---------------------------------------------------------------------------
# ColorManager
# ---------------------------------------------------------------------------

class ColorManager:
    """
    Gestionnaire central des groupes couleur et presets.

    Rôle :
        - Maintient la liste des ColorGroups (assignation manuelle ou auto via fixtures)
        - Maintient la palette de presets (défauts + utilisateur)
        - Applique une couleur RGBA sur un groupe → appelle le callback circuit
        - Émet des événements bus pour notifier l'UI
        - Sérialise/désérialise pour le save/load du show
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._groups:         dict[str, ColorGroup]  = {}
        self._presets:        dict[str, ColorPreset] = {}
        self._current_colors: dict[str, tuple[int, int, int, int]] = {}

        # Callback pour écrire dans les circuits (injecté depuis Engine)
        self._circuit_callback: Optional[Callable[[int, int], None]] = None

        self._load_default_presets()

    # --- Callback circuit ---

    def set_circuit_callback(self, callback: Callable[[int, int], None]) -> None:
        """Injecte la fonction Engine._set_circuit_for_color(circuit, value)."""
        self._circuit_callback = callback

    # --- Groupes ---

    def add_group(self, group: ColorGroup) -> None:
        """Ajoute ou remplace un groupe couleur."""
        if not group.name:
            raise ValueError("Un ColorGroup doit avoir un nom.")
        self._groups[group.name] = group
        self._bus.emit("color.groups_changed")
        logger.info("Groupe couleur ajouté : '%s'", group.name)

    def remove_group(self, name: str) -> None:
        """Supprime un groupe couleur par son nom."""
        if name in self._groups:
            del self._groups[name]
            self._current_colors.pop(name, None)
            self._bus.emit("color.groups_changed")
            logger.info("Groupe couleur supprimé : '%s'", name)

    def get_group(self, name: str) -> Optional[ColorGroup]:
        return self._groups.get(name)

    def get_group_names(self) -> list[str]:
        return list(self._groups.keys())

    def rename_group(self, old_name: str, new_name: str) -> None:
        """Renomme un groupe en préservant son état couleur."""
        if old_name not in self._groups:
            return
        if not new_name or new_name == old_name:
            return
        group = self._groups.pop(old_name)
        group.name = new_name
        color = self._current_colors.pop(old_name, (0, 0, 0, 0))
        self._groups[new_name] = group
        self._current_colors[new_name] = color
        self._bus.emit("color.groups_changed")

    # --- Application couleur ---

    def apply_color(self, group_name: str,
                    rgba: tuple[int, int, int, int]) -> bool:
        """
        Applique une couleur RGBA à un groupe.

        Convertit et envoie les valeurs aux circuits DMX via le callback.
        En mode RGB (sans ambre), la valeur ambre est ignorée.
        Retourne True si l'application a réussi.
        """
        group = self._groups.get(group_name)
        if not group or not group.is_valid:
            logger.warning("Groupe invalide ou introuvable : '%s'", group_name)
            return False

        r, g, b, a = rgba

        # Clamp 0-255
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        a = max(0, min(255, a))

        self._current_colors[group_name] = (r, g, b, a)

        if self._circuit_callback:
            self._circuit_callback(group.red,   r)
            self._circuit_callback(group.green, g)
            self._circuit_callback(group.blue,  b)
            if group.amber > 0:
                amber_value = self.rgb_to_amber(r, g, b) if group.auto_amber else a
                self._current_colors[group_name] = (r, g, b, amber_value)
                self._circuit_callback(group.amber, amber_value)

        self._bus.emit("color.applied", group_name=group_name, rgba=(r, g, b, a))
        return True

    def get_current_color(self, group_name: str) -> tuple[int, int, int, int]:
        """Retourne la dernière couleur appliquée à un groupe (0,0,0,0 si aucune)."""
        return self._current_colors.get(group_name, (0, 0, 0, 0))

    # --- Presets ---

    def _load_default_presets(self) -> None:
        for name, r, g, b, a in DEFAULT_PRESETS:
            self._presets[name] = ColorPreset(name=name, r=r, g=g, b=b, a=a)

    def add_preset(self, preset: ColorPreset) -> None:
        """Ajoute ou met à jour un preset utilisateur."""
        if not preset.name:
            raise ValueError("Un ColorPreset doit avoir un nom.")
        self._presets[preset.name] = preset
        self._bus.emit("color.presets_changed")
        logger.info("Preset couleur enregistré : '%s'", preset.name)

    def remove_preset(self, name: str) -> None:
        """Supprime un preset (ne peut pas supprimer les défauts)."""
        default_names = {p[0] for p in DEFAULT_PRESETS}
        if name in default_names:
            logger.warning("Impossible de supprimer un preset par défaut : '%s'", name)
            return
        if name in self._presets:
            del self._presets[name]
            self._bus.emit("color.presets_changed")

    def get_preset(self, name: str) -> Optional[ColorPreset]:
        return self._presets.get(name)

    def get_preset_names(self) -> list[str]:
        return list(self._presets.keys())

    def apply_preset(self, group_name: str, preset_name: str) -> bool:
        """Applique un preset couleur à un groupe."""
        preset = self._presets.get(preset_name)
        if not preset:
            logger.warning("Preset introuvable : '%s'", preset_name)
            return False
        return self.apply_color(group_name, preset.rgba)

    def save_current_as_preset(self, group_name: str, preset_name: str) -> bool:
        """Enregistre la couleur courante d'un groupe comme nouveau preset."""
        if group_name not in self._current_colors:
            return False
        r, g, b, a = self._current_colors[group_name]
        preset = ColorPreset(name=preset_name, r=r, g=g, b=b, a=a)
        self.add_preset(preset)
        return True

    # --- Conversions ---

    @staticmethod
    def rgb_to_hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
        """RGB (0-255) → HSV (H: 0-360, S: 0-100, V: 0-100)."""
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        return (h * 360.0, s * 100.0, v * 100.0)

    @staticmethod
    def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
        """HSV (H: 0-360, S: 0-100, V: 0-100) → RGB (0-255)."""
        r, g, b = colorsys.hsv_to_rgb(h / 360.0, s / 100.0, v / 100.0)
        return (int(r * 255), int(g * 255), int(b * 255))

    @staticmethod
    def rgb_to_dpg(r: int, g: int, b: int, a: int = 255
                   ) -> tuple[float, float, float, float]:
        """Convertit RGBA (0-255) en valeurs normalisées pour dpg (0.0-1.0)."""
        return (r / 255.0, g / 255.0, b / 255.0, a / 255.0)
    
    @staticmethod
    def rgb_to_amber(r: int, g: int, b: int) -> int:
        _, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        return int(255 * (1.0 - s) * v)

    # --- Sérialisation ---

    def to_dict(self) -> dict[str, Any]:
        default_names = {p[0] for p in DEFAULT_PRESETS}
        return {
            "groups": {
                name: group.to_dict()
                for name, group in self._groups.items()
            },
            # On ne sauve que les presets utilisateur (les défauts sont rechargés)
            "presets": {
                name: preset.to_dict()
                for name, preset in self._presets.items()
                if name not in default_names
            },
            "current_colors": {
                name: list(rgba)
                for name, rgba in self._current_colors.items()
            },
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        self._groups.clear()
        self._current_colors.clear()
        self._presets.clear()
        self._load_default_presets()

        for name, group_data in data.get("groups", {}).items():
            try:
                self._groups[name] = ColorGroup.from_dict(group_data)
            except Exception as exc:
                logger.error("Erreur chargement ColorGroup '%s': %s", name, exc)

        for name, preset_data in data.get("presets", {}).items():
            try:
                self._presets[name] = ColorPreset.from_dict(preset_data)
            except Exception as exc:
                logger.error("Erreur chargement ColorPreset '%s': %s", name, exc)

        for name, rgba in data.get("current_colors", {}).items():
            if isinstance(rgba, list) and len(rgba) == 4:
                self._current_colors[name] = tuple(rgba)