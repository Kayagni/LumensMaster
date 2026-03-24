"""
Moteur principal de LumensMaster.

Le moteur orchestre tous les modules, gère le cycle de calcul DMX,
et coordonne les sauvegardes/chargements de show.

Cycle de calcul DMX :
    1. Combiner les sorties de tous les modules (faders HTP, séquenceur, etc.)
    2. Appliquer le Grand Master
    3. Convertir circuits → canaux DMX via le patch
    4. Écrire dans le buffer DMX
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from lumensmaster.core.config import AppConfig
from lumensmaster.core.dmx import DMXBuffer, DMXOutput, DMXOutputDummy
from lumensmaster.core.events import EventBus
from lumensmaster.core.show import load_show, new_show, save_show
from lumensmaster.modules.faders import Faders
from lumensmaster.modules.grand_master import GrandMaster
from lumensmaster.modules.patch import Patch

logger = logging.getLogger(__name__)


class Engine:
    """
    Moteur central de LumensMaster.
    
    Initialise et connecte tous les modules, gère le cycle de vie
    de l'application.
    
    Utilisation :
        engine = Engine()
        engine.start()
        
        # ... l'application tourne ...
        
        engine.stop()
    """

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig()

        # Infrastructure
        self.bus = EventBus()
        self.dmx_buffer = DMXBuffer()

        # Sortie DMX
        self.dmx_output: DMXOutput | None = None

        # Modules
        self.patch = Patch(self.bus)
        self.grand_master = GrandMaster(self.bus)
        self.faders = Faders(self.bus, count=self.config.ui.fader_count)

        # État du show
        self._show_data: dict[str, Any] = new_show()
        self._show_path: str = ""
        self._dirty: bool = False  # True si des modifications non sauvegardées

        # Abonnements aux événements
        self.bus.on("fader.changed", self._on_fader_changed)
        self.bus.on("grandmaster.changed", self._on_grandmaster_changed)
        self.bus.on("patch.updated", self._on_patch_updated)

    @property
    def is_dirty(self) -> bool:
        """True si le show a des modifications non sauvegardées."""
        return self._dirty

    @property
    def show_name(self) -> str:
        return self._show_data.get("metadata", {}).get("name", "Sans titre")

    # --- Cycle de vie ---

    def start(self, dummy_dmx: bool = False) -> None:
        """
        Démarre le moteur.
        
        Args:
            dummy_dmx: Si True, utilise une sortie DMX factice (sans matériel).
        """
        # Initialiser la sortie DMX
        if dummy_dmx:
            self.dmx_output = DMXOutputDummy(self.dmx_buffer)
            self.dmx_output.connect()
        else:
            self.dmx_output = DMXOutput(
                self.dmx_buffer,
                port=self.config.dmx.port,
                fps=self.config.dmx.fps,
            )
            if self.config.dmx.port:
                self.dmx_output.connect()

        self.dmx_output.start()
        self.bus.emit("engine.started")
        logger.info("Moteur LumensMaster démarré")

    def stop(self) -> None:
        """Arrête le moteur proprement."""
        if self.dmx_output:
            self.dmx_output.stop()

        self.bus.emit("engine.stopped")
        self.bus.clear()
        logger.info("Moteur LumensMaster arrêté")

    # --- Calcul DMX ---

    def update_dmx(self) -> None:
        """
        Recalcule et met à jour le buffer DMX.
        
        Appelé à chaque modification d'un module (fader, GM, etc.).
        """
        # 1. Combiner les sorties en HTP
        htp_output = self.faders.compute_htp()

        # 2. Appliquer le Grand Master et écrire dans le buffer
        dmx_values: dict[int, int] = {}
        for circuit, value in htp_output.items():
            final_value = self.grand_master.apply(value)
            dmx_channel = self.patch.get_dmx_channel(circuit)
            # HTP au niveau DMX aussi (si plusieurs circuits pointent vers le même canal)
            if final_value > dmx_values.get(dmx_channel, 0):
                dmx_values[dmx_channel] = final_value

        # 3. Blackout du buffer, puis écrire les valeurs actives
        self.dmx_buffer.blackout()
        self.dmx_buffer.set_channels(dmx_values)

    # --- Callbacks événements ---

    def _on_fader_changed(self, **kwargs: Any) -> None:
        self._dirty = True
        self.update_dmx()

    def _on_grandmaster_changed(self, **kwargs: Any) -> None:
        self.update_dmx()

    def _on_patch_updated(self, **kwargs: Any) -> None:
        self._dirty = True
        self.update_dmx()

    # --- Connexion DMX ---

    def connect_dmx(self, port: str) -> bool:
        """
        Connecte (ou reconnecte) la sortie DMX sur un port donné.
        
        Args:
            port: Nom du port COM (ex: "COM3")
            
        Returns:
            True si la connexion a réussi.
        """
        if self.dmx_output:
            self.dmx_output.disconnect()
            result = self.dmx_output.connect(port)
            if result:
                self.config.dmx.port = port
            return result
        return False

    # --- Gestion du show ---

    def new_show(self) -> None:
        """Crée un nouveau show vide."""
        self._show_data = new_show()
        self._show_path = ""
        self._dirty = False

        # Reset des modules
        self.patch.clear()
        self.faders.all_down()
        self.grand_master.full()
        self.update_dmx()

        self.bus.emit("show.loaded")
        logger.info("Nouveau show créé")

    def save_current_show(self, path: str = "") -> bool:
        """
        Sauvegarde le show en cours.
        
        Args:
            path: Chemin du fichier. Si vide, utilise le dernier chemin connu.
        """
        save_path = path or self._show_path
        if not save_path:
            logger.warning("Aucun chemin de sauvegarde défini")
            return False

        # Collecter l'état de tous les modules
        self._show_data["patch"] = self.patch.to_dict()
        self._show_data["faders"] = self.faders.to_dict()
        self._show_data["grandmaster"] = self.grand_master.level

        if save_show(self._show_data, save_path):
            self._show_path = save_path
            self._dirty = False
            self.config.last_show_path = save_path
            self.bus.emit("show.saved", path=save_path)
            return True
        return False

    def load_existing_show(self, path: str) -> bool:
        """
        Charge un show existant.
        
        Args:
            path: Chemin du fichier de show.
        """
        data = load_show(path)
        if data is None:
            return False

        self._show_data = data
        self._show_path = path
        self._dirty = False

        # Restaurer l'état des modules
        self.patch.from_dict(data.get("patch", {}))
        self.faders.from_dict(data.get("faders", {}))
        self.grand_master.level = data.get("grandmaster", 255)

        self.update_dmx()
        self.config.last_show_path = path
        self.bus.emit("show.loaded")
        logger.info("Show chargé : %s", path)
        return True

    # --- Utilitaires ---

    @staticmethod
    def list_serial_ports() -> list[str]:
        """Liste les ports série disponibles sur le système."""
        try:
            import serial.tools.list_ports
            return [port.device for port in serial.tools.list_ports.comports()]
        except ImportError:
            logger.warning("pyserial non disponible pour lister les ports")
            return []