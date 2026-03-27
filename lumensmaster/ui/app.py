"""
Fenêtre principale de LumensMaster.

Gère le layout global, la barre d'outils, la barre de statut,
et instancie les vues des différents modules.
"""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from lumensmaster import __app_name__, __version__
from lumensmaster.core.engine import Engine
from lumensmaster.ui.theme import Colors, apply_theme
from lumensmaster.ui.views.faders_view import FadersView
from lumensmaster.ui.views.circuits_view import CircuitsView

logger = logging.getLogger(__name__)


class App:
    """
    Application principale LumensMaster.
    
    Utilisation :
        engine = Engine(config)
        app = App(engine)
        app.run()
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._faders_view: FadersView | None = None
        self._port_combo: int = 0
        self._circuits_view = None

    def run(self, dummy_dmx: bool = False) -> None:
        """Lance l'application (bloquant)."""
        dpg.create_context()
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
            dpg.start_dearpygui()
        finally:
            self._engine.stop()
            self._engine.config.save()
            dpg.destroy_context()

    def _build_ui(self) -> None:
        """Construit l'interface graphique complète."""
        with dpg.window(tag="main_window"):
            # Barre d'outils
            self._build_toolbar()

            dpg.add_spacer(height=8)
            dpg.add_separator()
            dpg.add_spacer(height=8)

            # Zone de contenu principale (faders pour l'instant)
            self._build_content()

            dpg.add_spacer(height=4)
            dpg.add_separator()

            # Barre de statut
            self._build_statusbar()

        dpg.set_primary_window("main_window", True)
        # Fenêtre flottante Circuits
        self._circuits_view = CircuitsView(self._engine)
        self._circuits_view.build()

    def _build_toolbar(self) -> None:
        """Construit la barre d'outils supérieure."""
        with dpg.group(horizontal=True):
            # Titre
            dpg.add_text(
                __app_name__,
                color=Colors.ACCENT,
            )
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

            dpg.add_button(
                label="Rafraîchir",
                callback=self._refresh_ports,
            )
            dpg.add_button(
                label="Connecter",
                callback=self._on_connect,
            )

            dpg.add_spacer(width=24)

            # --- Show ---
            dpg.add_text("Show :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)

            dpg.add_button(label="Nouveau", callback=self._on_new_show)
            dpg.add_button(label="Ouvrir", callback=self._on_open_show)
            dpg.add_button(label="Sauvegarder", callback=self._on_save_show)

            dpg.add_spacer(width=24)

            # --- Test rapide ---
            dpg.add_text("Test :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)
            dpg.add_button(
                label="Charger démo",
                callback=self._load_demo_contents,
            )

            # --- Commande fenêtre circuits ---
            dpg.add_spacer(width=24)
            dpg.add_text("Fenêtres :", color=Colors.TEXT_SECONDARY)
            dpg.add_spacer(width=4)
            dpg.add_button(
                label="Circuits",
                callback=self._toggle_circuits_window,
)

    def _build_content(self) -> None:
        """Construit la zone de contenu principale."""
        self._faders_view = FadersView(self._engine)
        self._faders_view.build(dpg.last_container())

    def _build_statusbar(self) -> None:
        """Construit la barre de statut inférieure."""
        with dpg.group(horizontal=True):
            dpg.add_text(
                "DMX : Non connecté",
                tag="status_dmx",
                color=Colors.TEXT_SECONDARY,
            )
            dpg.add_spacer(width=24)
            dpg.add_text(
                f"Show : {self._engine.show_name}",
                tag="status_show",
                color=Colors.TEXT_SECONDARY,
            )

    # --- Callbacks toolbar ---

    def _on_port_selected(self, sender: int, value: str) -> None:
        if value != "(aucun port)":
            self._engine.config.dmx.port = value

    def _refresh_ports(self) -> None:
        devices = self._engine.list_dmx_devices()
        device_labels = [
            f"#{d['index']} - {d['description']}" for d in devices
        ] if devices else ["(aucun device FTDI)"]
        dpg.configure_item(self._port_combo, items=device_labels)

    def _on_connect(self) -> None:
        selected = dpg.get_value(self._port_combo)
        if not selected or selected == "(aucun device FTDI)":
            self._update_status_dmx("Aucun device FTDI trouvé", Colors.WARNING)
            return

        # Extraire l'index du label "#0 - description"
        try:
            device_index = int(selected.split("#")[1].split(" ")[0])
        except (IndexError, ValueError):
            device_index = 0

        if self._engine.connect_dmx(device_index):
            self._update_status_dmx(f"Connecté : {selected}", Colors.SUCCESS)
        else:
            self._update_status_dmx(f"Échec connexion FTDI", Colors.ERROR)

    def _on_new_show(self) -> None:
        self._engine.new_show()
        if self._faders_view:
            for fid in range(1, self._engine.faders.count + 1):
                self._faders_view.update_fader_display(fid, 0)
        self._update_status_show()

    def _on_open_show(self) -> None:
        # Pour l'instant, boîte de dialogue basique
        # TODO: utiliser un file dialog Dear PyGui
        logger.info("Ouvrir un show (à implémenter avec file dialog)")

    def _on_save_show(self) -> None:
        if self._engine.save_current_show():
            self._update_status_show()
        else:
            # Pas de chemin connu → demander un chemin
            # TODO: utiliser un file dialog Dear PyGui
            logger.info("Sauvegarder sous (à implémenter avec file dialog)")

    def _load_demo_contents(self) -> None:
        """Charge un contenu de démonstration sur les faders pour tester."""
        # Fader 1 : circuits 1-4 à fond (douche face)
        self._engine.faders.set_contents(1, {1: 255})
        self._engine.faders.set_label(1, "Face")

        # Fader 2 : circuits 5-8 (contre)
        self._engine.faders.set_contents(2, {2: 255})
        self._engine.faders.set_label(2, "Contre")

        # Fader 3 : circuits 9-10 (latéraux)
        self._engine.faders.set_contents(3, {3: 255})
        self._engine.faders.set_label(3, "Latéraux")

        # Fader 4 : circuit 11 (douche spéciale)
        self._engine.faders.set_contents(4, {4: 255})
        self._engine.faders.set_label(4, "Spé")

        # Fader 5 : circuits 12-14 (cyclo RGB)
        self._engine.faders.set_contents(5, {12: 80, 13: 120, 14: 255})
        self._engine.faders.set_label(5, "Cyclo")

        logger.info("Contenu démo chargé sur les faders 1-5")

    # --- Mise à jour de la barre de statut ---

    def _update_status_dmx(self, text: str, color: tuple = Colors.TEXT_SECONDARY) -> None:
        dpg.set_value("status_dmx", f"DMX : {text}")
        dpg.configure_item("status_dmx", color=color)

    def _update_status_show(self) -> None:
        name = self._engine.show_name
        dirty = " *" if self._engine.is_dirty else ""
        dpg.set_value("status_show", f"Show : {name}{dirty}")

    def _toggle_circuits_window(self):
        """Ouvre ou ferme la fenêtre Circuits."""
        if self._circuits_view and dpg.does_item_exist(self._circuits_view._window_id):
            if dpg.is_item_shown(self._circuits_view._window_id):
                dpg.configure_item(self._circuits_view._window_id, show=False)
            else:
                dpg.configure_item(self._circuits_view._window_id, show=True)
        else:
            self._circuits_view = CircuitsView(self._engine)
            self._circuits_view.build()