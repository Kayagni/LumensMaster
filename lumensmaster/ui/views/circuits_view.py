"""
Vue Circuits : grille interactive des 512 circuits DMX.

Affiche une grille configurable (24×22 par défaut) de cellules représentant
chaque circuit. Permet la sélection, la saisie clavier, le pilotage à la
molette, et l'enregistrement sur fader.

Interactions :
    - Clic         → sélectionne un circuit
    - Ctrl+clic    → ajoute/retire de la sélection
    - Shift+clic   → sélection par plage
    - Molette      → +1/-1 sur la sélection
    - Saisie texte → valeur DMX ou % sur la sélection
    - Full (F)     → 255 sur la sélection
    - Zéro (Z)     → 0 sur la sélection
    - Échap        → désélectionner tout
"""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from lumensmaster.core.engine import Engine
from lumensmaster.ui.theme import Colors

logger = logging.getLogger(__name__)

DEFAULT_COLUMNS = 24
CELL_WIDTH = 52
CELL_HEIGHT = 38
CELL_SPACING = 2


class CircuitsView:
    """
    Fenêtre flottante affichant la grille des 512 circuits.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._columns = DEFAULT_COLUMNS
        self._window_id: int = 0
        self._cells: dict[int, dict] = {}  # circuit → {group, label, value, bar}
        self._input_buffer: str = ""
        self._input_widget: int = 0
        self._theme_normal: int = 0
        self._theme_selected: int = 0
        self._theme_active: int = 0

        # S'abonner aux changements de circuits
        self._engine.bus.on("circuit.changed", self._on_circuit_changed)

    def build(self) -> int:
        """
        Construit la fenêtre flottante des circuits.

        Returns:
            L'identifiant de la fenêtre Dear PyGui.
        """
        self._create_themes()

        self._window_id = dpg.add_window(
            label="Circuits",
            width=min(self._columns * (CELL_WIDTH + CELL_SPACING) + 40, 1350),
            height=700,
            pos=(10, 60),
            no_scrollbar=False,
            on_close=self._on_close,
        )

        with dpg.group(parent=self._window_id):
            # Barre d'outils de la vue circuits
            self._build_toolbar()

            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)

            # Grille des circuits
            self._build_grid()

        # Handler global pour le clavier et la molette
        # IMPORTANT : doit être créé au niveau racine, pas dans une fenêtre
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
            # Saisie de valeur
            dpg.add_text("Valeur :", color=Colors.TEXT_SECONDARY)
            self._input_widget = dpg.add_input_text(
                width=80,
                hint="0-255",
                on_enter=True,
                callback=self._on_value_input,
            )

            dpg.add_spacer(width=8)

            # Boutons rapides
            dpg.add_button(
                label="Full",
                width=50,
                callback=lambda: self._set_selected_value(255),
            )
            dpg.add_button(
                label="Zéro",
                width=50,
                callback=lambda: self._set_selected_value(0),
            )

            dpg.add_spacer(width=16)

            # Toggle DMX / %
            dpg.add_text("Affichage :", color=Colors.TEXT_SECONDARY)
            dpg.add_radio_button(
                items=["DMX", "%"],
                default_value="DMX",
                horizontal=True,
                callback=self._on_display_mode_changed,
            )

            dpg.add_spacer(width=16)

            # Enregistrement sur fader
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

            # Clear
            dpg.add_button(
                label="Clear sélection",
                callback=self._clear_selected,
            )
            dpg.add_button(
                label="Clear tout",
                callback=self._clear_all,
            )

    def _build_grid(self) -> None:
        """Construit la grille des 512 circuits."""
        row_count = (512 + self._columns - 1) // self._columns

        for row in range(row_count):
            with dpg.group(horizontal=True):
                for col in range(self._columns):
                    circuit = row * self._columns + col + 1
                    if circuit > 512:
                        break
                    self._build_cell(circuit)

    def _build_cell(self, circuit: int) -> None:
        """Construit une cellule individuelle pour un circuit."""
        cell = {}

        cell["button"] = dpg.add_button(
            label=f"{circuit}\n---",
            width=CELL_WIDTH,
            height=CELL_HEIGHT,
            callback=self._on_cell_click,
            user_data=circuit,
        )
        dpg.bind_item_theme(cell["button"], self._theme_normal)

        self._cells[circuit] = cell

    def _create_themes(self) -> None:
        """Crée les thèmes pour les différents états des cellules."""
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

        # Cellule avec valeur active (> 0)
        with dpg.theme() as self._theme_active:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (30, 50, 40))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (40, 65, 50))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (40, 65, 50))
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.SUCCESS)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        # Cellule sélectionnée ET active
        with dpg.theme() as self._theme_selected_active:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (40, 70, 80))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (50, 85, 100))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (50, 85, 100))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (140, 230, 255))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

    # --- Mise à jour de l'affichage ---

    def _update_cell(self, circuit: int) -> None:
        """Met à jour l'affichage d'une cellule."""
        cell = self._cells.get(circuit)
        if not cell:
            return

        level = self._engine.circuits.get_level(circuit)
        selected = self._engine.circuits.is_selected(circuit)
        value_str = self._engine.circuits.format_value(level)

        # Texte du bouton
        display = f"{circuit}\n{value_str}" if level > 0 else f"{circuit}\n---"
        dpg.set_item_label(cell["button"], display)

        # Thème selon l'état
        if selected and level > 0:
            dpg.bind_item_theme(cell["button"], self._theme_selected_active)
        elif selected:
            dpg.bind_item_theme(cell["button"], self._theme_selected)
        elif level > 0:
            dpg.bind_item_theme(cell["button"], self._theme_active)
        else:
            dpg.bind_item_theme(cell["button"], self._theme_normal)

    def _update_all_cells(self) -> None:
        """Met à jour l'affichage de toutes les cellules."""
        for circuit in range(1, 513):
            self._update_cell(circuit)

    def _update_selection_display(self) -> None:
        """Met à jour l'affichage de la sélection (thèmes seulement)."""
        for circuit in range(1, 513):
            self._update_cell(circuit)

    # --- Callbacks UI ---

    def _on_cell_click(self, sender: int, value: Any, user_data: int) -> None:
        """Clic sur une cellule de circuit."""
        circuit = user_data

        # Vérifier les modificateurs clavier
        ctrl = dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)
        shift = dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift)

        if ctrl:
            self._engine.circuits.select_add(circuit)
        elif shift:
            self._engine.circuits.select_range(circuit)
        else:
            self._engine.circuits.select(circuit)

        self._update_selection_display()

    def _on_value_input(self, sender: int, value: str) -> None:
        """Saisie de valeur dans le champ texte."""
        dmx_value = self._engine.circuits.parse_input(value)
        if dmx_value is not None:
            self._set_selected_value(dmx_value)
        # Vider le champ
        dpg.set_value(sender, "")

    def _on_key_press(self, sender: int, key: int) -> None:
        """Gestion des raccourcis clavier."""
        # Ne réagir que si la fenêtre circuits est focalisée
        if not dpg.does_item_exist(self._window_id):
            return
        if not dpg.is_item_focused(self._window_id):
            # Vérifier si un enfant de la fenêtre est focalisé
            focused = dpg.get_active_window()
            if focused != self._window_id:
                return

        # Ne pas intercepter si on est dans le champ de saisie
        if dpg.is_item_active(self._input_widget):
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

        # Vérifier que la souris est au-dessus de la fenêtre circuits
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
        # Mettre à jour le hint du champ de saisie
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
            logger.info("Rien à enregistrer (aucun circuit actif)")
            return

        self._engine.faders.set_contents(fader_id, snapshot)
        self._engine.faders.set_label(fader_id, f"Rec.{fader_id}")
        logger.info(
            "Enregistré %d circuits sur le fader %d",
            len(snapshot),
            fader_id,
        )

    def _clear_selected(self) -> None:
        """Remet les circuits sélectionnés à zéro."""
        self._engine.circuits.clear_selected()
        self._update_selection_display()
        self._engine.update_dmx()

    def _clear_all(self) -> None:
        """Remet tous les circuits à zéro."""
        self._engine.circuits.clear_all()
        self._update_all_cells()
        self._engine.update_dmx()

    def _set_selected_value(self, value: int) -> None:
        """Applique une valeur aux circuits sélectionnés."""
        self._engine.circuits.set_selected_level(value)
        self._update_selection_display()
        self._engine.update_dmx()

    def _on_close(self) -> None:
        """Appelé quand la fenêtre est fermée."""
        logger.debug("Fenêtre Circuits fermée")

    # --- Callback événement bus ---

    def _on_circuit_changed(self, circuit: int = 0, level: int = 0, **kwargs) -> None:
        """Appelé quand un circuit change de valeur (via le bus d'événements)."""
        if circuit > 0:
            self._update_cell(circuit)