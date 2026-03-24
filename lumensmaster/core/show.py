"""
Gestion des fichiers de show (sauvegarde et chargement).

Le format de show est un fichier JSON structuré contenant
l'état complet de tous les modules : patch, faders, séquenceur, etc.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from lumensmaster import __version__

logger = logging.getLogger(__name__)

SHOW_FILE_EXTENSION = ".lms"


def new_show() -> dict[str, Any]:
    """Crée un show vide avec la structure par défaut."""
    return {
        "metadata": {
            "version": __version__,
            "created": datetime.now().isoformat(),
            "modified": datetime.now().isoformat(),
            "name": "Nouveau show",
        },
        "patch": {},
        "faders": {},
        "grandmaster": 255,
    }


def save_show(show_data: dict[str, Any], path: str | Path) -> bool:
    """
    Sauvegarde un show dans un fichier JSON.
    
    Args:
        show_data: Données complètes du show.
        path: Chemin du fichier de destination.
        
    Returns:
        True si la sauvegarde a réussi.
    """
    filepath = Path(path)
    if filepath.suffix != SHOW_FILE_EXTENSION:
        filepath = filepath.with_suffix(SHOW_FILE_EXTENSION)

    show_data["metadata"]["modified"] = datetime.now().isoformat()
    show_data["metadata"]["version"] = __version__

    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(show_data, f, indent=2, ensure_ascii=False)
        logger.info("Show sauvegardé : %s", filepath)
        return True
    except Exception:
        logger.exception("Erreur de sauvegarde du show")
        return False


def load_show(path: str | Path) -> dict[str, Any] | None:
    """
    Charge un show depuis un fichier JSON.
    
    Args:
        path: Chemin du fichier de show.
        
    Returns:
        Les données du show, ou None en cas d'erreur.
    """
    filepath = Path(path)

    if not filepath.exists():
        logger.error("Fichier de show introuvable : %s", filepath)
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            show_data = json.load(f)
        logger.info("Show chargé : %s", filepath)
        return show_data
    except Exception:
        logger.exception("Erreur de chargement du show")
        return None