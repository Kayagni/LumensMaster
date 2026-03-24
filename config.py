"""
Configuration globale de LumensMaster.

Centralise les paramètres de l'application : DMX, MIDI, UI, etc.
Les valeurs par défaut peuvent être surchargées par un fichier de config.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "lumensmaster_config.json"


@dataclass
class DMXConfig:
    """Configuration de la sortie DMX."""
    port: str = ""
    fps: int = 40
    universe_size: int = 512


@dataclass
class UIConfig:
    """Configuration de l'interface graphique."""
    width: int = 1400
    height: int = 900
    fader_count: int = 24  # Nombre de faders affichés


@dataclass
class AppConfig:
    """Configuration principale de l'application."""
    dmx: DMXConfig = field(default_factory=DMXConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    last_show_path: str = ""

    def save(self, path: Path | None = None) -> None:
        """Sauvegarde la configuration dans un fichier JSON."""
        filepath = path or Path(CONFIG_FILENAME)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2, ensure_ascii=False)
            logger.info("Configuration sauvegardée : %s", filepath)
        except Exception:
            logger.exception("Erreur de sauvegarde de la configuration")

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """Charge la configuration depuis un fichier JSON."""
        filepath = path or Path(CONFIG_FILENAME)
        config = cls()

        if not filepath.exists():
            logger.info("Pas de fichier de config, utilisation des valeurs par défaut")
            return config

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            config.dmx = DMXConfig(**data.get("dmx", {}))
            config.ui = UIConfig(**data.get("ui", {}))
            config.last_show_path = data.get("last_show_path", "")
            logger.info("Configuration chargée : %s", filepath)
        except Exception:
            logger.exception("Erreur de chargement de la configuration")

        return config