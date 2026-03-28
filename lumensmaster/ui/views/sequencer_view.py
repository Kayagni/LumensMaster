"""
Vue Séquenceur : fenêtre flottante de gestion des cues.

Catégories de commandes :
    - Transport : GO, GO BACK, PAUSE, slider crossfade manuel, progression
    - Cues : créer, éditer, supprimer, enregistrer depuis circuits

Affichage :
    - Liste des cues en tableau (numéro, nom, temps, link)
    - Cue onstage surlignée en vert
    - Cue preset (suivante) surlignée en bleu
    - Barre de progression du crossfade
"""

from __future__ import annotations

import logging
from typing import Any

import dearpygui.dearpygui as dpg

from lumensmaster.core.engine import Engine
from lumensmaster.modules.sequencer import CrossfadeMode
from lumensmaster.ui.theme import Colors
from lumensmaster.ui.icons import get_icon_manager

logger = logging.getLogger(__name__)


class SequencerView:
    """Fenêtre flottante du séquenceur."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._icons = get_icon_manager()

        # Widgets
        self._window_id: int = 0
        self._handler_registry: int = 0
        self._cue_list_container: int = 0
        self._progress_bar: int = 0
        self._progress_text: int = 0
        self._crossfade_slider: int = 0
        self._status_text: int = 0
        self._onstage_text: int = 0
        self._preset_text: int = 0

        # Widgets enregistrement
        self._rec_number_input: int = 0
        self._rec_name_input: int = 0
        self._rec_fade_in: int = 0
        self._rec_fade_out: int = 0
        self._rec_delay_in: int = 0
        self._rec_delay_out: int = 0
        self._rec_link_time: int = 0

        # Widgets édition
        self._edit_cue_combo: int = 0

        # Catégories
        self._category_visible: dict[str, bool] = {"transport": True, "cues": False}
        self._category_groups: dict[str, int] = {}
        self._category_buttons: dict[str, int] = {}

        # Thèmes
        self._theme_toggle_on: int = 0
        self._theme_toggle_off: int = 0
        self._theme_cue_onstage: int = 0
        self._theme_cue_preset: int = 0
        self._theme_cue_normal: int = 0
        self._theme_go_btn: int = 0

        # Cue rows pour mise à jour
        self._cue_rows: dict[float, dict] = {}

        # S'abonner aux événements
        self._engine.bus.on("sequencer.state_changed", self._on_state_changed)
        self._engine.bus.on("sequencer.cues_changed", self._on_cues_changed)
        self._engine.bus.on("sequencer.progress_changed", self._on_progress_changed)

    def build(self) -> int:
        self._create_themes()

        self._window_id = dpg.add_window(
            label="Sequenceur",
            width=900,
            height=550,
            pos=(400, 60),
            no_scrollbar=False,
            on_close=self._on_close,
        )

        with dpg.group(parent=self._window_id):
            # Boutons catégories
            self._build_category_buttons()
            dpg.add_spacer(height=2)

            # Transport (visible par défaut)
            self._category_groups["transport"] = dpg.add_group(show=True)
            with dpg.group(parent=self._category_groups["transport"]):
                self._build_transport()
                dpg.add_spacer(height=2)
                dpg.add_separator()

            # Cues
            self._category_groups["cues"] = dpg.add_group(show=False)
            with dpg.group(parent=self._category_groups["cues"]):
                self._build_cue_editor()
                dpg.add_spacer(height=2)
                dpg.add_separator()

            dpg.add_spacer(height=4)

            # Liste des cues
            self._cue_list_container = dpg.add_group()

        self._rebuild_cue_list()

        # Handlers
        with dpg.handler_registry() as handler:
            dpg.add_key_press_handler(callback=self._on_key_press)
        self._handler_registry = handler

        # Appliquer le thème toggle initial
        for cat_key, btn_id in self._category_buttons.items():
            visible = self._category_visible.get(cat_key, False)
            dpg.bind_item_theme(btn_id,
                                self._theme_toggle_on if visible else self._theme_toggle_off)

        return self._window_id

    def destroy(self) -> None:
        if self._handler_registry:
            dpg.delete_item(self._handler_registry)
        if self._window_id and dpg.does_item_exist(self._window_id):
            dpg.delete_item(self._window_id)

    # --- Catégories ---

    def _build_category_buttons(self) -> None:
        with dpg.group(horizontal=True):
            self._category_buttons["transport"] = self._icons.image_button(
                icon_name="btn_Sequencer_Transport", tooltip="Transport",
                fallback_label="Trsp", callback=self._on_toggle_category,
                user_data="transport")
            dpg.add_spacer(width=4)
            self._category_buttons["cues"] = self._icons.image_button(
                icon_name="btn_Sequencer_Cues", tooltip="Gestion des cues",
                fallback_label="Cues", callback=self._on_toggle_category,
                user_data="cues")

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

    # --- Transport ---

    def _build_transport(self) -> None:
        # Ligne 1 : boutons de transport + info
        with dpg.group(horizontal=True):
            dpg.add_button(label="GO", width=80, height=40,
                           callback=self._on_go)
            dpg.add_spacer(width=8)
            dpg.add_button(label="GO BACK", width=80, height=40,
                           callback=self._on_go_back)
            dpg.add_spacer(width=8)
            dpg.add_button(label="PAUSE", width=80, height=40,
                           callback=self._on_pause)

            dpg.add_spacer(width=24)

            # Infos onstage / preset
            with dpg.group():
                self._onstage_text = dpg.add_text(
                    "Onstage : ---", color=Colors.SUCCESS)
                self._preset_text = dpg.add_text(
                    "Preset  : ---", color=Colors.ACCENT)

            dpg.add_spacer(width=24)

            # Status
            self._status_text = dpg.add_text(
                "IDLE", color=Colors.TEXT_SECONDARY)

        dpg.add_spacer(height=4)

        # Ligne 2 : crossfade
        with dpg.group(horizontal=True):
            dpg.add_text("Crossfade :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)

            # Barre de progression
            self._progress_bar = dpg.add_progress_bar(
                default_value=0.0, width=300)

            dpg.add_spacer(width=8)
            self._progress_text = dpg.add_text("0%", color=Colors.TEXT_SECONDARY)

            dpg.add_spacer(width=16)

            # Slider manuel
            dpg.add_text("Manuel :", color=Colors.TEXT_SECONDARY)
            self._crossfade_slider = dpg.add_slider_float(
                default_value=0.0, min_value=0.0, max_value=1.0,
                width=200, format="%.0f%%",
                callback=self._on_manual_slider,
            )

        self._update_transport_display()

    # --- Éditeur de cues ---

    def _build_cue_editor(self) -> None:
        # Ligne 1 : Enregistrer une nouvelle cue
        with dpg.group(horizontal=True):
            dpg.add_text("Nouvelle cue :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)

            dpg.add_text("N:", color=Colors.TEXT_SECONDARY)
            self._rec_number_input = dpg.add_input_float(
                default_value=self._engine.sequencer.get_next_free_number(),
                width=80, format="%.1f", step=0.1)

            dpg.add_text("Nom:", color=Colors.TEXT_SECONDARY)
            self._rec_name_input = dpg.add_input_text(
                width=150, hint="Nom de la cue")

            dpg.add_spacer(width=8)

            dpg.add_text("In:", color=Colors.TEXT_SECONDARY)
            self._rec_fade_in = dpg.add_input_float(
                default_value=3.0, width=60, format="%.1f", step=0.5)
            dpg.add_text("Out:", color=Colors.TEXT_SECONDARY)
            self._rec_fade_out = dpg.add_input_float(
                default_value=3.0, width=60, format="%.1f", step=0.5)
            dpg.add_text("DIn:", color=Colors.TEXT_SECONDARY)
            self._rec_delay_in = dpg.add_input_float(
                default_value=0.0, width=60, format="%.1f", step=0.5)
            dpg.add_text("DOut:", color=Colors.TEXT_SECONDARY)
            self._rec_delay_out = dpg.add_input_float(
                default_value=0.0, width=60, format="%.1f", step=0.5)
            dpg.add_text("Link:", color=Colors.TEXT_SECONDARY)
            self._rec_link_time = dpg.add_input_float(
                default_value=0.0, width=60, format="%.1f", step=0.5)

        dpg.add_spacer(height=4)

        # Ligne 2 : Boutons d'action
        with dpg.group(horizontal=True):
            dpg.add_button(label="REC (depuis circuits)",
                           callback=self._on_record_from_circuits)
            dpg.add_spacer(width=8)
            dpg.add_button(label="REC (depuis sortie DMX)",
                           callback=self._on_record_from_output)
            dpg.add_spacer(width=24)

            # Supprimer une cue
            dpg.add_text("Supprimer cue :", color=Colors.TEXT_SECONDARY)
            self._edit_cue_combo = dpg.add_combo(
                items=self._get_cue_labels(), width=200, default_value="")
            dpg.add_button(label="Supprimer", callback=self._on_delete_cue)

    # --- Liste des cues ---

    def _rebuild_cue_list(self) -> None:
        """Reconstruit la liste complète des cues."""
        self._cue_rows.clear()

        if dpg.does_item_exist(self._cue_list_container):
            children = dpg.get_item_children(self._cue_list_container, 1)
            if children:
                for child in children:
                    dpg.delete_item(child)

        seq = self._engine.sequencer

        if not seq.cues:
            with dpg.group(parent=self._cue_list_container):
                dpg.add_text("Aucune cue", color=Colors.TEXT_DISABLED)
            return

        # En-tête
        with dpg.group(parent=self._cue_list_container):
            with dpg.group(horizontal=True):
                dpg.add_text("", color=Colors.TEXT_DISABLED)
                dpg.add_button(label="N", width=60, enabled=False)
                dpg.add_button(label="Nom", width=180, enabled=False)
                dpg.add_button(label="Fade In", width=70, enabled=False)
                dpg.add_button(label="Fade Out", width=70, enabled=False)
                dpg.add_button(label="Del.In", width=70, enabled=False)
                dpg.add_button(label="Del.Out", width=70, enabled=False)
                dpg.add_button(label="Link", width=70, enabled=False)
                dpg.add_button(label="Circuits", width=70, enabled=False)

            dpg.add_separator()

            # Lignes de cues
            for i, cue in enumerate(seq.cues):
                self._build_cue_row(i, cue)

    def _build_cue_row(self, index: int, cue: Cue) -> None:
        """Construit une ligne pour une cue."""
        from lumensmaster.modules.sequencer import Cue  # import local

        seq = self._engine.sequencer
        is_onstage = (index == seq.current_index)
        is_preset = (index == seq.current_index + 1)

        row = {}

        with dpg.group(horizontal=True) as row_group:
            # Indicateur
            if is_onstage:
                indicator = dpg.add_text(">>", color=Colors.SUCCESS)
            elif is_preset:
                indicator = dpg.add_text(">", color=Colors.ACCENT)
            else:
                indicator = dpg.add_text("  ", color=Colors.TEXT_DISABLED)
            row["indicator"] = indicator

            # Numéro (cliquable pour GO TO)
            row["number_btn"] = dpg.add_button(
                label=f"{cue.number:.1f}", width=60,
                callback=self._on_cue_click,
                user_data=cue.number,
            )

            # Nom
            row["name_text"] = dpg.add_text(
                cue.name[:25] if cue.name else "---",
                color=Colors.TEXT_PRIMARY,
            )
            # Padding pour aligner
            if len(cue.name) < 25:
                dpg.add_spacer(width=max(0, (25 - len(cue.name)) * 7))

            # Temps
            row["fade_in_text"] = dpg.add_text(
                f"{cue.fade_in:.1f}s", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=24)
            row["fade_out_text"] = dpg.add_text(
                f"{cue.fade_out:.1f}s", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=24)
            row["delay_in_text"] = dpg.add_text(
                f"{cue.delay_in:.1f}s", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=24)
            row["delay_out_text"] = dpg.add_text(
                f"{cue.delay_out:.1f}s", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=24)

            # Link
            link_str = f"{cue.link_time:.1f}s" if cue.link_time > 0 else "---"
            link_color = Colors.WARNING if cue.link_time > 0 else Colors.TEXT_DISABLED
            row["link_text"] = dpg.add_text(link_str, color=link_color)
            dpg.add_spacer(width=24)

            # Nombre de circuits
            row["circuits_text"] = dpg.add_text(
                str(len(cue.contents)), color=Colors.TEXT_SECONDARY)

        row["group"] = row_group

        # Appliquer le thème selon l'état
        if is_onstage:
            dpg.bind_item_theme(row["number_btn"], self._theme_cue_onstage)
        elif is_preset:
            dpg.bind_item_theme(row["number_btn"], self._theme_cue_preset)
        else:
            dpg.bind_item_theme(row["number_btn"], self._theme_cue_normal)

        self._cue_rows[cue.number] = row

    # --- Mise à jour affichage ---

    def _update_transport_display(self) -> None:
        """Met à jour les infos de transport."""
        seq = self._engine.sequencer

        # Onstage
        current = seq.current_cue
        if current:
            dpg.set_value(self._onstage_text,
                          f"Onstage : {current.number:.1f} - {current.name}")
        else:
            dpg.set_value(self._onstage_text, "Onstage : ---")

        # Preset
        next_cue = seq.next_cue
        if next_cue:
            dpg.set_value(self._preset_text,
                          f"Preset  : {next_cue.number:.1f} - {next_cue.name}")
        else:
            dpg.set_value(self._preset_text, "Preset  : ---")

        # Status
        mode = seq.mode
        if mode == CrossfadeMode.IDLE:
            dpg.set_value(self._status_text, "IDLE")
            dpg.configure_item(self._status_text, color=Colors.TEXT_SECONDARY)
        elif mode == CrossfadeMode.TIMED:
            dpg.set_value(self._status_text, "CROSSFADE")
            dpg.configure_item(self._status_text, color=Colors.WARNING)
        elif mode == CrossfadeMode.MANUAL:
            dpg.set_value(self._status_text, "MANUEL")
            dpg.configure_item(self._status_text, color=Colors.ACCENT)
        elif mode == CrossfadeMode.PAUSED:
            dpg.set_value(self._status_text, "PAUSE")
            dpg.configure_item(self._status_text, color=Colors.ERROR)

    def _update_progress(self, progress: float) -> None:
        """Met à jour la barre de progression."""
        if dpg.does_item_exist(self._progress_bar):
            dpg.set_value(self._progress_bar, progress)
        if dpg.does_item_exist(self._progress_text):
            dpg.set_value(self._progress_text, f"{int(progress * 100)}%")

    def _get_cue_labels(self) -> list[str]:
        """Retourne les labels pour les combos de cues."""
        return [f"{c.number:.1f} - {c.name}" for c in self._engine.sequencer.cues]

    # --- Callbacks transport ---

    def _on_go(self) -> None:
        self._engine.sequencer.go()

    def _on_go_back(self) -> None:
        self._engine.sequencer.go_back()

    def _on_pause(self) -> None:
        self._engine.sequencer.pause()

    def _on_manual_slider(self, sender: int, value: float) -> None:
        """Slider de crossfade manuel."""
        seq = self._engine.sequencer
        if seq.mode == CrossfadeMode.IDLE:
            # Activer le mode manuel
            seq.set_manual_mode(True)
        if seq.mode == CrossfadeMode.MANUAL:
            seq.set_manual_progress(value)

    def _on_cue_click(self, sender: int, value: Any, user_data: float) -> None:
        """Clic sur un numéro de cue → GO TO."""
        self._engine.sequencer.go_to_cue(user_data)

    # --- Callbacks cues ---

    def _on_record_from_circuits(self) -> None:
        """Enregistre une cue depuis l'état des circuits."""
        contents = self._engine.circuits.get_active_snapshot()
        if not contents:
            logger.info("Rien a enregistrer (aucun circuit actif)")
            return
        self._record_cue(contents)

    def _on_record_from_output(self) -> None:
        """Enregistre une cue depuis la sortie DMX combinée (HTP)."""
        # Combiner toutes les sources
        htp = self._engine.faders.compute_htp()
        circuit_output = self._engine.circuits.get_output()
        for circuit, value in circuit_output.items():
            if value > htp.get(circuit, 0):
                htp[circuit] = value
        seq_output = self._engine.sequencer.get_output()
        for circuit, value in seq_output.items():
            if value > htp.get(circuit, 0):
                htp[circuit] = value

        if not htp:
            logger.info("Rien a enregistrer (sortie vide)")
            return
        self._record_cue(htp)

    def _record_cue(self, contents: dict[int, int]) -> None:
        """Enregistre une cue avec les paramètres du formulaire."""
        number = dpg.get_value(self._rec_number_input)
        name = dpg.get_value(self._rec_name_input)
        if not name:
            name = f"Cue {number:.1f}"

        self._engine.sequencer.record_cue(
            number=number,
            name=name,
            contents=contents,
            fade_in=max(0, dpg.get_value(self._rec_fade_in)),
            fade_out=max(0, dpg.get_value(self._rec_fade_out)),
            delay_in=max(0, dpg.get_value(self._rec_delay_in)),
            delay_out=max(0, dpg.get_value(self._rec_delay_out)),
            link_time=max(0, dpg.get_value(self._rec_link_time)),
        )

        # Incrémenter le numéro pour la prochaine cue
        dpg.set_value(self._rec_number_input,
                      self._engine.sequencer.get_next_free_number())
        dpg.set_value(self._rec_name_input, "")

    def _on_delete_cue(self) -> None:
        """Supprime la cue sélectionnée dans le combo."""
        selected = dpg.get_value(self._edit_cue_combo)
        if not selected:
            return
        try:
            cue_number = float(selected.split(" - ")[0])
            self._engine.sequencer.delete_cue(cue_number)
        except (ValueError, IndexError):
            pass

    # --- Callbacks événements bus ---

    def _on_state_changed(self, **kwargs) -> None:
        """Appelé quand l'état du séquenceur change (GO, PAUSE, etc.)."""
        self._update_transport_display()
        self._rebuild_cue_list()

    def _on_cues_changed(self, **kwargs) -> None:
        """Appelé quand la liste de cues change."""
        self._rebuild_cue_list()
        # Mettre à jour le combo de suppression
        if dpg.does_item_exist(self._edit_cue_combo):
            dpg.configure_item(self._edit_cue_combo,
                               items=self._get_cue_labels())
        self._update_transport_display()

    def _on_progress_changed(self, progress: float = 0.0, **kwargs) -> None:
        """Appelé pendant un crossfade pour mettre à jour la progression."""
        self._update_progress(progress)

    # --- Clavier ---

    def _on_key_press(self, sender: int, key: int) -> None:
        if not dpg.does_item_exist(self._window_id):
            return
        if not dpg.is_item_hovered(self._window_id):
            return

        # Vérifier qu'on n'est pas dans un champ de saisie
        for widget in (self._rec_name_input, self._rec_number_input):
            if widget and (dpg.is_item_active(widget) or dpg.is_item_focused(widget)):
                return

        if key == dpg.mvKey_Spacebar:
            self._on_go()
        elif key == dpg.mvKey_B:
            self._on_go_back()
        elif key == dpg.mvKey_P:
            self._on_pause()

    # --- Thèmes ---

    def _create_themes(self) -> None:
        # Toggle ON/OFF
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

        # Cue onstage (vert)
        with dpg.theme() as self._theme_cue_onstage:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (30, 70, 40))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (40, 90, 50))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (40, 90, 50))
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.SUCCESS)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        # Cue preset (bleu)
        with dpg.theme() as self._theme_cue_preset:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (30, 50, 80))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (40, 65, 100))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (40, 65, 100))
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.ACCENT)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

        # Cue normale
        with dpg.theme() as self._theme_cue_normal:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, Colors.BG_WIDGET)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.TEXT_SECONDARY)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)

    def _on_close(self) -> None:
        logger.debug("Fenetre Sequenceur fermee")