"""
Vue Faders : affiche les submasters sous forme de sliders verticaux.

Chaque fader affiche :
    - Son numéro
    - Un slider vertical (0-255)
    - La valeur numérique
    - Un label éditable
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from lumensmaster.core.engine import Engine
from lumensmaster.ui.theme import create_fader_theme, create_gm_theme


class FadersView:
    """
    Vue des faders submasters.
    
    Affiche une rangée de sliders verticaux + le Grand Master.
    """

    FADER_WIDTH = 50
    FADER_HEIGHT = 300

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._fader_widgets: dict[int, int] = {}  # fader_id → dpg widget id
        self._gm_widget: int = 0
        self._fader_theme: int = 0
        self._gm_theme: int = 0

    def build(self, parent: int) -> None:
        """Construit la vue des faders dans le conteneur parent."""
        self._fader_theme = create_fader_theme()
        self._gm_theme = create_gm_theme()

        with dpg.child_window(parent=parent, border=False, height=-1):
            with dpg.group(horizontal=True):
                # Zone des faders submasters
                self._build_faders_panel()

                dpg.add_spacer(width=16)

                # Séparateur visuel
                dpg.add_child_window(
                    width=2,
                    height=self.FADER_HEIGHT + 80,
                    no_scrollbar=True,
                    border=False,
                )

                dpg.add_spacer(width=16)

                # Grand Master
                self._build_grand_master()

    def _build_faders_panel(self) -> None:
        """Construit la zone des faders submasters."""
        with dpg.group(horizontal=True):
            for fader_id in range(1, self._engine.faders.count + 1):
                self._build_single_fader(fader_id)

    def _build_single_fader(self, fader_id: int) -> None:
        """Construit un fader individuel."""
        fader = self._engine.faders.get_fader(fader_id)
        if fader is None:
            return

        with dpg.group():
            # Label du fader (numéro)
            dpg.add_text(
                f"F{fader_id}",
                color=(140, 140, 160),
            )

            # Slider vertical
            slider = dpg.add_slider_int(
                default_value=0,
                min_value=0,
                max_value=255,
                vertical=True,
                width=self.FADER_WIDTH,
                height=self.FADER_HEIGHT,
                format="",
                callback=self._on_fader_move,
                user_data=fader_id,
            )
            dpg.bind_item_theme(slider, self._fader_theme)
            self._fader_widgets[fader_id] = slider

            # Valeur numérique
            dpg.add_text(
                "0",
                tag=f"fader_value_{fader_id}",
                color=(180, 200, 255),
            )

            dpg.add_spacer(width=4)

    def _build_grand_master(self) -> None:
        """Construit le Grand Master."""
        with dpg.group():
            dpg.add_text(
                "GM",
                color=(255, 180, 40),
            )

            self._gm_widget = dpg.add_slider_int(
                default_value=255,
                min_value=0,
                max_value=255,
                vertical=True,
                width=60,
                height=self.FADER_HEIGHT,
                format="",
                callback=self._on_gm_move,
            )
            dpg.bind_item_theme(self._gm_widget, self._gm_theme)

            dpg.add_text(
                "255",
                tag="gm_value",
                color=(255, 220, 140),
            )

            # Boutons Blackout / Full
            dpg.add_spacer(height=4)
            dpg.add_button(
                label="FULL",
                width=60,
                callback=self._on_gm_full,
            )
            dpg.add_button(
                label="BLACK",
                width=60,
                callback=self._on_gm_blackout,
            )

    # --- Callbacks ---

    def _on_fader_move(self, sender: int, value: int, user_data: int) -> None:
        """Appelé quand un slider de fader bouge."""
        fader_id = user_data
        self._engine.faders.set_level(fader_id, value)
        dpg.set_value(f"fader_value_{fader_id}", str(value))

    def _on_gm_move(self, sender: int, value: int) -> None:
        """Appelé quand le Grand Master bouge."""
        self._engine.grand_master.level = value
        dpg.set_value("gm_value", str(value))

    def _on_gm_full(self) -> None:
        self._engine.grand_master.full()
        dpg.set_value(self._gm_widget, 255)
        dpg.set_value("gm_value", "255")

    def _on_gm_blackout(self) -> None:
        self._engine.grand_master.blackout()
        dpg.set_value(self._gm_widget, 0)
        dpg.set_value("gm_value", "0")

    # --- Mise à jour externe ---

    def update_fader_display(self, fader_id: int, level: int) -> None:
        """Met à jour l'affichage d'un fader (ex: contrôle MIDI externe)."""
        widget = self._fader_widgets.get(fader_id)
        if widget:
            dpg.set_value(widget, level)
            dpg.set_value(f"fader_value_{fader_id}", str(level))