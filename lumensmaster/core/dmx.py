"""
Module de sortie DMX512 via OpenDMX (FTDI FT232).

Utilise l'API D2XX native de FTDI (via le package ftd2xx) pour un contrôle
fiable du timing DMX, en particulier le break signal.

Prérequis :
    - Driver FTDI D2XX installé (inclus dans le combined driver)
    - Le VCP doit être désactivé pour que D2XX puisse accéder au chip
    - pip install ftd2xx
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
DMX_START_CODE = b"\x00"
DMX_DEFAULT_FPS = 40

# Timing DMX512 (en secondes)
DMX_BREAK_DURATION = 0.000100    # Break >= 88us, on utilise 100us
DMX_MAB_DURATION = 0.000012      # Mark After Break >= 8us, on utilise 12us


class DMXBuffer:
    """Buffer partagé de 512 canaux DMX (thread-safe)."""

    def __init__(self) -> None:
        self._data = bytearray(DMX_CHANNELS)
        self._lock = threading.Lock()

    def set_channel(self, channel: int, value: int) -> None:
        if not 1 <= channel <= DMX_CHANNELS:
            return
        value = max(0, min(255, int(value)))
        with self._lock:
            self._data[channel - 1] = value

    def set_channels(self, channels: dict[int, int]) -> None:
        with self._lock:
            for channel, value in channels.items():
                if 1 <= channel <= DMX_CHANNELS:
                    self._data[channel - 1] = max(0, min(255, int(value)))

    def set_frame(self, data: bytearray) -> None:
        """Remplace le frame complet d'un seul coup (atomique)."""
        with self._lock:
            self._data = data

    def get_channel(self, channel: int) -> int:
        if not 1 <= channel <= DMX_CHANNELS:
            return 0
        with self._lock:
            return self._data[channel - 1]

    def get_frame(self) -> bytes:
        with self._lock:
            return bytes(self._data)

    def blackout(self) -> None:
        with self._lock:
            self._data = bytearray(DMX_CHANNELS)

    def __repr__(self) -> str:
        active = sum(1 for v in self._data if v > 0)
        return f"<DMXBuffer: {active} canaux actifs / {DMX_CHANNELS}>"


class DMXOutput:
    """
    Gère l'envoi DMX512 vers une interface OpenDMX via l'API D2XX.
    """

    def __init__(
        self,
        buffer: DMXBuffer,
        fps: int = DMX_DEFAULT_FPS,
    ) -> None:
        self._buffer = buffer
        self._fps = fps
        self._device = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._device_description: str = ""

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def device_description(self) -> str:
        return self._device_description

    def connect(self, device_index: int = 0) -> bool:
        """
        Ouvre la connexion vers le chip FTDI via D2XX.

        Args:
            device_index: Index du device FTDI (0 = premier trouvé).
        """
        try:
            import ftd2xx

            self._device = ftd2xx.open(device_index)
            self._device.setBaudRate(DMX_BAUDRATE)
            self._device.setDataCharacteristics(
                ftd2xx.defines.BITS_8,
                ftd2xx.defines.STOP_BITS_2,
                ftd2xx.defines.PARITY_NONE,
            )
            self._device.setFlowControl(ftd2xx.defines.FLOW_NONE, 0, 0)
            self._device.purge(ftd2xx.defines.PURGE_TX | ftd2xx.defines.PURGE_RX)
            self._device.setTimeouts(1000, 1000)

            self._device_description = f"FTDI #{device_index}"
            self._connected = True
            logger.info("DMX D2XX connecté : %s", self._device_description)
            return True

        except ImportError:
            logger.error("ftd2xx n'est pas installé (pip install ftd2xx)")
            return False
        except Exception:
            logger.exception("Erreur de connexion DMX D2XX (index=%d)", device_index)
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = None
        self._connected = False
        logger.info("DMX D2XX déconnecté")

    def start(self) -> None:
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
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._connected:
            self._buffer.blackout()
            self._send_frame()
            self.disconnect()
        logger.info("Thread DMX arrêté")

    def _send_frame(self) -> None:
        """
        Envoie un frame DMX512 complet :
            1. Break (TX bas >= 88us)
            2. MAB (TX relâché >= 8us)
            3. Start code + 512 octets
        """
        if not self._device or not self._connected:
            return
        try:
            self._device.setBreakOn()
            time.sleep(DMX_BREAK_DURATION)
            self._device.setBreakOff()
            time.sleep(DMX_MAB_DURATION)
            frame = DMX_START_CODE + self._buffer.get_frame()
            self._device.write(frame)
        except Exception:
            logger.exception("Erreur d'envoi DMX")
            self._connected = False

    def _output_loop(self) -> None:
        interval = 1.0 / self._fps
        while self._running:
            start = time.perf_counter()
            if self._connected:
                self._send_frame()
            elapsed = time.perf_counter() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    @staticmethod
    def list_devices() -> list[dict[str, str]]:
        """Liste les devices FTDI disponibles via D2XX."""
        try:
            import ftd2xx
            devices = []
            dev_list = ftd2xx.listDevices()
            if dev_list is None:
                return []
            for i in range(len(dev_list)):
                try:
                    dev = ftd2xx.getDeviceInfoDetail(i)
                    devices.append({
                        "index": str(i),
                        "serial": dev.get("serial", b"").decode("utf-8", errors="replace"),
                        "description": dev.get("description", b"").decode("utf-8", errors="replace"),
                    })
                except Exception:
                    pass
            return devices
        except ImportError:
            logger.warning("ftd2xx non disponible")
            return []
        except Exception:
            logger.exception("Erreur listage devices FTDI")
            return []


class DMXOutputDummy(DMXOutput):
    """Sortie DMX factice pour le développement sans interface physique."""

    def connect(self, device_index: int = 0) -> bool:
        self._device_description = "DUMMY"
        self._connected = True
        logger.info("DMX DUMMY connecté (pas de matériel)")
        return True

    def disconnect(self) -> None:
        self._connected = False
        logger.info("DMX DUMMY déconnecté")

    def _send_frame(self) -> None:
        pass