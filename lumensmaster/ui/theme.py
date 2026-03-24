"""
Thème visuel de LumensMaster.

Palette sombre professionnelle inspirée des consoles d'éclairage modernes.
Accent bleu lumineux pour les éléments interactifs.
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg


# --- Palette de couleurs ---

class Colors:
    """Couleurs de l'application."""
    # Fond
    BG_DARK = (18, 18, 24)
    BG_MEDIUM = (28, 28, 38)
    BG_LIGHT = (38, 38, 50)
    BG_WIDGET = (45, 45, 60)

    # Texte
    TEXT_PRIMARY = (220, 220, 230)
    TEXT_SECONDARY = (140, 140, 160)
    TEXT_DISABLED = (80, 80, 100)

    # Accent
    ACCENT = (60, 140, 255)
    ACCENT_HOVER = (80, 160, 255)
    ACCENT_ACTIVE = (40, 120, 235)

    # États
    SUCCESS = (50, 200, 100)
    WARNING = (255, 180, 40)
    ERROR = (255, 70, 70)

    # Faders
    FADER_BG = (30, 30, 42)
    FADER_FILL = (60, 140, 255)
    FADER_GRAB = (180, 200, 255)

    # Grand Master
    GM_FILL = (255, 180, 40)
    GM_GRAB = (255, 220, 140)


def apply_theme() -> int:
    """
    Crée et applique le thème global Dear PyGui.
    
    Returns:
        L'identifiant du thème créé.
    """
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            # Fond de fenêtre
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, Colors.BG_DARK)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, Colors.BG_MEDIUM)
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, Colors.BG_LIGHT)
            dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, Colors.BG_MEDIUM)

            # Texte
            dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.TEXT_PRIMARY)
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, Colors.TEXT_DISABLED)

            # Widgets
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, Colors.BG_WIDGET)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, Colors.BG_LIGHT)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, Colors.ACCENT_ACTIVE)

            # Boutons
            dpg.add_theme_color(dpg.mvThemeCol_Button, Colors.BG_LIGHT)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, Colors.ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, Colors.ACCENT_ACTIVE)

            # Sliders
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, Colors.FADER_GRAB)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, Colors.ACCENT)

            # Bordures et séparateurs
            dpg.add_theme_color(dpg.mvThemeCol_Border, (50, 50, 70, 100))
            dpg.add_theme_color(dpg.mvThemeCol_Separator, (50, 50, 70))

            # Header (pour les collapsibles)
            dpg.add_theme_color(dpg.mvThemeCol_Header, Colors.BG_LIGHT)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, Colors.ACCENT)
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, Colors.ACCENT_ACTIVE)

            # Style
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 2)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 8, 8)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 6)

    dpg.bind_theme(global_theme)
    return global_theme


def create_fader_theme() -> int:
    """Crée un thème spécifique pour les faders submasters."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvSliderInt):
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, Colors.FADER_BG)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (40, 40, 55))
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, Colors.FADER_FILL)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, Colors.ACCENT_HOVER)
            dpg.add_theme_style(dpg.mvStyleVar_GrabMinSize, 12)
    return theme


def create_gm_theme() -> int:
    """Crée un thème spécifique pour le Grand Master."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvSliderInt):
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, Colors.FADER_BG)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (50, 40, 30))
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, Colors.GM_FILL)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, Colors.GM_GRAB)
            dpg.add_theme_style(dpg.mvStyleVar_GrabMinSize, 14)
    return theme