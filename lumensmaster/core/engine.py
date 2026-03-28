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
from lumensmaster.modules.circuits import Circuits
from lumensmaster.modules.sequencer import Sequencer

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
        self.circuits = Circuits(self.bus)
        self.sequencer = Sequencer(self.bus)

        # État du show
        self._show_data: dict[str, Any] = new_show()
        self._show_path: str = ""
        self._dirty: bool = False  # True si des modifications non sauvegardées

        # Abonnements aux événements
        self.bus.on("fader.changed", self._on_fader_changed)
        self.bus.on("grandmaster.changed", self._on_grandmaster_changed)
        self.bus.on("patch.updated", self._on_patch_updated)
        self.bus.on("circuit.changed", self._on_circuit_changed)
        self.bus.on("sequencer.output_changed", self._on_sequencer_changed)

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
        if dummy_dmx:
            self.dmx_output = DMXOutputDummy(self.dmx_buffer)
            self.dmx_output.connect()
        else:
            self.dmx_output = DMXOutputDummy(self.dmx_buffer)
            self.dmx_output.connect()
            # On démarre en dummy, la connexion réelle se fait
            # via le bouton Connecter dans l'UI

        self.dmx_output.start()
        self.bus.emit("engine.started")
        logger.info("Moteur LumensMaster démarré")

    def stop(self) -> None:
        """Arrête le moteur proprement."""
        if self.dmx_output:
            self.dmx_output.stop()

        self.sequencer.stop()
        self.bus.emit("engine.stopped")
        self.bus.clear()
        logger.info("Moteur LumensMaster arrêté")

    # --- Calcul DMX ---

    def update_dmx(self):
        """
        Recalcule et met à jour le buffer DMX.
        Combine en HTP : circuits directs + faders.
        """
        # 1. Sorties des faders
        htp_output = self.faders.compute_htp()
    
        # 2. Combiner avec les niveaux directs des circuits (HTP)
        circuit_output = self.circuits.get_output()
        for circuit, value in circuit_output.items():
            if value > htp_output.get(circuit, 0):
                htp_output[circuit] = value

        # 3. Combiner avec la sortie du séquenceur (HTP)
        seq_output = self.sequencer.get_output()
        for circuit, value in seq_output.items():
            if value > htp_output.get(circuit, 0):
                htp_output[circuit] = value
    
        # 4. Appliquer le Grand Master et mapper via le patch
        new_frame = bytearray(512)
        for circuit, value in htp_output.items():
            final_value = self.grand_master.apply(value)
            dmx_channel = self.patch.get_dmx_channel(circuit)
            if 1 <= dmx_channel <= 512:
                idx = dmx_channel - 1
                if final_value > new_frame[idx]:
                    new_frame[idx] = final_value
    
        # 4. Appliquer d'un seul coup (atomique)
        self.dmx_buffer.set_frame(new_frame)

    # --- Callbacks événements ---

    def _on_fader_changed(self, **kwargs: Any) -> None:
        self._dirty = True
        self.update_dmx()

    def _on_grandmaster_changed(self, **kwargs: Any) -> None:
        self.update_dmx()

    def _on_patch_updated(self, **kwargs: Any) -> None:
        self._dirty = True
        self.update_dmx()

    def _on_circuit_changed(self, **kwargs):
        self._dirty = True
        self.update_dmx()

    def _on_sequencer_changed(self, **kwargs):
        self.update_dmx()

    # --- Connexion DMX ---

    def connect_dmx(self, device_index: int = 0) -> bool:
        """
        Connecte la sortie DMX sur un device FTDI donné.
        Remplace la sortie dummy par une vraie sortie si nécessaire.
        """
        if self.dmx_output:
            self.dmx_output.stop()

        self.dmx_output = DMXOutput(
            self.dmx_buffer,
            fps=self.config.dmx.fps,
        )

        if self.dmx_output.connect(device_index):
            self.dmx_output.start()
            self.update_dmx()
            return True
        else:
            # Échec : revenir en dummy
            self.dmx_output = DMXOutputDummy(self.dmx_buffer)
            self.dmx_output.connect()
            self.dmx_output.start()
            return False
        
    def get_circuit_source(self, circuit: int) -> dict[str, bool]:
        """
        Détermine les sources actives pour un circuit.
 
        Returns:
            Dict avec les clés "circuit", "fader", "sequencer" (True/False)
        """
        sources = {
            "circuit": False,
            "fader": False,
            "sequencer": False,
        }
 
        # Source directe (circuits)
        if self.circuits.get_level(circuit) > 0:
            sources["circuit"] = True
 
        # Source faders
        for fid in range(1, self.faders.count + 1):
            fader = self.faders.get_fader(fid)
            if fader and fader.level > 0 and circuit in fader.contents:
                if fader.get_output(circuit) > 0:
                    sources["fader"] = True
                    break
 
        # Source séquenceur
        seq_output = self.sequencer.get_output()
        if seq_output.get(circuit, 0) > 0:
            sources["sequencer"] = True
 
        return sources
 
    def get_effective_level(self, circuit: int) -> int:
        """
        Retourne le niveau effectif d'un circuit (HTP de toutes les sources).
        """
        level = 0
 
        # Direct
        direct = self.circuits.get_level(circuit)
        if direct > level:
            level = direct
 
        # Faders
        for fid in range(1, self.faders.count + 1):
            fader = self.faders.get_fader(fid)
            if fader and fader.level > 0 and circuit in fader.contents:
                output = fader.get_output(circuit)
                if output > level:
                    level = output
 
        # Séquenceur
        seq_output = self.sequencer.get_output()
        seq_level = seq_output.get(circuit, 0)
        if seq_level > level:
            level = seq_level
 
        return level
 
 
    def get_contributing_faders(self, circuit: int) -> list[int]:
        """
        Retourne la liste des IDs de faders qui contribuent à un circuit.
        Ne retourne que les faders dont le level > 0 et qui contiennent le circuit.
        """
        result = []
        for fid in range(1, self.faders.count + 1):
            fader = self.faders.get_fader(fid)
            if fader and fader.level > 0 and circuit in fader.contents:
                if fader.get_output(circuit) > 0:
                    result.append(fid)
        return result
    
    

    @staticmethod
    def list_dmx_devices() -> list[dict[str, str]]:
        """Liste les interfaces FTDI disponibles."""
        return DMXOutput.list_devices()

    # --- Gestion du show ---

    def new_show(self) -> None:
        """Crée un nouveau show vide."""
        self._show_data = new_show()
        self._show_path = ""
        self._dirty = False

        # Reset des modules
        self.patch.clear()
        self.faders.all_down()
        self.circuits.clear_all()
        self.grand_master.full()
        self.update_dmx()
        self.sequencer.from_dict({})

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
        self._show_data["circuits"] = self.circuits.to_dict()
        self._show_data["grandmaster"] = self.grand_master.level
        self._show_data["sequencer"] = self.sequencer.to_dict()

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
        self.circuits.from_dict(data.get("circuits", {}))
        self.grand_master.level = data.get("grandmaster", 255)
        self.sequencer.from_dict(data.get("sequencer", {}))

        self.update_dmx()
        self.config.last_show_path = path
        self.bus.emit("show.loaded")
        logger.info("Show chargé : %s", path)
        return True