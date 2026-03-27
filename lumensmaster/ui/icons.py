"""
Utilitaire de chargement d'icônes pour l'UI LumensMaster.

Charge les icônes PNG depuis le dossier assets/icons/ et les enregistre
comme textures Dear PyGui. Fournit un fallback texte si l'icône est absente.

Convention de nommage : btn_[NomFenêtre]_[NomBouton].png
Exemple : btn_Circuits_Valeurs.png

Usage :
    icons = IconManager()
    icons.load_all()

    # Créer un bouton avec icône + tooltip
    icons.image_button(
        icon_name="btn_Circuits_Valeurs",
        tooltip="Réglage des valeurs",
        fallback_label="Val",
        callback=my_callback,
    )
"""

from __future__ import annotations

import logging
from pathlib import Path

import dearpygui.dearpygui as dpg

logger = logging.getLogger(__name__)

# Chemin par défaut des icônes (relatif à la racine du projet)
DEFAULT_ICONS_DIR = Path(__file__).parent.parent.parent / "assets" / "icons"

ICON_SIZE = 24  # Taille attendue des icônes en pixels


class IconManager:
    """
    Gère le chargement et l'utilisation des icônes comme textures Dear PyGui.
    """

    def __init__(self, icons_dir: Path | None = None) -> None:
        self._icons_dir = icons_dir or DEFAULT_ICONS_DIR
        self._textures: dict[str, int] = {}  # nom -> texture_id
        self._registry: int = 0

    def load_all(self) -> None:
        """
        Charge toutes les icônes PNG du dossier assets/icons/.
        Doit être appelé après dpg.create_context().
        """
        if not self._icons_dir.exists():
            logger.warning("Dossier d'icônes introuvable : %s", self._icons_dir)
            return

        # Créer un texture registry
        self._registry = dpg.add_texture_registry(show=False)

        for png_file in sorted(self._icons_dir.glob("*.png")):
            self._load_icon(png_file)

        logger.info(
            "Icônes chargées : %d depuis %s",
            len(self._textures),
            self._icons_dir,
        )

    def _load_icon(self, path: Path) -> None:
        """Charge une icône PNG et l'enregistre comme texture."""
        name = path.stem  # btn_Circuits_Valeurs (sans .png)

        try:
            width, height, channels, data = dpg.load_image(str(path))
            if width <= 0 or height <= 0:
                logger.warning("Icône invalide : %s", path)
                return

            texture_id = dpg.add_static_texture(
                width=width,
                height=height,
                default_value=data,
                parent=self._registry,
            )

            self._textures[name] = texture_id
            logger.debug("Icône chargée : %s (%dx%d)", name, width, height)

        except Exception:
            logger.exception("Erreur de chargement de l'icône : %s", path)

    def has_icon(self, icon_name: str) -> bool:
        """Vérifie si une icône est chargée."""
        return icon_name in self._textures

    def get_texture(self, icon_name: str) -> int | None:
        """Retourne l'ID de texture d'une icône, ou None."""
        return self._textures.get(icon_name)

    def image_button(
        self,
        icon_name: str,
        tooltip: str,
        fallback_label: str,
        callback=None,
        user_data=None,
        width: int = 30,
        height: int = 30,
    ) -> int:
        """
        Crée un bouton avec icône et tooltip.
        Si l'icône n'est pas trouvée, crée un bouton texte classique.

        Args:
            icon_name: Nom de l'icône (sans .png).
            tooltip: Texte affiché au survol.
            fallback_label: Texte du bouton si l'icône est absente.
            callback: Fonction appelée au clic.
            user_data: Données passées au callback.
            width: Largeur du bouton.
            height: Hauteur du bouton.

        Returns:
            L'identifiant du widget créé.
        """
        texture_id = self._textures.get(icon_name)

        if texture_id is not None:
            # Bouton avec icône
            button_id = dpg.add_image_button(
                texture_id,
                width=width,
                height=height,
                callback=callback,
                user_data=user_data,
            )
        else:
            # Fallback : bouton texte
            button_id = dpg.add_button(
                label=fallback_label,
                width=width + 10,
                height=height,
                callback=callback,
                user_data=user_data,
            )

        # Tooltip au survol
        with dpg.tooltip(button_id):
            dpg.add_text(tooltip)

        return button_id


# Instance globale (singleton)
_icon_manager: IconManager | None = None


def get_icon_manager() -> IconManager:
    """Retourne l'instance globale du gestionnaire d'icônes."""
    global _icon_manager
    if _icon_manager is None:
        _icon_manager = IconManager()
    return _icon_manager