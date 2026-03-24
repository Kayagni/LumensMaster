"""
Module de sortie DMX512 via OpenDMX (FTDI).

L'OpenDMX utilise un chip FTDI (FT232R) pour convertir l'USB en signal DMX512.
Le protocole DMX512 consiste à envoyer un break signal suivi d'un start code (0x00)
puis de 512 octets de données de canaux, à une cadence de ~40 Hz.

Prérequis :
    - Driver FTDI VCP installé (Virtual COM Port)
    - L'interface OpenDMX apparaît comme un port COM sous Windows
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Constantes DMX512
DMX_CHANNELS = 512
DMX_BAUDRATE = 250000
DMX_BREAK_RATE = 76800     # Baud rate utilisé pour simuler le break
DMX_BREAK_BYTE = b"\x00"   # Octet envoyé au break rate pour générer le break
DMX_START_CODE = b"\x00"   # Start code standard DMX
DMX_DEFAULT_FPS = 40       # Fréquence de rafraîchissement par défaut


class DMXBuffer:
    """
    Buffer partagé de 512 canaux DMX (thread-safe).
    
    Les modules écrivent dans ce buffer, le thread DMX le lit
    et l'envoie à l'interface à chaque cycle.
    """

    def __init__(self) -> None:
        self._data = bytearray(DMX_CHANNELS)
        self._lock = threading.Lock()

    def set_channel(self, channel: int, value: int) -> None:
        """
        Définit la valeur d'un canal DMX.
        
        Args:
            channel: Numéro de canal DMX (1-512)
            value: Valeur (0-255)
        """
        if not 1 <= channel <= DMX_CHANNELS:
            return
        value = max(0, min(255, int(value)))
        with self._lock:
            self._data[channel - 1] = value

    def set_channels(self, channels: dict[int, int]) -> None:
        """
        Définit plusieurs canaux en une seule opération.
        
        Args:
            channels: Dictionnaire {numéro_canal: valeur}
        """
        with self._lock:
            for channel, value in channels.items():
                if 1 <= channel <= DMX_CHANNELS:
                    self._data[channel - 1] = max(0, min(255, int(value)))

    def get_channel(self, channel: int) -> int:
        """Retourne la valeur d'un canal DMX (1-512)."""
        if not 1 <= channel <= DMX_CHANNELS:
            return 0
        with self._lock:
            return self._data[channel - 1]

    def get_frame(self) -> bytes:
        """Retourne une copie du frame DMX complet (512 octets)."""
        with self._lock:
            return bytes(self._data)

    def blackout(self) -> None:
        """Met tous les canaux à zéro."""
        with self._lock:
            self._data = bytearray(DMX_CHANNELS)

    def __repr__(self) -> str:
        active = sum(1 for v in self._data if v > 0)
        return f"<DMXBuffer: {active} canaux actifs / {DMX_CHANNELS}>"


class DMXOutput:
    """
    Gère l'envoi DMX512 vers une interface OpenDMX (FTDI).
    
    Fonctionne dans un thread dédié qui lit le DMXBuffer partagé
    et l'envoie via le port série à la cadence configurée.
    
    Utilisation :
        buffer = DMXBuffer()
        output = DMXOutput(buffer, port="COM3")
        output.start()
        
        buffer.set_channel(1, 255)  # Canal 1 à fond
        
        output.stop()
    """

    def __init__(
        self,
        buffer: DMXBuffer,
        port: Optional[str] = None,
        fps: int = DMX_DEFAULT_FPS,
    ) -> None:
        self._buffer = buffer
        self._port_name = port
        self._fps = fps
        self._serial = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def port(self) -> Optional[str]:
        return self._port_name

    def connect(self, port: Optional[str] = None) -> bool:
        """
        Ouvre la connexion vers le port série de l'OpenDMX.
        
        Args:
            port: Port COM (ex: "COM3"). Si None, utilise le port configuré.
            
        Returns:
            True si la connexion a réussi.
        """
        if port:
            self._port_name = port

        if not self._port_name:
            logger.error("Aucun port DMX configuré")
            return False

        try:
            import serial

            self._serial = serial.Serial(
                port=self._port_name,
                baudrate=DMX_BAUDRATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_TWO,
                timeout=1,
            )
            self._connected = True
            logger.info("DMX connecté sur %s", self._port_name)
            return True

        except ImportError:
            logger.error("pyserial n'est pas installé (pip install pyserial)")
            return False
        except Exception:
            logger.exception("Erreur de connexion DMX sur %s", self._port_name)
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Ferme la connexion série."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None
        self._connected = False
        logger.info("DMX déconnecté")

    def start(self) -> None:
        """Démarre le thread d'envoi DMX."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._output_loop,
            name="DMXOutput",
            daemon=True,
        )
        self._thread.start()
        logger.info("Thread DMX démarré (%d FPS)", self._fps)

    def stop(self) -> None:
        """Arrête le thread d'envoi DMX et envoie un blackout."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        # Blackout de sécurité à l'arrêt
        if self._connected:
            self._buffer.blackout()
            self._send_frame()
            self.disconnect()

        logger.info("Thread DMX arrêté")

    def _send_frame(self) -> None:
        """Envoie un frame DMX complet (break + start code + 512 canaux)."""
        if not self._serial or not self._serial.is_open:
            return

        try:
            # Simuler le break DMX en changeant temporairement le baud rate
            self._serial.baudrate = DMX_BREAK_RATE
            self._serial.write(DMX_BREAK_BYTE)
            self._serial.flush()

            # Revenir au baud rate DMX et envoyer les données
            self._serial.baudrate = DMX_BAUDRATE
            frame = DMX_START_CODE + self._buffer.get_frame()
            self._serial.write(frame)
            self._serial.flush()

        except Exception:
            logger.exception("Erreur d'envoi DMX")

    def _output_loop(self) -> None:
        """Boucle principale du thread DMX."""
        interval = 1.0 / self._fps

        while self._running:
            start = time.perf_counter()

            if self._connected:
                self._send_frame()

            # Compensation du temps d'exécution pour maintenir le FPS
            elapsed = time.perf_counter() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)


class DMXOutputDummy(DMXOutput):
    """
    Sortie DMX factice pour le développement sans interface physique.
    
    Simule l'envoi DMX en loggant les changements sans matériel.
    Utile pour développer et tester l'UI.
    """

    def connect(self, port: Optional[str] = None) -> bool:
        self._port_name = port or "DUMMY"
        self._connected = True
        logger.info("DMX DUMMY connecté (pas de matériel)")
        return True

    def disconnect(self) -> None:
        self._connected = False
        logger.info("DMX DUMMY déconnecté")

    def _send_frame(self) -> None:
        pass  # Ne fait rien — pas de matériel