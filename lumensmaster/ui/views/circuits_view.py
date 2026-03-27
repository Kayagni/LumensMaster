"""
Vue Circuits : grille interactive des circuits DMX.

Affiche une grille configurable de cellules représentant chaque circuit.
Permet la sélection, la saisie clavier, le pilotage à la molette,
et l'enregistrement sur fader.

Configuration du layout :
    - Taille des cellules (largeur x hauteur)
    - Nombre maximum de circuits affichés (1-512)
    - Colonnes / lignes : ajustement automatique croisé

Interactions :
    - Clic         -> sélectionne un circuit
    - Ctrl+clic    -> ajoute/retire de la sélection
    - Shift+clic   -> sélection par plage
    - Molette      -> +1/-1 sur la sélection
    - Saisie texte -> valeur DMX ou % sur la sélection
    - Full (F)     -> 255 sur la sélection
    - Zéro (Z)     -> 0 sur la sélection
    - Échap        -> désélectionner tout
"""

from __future__ import annotations

import logging
import math
from typing import Any

import dearpygui.dearpygui as dpg

from lumensmaster.core.engine import Engine
from lumensmaster.ui.theme import Colors

logger = logging.getLogger(__name__)

# Valeurs par défaut
DEFAULT_COLUMNS = 24
DEFAULT_MAX_CIRCUITS = 512
DEFAULT_CELL_WIDTH = 52
DEFAULT_CELL_HEIGHT = 38
MIN_CELL_SIZE = 28
MAX_CELL_SIZE = 100
CELL_SPACING = 2


class CircuitsView:
    """
    Fenêtre flottante affichant la grille des circuits.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

        # Paramètres de layout
        self._columns = DEFAULT_COLUMNS
        self._max_circuits = DEFAULT_MAX_CIRCUITS
        self._cell_width = DEFAULT_CELL_WIDTH
        self._cell_height = DEFAULT_CELL_HEIGHT
        self._rows = math.ceil(self._max_circuits / self._columns)

        # Widgets
        self._window_id: int = 0
        self._grid_container: int = 0
        self._cells: dict[int, dict] = {}
        self._input_widget: int = 0
        self._handler_registry: int = 0

        # Widgets layout config
        self._columns_widget: int = 0
        self._rows_widget: int = 0
        self._max_circuits_widget: int = 0
        self._cell_width_widget: int = 0
        self._cell_height_widget: int = 0

        # Thèmes
        self._theme_normal: int = 0
        self._theme_selected: int = 0
        self._theme_active: int = 0
        self._theme_selected_active: int = 0

        # Flag pour éviter les boucles de callbacks
        self._updating_layout = False

        # S'abonner aux changements de circuits
        self._engine.bus.on("circuit.changed", self._on_circuit_changed)

    def build(self) -> int:
        """Construit la fenêtre flottante des circuits."""
        self._create_themes()

        window_width = min(
            self._columns * (self._cell_width + CELL_SPACING) + 60,
            1380,
        )

        self._window_id = dpg.add_window(
            label="Circuits",
            width=window_width,
            height=720,
            pos=(10, 60),
            no_scrollbar=False,
            on_close=self._on_close,
        )

        with dpg.group(parent=self._window_id):
            self._build_toolbar()

            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=2)

            self._build_layout_config()

            dpg.add_spacer(height=2)
            dpg.add_separator()
            dpg.add_spacer(height=4)

            # Conteneur de la grille (sera reconstruit dynamiquement)
            self._grid_container = dpg.add_group()

        # Construire la grille initiale
        self._rebuild_grid()

        # Handlers globaux (en dehors de toute fenêtre)
        with dpg.handler_registry() as handler:
            dpg.add_key_press_handler(callback=self._on_key_press)
            dpg.add_mouse_wheel_handler(callback=self._on_mouse_wheel)
        self._handler_registry = handler

        return self._window_id

    def destroy(self) -> None:
        """Détruit la fenêtre et libère les ressources."""
        if self._handler_registry:
            dpg.delete_item(self._handler_registry)
        if self._window_id and dpg.does_item_exist(self._window_id):
            dpg.delete_item(self._window_id)
        self._cells.clear()

    # --- Construction UI ---

    def _build_toolbar(self) -> None:
        """Barre d'outils de la fenêtre circuits."""
        with dpg.group(horizontal=True):
            dpg.add_text("Valeur :", color=Colors.TEXT_SECONDARY)
            self._input_widget = dpg.add_input_text(
                width=80,
                hint="0-255",
                on_enter=True,
                callback=self._on_value_input,
            )

            dpg.add_spacer(width=8)

            dpg.add_button(
                label="Full",
                width=50,
                callback=lambda: self._set_selected_value(255),
            )
            dpg.add_button(
                label="Zero",
                width=50,
                callback=lambda: self._set_selected_value(0),
            )

            dpg.add_spacer(width=16)

            dpg.add_text("Affichage :", color=Colors.TEXT_SECONDARY)
            dpg.add_radio_button(
                items=["DMX", "%"],
                default_value="DMX",
                horizontal=True,
                callback=self._on_display_mode_changed,
            )

            dpg.add_spacer(width=16)

            dpg.add_text("Enregistrer sur :", color=Colors.TEXT_SECONDARY)
            self._record_fader_input = dpg.add_input_int(
                default_value=1,
                min_value=1,
                max_value=self._engine.faders.count,
                min_clamped=True,
                max_clamped=True,
                width=60,
            )
            dpg.add_button(
                label="REC",
                callback=self._on_record_to_fader,
            )

            dpg.add_spacer(width=16)

            dpg.add_button(
                label="Clear sel.",
                callback=self._clear_selected,
            )
            dpg.add_button(
                label="Clear tout",
                callback=self._clear_all,
            )

    def _build_layout_config(self) -> None:
        """Barre de configuration du layout de la grille."""
        with dpg.group(horizontal=True):
            dpg.add_text("Layout :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)

            dpg.add_text("Col:", color=Colors.TEXT_SECONDARY)
            self._columns_widget = dpg.add_input_int(
                default_value=self._columns,
                min_value=1,
                max_value=512,
                min_clamped=True,
                max_clamped=True,
                width=90,
                callback=self._on_columns_changed,
            )

            dpg.add_spacer(width=8)

            dpg.add_text("Lig:", color=Colors.TEXT_SECONDARY)
            self._rows_widget = dpg.add_input_int(
                default_value=self._rows,
                min_value=1,
                max_value=512,
                min_clamped=True,
                max_clamped=True,
                width=90,
                callback=self._on_rows_changed,
            )

            dpg.add_spacer(width=8)

            dpg.add_text("Max:", color=Colors.TEXT_SECONDARY)
            self._max_circuits_widget = dpg.add_input_int(
                default_value=self._max_circuits,
                min_value=1,
                max_value=512,
                min_clamped=True,
                max_clamped=True,
                width=90,
                callback=self._on_max_circuits_changed,
            )

            dpg.add_spacer(width=16)

            dpg.add_text("Cellule :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)

            dpg.add_text("L:", color=Colors.TEXT_SECONDARY)
            self._cell_width_widget = dpg.add_input_int(
                default_value=self._cell_width,
                min_value=MIN_CELL_SIZE,
                max_value=MAX_CELL_SIZE,
                min_clamped=True,
                max_clamped=True,
                width=80,
                callback=self._on_cell_size_changed,
            )

            dpg.add_text("H:", color=Colors.TEXT_SECONDARY)
            self._cell_height_widget = dpg.add_input_int(
                default_value=self._cell_height,
                min_value=MIN_CELL_SIZE,
                max_value=MAX_CELL_SIZE,
                min_clamped=True,
                max_clamped=True,
                width=80,
                callback=self._on_cell_size_changed,
            )

            dpg.add_spacer(width=8)

            dpg.add_button(
                label="Reset layout",
                callback=self._reset_layout,
            )

    def _rebuild_grid(self) -> None:
        """Reconstruit la grille complète avec les paramètres actuels."""
        # Supprimer l'ancien contenu
        self._cells.clear()
        if dpg.does_item_exist(self._grid_container):
            children = dpg.get_item_children(self._grid_container, 1)
            if children:
                for child in children:
                    dpg.delete_item(child)

        # Construire la nouvelle grille
        total_circuits = min(self._max_circuits, 512)
        rows_needed = math.ceil(total_circuits / self._columns)

        with dpg.group(parent=self._grid_container):
            for row in range(rows_needed):
                with dpg.group(horizontal=True):
                    for col in range(self._columns):
                        circuit = row * self._columns + col + 1
                        if circuit > total_circuits:
                            break
                        self._build_cell(circuit)

        # Mettre à jour l'affichage des valeurs existantes
        self._update_all_cells()

        logger.debug(
            "Grille reconstruite : %d circuits, %d col x %d lig, cellules %dx%d",
            total_circuits,
            self._columns,
            rows_needed,
            self._cell_width,
            self._cell_height,
        )

    def _build_cell(self, circuit: int) -> None:
        """Construit une cellule individuelle pour un circuit."""
        level = self._engine.circuits.get_level(circuit)
        value_str = self._engine.circuits.format_value(level)
        display = f"{circuit}\n{value_str}" if level > 0 else f"{circuit}\n---"

        button = dpg.add_button(
            label=display,
            width=self._cell_width,
            height=self._cell_height,
            callback=self._on_cell_click,
            user_data=circuit,
        )

        selected = self._engine.circuits.is_selected(circuit)
        theme = self._get_cell_theme(selected, level > 0)
        dpg.bind_item_theme(button, theme)

        self._cells[circuit] = {"button": button}

    def _create_themes(self) -> None:
        """Crée les thèmes pour les différents états des cellules."""
        with dpg.theme() as self._theme_normal:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, Colors.BG_WIDGET)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.TEXT_SECONDARY)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        with dpg.theme() as self._theme_selected:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (40, 60, 100))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (50, 70, 120))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (50, 70, 120))
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.ACCENT)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        with dpg.theme() as self._theme_active:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (30, 50, 40))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (40, 65, 50))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (40, 65, 50))
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.SUCCESS)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        with dpg.theme() as self._theme_selected_active:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (40, 70, 80))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (50, 85, 100))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (50, 85, 100))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (140, 230, 255))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

    def _get_cell_theme(self, selected: bool, active: bool) -> int:
        """Retourne le thème approprié selon l'état de la cellule."""
        if selected and active:
            return self._theme_selected_active
        elif selected:
            return self._theme_selected
        elif active:
            return self._theme_active
        return self._theme_normal

    # --- Mise à jour de l'affichage ---

    def _update_cell(self, circuit: int) -> None:
        """Met à jour l'affichage d'une cellule."""
        cell = self._cells.get(circuit)
        if not cell:
            return

        level = self._engine.circuits.get_level(circuit)
        selected = self._engine.circuits.is_selected(circuit)
        value_str = self._engine.circuits.format_value(level)

        display = f"{circuit}\n{value_str}" if level > 0 else f"{circuit}\n---"
        dpg.set_item_label(cell["button"], display)

        theme = self._get_cell_theme(selected, level > 0)
        dpg.bind_item_theme(cell["button"], theme)

    def _update_all_cells(self) -> None:
        for circuit in self._cells:
            self._update_cell(circuit)

    def _update_selection_display(self) -> None:
        for circuit in self._cells:
            self._update_cell(circuit)

    # --- Callbacks layout ---

    def _on_columns_changed(self, sender: int, value: int) -> None:
        """Colonnes modifiées -> ajuster les lignes."""
        if self._updating_layout:
            return
        self._updating_layout = True

        self._columns = max(1, min(512, value))
        self._rows = math.ceil(self._max_circuits / self._columns)
        dpg.set_value(self._rows_widget, self._rows)

        self._rebuild_grid()
        self._updating_layout = False

    def _on_rows_changed(self, sender: int, value: int) -> None:
        """Lignes modifiées -> ajuster les colonnes."""
        if self._updating_layout:
            return
        self._updating_layout = True

        self._rows = max(1, min(512, value))
        self._columns = math.ceil(self._max_circuits / self._rows)
        dpg.set_value(self._columns_widget, self._columns)

        self._rebuild_grid()
        self._updating_layout = False

    def _on_max_circuits_changed(self, sender: int, value: int) -> None:
        """Max circuits modifié -> ajuster les lignes."""
        if self._updating_layout:
            return
        self._updating_layout = True

        self._max_circuits = max(1, min(512, value))
        self._rows = math.ceil(self._max_circuits / self._columns)
        dpg.set_value(self._rows_widget, self._rows)

        self._rebuild_grid()
        self._updating_layout = False

    def _on_cell_size_changed(self, sender: int, value: int) -> None:
        """Taille des cellules modifiée -> reconstruire la grille."""
        if self._updating_layout:
            return
        self._updating_layout = True

        self._cell_width = max(MIN_CELL_SIZE, min(MAX_CELL_SIZE, dpg.get_value(self._cell_width_widget)))
        self._cell_height = max(MIN_CELL_SIZE, min(MAX_CELL_SIZE, dpg.get_value(self._cell_height_widget)))

        self._rebuild_grid()
        self._updating_layout = False

    def _reset_layout(self) -> None:
        """Remet les paramètres de layout aux valeurs par défaut."""
        self._updating_layout = True

        self._columns = DEFAULT_COLUMNS
        self._rows = math.ceil(DEFAULT_MAX_CIRCUITS / DEFAULT_COLUMNS)
        self._max_circuits = DEFAULT_MAX_CIRCUITS
        self._cell_width = DEFAULT_CELL_WIDTH
        self._cell_height = DEFAULT_CELL_HEIGHT

        dpg.set_value(self._columns_widget, self._columns)
        dpg.set_value(self._rows_widget, self._rows)
        dpg.set_value(self._max_circuits_widget, self._max_circuits)
        dpg.set_value(self._cell_width_widget, self._cell_width)
        dpg.set_value(self._cell_height_widget, self._cell_height)

        self._updating_layout = False
        self._rebuild_grid()

    # --- Callbacks UI ---

    def _on_cell_click(self, sender: int, value: Any, user_data: int) -> None:
        """Clic sur une cellule de circuit."""
        circuit = user_data

        ctrl = dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)
        shift = dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift)

        if ctrl:
            self._engine.circuits.select_add(circuit)
        elif shift:
            self._engine.circuits.select_range(circuit)
        else:
            if self._engine.circuits.selection == {circuit}:
                self._engine.circuits.select_none()
            else:
                self._engine.circuits.select(circuit)

        self._update_selection_display()

    def _on_value_input(self, sender: int, value: str) -> None:
        """Saisie de valeur dans le champ texte."""
        dmx_value = self._engine.circuits.parse_input(value)
        if dmx_value is not None:
            self._set_selected_value(dmx_value)
        dpg.set_value(sender, "")

    def _on_key_press(self, sender: int, key: int) -> None:
        """Gestion des raccourcis clavier."""
        if not dpg.does_item_exist(self._window_id):
            return
        if not dpg.is_item_hovered(self._window_id):
            return

        if dpg.is_item_active(self._input_widget):
            return
        if dpg.is_item_focused(self._input_widget):
            return

        if key == dpg.mvKey_Escape:
            self._engine.circuits.select_none()
            self._update_selection_display()
        elif key == dpg.mvKey_F or key == dpg.mvKey_Home:
            self._set_selected_value(255)
        elif key == dpg.mvKey_Z or key == dpg.mvKey_End:
            self._set_selected_value(0)
        elif key == dpg.mvKey_Up:
            self._engine.circuits.nudge_selected(5)
            self._update_selection_display()
            self._engine.update_dmx()
        elif key == dpg.mvKey_Down:
            self._engine.circuits.nudge_selected(-5)
            self._update_selection_display()
            self._engine.update_dmx()

    def _on_mouse_wheel(self, sender: int, value: int) -> None:
        """Molette de la souris : +1/-1 sur les circuits sélectionnés."""
        if not self._engine.circuits.selection:
            return
        if not dpg.does_item_exist(self._window_id):
            return
        if not dpg.is_item_hovered(self._window_id):
            return

        delta = 1 if value > 0 else -1
        self._engine.circuits.nudge_selected(delta)
        self._update_selection_display()
        self._engine.update_dmx()

    def _on_display_mode_changed(self, sender: int, value: str) -> None:
        """Toggle entre DMX et %."""
        self._engine.circuits.display_percent = (value == "%")
        if self._engine.circuits.display_percent:
            dpg.configure_item(self._input_widget, hint="0-100%")
        else:
            dpg.configure_item(self._input_widget, hint="0-255")
        self._update_all_cells()

    def _on_record_to_fader(self) -> None:
        """Enregistre l'état courant des circuits sur un fader."""
        fader_id = dpg.get_value(self._record_fader_input)
        snapshot = self._engine.circuits.get_active_snapshot()

        if not snapshot:
            logger.info("Rien a enregistrer (aucun circuit actif)")
            return

        self._engine.faders.set_contents(fader_id, snapshot)
        self._engine.faders.set_label(fader_id, f"Rec.{fader_id}")
        logger.info(
            "Enregistre %d circuits sur le fader %d",
            len(snapshot),
            fader_id,
        )

    def _clear_selected(self) -> None:
        self._engine.circuits.clear_selected()
        self._update_selection_display()
        self._engine.update_dmx()

    def _clear_all(self) -> None:
        self._engine.circuits.clear_all()
        self._update_all_cells()
        self._engine.update_dmx()

    def _set_selected_value(self, value: int) -> None:
        self._engine.circuits.set_selected_level(value)
        self._update_selection_display()
        self._engine.update_dmx()

    def _on_close(self) -> None:
        logger.debug("Fenetre Circuits fermee")

    # --- Callback événement bus ---

    def _on_circuit_changed(self, circuit: int = 0, level: int = 0, **kwargs) -> None:
        if circuit > 0:
            self._update_cell(circuit)