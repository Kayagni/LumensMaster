"""
Vue Faders : fenêtre flottante des submasters avec Grand Master.

Faders visuels avec remplissage (drawlist) :
    - Partie inférieure remplie proportionnellement à la valeur
    - Barre de grab horizontale à la position de la valeur
    - Drag souris pour piloter

Tooltips :
    - Au survol d'un fader : liste des circuits pilotés

Catégories de commandes (toggle) :
    - Valeurs : saisie, full/zero, DMX/%, clear
    - Layout : colonnes, lignes, taille des faders
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

DEFAULT_FADER_COUNT = 64
DEFAULT_COLUMNS = 32
DEFAULT_FADER_WIDTH = 55
DEFAULT_SLIDER_HEIGHT = 250
MIN_FADER_WIDTH = 40
MAX_FADER_WIDTH = 100
MIN_SLIDER_HEIGHT = 100
MAX_SLIDER_HEIGHT = 500

# Couleurs faders
FADER_BG = (30, 30, 42, 255)
FADER_FILL = (60, 140, 255, 200)
FADER_GRAB = (180, 200, 255, 255)
FADER_BORDER = (50, 50, 70, 255)
GM_FILL = (255, 180, 40, 200)
GM_GRAB = (255, 220, 140, 255)


class FadersView:
    """Fenêtre flottante des faders submasters et Grand Master."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._icons = get_icon_manager()

        # Layout
        self._columns = DEFAULT_COLUMNS
        self._fader_width = DEFAULT_FADER_WIDTH
        self._slider_height = DEFAULT_SLIDER_HEIGHT
        self._fader_count = min(DEFAULT_FADER_COUNT, self._engine.faders.count)
        self._rows = math.ceil(self._fader_count / self._columns)

        # Sélection
        self._selection: set[int] = set()
        self._last_selected: int = 0
        self._display_percent: bool = False

        # Drag state
        self._dragging_fader: int | None = None
        self._dragging_gm: bool = False

        # Catégories visibles
        self._category_visible: dict[str, bool] = {"valeurs": False, "layout": False}

        # Widgets
        self._window_id: int = 0
        self._grid_container: int = 0
        self._gm_container: int = 0
        self._fader_widgets: dict[int, dict] = {}
        self._drawlist_to_fader: dict[int, int] = {}  # drawlist_id -> fader_id
        self._gm_widgets: dict[str, int] = {}
        self._input_widget: int = 0
        self._handler_registry: int = 0

        self._category_groups: dict[str, int] = {}
        self._category_buttons: dict[str, int] = {}

        self._columns_widget: int = 0
        self._rows_widget: int = 0
        self._fader_width_widget: int = 0
        self._slider_height_widget: int = 0
        self._fader_count_widget: int = 0

        # Thèmes
        self._theme_toggle_on: int = 0
        self._theme_toggle_off: int = 0
        self._theme_select_btn: int = 0
        self._theme_select_btn_on: int = 0

        self._updating_layout = False

    def build(self) -> int:
        self._create_themes()

        window_width = min(self._columns * (self._fader_width + 6) + 120, 1400)
        window_height = self._slider_height + 220

        self._window_id = dpg.add_window(
            label="Faders", width=window_width, height=window_height,
            pos=(10, 400), no_scrollbar=False, on_close=self._on_close,
        )

        with dpg.group(parent=self._window_id):
            self._build_category_buttons()
            dpg.add_spacer(height=2)

            self._category_groups["valeurs"] = dpg.add_group(show=False)
            with dpg.group(parent=self._category_groups["valeurs"]):
                self._build_toolbar_valeurs()
                dpg.add_spacer(height=2)
                dpg.add_separator()

            self._category_groups["layout"] = dpg.add_group(show=False)
            with dpg.group(parent=self._category_groups["layout"]):
                self._build_toolbar_layout()
                dpg.add_spacer(height=2)
                dpg.add_separator()

            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                self._grid_container = dpg.add_group()
                dpg.add_spacer(width=16)
                dpg.add_child_window(width=2, height=self._slider_height + 80,
                                     no_scrollbar=True, border=False)
                dpg.add_spacer(width=16)
                self._gm_container = dpg.add_group()

        self._rebuild_grid()
        self._rebuild_gm()

        # Handlers globaux
        with dpg.handler_registry() as handler:
            dpg.add_key_press_handler(callback=self._on_key_press)
            dpg.add_mouse_wheel_handler(callback=self._on_mouse_wheel)
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left,
                                        callback=self._on_mouse_click)
            dpg.add_mouse_move_handler(callback=self._on_mouse_move)
            dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Left,
                                          callback=self._on_mouse_release)
        self._handler_registry = handler

        return self._window_id

    def destroy(self) -> None:
        if self._handler_registry:
            dpg.delete_item(self._handler_registry)
        if self._window_id and dpg.does_item_exist(self._window_id):
            dpg.delete_item(self._window_id)
        self._fader_widgets.clear()
        self._drawlist_to_fader.clear()

    # --- Boutons catégories ---

    def _build_category_buttons(self) -> None:
        with dpg.group(horizontal=True):
            self._category_buttons["valeurs"] = self._icons.image_button(
                icon_name="btn_Faders_Valeurs", tooltip="Reglage des valeurs",
                fallback_label="Val", callback=self._on_toggle_category,
                user_data="valeurs")
            dpg.add_spacer(width=4)
            self._category_buttons["layout"] = self._icons.image_button(
                icon_name="btn_Faders_Layout_Cfg", tooltip="Configuration du layout",
                fallback_label="Lay", callback=self._on_toggle_category,
                user_data="layout")

        for cat_key, btn_id in self._category_buttons.items():
            dpg.bind_item_theme(btn_id, self._theme_toggle_off)

    def _on_toggle_category(self, sender: int, value: Any, user_data: str) -> None:
        cat_key = user_data
        self._category_visible[cat_key] = not self._category_visible[cat_key]
        visible = self._category_visible[cat_key]
        group_id = self._category_groups.get(cat_key)
        if group_id and dpg.does_item_exist(group_id):
            dpg.configure_item(group_id, show=visible)
        btn_id = self._category_buttons.get(cat_key)
        if btn_id and dpg.does_item_exist(btn_id):
            dpg.bind_item_theme(btn_id,
                                self._theme_toggle_on if visible else self._theme_toggle_off)

    # --- Toolbars ---

    def _build_toolbar_valeurs(self) -> None:
        with dpg.group(horizontal=True):
            dpg.add_text("Valeur :", color=Colors.TEXT_SECONDARY)
            self._input_widget = dpg.add_input_text(
                width=80, hint="0-255", on_enter=True, callback=self._on_value_input)
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
            dpg.add_button(label="Clear sel.", callback=self._clear_selected)
            dpg.add_button(label="Clear tout", callback=self._clear_all)

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
            dpg.add_text("Faders:", color=Colors.TEXT_SECONDARY)
            self._fader_count_widget = dpg.add_input_int(
                default_value=self._fader_count, min_value=1,
                max_value=self._engine.faders.count,
                min_clamped=True, max_clamped=True, width=90,
                callback=self._on_fader_count_changed)
            dpg.add_spacer(width=16)
            dpg.add_text("Fader :", color=Colors.TEXT_SECONDARY)
            dpg.add_text("L:", color=Colors.TEXT_SECONDARY)
            self._fader_width_widget = dpg.add_input_int(
                default_value=self._fader_width,
                min_value=MIN_FADER_WIDTH, max_value=MAX_FADER_WIDTH,
                min_clamped=True, max_clamped=True, width=80,
                callback=self._on_fader_size_changed)
            dpg.add_text("H:", color=Colors.TEXT_SECONDARY)
            self._slider_height_widget = dpg.add_input_int(
                default_value=self._slider_height,
                min_value=MIN_SLIDER_HEIGHT, max_value=MAX_SLIDER_HEIGHT,
                min_clamped=True, max_clamped=True, width=80,
                callback=self._on_fader_size_changed)
            dpg.add_spacer(width=8)
            dpg.add_button(label="Reset layout", callback=self._reset_layout)

    # --- Grille de faders ---

    def _rebuild_grid(self) -> None:
        self._fader_widgets.clear()
        self._drawlist_to_fader.clear()

        if dpg.does_item_exist(self._grid_container):
            children = dpg.get_item_children(self._grid_container, 1)
            if children:
                for child in children:
                    dpg.delete_item(child)

        rows_needed = math.ceil(self._fader_count / self._columns)
        with dpg.group(parent=self._grid_container):
            for row in range(rows_needed):
                with dpg.group(horizontal=True):
                    for col in range(self._columns):
                        fader_id = row * self._columns + col + 1
                        if fader_id > self._fader_count:
                            break
                        self._build_fader(fader_id)

    def _build_fader(self, fader_id: int) -> None:
        """Construit un fader individuel."""
        fader = self._engine.faders.get_fader(fader_id)
        if fader is None:
            return

        w = self._fader_width
        h = self._slider_height
        widgets = {}

        with dpg.group() as fader_group:
            # Bouton de sélection
            selected = fader_id in self._selection
            widgets["select_btn"] = dpg.add_button(
                label=f"F{fader_id}", width=w,
                callback=self._on_fader_select, user_data=fader_id)
            dpg.bind_item_theme(widgets["select_btn"],
                                self._theme_select_btn_on if selected else self._theme_select_btn)

            # Drawlist pour le fader visuel
            dl = dpg.add_drawlist(width=w, height=h)
            widgets["drawlist"] = dl
            self._drawlist_to_fader[dl] = fader_id

            # Dessiner le fader
            pad = 2
            fill_top = self._value_to_y(fader.level, h, pad)

            widgets["bg_rect"] = dpg.draw_rectangle(
                pmin=[0, 0], pmax=[w, h], fill=FADER_BG,
                color=FADER_BORDER, thickness=1, parent=dl)
            widgets["fill_rect"] = dpg.draw_rectangle(
                pmin=[pad, fill_top], pmax=[w - pad, h - pad],
                fill=FADER_FILL, color=(0, 0, 0, 0), parent=dl)
            widgets["grab_line"] = dpg.draw_line(
                p1=[0, fill_top], p2=[w, fill_top],
                color=FADER_GRAB, thickness=2, parent=dl)

            # Valeur
            value_str = self._format_value(fader.level)
            widgets["value_text"] = dpg.add_text(value_str, color=(180, 200, 255))

            # Label
            widgets["label_text"] = dpg.add_text(
                fader.label or "---", color=Colors.TEXT_SECONDARY)

            dpg.add_spacer(width=4)

        # Tooltip attaché au GROUP (pas au drawlist !)
        with dpg.tooltip(fader_group):
            widgets["tooltip_text"] = dpg.add_text("---")
        self._update_fader_tooltip(fader_id, widgets)

        self._fader_widgets[fader_id] = widgets

    def _rebuild_gm(self) -> None:
        if dpg.does_item_exist(self._gm_container):
            children = dpg.get_item_children(self._gm_container, 1)
            if children:
                for child in children:
                    dpg.delete_item(child)

        w = 60
        h = self._slider_height
        level = self._engine.grand_master.level
        pad = 2
        fill_top = self._value_to_y(level, h, pad)

        with dpg.group(parent=self._gm_container):
            dpg.add_text("GM", color=(255, 180, 40))

            dl = dpg.add_drawlist(width=w, height=h)
            self._gm_widgets["drawlist"] = dl

            self._gm_widgets["bg_rect"] = dpg.draw_rectangle(
                pmin=[0, 0], pmax=[w, h], fill=FADER_BG,
                color=FADER_BORDER, thickness=1, parent=dl)
            self._gm_widgets["fill_rect"] = dpg.draw_rectangle(
                pmin=[pad, fill_top], pmax=[w - pad, h - pad],
                fill=GM_FILL, color=(0, 0, 0, 0), parent=dl)
            self._gm_widgets["grab_line"] = dpg.draw_line(
                p1=[0, fill_top], p2=[w, fill_top],
                color=GM_GRAB, thickness=2, parent=dl)

            self._gm_widgets["value_text"] = dpg.add_text(
                self._format_value(level), color=(255, 220, 140))

            dpg.add_spacer(height=4)
            dpg.add_button(label="FULL", width=w, callback=self._on_gm_full)
            dpg.add_button(label="BLACK", width=w, callback=self._on_gm_blackout)

    # --- Calculs visuels ---

    @staticmethod
    def _value_to_y(value: int, height: int, pad: int = 2) -> float:
        """Convertit une valeur DMX (0-255) en coordonnée Y dans le drawlist."""
        inner_h = height - 2 * pad
        fill_h = (value / 255) * inner_h
        return height - pad - fill_h

    def _update_fader_visual(self, fader_id: int) -> None:
        """Met à jour le dessin d'un fader."""
        widgets = self._fader_widgets.get(fader_id)
        if not widgets:
            return

        fader = self._engine.faders.get_fader(fader_id)
        if fader is None:
            return

        w = self._fader_width
        h = self._slider_height
        pad = 2
        fill_top = self._value_to_y(fader.level, h, pad)

        if dpg.does_item_exist(widgets["fill_rect"]):
            dpg.configure_item(widgets["fill_rect"],
                               pmin=[pad, fill_top], pmax=[w - pad, h - pad])
        if dpg.does_item_exist(widgets["grab_line"]):
            dpg.configure_item(widgets["grab_line"],
                               p1=[0, fill_top], p2=[w, fill_top])
        if dpg.does_item_exist(widgets["value_text"]):
            dpg.set_value(widgets["value_text"], self._format_value(fader.level))
        if dpg.does_item_exist(widgets["label_text"]):
            dpg.set_value(widgets["label_text"], fader.label or "---")

    def _update_gm_visual(self) -> None:
        level = self._engine.grand_master.level
        w = 60
        h = self._slider_height
        pad = 2
        fill_top = self._value_to_y(level, h, pad)

        if dpg.does_item_exist(self._gm_widgets.get("fill_rect", 0)):
            dpg.configure_item(self._gm_widgets["fill_rect"],
                               pmin=[pad, fill_top], pmax=[w - pad, h - pad])
        if dpg.does_item_exist(self._gm_widgets.get("grab_line", 0)):
            dpg.configure_item(self._gm_widgets["grab_line"],
                               p1=[0, fill_top], p2=[w, fill_top])
        if dpg.does_item_exist(self._gm_widgets.get("value_text", 0)):
            dpg.set_value(self._gm_widgets["value_text"], self._format_value(level))

    def _update_fader_tooltip(self, fader_id: int, widgets: dict | None = None) -> None:
        """Met à jour le tooltip d'un fader avec la liste des circuits pilotés."""
        if widgets is None:
            widgets = self._fader_widgets.get(fader_id)
        if not widgets or not dpg.does_item_exist(widgets.get("tooltip_text", 0)):
            return

        fader = self._engine.faders.get_fader(fader_id)
        if fader and fader.contents:
            circuits_str = ", ".join(str(c) for c in sorted(fader.contents.keys()))
            text = f"F{fader_id} - Circuits : {circuits_str}"
        else:
            text = f"F{fader_id} - Vide"
        dpg.set_value(widgets["tooltip_text"], text)

    def _update_fader_display(self, fader_id: int) -> None:
        self._update_fader_visual(fader_id)
        self._update_fader_tooltip(fader_id)

        widgets = self._fader_widgets.get(fader_id)
        if widgets and dpg.does_item_exist(widgets.get("select_btn", 0)):
            selected = fader_id in self._selection
            dpg.bind_item_theme(widgets["select_btn"],
                                self._theme_select_btn_on if selected else self._theme_select_btn)

    def _update_all_displays(self) -> None:
        for fader_id in self._fader_widgets:
            self._update_fader_display(fader_id)
        self._update_gm_visual()

    def _update_selection_display(self) -> None:
        for fader_id, widgets in self._fader_widgets.items():
            if dpg.does_item_exist(widgets.get("select_btn", 0)):
                selected = fader_id in self._selection
                dpg.bind_item_theme(widgets["select_btn"],
                                    self._theme_select_btn_on if selected else self._theme_select_btn)

    # --- Mouse drag pour drawlist faders ---

    def _on_mouse_click(self, sender: int, value: Any) -> None:
        """Clic souris : démarrer le drag sur un fader si survolé."""
        if not dpg.does_item_exist(self._window_id):
            return
        if not dpg.is_item_hovered(self._window_id):
            return

        # Vérifier le Grand Master
        gm_dl = self._gm_widgets.get("drawlist")
        if gm_dl and dpg.does_item_exist(gm_dl) and dpg.is_item_hovered(gm_dl):
            self._dragging_gm = True
            self._apply_mouse_to_fader_dl(gm_dl, is_gm=True)
            return

        # Vérifier les faders
        for dl_id, fader_id in self._drawlist_to_fader.items():
            if dpg.does_item_exist(dl_id) and dpg.is_item_hovered(dl_id):
                self._dragging_fader = fader_id
                self._apply_mouse_to_fader_dl(dl_id, is_gm=False)
                return

    def _on_mouse_move(self, sender: int, value: Any) -> None:
        """Mouvement souris : mettre à jour le fader si en train de dragger."""
        if self._dragging_gm:
            gm_dl = self._gm_widgets.get("drawlist")
            if gm_dl:
                self._apply_mouse_to_fader_dl(gm_dl, is_gm=True)
        elif self._dragging_fader is not None:
            widgets = self._fader_widgets.get(self._dragging_fader)
            if widgets:
                self._apply_mouse_to_fader_dl(widgets["drawlist"], is_gm=False)

    def _on_mouse_release(self, sender: int, value: Any) -> None:
        """Relâchement souris : arrêter le drag."""
        self._dragging_fader = None
        self._dragging_gm = False

    def _apply_mouse_to_fader_dl(self, dl_id: int, is_gm: bool) -> None:
        """Calcule la valeur du fader à partir de la position de la souris."""
        if not dpg.does_item_exist(dl_id):
            return

        mouse_y = dpg.get_mouse_pos()[1]
        rect_min = dpg.get_item_rect_min(dl_id)
        rect_max = dpg.get_item_rect_max(dl_id)
        height = rect_max[1] - rect_min[1]

        if height <= 0:
            return

        relative_y = max(0, min(height, mouse_y - rect_min[1]))
        value = int((1.0 - relative_y / height) * 255)
        value = max(0, min(255, value))

        if is_gm:
            self._engine.grand_master.level = value
            self._update_gm_visual()
        else:
            fader_id = self._dragging_fader
            if fader_id is not None:
                self._engine.faders.set_level(fader_id, value)
                self._update_fader_visual(fader_id)

    # --- Sélection ---

    def _on_fader_select(self, sender: int, value: Any, user_data: int) -> None:
        fader_id = user_data
        ctrl = dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)
        shift = dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift)

        if ctrl:
            if fader_id in self._selection:
                self._selection.discard(fader_id)
            else:
                self._selection.add(fader_id)
            self._last_selected = fader_id
        elif shift:
            if self._last_selected == 0:
                self._selection = {fader_id}
            else:
                start = min(self._last_selected, fader_id)
                end = max(self._last_selected, fader_id)
                for fid in range(start, end + 1):
                    self._selection.add(fid)
        else:
            if self._selection == {fader_id}:
                self._selection.clear()
                self._last_selected = 0
            else:
                self._selection = {fader_id}
                self._last_selected = fader_id

        self._update_selection_display()

    # --- Callbacks valeurs ---

    def _on_value_input(self, sender: int, value: str) -> None:
        dmx_value = self._parse_input(value)
        if dmx_value is not None:
            self._set_selected_value(dmx_value)
        dpg.set_value(sender, "")

    def _set_selected_value(self, value: int) -> None:
        for fader_id in self._selection:
            self._engine.faders.set_level(fader_id, value)
            self._update_fader_display(fader_id)

    def _clear_selected(self) -> None:
        for fader_id in self._selection:
            self._engine.faders.set_level(fader_id, 0)
            self._update_fader_display(fader_id)

    def _clear_all(self) -> None:
        self._engine.faders.all_down()
        self._update_all_displays()

    def _on_gm_full(self) -> None:
        self._engine.grand_master.full()
        self._update_gm_visual()

    def _on_gm_blackout(self) -> None:
        self._engine.grand_master.blackout()
        self._update_gm_visual()

    def _on_display_mode_changed(self, sender: int, value: str) -> None:
        self._display_percent = (value == "%")
        if self._display_percent:
            dpg.configure_item(self._input_widget, hint="0-100%")
        else:
            dpg.configure_item(self._input_widget, hint="0-255")
        self._update_all_displays()

    # --- Callbacks layout ---

    def _on_columns_changed(self, sender: int, value: int) -> None:
        if self._updating_layout:
            return
        self._updating_layout = True
        self._columns = max(1, min(512, value))
        self._rows = math.ceil(self._fader_count / self._columns)
        dpg.set_value(self._rows_widget, self._rows)
        self._rebuild_grid()
        self._updating_layout = False

    def _on_rows_changed(self, sender: int, value: int) -> None:
        if self._updating_layout:
            return
        self._updating_layout = True
        self._rows = max(1, min(512, value))
        self._columns = math.ceil(self._fader_count / self._rows)
        dpg.set_value(self._columns_widget, self._columns)
        self._rebuild_grid()
        self._updating_layout = False

    def _on_fader_count_changed(self, sender: int, value: int) -> None:
        if self._updating_layout:
            return
        self._updating_layout = True
        self._fader_count = max(1, min(self._engine.faders.count, value))
        self._rows = math.ceil(self._fader_count / self._columns)
        dpg.set_value(self._rows_widget, self._rows)
        self._rebuild_grid()
        self._updating_layout = False

    def _on_fader_size_changed(self, sender: int, value: int) -> None:
        if self._updating_layout:
            return
        self._updating_layout = True
        self._fader_width = max(MIN_FADER_WIDTH, min(MAX_FADER_WIDTH,
                                dpg.get_value(self._fader_width_widget)))
        self._slider_height = max(MIN_SLIDER_HEIGHT, min(MAX_SLIDER_HEIGHT,
                                  dpg.get_value(self._slider_height_widget)))
        self._rebuild_grid()
        self._rebuild_gm()
        self._updating_layout = False

    def _reset_layout(self) -> None:
        self._updating_layout = True
        self._columns = DEFAULT_COLUMNS
        self._fader_count = min(DEFAULT_FADER_COUNT, self._engine.faders.count)
        self._rows = math.ceil(self._fader_count / self._columns)
        self._fader_width = DEFAULT_FADER_WIDTH
        self._slider_height = DEFAULT_SLIDER_HEIGHT
        dpg.set_value(self._columns_widget, self._columns)
        dpg.set_value(self._rows_widget, self._rows)
        dpg.set_value(self._fader_count_widget, self._fader_count)
        dpg.set_value(self._fader_width_widget, self._fader_width)
        dpg.set_value(self._slider_height_widget, self._slider_height)
        self._updating_layout = False
        self._rebuild_grid()
        self._rebuild_gm()

    # --- Clavier / molette ---

    def _on_key_press(self, sender: int, key: int) -> None:
        if not dpg.does_item_exist(self._window_id):
            return
        if not dpg.is_item_hovered(self._window_id):
            return
        if self._input_widget and (dpg.is_item_active(self._input_widget)
                                    or dpg.is_item_focused(self._input_widget)):
            return

        if key == dpg.mvKey_Escape:
            self._selection.clear()
            self._last_selected = 0
            self._update_selection_display()
        elif key == dpg.mvKey_F or key == dpg.mvKey_Home:
            self._set_selected_value(255)
        elif key == dpg.mvKey_Z or key == dpg.mvKey_End:
            self._set_selected_value(0)
        elif key == dpg.mvKey_Up:
            self._nudge_selected(5)
        elif key == dpg.mvKey_Down:
            self._nudge_selected(-5)

    def _on_mouse_wheel(self, sender: int, value: int) -> None:
        if not self._selection:
            return
        if not dpg.does_item_exist(self._window_id):
            return
        if not dpg.is_item_hovered(self._window_id):
            return
        self._nudge_selected(1 if value > 0 else -1)

    def _nudge_selected(self, delta: int) -> None:
        for fader_id in self._selection:
            fader = self._engine.faders.get_fader(fader_id)
            if fader:
                new_level = max(0, min(255, fader.level + delta))
                self._engine.faders.set_level(fader_id, new_level)
                self._update_fader_visual(fader_id)

    # --- Utilitaires ---

    def _format_value(self, value: int) -> str:
        if self._display_percent:
            return f"{round(value * 100 / 255)}%"
        return str(value)

    def _parse_input(self, text: str) -> int | None:
        text = text.strip()
        if not text:
            return None
        try:
            if text.endswith("%"):
                p = int(text[:-1])
                return round(p * 255 / 100) if 0 <= p <= 100 else None
            v = int(text)
            if self._display_percent:
                return round(v * 255 / 100) if 0 <= v <= 100 else None
            return v if 0 <= v <= 255 else None
        except ValueError:
            return None

    # --- Thèmes ---

    def _create_themes(self) -> None:
        with dpg.theme() as self._theme_select_btn:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, Colors.BG_WIDGET)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.TEXT_SECONDARY)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        with dpg.theme() as self._theme_select_btn_on:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (40, 60, 100))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (50, 70, 120))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (50, 70, 120))
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.ACCENT)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        with dpg.theme() as self._theme_toggle_on:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Button, Colors.ACCENT_ACTIVE)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, Colors.ACCENT_HOVER)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, Colors.ACCENT)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)

        with dpg.theme() as self._theme_toggle_off:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Button, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (55, 55, 70))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, Colors.BG_WIDGET)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)

    def _on_close(self) -> None:
        logger.debug("Fenetre Faders fermee")

    def update_fader_from_external(self, fader_id: int, level: int) -> None:
        self._update_fader_display(fader_id)