"""
Vue Audio : bibliothèque de fichiers, lecteurs et sons directs.

Sections (repliables) :
    - Bibliothèque : ajout de fichiers, durée, cue in / cue out,
      préchargement (décodé en mémoire = déclenchement sans délai disque)
    - Lecteurs     : transport, position (glisser pour seek), volume,
      loop, fondus, cue in/out réglés depuis la position courante
    - Sons         : sons définis joués directement (sans lecteur)

Rafraîchissement :
    - audio.changed (structural=True) : reconstruction des sections
      (sauf pendant une saisie dans cette vue)
    - audio.state_changed : états play/pause
    - audio.tick (10 Hz pendant la lecture) : positions
    Libellés ASCII / Latin-1 uniquement (police par défaut de Dear PyGui).
"""

from __future__ import annotations

import logging
from typing import Any

import dearpygui.dearpygui as dpg

from lumensmaster.core.engine import Engine
from lumensmaster.modules.audio import LOOP_MODES, RETRIGGER_MODES
from lumensmaster.ui.theme import Colors

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg")


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60)
    return f"{int(m):02d}:{s:04.1f}"


class AudioView:
    """Fenêtre flottante Audio."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._audio = engine.audio

        self._window_id: int = 0
        self._library_container: int = 0
        self._players_container: int = 0
        self._sounds_container: int = 0
        self._status_text: int = 0
        self._file_dialog: int = 0

        self._suppress_rebuild = False
        self._player_widgets: dict[int, dict[str, int]] = {}
        self._sound_widgets: dict[int, dict[str, int]] = {}

        self._theme_playing: int = 0
        self._theme_idle: int = 0

        bus = engine.bus
        bus.on("audio.changed", self._on_changed)
        bus.on("audio.state_changed", self._on_state_changed)
        bus.on("audio.tick", self._on_tick)
        bus.on("show.loaded", lambda **kw: self._rebuild_all())
        bus.on("engine.started", lambda **kw: self._update_status())

    # ------------------------------------------------------------------
    #  Construction
    # ------------------------------------------------------------------

    def build(self) -> int:
        self._create_themes()
        self._window_id = dpg.add_window(
            label="Audio", width=1000, height=640, pos=(300, 100),
            show=False, on_close=lambda: None)

        with dpg.file_dialog(label="Ajouter des fichiers audio",
                             directory_selector=False, show=False,
                             file_count=50, width=700, height=420,
                             callback=self._on_files_selected,
                             cancel_callback=lambda: None) as self._file_dialog:
            dpg.add_file_extension("Audio{.wav,.mp3,.flac,.ogg}",
                                   color=(0, 255, 0, 255))
            dpg.add_file_extension(".*")

        with dpg.group(parent=self._window_id):
            with dpg.group(horizontal=True):
                self._status_text = dpg.add_text("", color=Colors.TEXT_SECONDARY)
                dpg.add_spacer(width=16)
                dpg.add_button(label="Tout stopper", width=110,
                               callback=lambda: self._audio.stop_all(-1))
                dpg.add_button(label="Stop immediat", width=110,
                               callback=lambda: self._audio.stop_all(0))

            with dpg.collapsing_header(label="Bibliotheque", default_open=True):
                self._library_container = dpg.add_group()
            with dpg.collapsing_header(label="Lecteurs", default_open=True):
                self._players_container = dpg.add_group()
            with dpg.collapsing_header(label="Sons directs", default_open=True):
                self._sounds_container = dpg.add_group()

        self._rebuild_all()
        return self._window_id

    def toggle(self) -> None:
        if self._window_id and dpg.does_item_exist(self._window_id):
            dpg.configure_item(self._window_id,
                               show=not dpg.is_item_shown(self._window_id))

    def _rebuild_all(self) -> None:
        if not self._window_id or not dpg.does_item_exist(self._window_id):
            return
        self._build_library()
        self._build_players()
        self._build_sounds()
        self._update_status()

    @staticmethod
    def _clear(container: int) -> None:
        if container and dpg.does_item_exist(container):
            dpg.delete_item(container, children_only=True)

    def _file_labels(self) -> tuple[list[str], dict[int, str]]:
        labels = ["(aucun)"]
        by_id: dict[int, str] = {}
        for f in self._audio.files:
            label = f"{f.id} - {f.name}" + (" [ABSENT]" if f.missing else "")
            labels.append(label)
            by_id[f.id] = label
        return labels, by_id

    @staticmethod
    def _parse_file_label(label: str) -> int:
        try:
            return int(label.split(" - ")[0])
        except (ValueError, IndexError):
            return 0

    # --- Bibliothèque -----------------------------------------------

    def _build_library(self) -> None:
        parent = self._library_container
        self._clear(parent)
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_button(label="Ajouter des fichiers",
                           callback=lambda: dpg.show_item(self._file_dialog))
            dpg.add_text("WAV, MP3, FLAC, OGG - chemins relatifs au .lms a la "
                         "sauvegarde", color=Colors.TEXT_DISABLED)
        files = self._audio.files
        if not files:
            dpg.add_text("Aucun fichier", color=Colors.TEXT_DISABLED, parent=parent)
            return
        with dpg.table(parent=parent, header_row=True, row_background=True,
                       borders_innerH=True, policy=dpg.mvTable_SizingFixedFit):
            for label in ("Id", "Fichier", "Duree", "Cue in (s)", "Cue out (s)",
                          "Precharge", ""):
                dpg.add_table_column(label=label)
            for f in files:
                with dpg.table_row():
                    dpg.add_text(str(f.id), color=Colors.TEXT_SECONDARY)
                    t = dpg.add_text(f.name, color=Colors.ERROR if f.missing
                                     else Colors.TEXT_PRIMARY)
                    with dpg.tooltip(t):
                        dpg.add_text(f.path + ("\nFICHIER INTROUVABLE" if f.missing else ""))
                    dpg.add_text(fmt_time(f.duration), color=Colors.TEXT_SECONDARY)
                    dpg.add_input_float(default_value=f.cue_in, width=90,
                                        format="%.2f", step=0.1, min_value=0.0,
                                        min_clamped=True, on_enter=True,
                                        callback=self._on_cue_in, user_data=f.id)
                    dpg.add_input_float(default_value=f.cue_out, width=90,
                                        format="%.2f", step=0.1, min_value=0.0,
                                        min_clamped=True, on_enter=True,
                                        callback=self._on_cue_out, user_data=f.id)
                    cb = dpg.add_checkbox(default_value=f.preload,
                                          callback=self._on_preload, user_data=f.id)
                    with dpg.tooltip(cb):
                        dpg.add_text("Decode en memoire : declenchement sans lecture\n"
                                     "disque (~23 Mo par minute de son).")
                    dpg.add_button(label="Retirer", callback=self._on_remove_file,
                                   user_data=f.id)
        dpg.add_text(f"Memoire prechargee : {self._audio.cache_megabytes():.0f} Mo",
                     color=Colors.TEXT_DISABLED, parent=parent)

    # --- Lecteurs ---------------------------------------------------

    def _build_players(self) -> None:
        parent = self._players_container
        self._clear(parent)
        self._player_widgets.clear()
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_text("Nombre de lecteurs", color=Colors.TEXT_SECONDARY)
            dpg.add_input_int(default_value=len(self._audio.players), width=90,
                              min_value=1, max_value=16, min_clamped=True,
                              max_clamped=True, on_enter=True,
                              callback=lambda s, v: self._audio.set_player_count(v))
        labels, by_id = self._file_labels()
        loop_labels = [l for _, l in LOOP_MODES]
        with dpg.table(parent=parent, header_row=True, row_background=True,
                       borders_innerH=True, policy=dpg.mvTable_SizingFixedFit):
            for label in ("#", "Fichier", "", "", "Position", "Reste", "Volume",
                          "Loop", "Fade in", "Fade out", "Cues"):
                dpg.add_table_column(label=label)
            for p in self._audio.players:
                st = self._audio.player_status(p.index)
                w: dict[str, int] = {}
                with dpg.table_row():
                    dpg.add_text(str(p.index), color=Colors.ACCENT)
                    dpg.add_combo(items=labels, width=180,
                                  default_value=by_id.get(p.file_id, "(aucun)"),
                                  callback=self._on_player_file, user_data=p.index)
                    w["play"] = dpg.add_button(label="Play", width=55,
                                               callback=self._on_player_toggle,
                                               user_data=p.index)
                    dpg.add_button(label="Stop", width=45,
                                   callback=lambda s, a, u: self._audio.player_stop(u),
                                   user_data=p.index)
                    w["pos"] = dpg.add_slider_float(
                        default_value=st["position"], min_value=0.0,
                        max_value=max(st["duration"], 0.01), width=200,
                        format="%.1f s", callback=self._on_player_seek,
                        user_data=p.index)
                    w["remain"] = dpg.add_text(
                        "-" + fmt_time(st["duration"] - st["position"]),
                        color=Colors.TEXT_SECONDARY)
                    dpg.add_slider_float(default_value=p.volume, min_value=0.0,
                                         max_value=100.0, width=110, format="%.0f %%",
                                         callback=self._on_player_volume,
                                         user_data=p.index)
                    dpg.add_combo(items=loop_labels, width=95,
                                  default_value=dict(LOOP_MODES)[p.loop_mode],
                                  callback=self._on_player_loop, user_data=p.index)
                    dpg.add_input_float(default_value=p.fade_in, width=70,
                                        format="%.1f", step=0, min_value=0.0,
                                        min_clamped=True,
                                        callback=self._on_player_fade,
                                        user_data=(p.index, "fade_in"))
                    dpg.add_input_float(default_value=p.fade_out, width=70,
                                        format="%.1f", step=0, min_value=0.0,
                                        min_clamped=True,
                                        callback=self._on_player_fade,
                                        user_data=(p.index, "fade_out"))
                    with dpg.group(horizontal=True):
                        b = dpg.add_button(label="In", width=30,
                                           callback=self._on_set_cue,
                                           user_data=(p.index, "in"))
                        with dpg.tooltip(b):
                            dpg.add_text("Cue in du fichier = position courante")
                        b = dpg.add_button(label="Out", width=34,
                                           callback=self._on_set_cue,
                                           user_data=(p.index, "out"))
                        with dpg.tooltip(b):
                            dpg.add_text("Cue out du fichier = position courante")
                self._player_widgets[p.index] = w
        self._update_player_states()

    # --- Sons ---------------------------------------------------------

    def _build_sounds(self) -> None:
        parent = self._sounds_container
        self._clear(parent)
        self._sound_widgets.clear()
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_button(label="Nouveau son", callback=self._on_new_sound)
            dpg.add_text("Declenchables ici ou depuis un banger (Audio > Jouer son)",
                         color=Colors.TEXT_DISABLED)
        sounds = self._audio.sounds
        if not sounds:
            dpg.add_text("Aucun son", color=Colors.TEXT_DISABLED, parent=parent)
            return
        labels, by_id = self._file_labels()
        retrig_labels = [l for _, l in RETRIGGER_MODES]
        with dpg.table(parent=parent, header_row=True, row_background=True,
                       borders_innerH=True, policy=dpg.mvTable_SizingFixedFit):
            for label in ("N", "Nom", "Fichier", "", "", "Volume", "Fade in",
                          "Fade out", "Loop", "Relance", ""):
                dpg.add_table_column(label=label)
            for s in sounds:
                w: dict[str, int] = {}
                with dpg.table_row():
                    dpg.add_text(str(s.number), color=Colors.ACCENT)
                    dpg.add_input_text(default_value=s.name, width=130,
                                       callback=self._on_sound_field,
                                       user_data=(s.number, "name"))
                    dpg.add_combo(items=labels, width=170,
                                  default_value=by_id.get(s.file_id, "(aucun)"),
                                  callback=self._on_sound_file, user_data=s.number)
                    w["play"] = dpg.add_button(
                        label="Jouer", width=55,
                        callback=lambda sd, a, u: self._audio.play_sound(u),
                        user_data=s.number)
                    dpg.add_button(label="Stop", width=45,
                                   callback=lambda sd, a, u: self._audio.stop_sound(u),
                                   user_data=s.number)
                    dpg.add_slider_float(default_value=s.volume, min_value=0.0,
                                         max_value=100.0, width=110, format="%.0f %%",
                                         callback=self._on_sound_field,
                                         user_data=(s.number, "volume"))
                    for key in ("fade_in", "fade_out"):
                        dpg.add_input_float(default_value=getattr(s, key), width=70,
                                            format="%.1f", step=0, min_value=0.0,
                                            min_clamped=True,
                                            callback=self._on_sound_field,
                                            user_data=(s.number, key))
                    dpg.add_checkbox(default_value=s.loop,
                                     callback=self._on_sound_field,
                                     user_data=(s.number, "loop"))
                    dpg.add_combo(items=retrig_labels, width=100,
                                  default_value=dict(RETRIGGER_MODES)[s.retrigger],
                                  callback=self._on_sound_retrigger,
                                  user_data=s.number)
                    dpg.add_button(label="X", width=24, callback=self._on_delete_sound,
                                   user_data=s.number)
                self._sound_widgets[s.number] = w
        self._update_sound_states()

    # ------------------------------------------------------------------
    #  Mises à jour d'état
    # ------------------------------------------------------------------

    def _update_status(self) -> None:
        if not self._status_text or not dpg.does_item_exist(self._status_text):
            return
        if self._audio.output_running:
            dpg.set_value(self._status_text, "Sortie audio : OK")
            dpg.configure_item(self._status_text, color=Colors.SUCCESS)
        else:
            dpg.set_value(self._status_text,
                          "Sortie audio : indisponible (voir les logs)")
            dpg.configure_item(self._status_text, color=Colors.ERROR)

    def _update_player_states(self) -> None:
        for index, w in self._player_widgets.items():
            playing = self._audio.player_is_playing(index)
            btn = w.get("play")
            if btn and dpg.does_item_exist(btn):
                dpg.configure_item(btn, label="Pause" if playing else "Play")
                dpg.bind_item_theme(btn, self._theme_playing if playing
                                    else self._theme_idle)

    def _update_sound_states(self) -> None:
        for number, w in self._sound_widgets.items():
            btn = w.get("play")
            if btn and dpg.does_item_exist(btn):
                dpg.bind_item_theme(btn, self._theme_playing
                                    if self._audio.sound_is_playing(number)
                                    else self._theme_idle)

    def _update_positions(self) -> None:
        for index, w in self._player_widgets.items():
            st = self._audio.player_status(index)
            slider = w.get("pos")
            if slider and dpg.does_item_exist(slider) and not dpg.is_item_active(slider):
                dpg.set_value(slider, st["position"])
            remain = w.get("remain")
            if remain and dpg.does_item_exist(remain):
                dpg.set_value(remain, "-" + fmt_time(st["duration"] - st["position"]))

    # ------------------------------------------------------------------
    #  Callbacks bus
    # ------------------------------------------------------------------

    def _on_changed(self, structural: bool = True, **kwargs: Any) -> None:
        if structural and not self._suppress_rebuild:
            self._rebuild_all()

    def _on_state_changed(self, **kwargs: Any) -> None:
        self._update_player_states()
        self._update_sound_states()
        self._update_positions()

    def _on_tick(self, **kwargs: Any) -> None:
        if self._window_id and dpg.does_item_exist(self._window_id) \
                and dpg.is_item_shown(self._window_id):
            self._update_positions()
            self._update_sound_states()

    # ------------------------------------------------------------------
    #  Callbacks UI
    # ------------------------------------------------------------------

    def _edit(self, fn, *args, **kwargs) -> None:
        self._suppress_rebuild = True
        try:
            fn(*args, **kwargs)
        finally:
            self._suppress_rebuild = False

    def _on_files_selected(self, sender: int, app_data: dict) -> None:
        paths = list((app_data or {}).get("selections", {}).values())
        added = 0
        for path in paths:
            if path.lower().endswith(AUDIO_EXTENSIONS):
                self._audio.add_file(path)
                added += 1
            else:
                logger.warning("Format non supporte ignore : %s", path)
        logger.info("%d fichier(s) audio ajoute(s)", added)

    def _on_cue_in(self, sender: int, value: float, user_data: int) -> None:
        self._audio.set_cues(user_data, cue_in=value)

    def _on_cue_out(self, sender: int, value: float, user_data: int) -> None:
        self._audio.set_cues(user_data, cue_out=value)

    def _on_preload(self, sender: int, value: bool, user_data: int) -> None:
        self._audio.set_preload(user_data, value)

    def _on_remove_file(self, sender: int, app_data: Any, user_data: int) -> None:
        self._audio.remove_file(user_data)

    def _on_player_file(self, sender: int, value: str, user_data: int) -> None:
        file_id = self._parse_file_label(value)
        if file_id:
            self._audio.player_load(user_data, file_id)
        else:
            self._audio.player_unload(user_data)

    def _on_player_toggle(self, sender: int, app_data: Any, user_data: int) -> None:
        self._audio.player_toggle(user_data)

    def _on_player_seek(self, sender: int, value: float, user_data: int) -> None:
        self._audio.player_seek(user_data, value)

    def _on_player_volume(self, sender: int, value: float, user_data: int) -> None:
        self._edit(self._audio.player_set_volume, user_data, value)

    def _on_player_loop(self, sender: int, value: str, user_data: int) -> None:
        for key, label in LOOP_MODES:
            if label == value:
                self._edit(self._audio.player_set_loop, user_data, key)

    def _on_player_fade(self, sender: int, value: float, user_data: tuple) -> None:
        index, key = user_data
        self._edit(self._audio.player_update, index, **{key: value})

    def _on_set_cue(self, sender: int, app_data: Any, user_data: tuple) -> None:
        index, which = user_data
        p = self._audio.get_player(index)
        if p is None or not p.file_id:
            return
        pos = self._audio.player_status(index)["position"]
        if which == "in":
            self._audio.set_cues(p.file_id, cue_in=pos)
        else:
            self._audio.set_cues(p.file_id, cue_out=pos)

    def _on_new_sound(self) -> None:
        files = self._audio.files
        self._audio.create_sound(file_id=files[0].id if files else 0)

    def _on_delete_sound(self, sender: int, app_data: Any, user_data: int) -> None:
        self._audio.delete_sound(user_data)

    def _on_sound_field(self, sender: int, value: Any, user_data: tuple) -> None:
        number, key = user_data
        self._edit(self._audio.update_sound, number, **{key: value})

    def _on_sound_file(self, sender: int, value: str, user_data: int) -> None:
        self._edit(self._audio.update_sound, user_data,
                   file_id=self._parse_file_label(value))

    def _on_sound_retrigger(self, sender: int, value: str, user_data: int) -> None:
        for key, label in RETRIGGER_MODES:
            if label == value:
                self._edit(self._audio.update_sound, user_data, retrigger=key)

    # ------------------------------------------------------------------
    #  Thèmes / layout
    # ------------------------------------------------------------------

    def _create_themes(self) -> None:
        with dpg.theme() as self._theme_playing:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (30, 90, 45))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (40, 110, 55))
                dpg.add_theme_color(dpg.mvThemeCol_Text, Colors.SUCCESS)
        with dpg.theme() as self._theme_idle:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, Colors.BG_WIDGET)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, Colors.BG_LIGHT)

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
