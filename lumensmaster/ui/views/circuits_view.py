"""
Vue Circuits : grille interactive des circuits DMX avec groupes.

Barre de catégories :
    Boutons toggle avec icônes pour afficher/masquer les catégories :
    - Valeurs : saisie, full/zero, DMX/%, REC, clear
    - Layout : colonnes, lignes, max, taille cellules
    - Groupes : créer, ajouter, retirer, supprimer

Sections réorganisables et repliables :
    - Section "Tous les circuits" (grille principale)
    - Sections groupes utilisateur
"""

from __future__ import annotations

import logging
import math
from typing import Any

import dearpygui.dearpygui as dpg

from lumensmaster.core.engine import Engine
from lumensmaster.ui.theme import Colors
from lumensmaster.ui.icons import get_icon_manager

logger = logging.getLogger(__name__)

# Identifiant spécial pour la section "tous les circuits"
ALL_CIRCUITS_SECTION = "__ALL__"

# Valeurs par défaut
DEFAULT_COLUMNS = 24
DEFAULT_MAX_CIRCUITS = 512
DEFAULT_CELL_WIDTH = 52
DEFAULT_CELL_HEIGHT = 38
MIN_CELL_SIZE = 28
MAX_CELL_SIZE = 100
CELL_SPACING = 2


class CircuitsView:
    """Fenêtre flottante affichant la grille des circuits et les groupes."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._icons = get_icon_manager()

        # Paramètres de layout
        self._columns = DEFAULT_COLUMNS
        self._max_circuits = DEFAULT_MAX_CIRCUITS
        self._cell_width = DEFAULT_CELL_WIDTH
        self._cell_height = DEFAULT_CELL_HEIGHT
        self._rows = math.ceil(self._max_circuits / self._columns)

        # Ordre d'affichage des sections
        self._section_order: list[str] = [ALL_CIRCUITS_SECTION]
        self._all_collapsed: bool = False

        # État des catégories de commandes (visible ou non)
        self._category_visible: dict[str, bool] = {
            "valeurs": False,
            "layout": False,
            "groupes": False,
        }

        # Widgets
        self._window_id: int = 0
        self._sections_container: int = 0
        self._cells: dict[str, dict[int, dict]] = {}
        self._input_widget: int = 0
        self._group_name_input: int = 0
        self._handler_registry: int = 0

        # Widgets des catégories (groupes affichables/masquables)
        self._category_groups: dict[str, int] = {}
        self._category_buttons: dict[str, int] = {}

        # Widgets layout config
        self._columns_widget: int = 0
        self._rows_widget: int = 0
        self._max_circuits_widget: int = 0
        self._cell_width_widget: int = 0
        self._cell_height_widget: int = 0

        # Widgets gestion groupes
        self._group_combo_add: int = 0
        self._group_combo_remove: int = 0
        self._group_combo_delete: int = 0

        # Thèmes
        self._theme_normal: int = 0
        self._theme_selected: int = 0
        self._theme_active: int = 0
        self._theme_selected_active: int = 0
        self._theme_toggle_on: int = 0
        self._theme_toggle_off: int = 0

        self._updating_layout = False

        # S'abonner aux événements
        self._engine.bus.on("circuit.changed", self._on_circuit_changed)
        self._engine.bus.on("groups.changed", self._on_groups_changed)

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
            # Barre de boutons de catégories
            self._build_category_buttons()

            dpg.add_spacer(height=2)

            # Catégorie Valeurs (masquée par défaut)
            self._category_groups["valeurs"] = dpg.add_group(show=False)
            with dpg.group(parent=self._category_groups["valeurs"]):
                self._build_toolbar_valeurs()
                dpg.add_spacer(height=2)
                dpg.add_separator()

            # Catégorie Layout (masquée par défaut)
            self._category_groups["layout"] = dpg.add_group(show=False)
            with dpg.group(parent=self._category_groups["layout"]):
                self._build_toolbar_layout()
                dpg.add_spacer(height=2)
                dpg.add_separator()

            # Catégorie Groupes (masquée par défaut)
            self._category_groups["groupes"] = dpg.add_group(show=False)
            with dpg.group(parent=self._category_groups["groupes"]):
                self._build_toolbar_groupes()
                dpg.add_spacer(height=2)
                dpg.add_separator()

            dpg.add_spacer(height=4)

            # Conteneur des sections
            self._sections_container = dpg.add_group()

        self._sync_section_order()
        self._rebuild_all_sections()

        # Handlers globaux
        with dpg.handler_registry() as handler:
            dpg.add_key_press_handler(callback=self._on_key_press)
            dpg.add_mouse_wheel_handler(callback=self._on_mouse_wheel)
        self._handler_registry = handler

        return self._window_id

    def destroy(self) -> None:
        if self._handler_registry:
            dpg.delete_item(self._handler_registry)
        if self._window_id and dpg.does_item_exist(self._window_id):
            dpg.delete_item(self._window_id)
        self._cells.clear()

    # --- Boutons de catégories ---

    def _build_category_buttons(self) -> None:
        """Construit la barre de boutons toggle pour les catégories."""
        with dpg.group(horizontal=True):
            self._category_buttons["valeurs"] = self._icons.image_button(
                icon_name="btn_Circuits_Valeurs",
                tooltip="Reglage des valeurs",
                fallback_label="Val",
                callback=self._on_toggle_category,
                user_data="valeurs",
            )

            dpg.add_spacer(width=4)

            self._category_buttons["layout"] = self._icons.image_button(
                icon_name="btn_Circuits_Layout_Cfg",
                tooltip="Configuration du layout",
                fallback_label="Lay",
                callback=self._on_toggle_category,
                user_data="layout",
            )

            dpg.add_spacer(width=4)

            self._category_buttons["groupes"] = self._icons.image_button(
                icon_name="btn_Circuits_Groupes",
                tooltip="Gestion des groupes",
                fallback_label="Grp",
                callback=self._on_toggle_category,
                user_data="groupes",
            )

        # Appliquer les thèmes initiaux (tous off)
        for cat_key, btn_id in self._category_buttons.items():
            dpg.bind_item_theme(btn_id, self._theme_toggle_off)

    def _on_toggle_category(self, sender: int, value: Any, user_data: str) -> None:
        """Bascule la visibilité d'une catégorie de commandes."""
        cat_key = user_data
        self._category_visible[cat_key] = not self._category_visible[cat_key]
        visible = self._category_visible[cat_key]

        # Afficher/masquer le groupe
        group_id = self._category_groups.get(cat_key)
        if group_id and dpg.does_item_exist(group_id):
            dpg.configure_item(group_id, show=visible)

        # Mettre à jour le thème du bouton
        btn_id = self._category_buttons.get(cat_key)
        if btn_id and dpg.does_item_exist(btn_id):
            theme = self._theme_toggle_on if visible else self._theme_toggle_off
            dpg.bind_item_theme(btn_id, theme)

    # --- Toolbar Valeurs ---

    def _build_toolbar_valeurs(self) -> None:
        with dpg.group(horizontal=True):
            dpg.add_text("Valeur :", color=Colors.TEXT_SECONDARY)
            self._input_widget = dpg.add_input_text(
                width=80, hint="0-255", on_enter=True,
                callback=self._on_value_input,
            )
            dpg.add_spacer(width=8)
            dpg.add_button(label="Full", width=50,
                           callback=lambda: self._set_selected_value(255))
            dpg.add_button(label="Zero", width=50,
                           callback=lambda: self._set_selected_value(0))
            dpg.add_spacer(width=16)

            dpg.add_text("Affichage :", color=Colors.TEXT_SECONDARY)
            dpg.add_radio_button(items=["DMX", "%"], default_value="DMX",
                                 horizontal=True, callback=self._on_display_mode_changed)
            dpg.add_spacer(width=16)

            dpg.add_text("Enregistrer sur :", color=Colors.TEXT_SECONDARY)
            self._record_fader_input = dpg.add_input_int(
                default_value=1, min_value=1,
                max_value=self._engine.faders.count,
                min_clamped=True, max_clamped=True, width=80,
            )
            dpg.add_button(label="REC", callback=self._on_record_to_fader)
            dpg.add_spacer(width=16)

            dpg.add_button(label="Clear sel.", callback=self._clear_selected)
            dpg.add_button(label="Clear tout", callback=self._clear_all)

    # --- Toolbar Layout ---

    def _build_toolbar_layout(self) -> None:
        with dpg.group(horizontal=True):
            dpg.add_text("Col:", color=Colors.TEXT_SECONDARY)
            self._columns_widget = dpg.add_input_int(
                default_value=self._columns, min_value=1, max_value=512,
                min_clamped=True, max_clamped=True, width=90,
                callback=self._on_columns_changed)
            dpg.add_spacer(width=8)

            dpg.add_text("Lig:", color=Colors.TEXT_SECONDARY)
            self._rows_widget = dpg.add_input_int(
                default_value=self._rows, min_value=1, max_value=512,
                min_clamped=True, max_clamped=True, width=90,
                callback=self._on_rows_changed)
            dpg.add_spacer(width=8)

            dpg.add_text("Max:", color=Colors.TEXT_SECONDARY)
            self._max_circuits_widget = dpg.add_input_int(
                default_value=self._max_circuits, min_value=1, max_value=512,
                min_clamped=True, max_clamped=True, width=90,
                callback=self._on_max_circuits_changed)
            dpg.add_spacer(width=16)

            dpg.add_text("Cellule :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)
            dpg.add_text("L:", color=Colors.TEXT_SECONDARY)
            self._cell_width_widget = dpg.add_input_int(
                default_value=self._cell_width,
                min_value=MIN_CELL_SIZE, max_value=MAX_CELL_SIZE,
                min_clamped=True, max_clamped=True, width=80,
                callback=self._on_cell_size_changed)
            dpg.add_text("H:", color=Colors.TEXT_SECONDARY)
            self._cell_height_widget = dpg.add_input_int(
                default_value=self._cell_height,
                min_value=MIN_CELL_SIZE, max_value=MAX_CELL_SIZE,
                min_clamped=True, max_clamped=True, width=80,
                callback=self._on_cell_size_changed)
            dpg.add_spacer(width=8)
            dpg.add_button(label="Reset layout", callback=self._reset_layout)

    # --- Toolbar Groupes ---

    def _build_toolbar_groupes(self) -> None:
        with dpg.group(horizontal=True):
            self._group_name_input = dpg.add_input_text(
                width=120, hint="Nom du groupe", on_enter=True,
                callback=self._on_create_group,
            )
            dpg.add_button(label="Creer groupe", callback=self._on_create_group)
            dpg.add_spacer(width=12)

            dpg.add_text("Ajouter a :", color=Colors.TEXT_SECONDARY)
            self._group_combo_add = dpg.add_combo(items=[], width=120, default_value="")
            dpg.add_button(label="+", width=30, callback=self._on_add_to_group)
            dpg.add_spacer(width=12)

            dpg.add_text("Retirer de :", color=Colors.TEXT_SECONDARY)
            self._group_combo_remove = dpg.add_combo(items=[], width=120, default_value="")
            dpg.add_button(label="-", width=30, callback=self._on_remove_from_group)
            dpg.add_spacer(width=12)

            dpg.add_text("Supprimer :", color=Colors.TEXT_SECONDARY)
            self._group_combo_delete = dpg.add_combo(items=[], width=120, default_value="")
            dpg.add_button(label="X", width=30, callback=self._on_delete_group)

    def _update_group_combos(self) -> None:
        names = self._engine.circuits.get_group_names()
        for combo in (self._group_combo_add, self._group_combo_remove,
                      self._group_combo_delete):
            if dpg.does_item_exist(combo):
                dpg.configure_item(combo, items=names)
                if names:
                    dpg.set_value(combo, names[0])
                else:
                    dpg.set_value(combo, "")

    # --- Gestion de l'ordre des sections ---

    def _sync_section_order(self) -> None:
        group_names = set(self._engine.circuits.get_group_names())
        current_keys = set(self._section_order)

        for name in self._engine.circuits.get_group_names():
            if name not in current_keys:
                self._section_order.append(name)

        self._section_order = [
            s for s in self._section_order
            if s == ALL_CIRCUITS_SECTION or s in group_names
        ]

        if ALL_CIRCUITS_SECTION not in self._section_order:
            self._section_order.insert(0, ALL_CIRCUITS_SECTION)

    def _move_section(self, section_key: str, direction: int) -> None:
        try:
            idx = self._section_order.index(section_key)
        except ValueError:
            return
        new_idx = idx + direction
        if 0 <= new_idx < len(self._section_order):
            self._section_order.pop(idx)
            self._section_order.insert(new_idx, section_key)
            self._rebuild_all_sections()

    # --- Reconstruction des sections ---

    def _rebuild_all_sections(self) -> None:
        self._cells.clear()

        if dpg.does_item_exist(self._sections_container):
            children = dpg.get_item_children(self._sections_container, 1)
            if children:
                for child in children:
                    dpg.delete_item(child)

        self._update_group_combos()

        with dpg.group(parent=self._sections_container):
            for section_key in self._section_order:
                if section_key == ALL_CIRCUITS_SECTION:
                    self._build_all_circuits_section()
                else:
                    group = self._engine.circuits.get_group(section_key)
                    if group:
                        self._build_group_section(group)

    def _build_section_header(
        self, label: str, count: int, collapsed: bool,
        section_key: str, color: tuple = Colors.ACCENT,
    ) -> None:
        with dpg.group(horizontal=True):
            toggle_label = "[+]" if collapsed else "[-]"
            dpg.add_button(
                label=toggle_label, width=35,
                callback=self._on_toggle_section,
                user_data=section_key,
            )
            dpg.add_text(f"  {label}  ({count} circuits)", color=color)
            dpg.add_spacer(width=16)
            dpg.add_button(
                label="^", width=30,
                callback=self._on_move_section_up,
                user_data=section_key,
            )
            dpg.add_button(
                label="v", width=30,
                callback=self._on_move_section_down,
                user_data=section_key,
            )

    def _build_all_circuits_section(self) -> None:
        total = min(self._max_circuits, 512)
        self._build_section_header(
            label="Tous les circuits", count=total,
            collapsed=self._all_collapsed,
            section_key=ALL_CIRCUITS_SECTION,
            color=Colors.TEXT_PRIMARY,
        )
        if not self._all_collapsed:
            self._cells[ALL_CIRCUITS_SECTION] = {}
            rows_needed = math.ceil(total / self._columns)
            with dpg.group():
                for row in range(rows_needed):
                    with dpg.group(horizontal=True):
                        for col in range(self._columns):
                            circuit = row * self._columns + col + 1
                            if circuit > total:
                                break
                            self._build_cell(circuit, ALL_CIRCUITS_SECTION)
        dpg.add_spacer(height=4)
        dpg.add_separator()
        dpg.add_spacer(height=4)

    def _build_group_section(self, group) -> None:
        self._build_section_header(
            label=group.name, count=len(group.circuits),
            collapsed=group.collapsed, section_key=group.name,
        )
        if not group.collapsed and group.circuits:
            self._cells[group.name] = {}
            rows_needed = math.ceil(len(group.circuits) / self._columns)
            with dpg.group():
                for row in range(rows_needed):
                    with dpg.group(horizontal=True):
                        for col in range(self._columns):
                            idx = row * self._columns + col
                            if idx >= len(group.circuits):
                                break
                            circuit = group.circuits[idx]
                            self._build_cell(circuit, group.name)
        dpg.add_spacer(height=4)
        dpg.add_separator()
        dpg.add_spacer(height=4)

    def _build_cell(self, circuit: int, section_key: str) -> None:
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

        if section_key not in self._cells:
            self._cells[section_key] = {}
        self._cells[section_key][circuit] = {"button": button}

    # --- Thèmes ---

    def _create_themes(self) -> None:
        # Cellule normale
        with dpg.theme() as self._theme_normal:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, Colors.BG_WIDGET)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.TEXT_SECONDARY)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        # Cellule sélectionnée
        with dpg.theme() as self._theme_selected:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (40, 60, 100))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (50, 70, 120))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (50, 70, 120))
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.ACCENT)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        # Cellule active (valeur > 0)
        with dpg.theme() as self._theme_active:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (30, 50, 40))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (40, 65, 50))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (40, 65, 50))
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.SUCCESS)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        # Cellule sélectionnée + active
        with dpg.theme() as self._theme_selected_active:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (40, 70, 80))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (50, 85, 100))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (50, 85, 100))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (140, 230, 255))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        # Bouton toggle ON (catégorie active)
        with dpg.theme() as self._theme_toggle_on:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Button, Colors.ACCENT_ACTIVE)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, Colors.ACCENT_HOVER)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, Colors.ACCENT)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)

        # Bouton toggle OFF (catégorie masquée)
        with dpg.theme() as self._theme_toggle_off:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Button, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (55, 55, 70))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, Colors.BG_WIDGET)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)

    def _get_cell_theme(self, selected: bool, active: bool) -> int:
        if selected and active:
            return self._theme_selected_active
        elif selected:
            return self._theme_selected
        elif active:
            return self._theme_active
        return self._theme_normal

    # --- Mise à jour de l'affichage ---

    def _update_cell(self, circuit: int) -> None:
        level = self._engine.circuits.get_level(circuit)
        selected = self._engine.circuits.is_selected(circuit)
        value_str = self._engine.circuits.format_value(level)
        display = f"{circuit}\n{value_str}" if level > 0 else f"{circuit}\n---"
        theme = self._get_cell_theme(selected, level > 0)

        for section_store in self._cells.values():
            cell = section_store.get(circuit)
            if cell and dpg.does_item_exist(cell["button"]):
                dpg.set_item_label(cell["button"], display)
                dpg.bind_item_theme(cell["button"], theme)

    def _update_all_cells(self) -> None:
        for section_store in self._cells.values():
            for circuit in section_store:
                self._update_cell(circuit)

    def _update_selection_display(self) -> None:
        self._update_all_cells()

    # --- Callbacks sections ---

    def _on_toggle_section(self, sender: int, value: Any, user_data: str) -> None:
        if user_data == ALL_CIRCUITS_SECTION:
            self._all_collapsed = not self._all_collapsed
            self._rebuild_all_sections()
        else:
            self._engine.circuits.toggle_group_collapsed(user_data)

    def _on_move_section_up(self, sender: int, value: Any, user_data: str) -> None:
        self._move_section(user_data, -1)

    def _on_move_section_down(self, sender: int, value: Any, user_data: str) -> None:
        self._move_section(user_data, +1)

    # --- Callbacks groupes ---

    def _on_create_group(self, sender=None, value=None) -> None:
        name = dpg.get_value(self._group_name_input)
        if not name or not name.strip():
            return
        name = name.strip()
        if not self._engine.circuits.selection:
            logger.info("Aucun circuit selectionne pour creer le groupe")
            return
        if self._engine.circuits.get_group(name):
            logger.info("Un groupe '%s' existe deja", name)
            return
        self._engine.circuits.create_group(name)
        dpg.set_value(self._group_name_input, "")

    def _on_add_to_group(self) -> None:
        group_name = dpg.get_value(self._group_combo_add)
        if not group_name or not self._engine.circuits.selection:
            return
        self._engine.circuits.add_to_group(group_name)

    def _on_remove_from_group(self) -> None:
        group_name = dpg.get_value(self._group_combo_remove)
        if not group_name or not self._engine.circuits.selection:
            return
        self._engine.circuits.remove_from_group(group_name)

    def _on_delete_group(self) -> None:
        group_name = dpg.get_value(self._group_combo_delete)
        if not group_name:
            return
        self._engine.circuits.delete_group(group_name)

    def _on_groups_changed(self, **kwargs) -> None:
        self._sync_section_order()
        self._rebuild_all_sections()

    # --- Callbacks layout ---

    def _on_columns_changed(self, sender: int, value: int) -> None:
        if self._updating_layout:
            return
        self._updating_layout = True
        self._columns = max(1, min(512, value))
        self._rows = math.ceil(self._max_circuits / self._columns)
        dpg.set_value(self._rows_widget, self._rows)
        self._rebuild_all_sections()
        self._updating_layout = False

    def _on_rows_changed(self, sender: int, value: int) -> None:
        if self._updating_layout:
            return
        self._updating_layout = True
        self._rows = max(1, min(512, value))
        self._columns = math.ceil(self._max_circuits / self._rows)
        dpg.set_value(self._columns_widget, self._columns)
        self._rebuild_all_sections()
        self._updating_layout = False

    def _on_max_circuits_changed(self, sender: int, value: int) -> None:
        if self._updating_layout:
            return
        self._updating_layout = True
        self._max_circuits = max(1, min(512, value))
        self._rows = math.ceil(self._max_circuits / self._columns)
        dpg.set_value(self._rows_widget, self._rows)
        self._rebuild_all_sections()
        self._updating_layout = False

    def _on_cell_size_changed(self, sender: int, value: int) -> None:
        if self._updating_layout:
            return
        self._updating_layout = True
        self._cell_width = max(MIN_CELL_SIZE, min(MAX_CELL_SIZE, dpg.get_value(self._cell_width_widget)))
        self._cell_height = max(MIN_CELL_SIZE, min(MAX_CELL_SIZE, dpg.get_value(self._cell_height_widget)))
        self._rebuild_all_sections()
        self._updating_layout = False

    def _reset_layout(self) -> None:
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
        self._rebuild_all_sections()

    # --- Callbacks UI ---

    def _on_cell_click(self, sender: int, value: Any, user_data: int) -> None:
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
        dmx_value = self._engine.circuits.parse_input(value)
        if dmx_value is not None:
            self._set_selected_value(dmx_value)
        dpg.set_value(sender, "")

    def _on_key_press(self, sender: int, key: int) -> None:
        if not dpg.does_item_exist(self._window_id):
            return
        if not dpg.is_item_hovered(self._window_id):
            return
        # Ne pas intercepter dans les champs de saisie
        for widget in (self._input_widget, self._group_name_input):
            if widget and (dpg.is_item_active(widget) or dpg.is_item_focused(widget)):
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
        self._engine.circuits.display_percent = (value == "%")
        if self._engine.circuits.display_percent:
            dpg.configure_item(self._input_widget, hint="0-100%")
        else:
            dpg.configure_item(self._input_widget, hint="0-255")
        self._update_all_cells()

    def _on_record_to_fader(self) -> None:
        fader_id = dpg.get_value(self._record_fader_input)
        snapshot = self._engine.circuits.get_active_snapshot()
        if not snapshot:
            logger.info("Rien a enregistrer (aucun circuit actif)")
            return
        self._engine.faders.set_contents(fader_id, snapshot)
        self._engine.faders.set_label(fader_id, f"Rec.{fader_id}")
        logger.info("Enregistre %d circuits sur le fader %d", len(snapshot), fader_id)

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