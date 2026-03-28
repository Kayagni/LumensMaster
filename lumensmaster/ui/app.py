"""
Fenêtre principale de LumensMaster.

Gère la barre d'outils, la barre de statut, et instancie les fenêtres
flottantes (Circuits, Faders, etc.).
"""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from lumensmaster import __app_name__, __version__
from lumensmaster.core.engine import Engine
from lumensmaster.ui.theme import Colors, apply_theme
from lumensmaster.ui.icons import get_icon_manager
from lumensmaster.ui.views.circuits_view import CircuitsView
from lumensmaster.ui.views.faders_view import FadersView
from lumensmaster.ui.views.sequencer_view import SequencerView

logger = logging.getLogger(__name__)


class App:
    """Application principale LumensMaster."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._circuits_view: CircuitsView | None = None
        self._faders_view: FadersView | None = None
        self._port_combo: int = 0
        self._sequencer_view: SequencerView | None = None
        self._file_dialog_save: int = 0
        self._file_dialog_open: int = 0
        self._show_name_input: int = 0

    def run(self, dummy_dmx: bool = False) -> None:
        """Lance l'application (bloquant)."""
        dpg.create_context()
 
        # Charger les icônes
        get_icon_manager().load_all()
 
        dpg.create_viewport(
            title=f"{__app_name__} v{__version__}",
            width=self._engine.config.ui.width,
            height=self._engine.config.ui.height,
            min_width=800,
            min_height=600,
        )
 
        apply_theme()
        self._build_ui()
 
        dpg.setup_dearpygui()
        dpg.show_viewport()
 
        # Démarrer le moteur
        self._engine.start(dummy_dmx=dummy_dmx)
 
        try:
            # Boucle de rendu manuelle (au lieu de dpg.start_dearpygui)
            # Permet d'exécuter poll_ui() du séquenceur à chaque frame
            # depuis le thread principal (Dear PyGui n'est pas thread-safe)
            while dpg.is_dearpygui_running():
                self._engine.sequencer.poll_ui()
                dpg.render_dearpygui_frame()
        finally:
            self._engine.stop()
            self._engine.config.save()
            dpg.destroy_context()

    def _build_ui(self) -> None:
        """Construit l'interface graphique."""
        with dpg.window(tag="main_window"):
            self._build_toolbar()
            dpg.add_spacer(height=4)
            dpg.add_separator()
            self._build_statusbar()

        dpg.set_primary_window("main_window", True)

        # Fenêtres flottantes (après le bloc main_window)
        self._circuits_view = CircuitsView(self._engine)
        self._circuits_view.build()

        self._faders_view = FadersView(self._engine)
        self._faders_view.build()

        self._sequencer_view = SequencerView(self._engine)
        self._sequencer_view.build()

        # File dialogs (créés au niveau racine, pas dans une fenêtre)
        with dpg.file_dialog(
            label="Ouvrir un show",
            directory_selector=False,
            show=False,
            callback=self._on_open_file_selected,
            cancel_callback=lambda: None,
            width=700,
            height=400,
        ) as self._file_dialog_open:
            dpg.add_file_extension(".lms", color=(0, 255, 0, 255))
            dpg.add_file_extension(".*")
 
        with dpg.file_dialog(
            label="Sauvegarder le show",
            directory_selector=False,
            show=False,
            callback=self._on_save_file_selected,
            cancel_callback=lambda: None,
            width=700,
            height=400,
        ) as self._file_dialog_save:
            dpg.add_file_extension(".lms", color=(0, 255, 0, 255))
            dpg.add_file_extension(".*")

    def _build_toolbar(self) -> None:
        """Construit la barre d'outils supérieure."""
        with dpg.group(horizontal=True):
            # Titre
            dpg.add_text(__app_name__, color=Colors.ACCENT)
            dpg.add_spacer(width=24)

            # --- DMX ---
            dpg.add_text("DMX :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)

            devices = self._engine.list_dmx_devices()
            device_labels = [
                f"#{d['index']} - {d['description']}" for d in devices
            ] if devices else ["(aucun device FTDI)"]

            self._port_combo = dpg.add_combo(
                items=device_labels,
                default_value=device_labels[0] if device_labels else "(aucun device FTDI)",
                width=200,
                callback=self._on_port_selected,
            )

            dpg.add_button(label="Rafraichir", callback=self._refresh_ports)
            dpg.add_button(label="Connecter", callback=self._on_connect)

            dpg.add_spacer(width=24)

            # --- Show ---
            dpg.add_text("Show :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)
 
            self._show_name_input = dpg.add_input_text(
                default_value=self._engine.show_name,
                width=150,
                hint="Nom du show",
                on_enter=True,
                callback=self._on_show_name_changed,
            )
 
            dpg.add_button(label="Nouveau", callback=self._on_new_show)
            dpg.add_button(label="Ouvrir", callback=self._on_open_show)
            dpg.add_button(label="Sauvegarder", callback=self._on_save_show)
            dpg.add_button(label="Sauv. sous", callback=self._on_save_as_show)

            # --- Fenêtres ---
            dpg.add_text("Fenetres :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)

            dpg.add_button(label="Circuits", callback=self._toggle_circuits_window)
            dpg.add_button(label="Faders", callback=self._toggle_faders_window)

            dpg.add_button(label="Sequenceur", callback=self._toggle_sequencer_window)

    def _build_statusbar(self) -> None:
        """Barre de statut inférieure."""
        with dpg.group(horizontal=True):
            dpg.add_text(
                "DMX : Non connecte",
                tag="status_dmx",
                color=Colors.TEXT_SECONDARY,
            )
            dpg.add_spacer(width=24)
            dpg.add_text(
                f"Show : {self._engine.show_name}",
                tag="status_show",
                color=Colors.TEXT_SECONDARY,
            )

    # --- Callbacks DMX ---

    def _on_port_selected(self, sender: int, value: str) -> None:
        pass  # La sélection est lue au moment du clic Connecter

    def _refresh_ports(self) -> None:
        devices = self._engine.list_dmx_devices()
        device_labels = [
            f"#{d['index']} - {d['description']}" for d in devices
        ] if devices else ["(aucun device FTDI)"]
        dpg.configure_item(self._port_combo, items=device_labels)

    def _on_connect(self) -> None:
        selected = dpg.get_value(self._port_combo)
        if not selected or selected == "(aucun device FTDI)":
            self._update_status_dmx("Aucun device FTDI trouve", Colors.WARNING)
            return

        try:
            device_index = int(selected.split("#")[1].split(" ")[0])
        except (IndexError, ValueError):
            device_index = 0

        if self._engine.connect_dmx(device_index):
            self._update_status_dmx(f"Connecte : {selected}", Colors.SUCCESS)
        else:
            self._update_status_dmx("Echec connexion FTDI", Colors.ERROR)

    # --- Callbacks Show ---

    def _on_show_name_changed(self, sender: int, value: str) -> None:
        """Met à jour le nom du show dans les metadata."""
        self._engine._show_data.setdefault("metadata", {})["name"] = value
        self._engine._dirty = True
        self._update_status_show()
 
    def _on_new_show(self) -> None:
        self._engine.new_show()
        self._refresh_all_views()
        self._update_status_show()
        dpg.set_value(self._show_name_input, self._engine.show_name)
 
    def _on_open_show(self) -> None:
        """Affiche le file dialog d'ouverture."""
        dpg.show_item(self._file_dialog_open)
 
    def _on_save_show(self) -> None:
        """Sauvegarde : si chemin connu, sauvegarde directement. Sinon, file dialog."""
        if self._engine.save_current_show():
            self._update_status_show()
        else:
            dpg.show_item(self._file_dialog_save)
 
    def _on_save_as_show(self) -> None:
        """Affiche le file dialog de sauvegarde (forcer un nouveau chemin)."""
        dpg.show_item(self._file_dialog_save)
 
    def _on_open_file_selected(self, sender: int, app_data: dict) -> None:
        """Callback du file dialog d'ouverture."""
        selections = app_data.get("selections", {})
        if not selections:
            return
        filepath = list(selections.values())[0]
        if self._engine.load_existing_show(filepath):
            self._refresh_all_views()
            self._update_status_show()
            dpg.set_value(self._show_name_input, self._engine.show_name)
            logger.info("Show ouvert : %s", filepath)
        else:
            logger.error("Impossible d'ouvrir le show : %s", filepath)
 
    def _on_save_file_selected(self, sender: int, app_data: dict) -> None:
        """Callback du file dialog de sauvegarde."""
        file_path_name = app_data.get("file_path_name", "")
        if not file_path_name:
            return
        if not file_path_name.endswith(".lms"):
            file_path_name += ".lms"
 
        # Mettre à jour le nom du show depuis le champ de saisie
        current_name = dpg.get_value(self._show_name_input)
        if current_name:
            self._engine._show_data.setdefault("metadata", {})["name"] = current_name
 
        if self._engine.save_current_show(file_path_name):
            self._update_status_show()
            logger.info("Show sauvegardé : %s", file_path_name)
        else:
            logger.error("Impossible de sauvegarder le show")

    # --- Callbacks Fenêtres ---

    def _toggle_circuits_window(self) -> None:
        """Affiche ou masque la fenêtre Circuits."""
        if self._circuits_view and dpg.does_item_exist(self._circuits_view._window_id):
            shown = dpg.is_item_shown(self._circuits_view._window_id)
            dpg.configure_item(self._circuits_view._window_id, show=not shown)
        else:
            self._circuits_view = CircuitsView(self._engine)
            self._circuits_view.build()

    def _toggle_faders_window(self) -> None:
        """Affiche ou masque la fenêtre Faders."""
        if self._faders_view and dpg.does_item_exist(self._faders_view._window_id):
            shown = dpg.is_item_shown(self._faders_view._window_id)
            dpg.configure_item(self._faders_view._window_id, show=not shown)
        else:
            self._faders_view = FadersView(self._engine)
            self._faders_view.build()

    def _toggle_sequencer_window(self) -> None:
        """Affiche ou masque la fenêtre Séquenceur."""
        if self._sequencer_view and dpg.does_item_exist(self._sequencer_view._window_id):
            shown = dpg.is_item_shown(self._sequencer_view._window_id)
            dpg.configure_item(self._sequencer_view._window_id, show=not shown)
        else:
            self._sequencer_view = SequencerView(self._engine)
            self._sequencer_view.build()

    # --- Mise à jour statut ---

    def _update_status_dmx(self, text: str, color: tuple = Colors.TEXT_SECONDARY) -> None:
        dpg.set_value("status_dmx", f"DMX : {text}")
        dpg.configure_item("status_dmx", color=color)

    def _update_status_show(self) -> None:
        name = self._engine.show_name
        dirty = " *" if self._engine.is_dirty else ""
        dpg.set_value("status_show", f"Show : {name}{dirty}")

    def _refresh_all_views(self) -> None:
        """Rafraîchit toutes les vues après chargement d'un show."""
        self._engine.bus.emit("sequencer.cues_changed")
        self._engine.bus.emit("sequencer.state_changed")
        self._engine.bus.emit("sequencer.output_changed")
        if self._circuits_view:
            self._circuits_view._rebuild_all_sections()
        if self._faders_view:
            self._faders_view._update_all_displays()