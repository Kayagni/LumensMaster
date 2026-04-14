"""
lumensmaster/ui/views/trichromie_view.py
Fenêtre flottante de gestion de la trichromie / quadrichromie.

Fonctionnalités :
    - Sélection du groupe couleur actif (ColorGroup)
    - Création / suppression de groupes et assignation manuelle des circuits R,G,B,A
    - Color picker natif Dear PyGui (roue + triangle HSV)
    - Palette de presets avec boutons colorés
    - Sauvegarde d'un preset depuis la couleur courante
    - REC vers un fader ou vers la cue courante du séquenceur
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

from lumensmaster.modules.trichromie import ColorGroup, ColorPreset

if TYPE_CHECKING:
    from lumensmaster.core.engine import Engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Couleurs du thème
# ---------------------------------------------------------------------------

class Colors:
    BG_WINDOW  = (25, 25, 35, 240)
    BG_CHILD   = (30, 30, 42)
    BG_LIGHT   = (45, 45, 60)
    BG_WIDGET  = (20, 20, 30)
    ACCENT     = (100, 140, 220)
    TEXT       = (220, 220, 235)
    TEXT_DIM   = (140, 140, 160)
    RED_DARK   = (180,  40,  40)
    GREEN_DARK = ( 40, 160,  40)
    ORANGE     = (200, 120,  40)


# ---------------------------------------------------------------------------
# TrichromieView
# ---------------------------------------------------------------------------

class TrichromieView:
    """Fenêtre flottante de contrôle de la trichromie."""

    TAG_WINDOW    = "trichromie_window"
    TAG_PICKER    = "trichromie_color_picker"
    TAG_GRP_COMBO = "trichromie_group_combo"
    TAG_NEW_NAME  = "trichromie_new_group_name"

    def __init__(self, engine: "Engine") -> None:
        self._engine = engine
        self._manager = engine.color_manager

        # Groupe actif
        self._active_group: str | None = None

        # IDs dynamiques (boutons presets, inputs circuits)
        self._preset_buttons:   dict[str, int] = {}
        self._preset_container = "trichromie_preset_container"

        # Widgets circuits
        self._input_red   = "trichromie_in_red"
        self._input_green = "trichromie_in_green"
        self._input_blue  = "trichromie_in_blue"
        self._input_amber = "trichromie_in_amber"

        # REC
        self._rec_fader_input = "trichromie_rec_fader"
        self._rec_cue_input   = "trichromie_rec_cue"

        # Écoute des événements bus
        self._manager._bus.on("color.groups_changed",  self._on_groups_changed)
        self._manager._bus.on("color.presets_changed", self._on_presets_changed)

    # -----------------------------------------------------------------------
    # Construction UI
    # -----------------------------------------------------------------------

    def build(self) -> None:
        """Construit la fenêtre. Appelé une seule fois depuis app.py."""
        with dpg.window(
            tag=self.TAG_WINDOW,
            label="Trichromie / Quadrichromie",
            width=400,
            height=640,
            pos=(60, 60),
            show=False,
            on_close=self._on_close,
        ):
            self._build_group_section()
            dpg.add_spacer(height=6)
            self._build_picker_section()
            dpg.add_spacer(height=6)
            self._build_preset_section()
            dpg.add_spacer(height=6)
            self._build_rec_section()

        self._apply_theme()

    # -----------------------------------------------------------------------
    # Section : Groupe couleur
    # -----------------------------------------------------------------------

    def _build_group_section(self) -> None:
        with dpg.collapsing_header(label="Groupe couleur", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_combo(
                    tag=self.TAG_GRP_COMBO,
                    items=self._manager.get_group_names(),
                    width=200,
                    callback=self._on_group_selected,
                )
                dpg.add_button(
                    label="+",
                    width=28,
                    callback=self._show_new_group_dialog,
                )
                dpg.add_button(
                    label="✕",
                    width=28,
                    callback=self._delete_active_group,
                )

            dpg.add_spacer(height=4)
            dpg.add_text("Circuits DMX :", color=Colors.TEXT_DIM)

            with dpg.group(horizontal=True):
                dpg.add_text("R :", color=(220, 60, 60))
                dpg.add_input_int(
                    tag=self._input_red, width=80,
                    min_value=0, max_value=512,
                    callback=self._on_circuit_changed,
                )
                dpg.add_text("G :", color=(60, 200, 60))
                dpg.add_input_int(
                    tag=self._input_green, width=80,
                    min_value=0, max_value=512,
                    callback=self._on_circuit_changed,
                )
                dpg.add_text("B :", color=(60, 100, 240))
                dpg.add_input_int(
                    tag=self._input_blue, width=80,
                    min_value=0, max_value=512,
                    callback=self._on_circuit_changed,
                )

            with dpg.group(horizontal=True):
                dpg.add_text("A (Ambre) :", color=(230, 160, 40))
                dpg.add_input_int(
                    tag=self._input_amber, width=60,
                    min_value=0, max_value=512,
                    callback=self._on_circuit_changed,
                )
                dpg.add_text("(0 = non assigné)", color=Colors.TEXT_DIM)

    # -----------------------------------------------------------------------
    # Section : Color Picker
    # -----------------------------------------------------------------------

    def _build_picker_section(self) -> None:
        with dpg.collapsing_header(label="Couleur", default_open=True):
            dpg.add_color_picker(
                tag=self.TAG_PICKER,
                default_value=(0, 0, 0, 255),
                no_alpha=True,
                picker_mode=dpg.mvColorPicker_wheel,
                display_rgb=True,
                display_hsv=True,
                width=370,
                callback=self._on_picker_changed,
            )
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_text("Ambre :", color=(230, 160, 40))
                dpg.add_text("AUTO", tag="trichromie_amber_mode",
                            color=(100, 200, 100))
                dpg.add_text("0", tag="trichromie_amber_value",
                            color=(230, 160, 40))
                dpg.add_spacer(width=16)
                dpg.add_checkbox(
                    label="Manuel",
                    tag="trichromie_amber_manual_toggle",
                    default_value=False,
                    callback=self._on_amber_mode_toggled,
                )
            dpg.add_slider_int(
                tag="trichromie_amber_slider",
                default_value=0,
                min_value=0, max_value=255,
                width=340,
                format="Ambre manuel : %d",
                callback=self._on_amber_manual_changed,
                show=False,
            )

    # -----------------------------------------------------------------------
    # Section : Presets
    # -----------------------------------------------------------------------

    def _build_preset_section(self) -> None:
        with dpg.collapsing_header(label="Presets", default_open=True):
            # Boutons presets
            with dpg.group(tag=self._preset_container):
                self._rebuild_preset_buttons()

            dpg.add_spacer(height=4)
            dpg.add_separator()
            dpg.add_spacer(height=4)

            # Sauvegarde preset
            with dpg.group(horizontal=True):
                dpg.add_text("Nouveau preset :", color=Colors.TEXT_DIM)
                dpg.add_input_text(
                    tag="trichromie_new_preset_name",
                    hint="Nom du preset",
                    width=150,
                )
                dpg.add_button(
                    label="Enregistrer",
                    callback=self._save_current_as_preset,
                )

    def _rebuild_preset_buttons(self) -> None:
        """Reconstruit la grille de boutons de presets."""
        for tag in self._preset_buttons.values():
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        self._preset_buttons.clear()

        names = self._manager.get_preset_names()
        cols = 4

        with dpg.group(parent=self._preset_container):
            row_group = None
            for i, name in enumerate(names):
                if i % cols == 0:
                    row_group = dpg.add_group(horizontal=True,
                                              parent=self._preset_container)

                preset = self._manager.get_preset(name)
                r, g, b = preset.rgb_normalized if preset else (0.5, 0.5, 0.5)

                btn_id = dpg.add_button(
                    label=name,
                    width=86,
                    height=28,
                    parent=row_group,
                    user_data=name,
                    callback=self._on_preset_clicked,
                )

                with dpg.theme() as t:
                    with dpg.theme_component(dpg.mvButton):
                        dpg.add_theme_color(
                            dpg.mvThemeCol_Button,
                            (int(r * 180), int(g * 180), int(b * 180), 255),
                        )
                        dpg.add_theme_color(
                            dpg.mvThemeCol_ButtonHovered,
                            (int(r * 220), int(g * 220), int(b * 220), 255),
                        )
                        dpg.add_theme_color(
                            dpg.mvThemeCol_Text,
                            (240, 240, 240) if (r + g + b) < 1.5 else (20, 20, 20),
                        )
                dpg.bind_item_theme(btn_id, t)
                self._preset_buttons[name] = btn_id

    # -----------------------------------------------------------------------
    # Section : REC
    # -----------------------------------------------------------------------

    def _build_rec_section(self) -> None:
        with dpg.collapsing_header(label="Enregistrement", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_text("REC → Fader n°")
                dpg.add_input_int(
                    tag=self._rec_fader_input,
                    width=60,
                    min_value=1, max_value=48,
                    default_value=1,
                )
                dpg.add_button(
                    label="REC Fader",
                    callback=self._rec_to_fader,
                    width=90,
                )

            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                dpg.add_text("REC → Cue :")
                dpg.add_input_text(
                    tag=self._rec_cue_input,
                    hint="numéro (ex: 5 ou 5.5)",
                    width=120,
                )
                dpg.add_button(
                    label="REC Cue",
                    callback=self._rec_to_cue,
                    width=90,
                )

    # -----------------------------------------------------------------------
    # Callbacks UI
    # -----------------------------------------------------------------------

    def _on_group_selected(self, sender, app_data: str) -> None:
        """Charge les données du groupe sélectionné dans les widgets."""
        self._active_group = app_data
        group = self._manager.get_group(app_data)
        if not group:
            return

        dpg.set_value(self._input_red,   group.red)
        dpg.set_value(self._input_green, group.green)
        dpg.set_value(self._input_blue,  group.blue)
        dpg.set_value(self._input_amber, group.amber)

        # Restaure la couleur courante dans le picker
        r, g, b, a = self._manager.get_current_color(app_data)
        dpg.set_value(self.TAG_PICKER, (r, g, b, 255))

        logger.debug("Groupe actif : '%s'", app_data)
        group = self._manager.get_group(app_data)
        if group:
            manual = not group.auto_amber
            if dpg.does_item_exist("trichromie_amber_manual_toggle"):
                dpg.set_value("trichromie_amber_manual_toggle", manual)
                dpg.configure_item("trichromie_amber_slider", show=manual)
                dpg.set_value("trichromie_amber_mode",
                              "MANUEL" if manual else "AUTO")
        self._refresh_amber_display()

    def _on_circuit_changed(self, sender, app_data: int) -> None:
        """Met à jour les circuits du groupe actif sans appliquer de couleur."""
        if not self._active_group:
            return
        group = self._manager.get_group(self._active_group)
        if not group:
            return

        group.red   = dpg.get_value(self._input_red)
        group.green = dpg.get_value(self._input_green)
        group.blue  = dpg.get_value(self._input_blue)
        group.amber = dpg.get_value(self._input_amber)

        logger.debug("Circuits mis à jour — R:%d G:%d B:%d A:%d",
                     group.red, group.green, group.blue, group.amber)

    def _on_picker_changed(self, sender, app_data: list[float]) -> None:
        """Appelé à chaque changement du color picker → applique la couleur."""
        if not self._active_group:
            return

        # app_data = [R, G, B, A] normalisés 0.0-1.0
        r = int(app_data[0] * 255)
        g = int(app_data[1] * 255)
        b = int(app_data[2] * 255)

        # Ambre : on récupère la valeur actuelle du groupe (le picker est RGB)
        cur = self._manager.get_current_color(self._active_group)
        a = cur[3]

        self._manager.apply_color(self._active_group, (r, g, b, a))
        self._refresh_amber_display()

    def _on_amber_mode_toggled(self, sender, app_data: bool) -> None:
        manual = app_data
        dpg.configure_item("trichromie_amber_slider", show=manual)
        dpg.set_value("trichromie_amber_mode", "MANUEL" if manual else "AUTO")
        dpg.configure_item("trichromie_amber_mode",
                        color=(200, 150, 40) if manual else (100, 200, 100))
        if not self._active_group:
            return
        group = self._manager.get_group(self._active_group)
        if group:
            group.auto_amber = not manual
            cur = self._manager.get_current_color(self._active_group)
            self._manager.apply_color(self._active_group, cur)
            self._refresh_amber_display()

    def _on_amber_manual_changed(self, sender, app_data: int) -> None:
        if not self._active_group:
            return
        group = self._manager.get_group(self._active_group)
        if not group or group.amber == 0:
            return
        cur = self._manager.get_current_color(self._active_group)
        r, g, b, _ = cur
        self._manager.apply_color(self._active_group, (r, g, b, app_data))
        self._refresh_amber_display()

    def _refresh_amber_display(self) -> None:
        if not self._active_group:
            return
        cur = self._manager.get_current_color(self._active_group)
        if dpg.does_item_exist("trichromie_amber_value"):
            dpg.set_value("trichromie_amber_value", str(cur[3]))
        if dpg.does_item_exist("trichromie_amber_slider"):
            dpg.set_value("trichromie_amber_slider", cur[3])

    def _on_preset_clicked(self, sender, app_data, user_data: str) -> None:
        """Applique un preset au groupe actif et met à jour le picker."""
        if not self._active_group:
            logger.warning("Aucun groupe actif pour appliquer le preset '%s'", user_data)
            return

        if self._manager.apply_preset(self._active_group, user_data):
            r, g, b, a = self._manager.get_current_color(self._active_group)
            dpg.set_value(self.TAG_PICKER, (r, g, b, 255))
            self._refresh_amber_display()

    def _save_current_as_preset(self) -> None:
        """Enregistre la couleur courante comme nouveau preset."""
        if not self._active_group:
            return
        name = dpg.get_value("trichromie_new_preset_name").strip()
        if not name:
            return

        if self._manager.save_current_as_preset(self._active_group, name):
            dpg.set_value("trichromie_new_preset_name", "")

    # -----------------------------------------------------------------------
    # Création / suppression de groupes
    # -----------------------------------------------------------------------

    def _show_new_group_dialog(self) -> None:
        """Affiche un dialogue modal de création de groupe."""
        if dpg.does_item_exist("trichromie_new_group_modal"):
            dpg.delete_item("trichromie_new_group_modal")

        with dpg.window(
            tag="trichromie_new_group_modal",
            label="Nouveau groupe couleur",
            modal=True,
            width=300,
            pos=(200, 200),
            no_scrollbar=True,
        ):
            dpg.add_text("Nom du groupe :")
            dpg.add_input_text(
                tag=self.TAG_NEW_NAME,
                hint="ex: PAR LED jardin 1",
                width=270,
            )
            dpg.add_spacer(height=8)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Créer", width=100,
                               callback=self._create_group_from_dialog)
                dpg.add_button(label="Annuler", width=100,
                               callback=lambda: dpg.delete_item(
                                   "trichromie_new_group_modal"))

    def _create_group_from_dialog(self) -> None:
        name = dpg.get_value(self.TAG_NEW_NAME).strip()
        if dpg.does_item_exist("trichromie_new_group_modal"):
            dpg.delete_item("trichromie_new_group_modal")
        if not name:
            return

        group = ColorGroup(name=name)
        self._manager.add_group(group)

        # Sélectionner le groupe créé
        self._active_group = name
        dpg.set_value(self.TAG_GRP_COMBO, name)
        dpg.set_value(self._input_red,   0)
        dpg.set_value(self._input_green, 0)
        dpg.set_value(self._input_blue,  0)
        dpg.set_value(self._input_amber, 0)

    def _delete_active_group(self) -> None:
        if not self._active_group:
            return
        self._manager.remove_group(self._active_group)
        self._active_group = None
        dpg.set_value(self.TAG_GRP_COMBO, "")

    # -----------------------------------------------------------------------
    # REC
    # -----------------------------------------------------------------------

    def _get_active_rgba(self) -> tuple[int, int, int, int] | None:
        if not self._active_group:
            logger.warning("REC : aucun groupe actif")
            return None
        return self._manager.get_current_color(self._active_group)

    def _rec_to_fader(self) -> None:
        """Enregistre la couleur courante (circuits RGB/A) dans un fader."""
        rgba = self._get_active_rgba()
        if rgba is None:
            return
        fader_num = dpg.get_value(self._rec_fader_input)

        group = self._manager.get_group(self._active_group)
        if not group or not group.is_valid:
            return

        r, g, b, a = rgba
        # Écrire dans les circuits du groupe
        circuits_values = {group.red: r, group.green: g, group.blue: b}
        if group.amber > 0:
            circuits_values[group.amber] = a

        # Envoyer au fader via l'engine (le fader "capture" l'état DMX courant)
        # Note : l'API exacte dépend de l'implémentation des faders.
        # Ici on copie les circuits dans le fader ciblé.
        try:
            fader = self._engine.faders.get_fader(fader_num)
            if fader:
                fader.set_contents(circuits_values)
                logger.info("REC couleur → Fader %d (groupe '%s')",
                            fader_num, self._active_group)
        except Exception as exc:
            logger.error("Erreur REC fader : %s", exc)

    def _rec_to_cue(self) -> None:
        """Enregistre la couleur courante dans une cue du séquenceur."""
        rgba = self._get_active_rgba()
        if rgba is None:
            return

        cue_str = dpg.get_value(self._rec_cue_input).strip()
        try:
            cue_num = float(cue_str) if cue_str else None
        except ValueError:
            logger.warning("REC cue : numéro invalide '%s'", cue_str)
            return

        group = self._manager.get_group(self._active_group)
        if not group or not group.is_valid:
            return

        r, g, b, a = rgba
        circuits_values = {group.red: r, group.green: g, group.blue: b}
        if group.amber > 0:
            circuits_values[group.amber] = a

        try:
            # Merge avec le contenu existant de la cue
            seq = self._engine.sequencer
            existing_cue = seq.get_cue(cue_num) if cue_num else None

            if existing_cue:
                merged = dict(existing_cue.contents)
                merged.update(circuits_values)
                seq.record_cue(
                    number=existing_cue.number,
                    name=existing_cue.name,
                    contents=merged,
                    fade_in=existing_cue.fade_in,
                    fade_out=existing_cue.fade_out,
                )
                logger.info("REC couleur → Cue %.1f mise à jour", existing_cue.number)
            else:
                # Cue suivante ou numéro donné
                if cue_num is None:
                    cue_num = round(
                        (seq.get_cue_count() + 1) * 1.0, 1
                    )
                name = f"Couleur {self._active_group}"
                seq.record_cue(
                    number=cue_num,
                    name=name,
                    contents=circuits_values,
                )
                logger.info("REC couleur → Nouvelle Cue %.1f '%s'", cue_num, name)
        except Exception as exc:
            logger.error("Erreur REC cue : %s", exc)

    # -----------------------------------------------------------------------
    # Callbacks bus
    # -----------------------------------------------------------------------

    def _on_groups_changed(self, **kwargs) -> None:
        """Mise à jour du combo de groupes."""
        names = self._manager.get_group_names()
        dpg.configure_item(self.TAG_GRP_COMBO, items=names)
        if self._active_group and self._active_group not in names:
            self._active_group = None
            dpg.set_value(self.TAG_GRP_COMBO, "")

    def _on_presets_changed(self, **kwargs) -> None:
        """Reconstruit la grille de presets."""
        if dpg.does_item_exist(self._preset_container):
            self._rebuild_preset_buttons()

    # -----------------------------------------------------------------------
    # Visibilité
    # -----------------------------------------------------------------------

    def show(self) -> None:
        dpg.configure_item(self.TAG_WINDOW, show=True)

    def hide(self) -> None:
        dpg.configure_item(self.TAG_WINDOW, show=False)

    def toggle(self) -> None:
        visible = dpg.is_item_shown(self.TAG_WINDOW)
        dpg.configure_item(self.TAG_WINDOW, show=not visible)

    def _on_close(self) -> None:
        logger.debug("Fenêtre Trichromie fermée")

    # -----------------------------------------------------------------------
    # Thème
    # -----------------------------------------------------------------------

    def _apply_theme(self) -> None:
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg,    Colors.BG_WINDOW)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg,     Colors.BG_CHILD)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg,     Colors.BG_WIDGET)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_Text,        Colors.TEXT)
                dpg.add_theme_color(dpg.mvThemeCol_Button,      Colors.BG_LIGHT)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (55, 55, 70))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, Colors.BG_WIDGET)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
        dpg.bind_item_theme(self.TAG_WINDOW, theme)