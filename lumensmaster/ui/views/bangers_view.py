"""
Vue Bangers : fenêtre flottante de gestion et de déclenchement des bangers.

Grille (exploitation) :
    - Clic gauche  : lance le banger ; s'il tourne, rollback (comme WhiteCat)
    - Clic droit   : sélectionne le banger pour l'éditer
    - Couleurs     : orange = en cours ; vert = parti (rollback possible)

Éditeur :
    - Nom, loop, période de boucle
    - Tableau d'événements : actif, délai, famille, action, paramètres
      (les champs sont générés depuis le registre d'actions)
    - Lancer / Stop / Rollback pour tester

Rafraîchissement :
    Les éditions de champs (paramètres, délai, nom) ne reconstruisent pas
    l'éditeur, pour ne pas perdre le focus pendant la saisie. Les
    changements structurels (ajout, suppression, famille, action) le
    reconstruisent.
"""

from __future__ import annotations

import logging
from typing import Any

import dearpygui.dearpygui as dpg

from lumensmaster.core.engine import Engine
from lumensmaster.modules.bangers import (
    FAMILIES, ActionParam, BangerEvent, actions_for, describe_event, get_action,
)
from lumensmaster.ui.theme import Colors

logger = logging.getLogger(__name__)


class BangersView:
    """Fenêtre flottante des bangers."""

    GRID_COLUMNS = 6
    BTN_W = 110
    BTN_H = 42

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._mgr = engine.bangers

        self._window_id: int = 0
        self._grid_container: int = 0
        self._editor_container: int = 0
        self._enabled_checkbox: int = 0
        self._copy_target_input: int = 0
        self._delete_btn: int = 0
        self._status_text: int = 0
        self._grid_handler: int = 0

        self._selected: int = 0
        self._confirm_delete: bool = False
        self._suppress_rebuild: bool = False
        self._grid_buttons: dict[int, int] = {}      # numéro -> bouton
        self._grid_items: dict[int, int] = {}        # bouton -> numéro

        # Thèmes
        self._theme_idle: int = 0
        self._theme_running: int = 0
        self._theme_fired: int = 0

        bus = self._engine.bus
        bus.on("bangers.changed", self._on_bangers_changed)
        bus.on("banger.state_changed", self._on_state_changed)
        bus.on("banger.event_fired", self._on_event_fired)
        bus.on("bangers.enabled_changed", self._on_enabled_changed)
        bus.on("show.loaded", self._on_show_loaded)

    # ------------------------------------------------------------------
    #  Construction
    # ------------------------------------------------------------------

    def build(self) -> int:
        self._create_themes()

        self._window_id = dpg.add_window(
            label="Bangers", width=820, height=560, pos=(420, 120),
            show=False, no_scrollbar=False, on_close=self._on_close,
        )

        with dpg.item_handler_registry() as self._grid_handler:
            dpg.add_item_clicked_handler(button=dpg.mvMouseButton_Right,
                                         callback=self._on_grid_right_click)

        with dpg.group(parent=self._window_id):
            with dpg.group(horizontal=True):
                self._enabled_checkbox = dpg.add_checkbox(
                    label="Bangers depuis les cues",
                    default_value=self._mgr.enabled,
                    callback=self._on_enabled_toggle)
                dpg.add_spacer(width=16)
                dpg.add_button(label="Nouveau", callback=self._on_new)
                self._delete_btn = dpg.add_button(label="Supprimer",
                                                  callback=self._on_delete)
                dpg.add_spacer(width=8)
                dpg.add_text("Copier vers", color=Colors.TEXT_SECONDARY)
                self._copy_target_input = dpg.add_input_int(
                    default_value=0, width=90, min_value=0, max_value=128,
                    min_clamped=True, max_clamped=True)
                dpg.add_button(label="Copier", callback=self._on_copy)
                dpg.add_spacer(width=8)
                dpg.add_button(label="Tout stopper",
                               callback=lambda: self._mgr.stop_all())

            dpg.add_text("Clic gauche : lancer / rollback  -  Clic droit : editer",
                         color=Colors.TEXT_DISABLED)
            dpg.add_separator()
            self._grid_container = dpg.add_group()
            dpg.add_spacer(height=4)
            dpg.add_separator()
            self._editor_container = dpg.add_group()

        self._rebuild_grid()
        self._rebuild_editor()
        return self._window_id

    def toggle(self) -> None:
        if self._window_id and dpg.does_item_exist(self._window_id):
            shown = dpg.is_item_shown(self._window_id)
            dpg.configure_item(self._window_id, show=not shown)

    def _clear(self, container: int) -> None:
        if container and dpg.does_item_exist(container):
            dpg.delete_item(container, children_only=True)

    # --- Grille -------------------------------------------------------

    def _rebuild_grid(self) -> None:
        self._clear(self._grid_container)
        self._grid_buttons.clear()
        self._grid_items.clear()
        bangers = self._mgr.bangers
        if not bangers:
            dpg.add_text("Aucun banger - cliquer sur Nouveau",
                         color=Colors.TEXT_DISABLED, parent=self._grid_container)
            return

        row = 0
        for i, b in enumerate(bangers):
            if i % self.GRID_COLUMNS == 0:
                row = dpg.add_group(horizontal=True, parent=self._grid_container)
            prefix = "> " if b.number == self._selected else ""
            label = f"{prefix}{b.number}  {b.name[:12]}"
            if b.loop:
                label += " (L)"
            btn = dpg.add_button(label=label, width=self.BTN_W, height=self.BTN_H,
                                 parent=row, callback=self._on_grid_click,
                                 user_data=b.number)
            dpg.bind_item_handler_registry(btn, self._grid_handler)
            with dpg.tooltip(btn):
                dpg.add_text(f"Banger {b.number} : {b.name}")
                for ev in b.events:
                    mark = "" if ev.enabled else " [inactif]"
                    dpg.add_text(f"  {ev.delay:5.2f}s  {describe_event(ev)}{mark}",
                                 color=Colors.TEXT_SECONDARY)
            self._grid_buttons[b.number] = btn
            self._grid_items[btn] = b.number
            self._apply_button_theme(b.number)

    def _apply_button_theme(self, number: int) -> None:
        btn = self._grid_buttons.get(number)
        if not btn or not dpg.does_item_exist(btn):
            return
        if self._mgr.is_running(number):
            theme = self._theme_running
        elif self._mgr.has_rollback(number):
            theme = self._theme_fired
        else:
            theme = self._theme_idle
        dpg.bind_item_theme(btn, theme)

    # --- Éditeur ------------------------------------------------------

    def _rebuild_editor(self) -> None:
        self._clear(self._editor_container)
        parent = self._editor_container
        b = self._mgr.get(self._selected)
        if b is None:
            dpg.add_text("Aucun banger selectionne (clic droit dans la grille)",
                         color=Colors.TEXT_DISABLED, parent=parent)
            return

        with dpg.group(horizontal=True, parent=parent):
            dpg.add_text(f"Banger {b.number}", color=Colors.ACCENT)
            dpg.add_input_text(default_value=b.name, width=180, hint="Nom",
                               callback=self._on_name_changed)
            dpg.add_spacer(width=8)
            dpg.add_checkbox(label="Loop", default_value=b.loop,
                             callback=self._on_loop_changed)
            dpg.add_text("Periode", color=Colors.TEXT_SECONDARY)
            dpg.add_input_float(default_value=b.loop_interval, width=90,
                                format="%.2f s", step=0.5, min_value=0.0,
                                min_clamped=True, callback=self._on_loop_interval)
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("0 = duree du banger. La periode n'est jamais plus\n"
                             "courte que le dernier delai.")

        with dpg.group(horizontal=True, parent=parent):
            dpg.add_button(label="Lancer", width=80,
                           callback=lambda: self._mgr.start(self._selected))
            dpg.add_button(label="Stop", width=80,
                           callback=lambda: self._mgr.stop(self._selected))
            dpg.add_button(label="Rollback", width=80,
                           callback=lambda: self._mgr.rollback(self._selected))
            dpg.add_spacer(width=12)
            self._status_text = dpg.add_text("", color=Colors.TEXT_SECONDARY)
        self._update_status()

        dpg.add_spacer(height=4, parent=parent)

        with dpg.table(parent=parent, header_row=True, borders_innerH=True,
                       borders_outerH=True, row_background=True,
                       policy=dpg.mvTable_SizingFixedFit):
            dpg.add_table_column(label="On")
            dpg.add_table_column(label="Delai (s)")
            dpg.add_table_column(label="Famille")
            dpg.add_table_column(label="Action")
            dpg.add_table_column(label="Parametres", width_stretch=True)
            dpg.add_table_column(label="")

            for idx, ev in enumerate(b.events):
                self._build_event_row(b.number, idx, ev)

        with dpg.group(horizontal=True, parent=parent):
            dpg.add_button(label="+ Evenement", callback=self._on_add_event)
            dpg.add_text("Les delais sont comptes depuis le depart du banger.",
                         color=Colors.TEXT_DISABLED)

    def _build_event_row(self, number: int, idx: int, ev: BangerEvent) -> None:
        spec = get_action(ev.family, ev.action)
        family_labels = list(FAMILIES.values())
        family_keys = list(FAMILIES.keys())
        specs = actions_for(ev.family)

        with dpg.table_row():
            dpg.add_checkbox(default_value=ev.enabled,
                             callback=self._on_event_enabled, user_data=idx)
            dpg.add_input_float(default_value=ev.delay, width=90, format="%.2f",
                                step=0.1, min_value=0.0, min_clamped=True,
                                callback=self._on_event_delay, user_data=idx)
            dpg.add_combo(items=family_labels,
                          default_value=FAMILIES.get(ev.family, ev.family),
                          width=110, callback=self._on_event_family,
                          user_data=(idx, family_labels, family_keys))
            dpg.add_combo(items=[s.label for s in specs],
                          default_value=spec.label if spec else "?",
                          width=160, callback=self._on_event_action,
                          user_data=(idx, specs))
            with dpg.group(horizontal=True):
                if spec is None:
                    dpg.add_text(f"Action inconnue : {ev.family}/{ev.action}",
                                 color=Colors.ERROR)
                else:
                    params = spec.resolve_params(ev.params)
                    for p in spec.params:
                        self._build_param_widget(idx, p, params[p.key])
            with dpg.group(horizontal=True):
                dpg.add_button(label="^", width=22, callback=self._on_move_event,
                               user_data=(idx, -1))
                dpg.add_button(label="v", width=22, callback=self._on_move_event,
                               user_data=(idx, 1))
                dpg.add_button(label="X", width=22, callback=self._on_remove_event,
                               user_data=idx)

    def _build_param_widget(self, idx: int, p: ActionParam, value: Any) -> None:
        dpg.add_text(p.label, color=Colors.TEXT_SECONDARY)
        user_data = (idx, p)
        if p.kind == "choice":
            labels = [c[1] for c in p.choices]
            current = dict(p.choices).get(value, labels[0] if labels else "")
            dpg.add_combo(items=labels, default_value=current, width=90,
                          callback=self._on_param_choice, user_data=user_data)
        elif p.kind in ("float", "cue"):
            kwargs: dict[str, Any] = {}
            if p.min_value is not None:
                kwargs.update(min_value=float(p.min_value), min_clamped=True)
            if p.max_value is not None:
                kwargs.update(max_value=float(p.max_value), max_clamped=True)
            dpg.add_input_float(default_value=float(value), width=90,
                                format="%.1f" if p.kind == "cue" else "%.2f",
                                step=1.0 if p.kind == "cue" else 0.1,
                                callback=self._on_param_value,
                                user_data=user_data, **kwargs)
        else:
            kwargs = {}
            if p.min_value is not None:
                kwargs.update(min_value=int(p.min_value), min_clamped=True)
            if p.max_value is not None:
                kwargs.update(max_value=int(p.max_value), max_clamped=True)
            dpg.add_input_int(default_value=int(value), width=90,
                              callback=self._on_param_value,
                              user_data=user_data, **kwargs)
            if p.kind == "banger":
                target = self._mgr.get(int(value))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(target.name if target else "(banger inexistant)")

    def _update_status(self) -> None:
        if not self._status_text or not dpg.does_item_exist(self._status_text):
            return
        n = self._selected
        if self._mgr.is_running(n):
            dpg.set_value(self._status_text, "EN COURS")
            dpg.configure_item(self._status_text, color=Colors.WARNING)
        elif self._mgr.has_rollback(n):
            dpg.set_value(self._status_text, "Termine (rollback possible)")
            dpg.configure_item(self._status_text, color=Colors.SUCCESS)
        else:
            dpg.set_value(self._status_text, "Pret")
            dpg.configure_item(self._status_text, color=Colors.TEXT_SECONDARY)

    # ------------------------------------------------------------------
    #  Callbacks UI
    # ------------------------------------------------------------------

    def _select(self, number: int) -> None:
        self._selected = number
        self._confirm_delete = False
        if dpg.does_item_exist(self._delete_btn):
            dpg.configure_item(self._delete_btn, label="Supprimer")
        self._rebuild_grid()
        self._rebuild_editor()

    def _on_grid_click(self, sender: int, app_data: Any, user_data: int) -> None:
        self._mgr.trigger_or_rollback(user_data)

    def _on_grid_right_click(self, sender: int, app_data: Any) -> None:
        item = app_data[1] if isinstance(app_data, (list, tuple)) else app_data
        number = self._grid_items.get(item)
        if number:
            self._select(number)

    def _on_enabled_toggle(self, sender: int, value: bool) -> None:
        self._mgr.enabled = value

    def _on_new(self) -> None:
        b = self._mgr.create()
        if b is None:
            logger.warning("Plus de numéro de banger disponible")
            return
        self._select(b.number)

    def _on_delete(self) -> None:
        if not self._mgr.get(self._selected):
            return
        if not self._confirm_delete:
            self._confirm_delete = True
            dpg.configure_item(self._delete_btn, label="Confirmer ?")
            return
        self._mgr.delete(self._selected)
        self._select(0)

    def _on_copy(self) -> None:
        target = dpg.get_value(self._copy_target_input)
        if not self._mgr.get(self._selected) or target <= 0:
            return
        if self._mgr.copy(self._selected, target):
            self._select(target)

    def _edit(self, fn, *args, **kwargs) -> None:
        """Modification sans reconstruction de l'éditeur (garde le focus)."""
        self._suppress_rebuild = True
        try:
            fn(*args, **kwargs)
        finally:
            self._suppress_rebuild = False

    def _on_name_changed(self, sender: int, value: str) -> None:
        self._edit(self._mgr.update, self._selected, name=value)

    def _on_loop_changed(self, sender: int, value: bool) -> None:
        self._edit(self._mgr.update, self._selected, loop=value)

    def _on_loop_interval(self, sender: int, value: float) -> None:
        self._edit(self._mgr.update, self._selected, loop_interval=value)

    def _on_add_event(self) -> None:
        self._mgr.add_event(self._selected)

    def _on_event_enabled(self, sender: int, value: bool, user_data: int) -> None:
        self._edit(self._mgr.update_event, self._selected, user_data, enabled=value)

    def _on_event_delay(self, sender: int, value: float, user_data: int) -> None:
        self._edit(self._mgr.update_event, self._selected, user_data, delay=value)

    def _on_event_family(self, sender: int, value: str, user_data: tuple) -> None:
        idx, labels, keys = user_data
        if value in labels:
            self._mgr.update_event(self._selected, idx, family=keys[labels.index(value)])

    def _on_event_action(self, sender: int, value: str, user_data: tuple) -> None:
        idx, specs = user_data
        for s in specs:
            if s.label == value:
                self._mgr.update_event(self._selected, idx, action=s.action)
                return

    def _on_param_value(self, sender: int, value: Any, user_data: tuple) -> None:
        idx, p = user_data
        self._edit(self._mgr.update_event, self._selected, idx,
                   params={p.key: value})

    def _on_param_choice(self, sender: int, value: str, user_data: tuple) -> None:
        idx, p = user_data
        for key, label in p.choices:
            if label == value:
                self._edit(self._mgr.update_event, self._selected, idx,
                           params={p.key: key})
                return

    def _on_move_event(self, sender: int, app_data: Any, user_data: tuple) -> None:
        idx, direction = user_data
        self._mgr.move_event(self._selected, idx, direction)

    def _on_remove_event(self, sender: int, app_data: Any, user_data: int) -> None:
        self._mgr.remove_event(self._selected, user_data)

    # ------------------------------------------------------------------
    #  Callbacks bus (thread principal : poll() et UI)
    # ------------------------------------------------------------------

    def _on_bangers_changed(self, number: int = 0, **kwargs: Any) -> None:
        if not self._window_id or not dpg.does_item_exist(self._window_id):
            return
        if self._selected and self._mgr.get(self._selected) is None:
            self._selected = 0
        self._rebuild_grid()
        if not self._suppress_rebuild:
            self._rebuild_editor()

    def _on_state_changed(self, number: int = 0, **kwargs: Any) -> None:
        self._apply_button_theme(number)
        if number == self._selected:
            self._update_status()

    def _on_event_fired(self, number: int = 0, **kwargs: Any) -> None:
        self._apply_button_theme(number)

    def _on_enabled_changed(self, enabled: bool = True, **kwargs: Any) -> None:
        if self._enabled_checkbox and dpg.does_item_exist(self._enabled_checkbox):
            dpg.set_value(self._enabled_checkbox, enabled)

    def _on_show_loaded(self, **kwargs: Any) -> None:
        self._on_enabled_changed(self._mgr.enabled)
        self._select(0)

    # ------------------------------------------------------------------
    #  Thèmes / layout
    # ------------------------------------------------------------------

    def _create_themes(self) -> None:
        def button_theme(bg, hover, text):
            with dpg.theme() as t:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, bg)
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, hover)
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, hover)
                    dpg.add_theme_color(dpg.mvThemeCol_Text, text)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
            return t

        self._theme_idle = button_theme(Colors.BG_WIDGET, Colors.BG_LIGHT,
                                        Colors.TEXT_PRIMARY)
        self._theme_running = button_theme((120, 80, 10), (150, 100, 20),
                                           Colors.WARNING)
        self._theme_fired = button_theme((30, 70, 40), (40, 90, 50),
                                         Colors.SUCCESS)

    def get_layout_state(self) -> dict:
        state = {"visible": (dpg.does_item_exist(self._window_id)
                             and dpg.is_item_shown(self._window_id))}
        if dpg.does_item_exist(self._window_id):
            pos = dpg.get_item_pos(self._window_id)
            state["pos_x"], state["pos_y"] = pos[0], pos[1]
            state["width"] = dpg.get_item_width(self._window_id)
            state["height"] = dpg.get_item_height(self._window_id)
        return state

    def apply_layout_state(self, state: dict) -> None:
        if not dpg.does_item_exist(self._window_id):
            return
        if "pos_x" in state and "pos_y" in state:
            dpg.set_item_pos(self._window_id, [state["pos_x"], state["pos_y"]])
        if "width" in state:
            dpg.configure_item(self._window_id, width=state["width"])
        if "height" in state:
            dpg.configure_item(self._window_id, height=state["height"])
        dpg.configure_item(self._window_id, show=state.get("visible", False))

    def _on_close(self) -> None:
        logger.debug("Fenetre Bangers fermee")
