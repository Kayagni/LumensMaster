"""
Module Audio : lecteurs, sons directs et mixeur.

Deux façons de jouer un fichier, qui partagent le même mixeur :
    - Lecteurs (Player) : N emplacements persistants, façon WhiteCat.
      Un fichier est chargé (décodeur ouvert, positionné au cue in) puis
      piloté : play / pause / stop / seek / volume / loop.
    - Sons (Sound) : sons définis à l'avance, joués directement
      (« fire and forget ») depuis la fenêtre Audio ou un banger,
      sans passer par un lecteur. Plusieurs sons peuvent jouer en même
      temps ; un son peut redémarrer ou se superposer à lui-même.

Latence :
    La latence de sortie (tampon du device, buffer_ms) est la même pour
    tout. Ce qui varie, c'est le temps d'ouverture/décodage au moment du
    déclenchement : il est nul pour un fichier préchargé (décodé en
    mémoire) et pour un lecteur déjà chargé. Un son non préchargé ouvre
    son fichier au déclenchement.

Bibliothèque :
    Les fichiers utilisés par le show, avec cue in / cue out (secondes)
    propres à chaque fichier (comme WhiteCat). Identifiés par un id
    entier stable (utilisé par les lecteurs, les sons et les bangers).
    En mémoire les chemins sont absolus ; dans le .lms ils sont relatifs
    au dossier du show quand c'est possible.

Threading :
    - Le callback du device (thread audio) appelle Mixer.render(). Il ne
      touche jamais à Dear PyGui ni au bus : il pose un flag.
    - Toutes les autres méthodes s'appellent depuis le thread principal.
      poll_ui() (boucle de rendu) émet audio.state_changed / audio.tick.
    - Mixer.lock protège les voix entre les deux threads.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

try:
    import miniaudio
except ImportError:  # pragma: no cover - dépend de l'installation
    miniaudio = None

from lumensmaster.core.events import EventBus
from lumensmaster.modules import bangers as bg

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
MIN_FADE = 0.008            # s : micro-fondu anti-clic sur tout arrêt
PRELOAD_MAX_SECONDS = 60.0  # préchargement proposé par défaut en dessous
DEFAULT_BUFFER_MS = 60
TICK_INTERVAL = 0.1         # s : rafraîchissement des positions (UI)
MAX_SOUNDS = 128
LOOP_MODES = [("off", "Off"), ("file", "Fichier"), ("cue", "Cue in/out")]
RETRIGGER_MODES = [("restart", "Redémarre"), ("overlap", "Superpose")]

_EMPTY = np.zeros((0, CHANNELS), dtype=np.float32)


# ═══════════════════════════════════════════════════════════════════
#  Sources de samples (float32, stéréo, 48 kHz)
# ═══════════════════════════════════════════════════════════════════

class Source:
    length: int = 0  # en frames (0 = inconnu)

    def read(self, n: int) -> np.ndarray:
        raise NotImplementedError

    def seek(self, frame: int) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class MemorySource(Source):
    """Samples déjà décodés (préchargement ou tests)."""

    def __init__(self, samples: np.ndarray) -> None:
        self._s = samples
        self.length = len(samples)
        self._pos = 0

    def read(self, n: int) -> np.ndarray:
        chunk = self._s[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def seek(self, frame: int) -> None:
        self._pos = max(0, min(self.length, int(frame)))


class DecoderSource(Source):
    """
    Décodage au fil de l'eau avec le ma_decoder de miniaudio.

    Utilise l'API bas niveau (miniaudio.ffi / miniaudio.lib) pour pouvoir
    faire des seeks sur place, sans réouvrir le fichier.
    ⚠ Dépend de fonctions internes de pyminiaudio : version épinglée
    dans requirements.txt.
    """

    _CAPACITY = 16384

    def __init__(self, path: str, duration: float = 0.0) -> None:
        if miniaudio is None:
            raise RuntimeError("miniaudio non installé")
        ffi, lib = miniaudio.ffi, miniaudio.lib
        self._ffi, self._lib = ffi, lib
        self._decoder = ffi.new("ma_decoder *")
        config = lib.ma_decoder_config_init(
            miniaudio.SampleFormat.FLOAT32.value, CHANNELS, SAMPLE_RATE)
        if sys.platform == "win32":
            name = miniaudio._get_filename_bytes_w(path)
            result = lib.ma_decoder_init_file_w(name, ffi.addressof(config),
                                                self._decoder)
        else:
            name = miniaudio._get_filename_bytes(path)
            result = lib.ma_decoder_init_file(name, ffi.addressof(config),
                                              self._decoder)
        if result != lib.MA_SUCCESS:
            raise miniaudio.DecodeError("impossible d'ouvrir le décodeur", result)
        self._open = True
        self._buf = ffi.new("float[]", self._CAPACITY * CHANNELS)
        self._frames_read = ffi.new("ma_uint64 *")
        self.length = int(duration * SAMPLE_RATE)

    def read(self, n: int) -> np.ndarray:
        if not self._open or n <= 0:
            return _EMPTY
        parts = []
        while n > 0:
            k = min(n, self._CAPACITY)
            result = self._lib.ma_decoder_read_pcm_frames(
                self._decoder, self._buf, k, self._frames_read)
            got = int(self._frames_read[0])
            if got > 0:
                raw = self._ffi.buffer(self._buf, got * CHANNELS * 4)
                parts.append(np.frombuffer(raw, dtype=np.float32)
                             .reshape(-1, CHANNELS).copy())
                n -= got
            if got < k or result != self._lib.MA_SUCCESS:
                break
        if not parts:
            return _EMPTY
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    def seek(self, frame: int) -> None:
        if self._open:
            self._lib.ma_decoder_seek_to_pcm_frame(self._decoder, max(0, int(frame)))

    def close(self) -> None:
        if self._open:
            self._open = False
            self._lib.ma_decoder_uninit(self._decoder)

    def __del__(self) -> None:  # filet de sécurité
        try:
            self.close()
        except Exception:
            pass


def decode_to_memory(path: str) -> np.ndarray:
    """Décode un fichier complet en float32 stéréo 48 kHz."""
    if miniaudio is None:
        raise RuntimeError("miniaudio non installé")
    decoded = miniaudio.decode_file(path, miniaudio.SampleFormat.FLOAT32,
                                    CHANNELS, SAMPLE_RATE)
    return np.frombuffer(decoded.samples, dtype=np.float32).reshape(-1, CHANNELS).copy()


def probe_duration(path: str) -> float:
    if miniaudio is None:
        raise RuntimeError("miniaudio non installé")
    return float(miniaudio.get_file_info(path).duration)


# ═══════════════════════════════════════════════════════════════════
#  Voix et mixeur (thread audio)
# ═══════════════════════════════════════════════════════════════════

class Voice:
    """
    Une lecture en cours d'une source.

    start      : point de rembobinage (cue in)
    end        : frame de fin (cue out) ou None = fin de la source
    loop       : boucle ; loop_start = point de reprise
    on_end     : "rewind" (lecteur : pause au cue in) | "finish" (son)
    Le gain est un volume normalisé 0..1 ; l'amplitude appliquée est
    gain² (courbe proche de la perception, fondus plus naturels).
    """

    def __init__(self, source: Source, start: int = 0, end: int | None = None,
                 loop: bool = False, loop_start: int | None = None,
                 on_end: str = "finish") -> None:
        self.source = source
        self.start = max(0, start)
        self.end = end
        self.loop = loop
        self.loop_start = self.start if loop_start is None else loop_start
        self.on_end = on_end
        self.pos = self.start
        if self.start:
            source.seek(self.start)
        self.paused = True
        self.finished = False
        self.gain = 0.0
        self._target = 0.0
        self._step = 0.0
        self._after_fade: str | None = None
        self.changed = False

    @property
    def fading_out(self) -> bool:
        return self._after_fade is not None

    def fade_to(self, target: float, seconds: float, after: str | None = None) -> None:
        self._target = max(0.0, min(1.0, target))
        frames = max(1, int(max(seconds, MIN_FADE) * SAMPLE_RATE))
        self._step = (self._target - self.gain) / frames
        self._after_fade = after
        if self._step == 0.0:
            self._complete_fade()

    def seek(self, frame: int) -> None:
        self.pos = max(0, int(frame))
        self.source.seek(self.pos)

    def rewind(self) -> None:
        self.seek(self.start)

    def _complete_fade(self) -> None:
        self._step = 0.0
        action, self._after_fade = self._after_fade, None
        if action == "pause":
            self.paused = True
        elif action == "rewind":
            self.rewind()
            self.paused = True
        elif action == "finish":
            self.finished = True
        if action:
            self.changed = True

    def _reach_end(self) -> None:
        if self.on_end == "rewind":
            self.rewind()
            self.paused = True
            self._step = 0.0
            self._after_fade = None
        else:
            self.finished = True
        self.changed = True

    def render(self, n: int) -> np.ndarray | None:
        if self.finished or self.paused:
            return None
        out = np.zeros((n, CHANNELS), dtype=np.float32)
        filled = 0
        loops = 0
        while filled < n:
            want = n - filled
            if self.end is not None:
                want = min(want, self.end - self.pos)
            chunk = self.source.read(want) if want > 0 else _EMPTY
            m = len(chunk)
            if m:
                out[filled:filled + m] = chunk
                filled += m
                self.pos += m
            at_end = (want <= 0 or m < want
                      or (self.end is not None and self.pos >= self.end))
            if not at_end:
                continue
            region = (self.end if self.end is not None else self.source.length) \
                - self.loop_start
            if self.loop and loops < 8 and (region > 0 or self.end is None):
                self.pos = self.loop_start
                self.source.seek(self.loop_start)
                loops += 1
                continue
            self._reach_end()
            break

        if self._step:
            g = self.gain + self._step * np.arange(1, n + 1, dtype=np.float32)
            if self._step > 0:
                np.minimum(g, self._target, out=g)
            else:
                np.maximum(g, self._target, out=g)
            self.gain = float(g[-1])
            out *= (g * g)[:, None]
            if self.gain == self._target:
                self._complete_fade()
        elif self.gain != 1.0:
            out *= self.gain * self.gain
        return out


class Mixer:
    """Somme des voix actives. render() est appelé par le thread audio."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._voices: dict[int, Voice] = {}
        self._next_id = 1
        self.state_dirty = False
        self.underflow_guard = 0

    def add(self, voice: Voice) -> int:
        with self.lock:
            vid = self._next_id
            self._next_id += 1
            self._voices[vid] = voice
            return vid

    def get(self, vid: int | None) -> Voice | None:
        if vid is None:
            return None
        return self._voices.get(vid)

    def remove(self, vid: int | None) -> None:
        if vid is None:
            return
        with self.lock:
            voice = self._voices.pop(vid, None)
        if voice:
            voice.source.close()

    def active_count(self) -> int:
        return sum(1 for v in self._voices.values()
                   if not v.paused and not v.finished)

    def render(self, n: int) -> np.ndarray:
        acc = np.zeros((n, CHANNELS), dtype=np.float32)
        with self.lock:
            for vid, voice in list(self._voices.items()):
                try:
                    out = voice.render(n)
                except Exception:
                    logger.exception("Erreur de rendu audio (voix %d)", vid)
                    voice.finished = True
                    voice.changed = True
                    out = None
                if out is not None:
                    acc += out
                if voice.changed:
                    voice.changed = False
                    self.state_dirty = True
                if voice.finished:
                    del self._voices[vid]
                    voice.source.close()
        np.clip(acc, -1.0, 1.0, out=acc)
        return acc


# ═══════════════════════════════════════════════════════════════════
#  Sortie (device miniaudio)
# ═══════════════════════════════════════════════════════════════════

class DeviceOutput:
    """Device de sortie par défaut du système."""

    def __init__(self, mixer: Mixer, buffer_ms: int = DEFAULT_BUFFER_MS) -> None:
        self._mixer = mixer
        self._buffer_ms = buffer_ms
        self._device = None

    @property
    def running(self) -> bool:
        return self._device is not None

    def start(self) -> bool:
        if miniaudio is None:
            logger.error("miniaudio non installé : audio désactivé")
            return False
        try:
            self._device = miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.FLOAT32,
                nchannels=CHANNELS, sample_rate=SAMPLE_RATE,
                buffersize_msec=self._buffer_ms, app_name="LumensMaster")
            gen = self._generator()
            next(gen)
            self._device.start(gen)
            logger.info("Sortie audio : %s (%d ms)",
                        getattr(self._device, "name", "?"), self._buffer_ms)
            return True
        except Exception:
            logger.exception("Impossible d'ouvrir la sortie audio")
            self._device = None
            return False

    def _generator(self):
        frames = yield b""
        while True:
            frames = yield self._mixer.render(frames)

    def stop(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                logger.exception("Erreur à la fermeture de la sortie audio")
            self._device = None


# ═══════════════════════════════════════════════════════════════════
#  Modèle : bibliothèque, lecteurs, sons
# ═══════════════════════════════════════════════════════════════════

@dataclass
class AudioFile:
    id: int
    path: str                 # absolu en mémoire
    duration: float = 0.0
    cue_in: float = 0.0
    cue_out: float = 0.0      # 0 = fin du fichier
    preload: bool = True
    missing: bool = False

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def region_frames(self) -> tuple[int, int | None]:
        start = int(self.cue_in * SAMPLE_RATE)
        end = int(self.cue_out * SAMPLE_RATE) if self.cue_out > self.cue_in else None
        return start, end


@dataclass
class Player:
    index: int
    file_id: int = 0
    volume: float = 100.0     # %
    loop_mode: str = "off"    # off | file | cue
    fade_in: float = 0.0
    fade_out: float = 0.0
    voice_id: int | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {"file_id": self.file_id, "volume": self.volume,
                "loop_mode": self.loop_mode, "fade_in": self.fade_in,
                "fade_out": self.fade_out}


@dataclass
class Sound:
    number: int
    name: str = ""
    file_id: int = 0
    volume: float = 100.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    loop: bool = False            # boucle entre cue in et cue out
    retrigger: str = "restart"    # restart | overlap

    def to_dict(self) -> dict[str, Any]:
        return {"number": self.number, "name": self.name,
                "file_id": self.file_id, "volume": self.volume,
                "fade_in": self.fade_in, "fade_out": self.fade_out,
                "loop": self.loop, "retrigger": self.retrigger}


def _vol(percent: float) -> float:
    return max(0.0, min(1.0, float(percent) / 100.0))


class AudioManager:
    """Façade audio (thread principal)."""

    def __init__(self, bus: EventBus, player_count: int = 4) -> None:
        self._bus = bus
        self.mixer = Mixer()
        self._output: DeviceOutput | None = None
        self._files: dict[int, AudioFile] = {}
        self._cache: dict[int, np.ndarray] = {}
        self._next_file_id = 1
        self._players: list[Player] = [Player(i) for i in range(1, player_count + 1)]
        self._sounds: dict[int, Sound] = {}
        self._sound_voices: dict[int, list[int]] = {}
        self._last_tick = 0.0
        bg.PARAM_FORMATTERS["file"] = self._format_file
        bg.PARAM_FORMATTERS["sound"] = self._format_sound

    def _format_file(self, file_id: int) -> str:
        f = self._files.get(int(file_id))
        return f.name if f else f"#{file_id} (absent)"

    def _format_sound(self, number: int) -> str:
        s = self._sounds.get(int(number))
        return f"{number} {s.name}" if s else f"{number} (absent)"

    # --- Cycle de vie ------------------------------------------------

    def start(self, buffer_ms: int = DEFAULT_BUFFER_MS) -> bool:
        self._output = DeviceOutput(self.mixer, buffer_ms)
        return self._output.start()

    def stop(self) -> None:
        if self._output:
            self._output.stop()
        self._release_all_voices()

    @property
    def output_running(self) -> bool:
        return bool(self._output and self._output.running)

    def poll_ui(self) -> None:
        """Thread principal, chaque frame."""
        if self.mixer.state_dirty:
            self.mixer.state_dirty = False
            self._prune_sound_voices()
            self._bus.emit("audio.state_changed")
        now = time.perf_counter()
        if now - self._last_tick >= TICK_INTERVAL:
            self._last_tick = now
            if self.mixer.active_count():
                self._bus.emit("audio.tick")

    # --- Bibliothèque ------------------------------------------------

    @property
    def files(self) -> list[AudioFile]:
        return sorted(self._files.values(), key=lambda f: f.name.lower())

    def get_file(self, file_id: int) -> AudioFile | None:
        return self._files.get(file_id)

    def add_file(self, path: str, file_id: int | None = None,
                 cue_in: float = 0.0, cue_out: float = 0.0,
                 preload: bool | None = None) -> AudioFile:
        path = os.path.abspath(path)
        for f in self._files.values():
            if os.path.normcase(f.path) == os.path.normcase(path) and file_id is None:
                return f
        if file_id is None:
            file_id = self._next_file_id
        self._next_file_id = max(self._next_file_id, file_id + 1)
        f = AudioFile(id=file_id, path=path, cue_in=cue_in, cue_out=cue_out)
        try:
            f.duration = probe_duration(path)
        except Exception:
            f.missing = True
            logger.warning("Fichier audio introuvable ou illisible : %s", path)
        f.preload = (f.duration <= PRELOAD_MAX_SECONDS) if preload is None else preload
        self._files[file_id] = f
        if f.preload and not f.missing:
            self._load_cache(f)
        self._emit_changed()
        return f

    def remove_file(self, file_id: int) -> bool:
        if file_id not in self._files:
            return False
        for p in self._players:
            if p.file_id == file_id:
                self.player_unload(p.index)
        for s in self._sounds.values():
            if s.file_id == file_id:
                self.stop_sound(s.number, 0)
        del self._files[file_id]
        self._cache.pop(file_id, None)
        self._emit_changed()
        return True

    def set_cues(self, file_id: int, cue_in: float | None = None,
                 cue_out: float | None = None) -> None:
        f = self._files.get(file_id)
        if f is None:
            return
        if cue_in is not None:
            f.cue_in = max(0.0, min(float(cue_in), f.duration or float(cue_in)))
        if cue_out is not None:
            f.cue_out = max(0.0, min(float(cue_out), f.duration or float(cue_out)))
        # Appliquer aux lecteurs chargés avec ce fichier
        for p in self._players:
            if p.file_id == file_id:
                self._apply_region(p)
        self._emit_changed()

    def set_preload(self, file_id: int, preload: bool) -> None:
        f = self._files.get(file_id)
        if f is None:
            return
        f.preload = bool(preload)
        if f.preload and not f.missing:
            self._load_cache(f)
        else:
            self._cache.pop(file_id, None)
        self._emit_changed()

    def _load_cache(self, f: AudioFile) -> None:
        if f.id in self._cache:
            return
        try:
            t0 = time.perf_counter()
            self._cache[f.id] = decode_to_memory(f.path)
            logger.info("Préchargé : %s (%.1f s, %.0f ms)", f.name, f.duration,
                        (time.perf_counter() - t0) * 1000)
        except Exception:
            logger.exception("Préchargement impossible : %s", f.path)

    def _make_source(self, f: AudioFile) -> Source:
        samples = self._cache.get(f.id)
        if samples is not None:
            return MemorySource(samples)
        return DecoderSource(f.path, f.duration)

    def cache_megabytes(self) -> float:
        return sum(a.nbytes for a in self._cache.values()) / 1e6

    # --- Lecteurs ----------------------------------------------------

    @property
    def players(self) -> list[Player]:
        return list(self._players)

    def get_player(self, index: int) -> Player | None:
        if 1 <= index <= len(self._players):
            return self._players[index - 1]
        return None

    def set_player_count(self, count: int) -> None:
        count = max(1, min(16, int(count)))
        while len(self._players) > count:
            p = self._players.pop()
            self.mixer.remove(p.voice_id)
        while len(self._players) < count:
            self._players.append(Player(len(self._players) + 1))
        self._emit_changed()

    def _player_voice(self, p: Player) -> Voice | None:
        v = self.mixer.get(p.voice_id)
        if v is None:
            p.voice_id = None
        return v

    def _loop_params(self, p: Player, f: AudioFile) -> dict[str, Any]:
        start, end = f.region_frames()
        if p.loop_mode == "file":
            return dict(start=start, end=None, loop=True, loop_start=0)
        if p.loop_mode == "cue":
            return dict(start=start, end=end, loop=True, loop_start=start)
        return dict(start=start, end=end, loop=False, loop_start=start)

    def _apply_region(self, p: Player) -> None:
        f = self._files.get(p.file_id)
        with self.mixer.lock:
            v = self._player_voice(p)
            if v is None or f is None:
                return
            params = self._loop_params(p, f)
            v.start, v.end = params["start"], params["end"]
            v.loop, v.loop_start = params["loop"], params["loop_start"]

    def player_load(self, index: int, file_id: int) -> bool:
        """Charge un fichier (décodeur ouvert, prêt au cue in, en pause)."""
        p = self.get_player(index)
        f = self._files.get(file_id)
        if p is None:
            return False
        self.player_unload(index, emit=False)
        p.file_id = file_id
        if f is None or f.missing:
            self._emit_changed()
            return False
        try:
            source = self._make_source(f)
        except Exception:
            logger.exception("Lecteur %d : chargement impossible", index)
            self._emit_changed()
            return False
        voice = Voice(source, on_end="rewind", **self._loop_params(p, f))
        p.voice_id = self.mixer.add(voice)
        self._emit_changed()
        return True

    def player_unload(self, index: int, emit: bool = True) -> None:
        p = self.get_player(index)
        if p is None:
            return
        self.mixer.remove(p.voice_id)
        p.voice_id = None
        if emit:
            p.file_id = 0
            self._emit_changed()

    def player_play(self, index: int) -> None:
        p = self.get_player(index)
        if p is None:
            return
        v = self._player_voice(p)
        if v is None:
            if not self.player_load(index, p.file_id):
                return
            v = self._player_voice(p)
        with self.mixer.lock:
            if v.paused:
                v.gain = 0.0
                v.paused = False
                v.fade_to(_vol(p.volume), p.fade_in)
            elif v.fading_out:
                v.fade_to(_vol(p.volume), MIN_FADE)  # annule un stop en cours
        self._bus.emit("audio.state_changed")

    def player_pause(self, index: int, fade: float | None = None) -> None:
        self._player_fade_action(index, fade, "pause")

    def player_stop(self, index: int, fade: float | None = None) -> None:
        """Arrête et revient au cue in (fondu = fade_out du lecteur par défaut)."""
        p = self.get_player(index)
        v = self._player_voice(p) if p else None
        if v is not None and v.paused:
            with self.mixer.lock:
                v.rewind()
            self._bus.emit("audio.state_changed")
            return
        self._player_fade_action(index, fade, "rewind")

    def _player_fade_action(self, index: int, fade: float | None, after: str) -> None:
        p = self.get_player(index)
        if p is None:
            return
        v = self._player_voice(p)
        if v is None or v.paused:
            return
        seconds = p.fade_out if fade is None or fade < 0 else fade
        with self.mixer.lock:
            v.fade_to(0.0, seconds, after=after)

    def player_toggle(self, index: int) -> None:
        if self.player_is_playing(index):
            self.player_pause(index, fade=MIN_FADE)
        else:
            self.player_play(index)

    def player_seek(self, index: int, seconds: float) -> None:
        p = self.get_player(index)
        v = self._player_voice(p) if p else None
        if v is None:
            return
        with self.mixer.lock:
            v.seek(int(max(0.0, seconds) * SAMPLE_RATE))
        self._bus.emit("audio.tick")

    def player_set_volume(self, index: int, volume: float, fade: float = 0.0) -> None:
        p = self.get_player(index)
        if p is None:
            return
        p.volume = max(0.0, min(100.0, float(volume)))
        v = self._player_voice(p)
        if v is not None and not v.paused and not v.fading_out:
            with self.mixer.lock:
                v.fade_to(_vol(p.volume), fade)
        self._emit_changed(structural=False)

    def player_set_loop(self, index: int, mode: str) -> None:
        p = self.get_player(index)
        if p is None or mode not in dict(LOOP_MODES):
            return
        p.loop_mode = mode
        self._apply_region(p)
        self._emit_changed(structural=False)

    def player_update(self, index: int, **kwargs: Any) -> None:
        p = self.get_player(index)
        if p is None:
            return
        for key in ("fade_in", "fade_out"):
            if key in kwargs:
                setattr(p, key, max(0.0, float(kwargs[key])))
        self._emit_changed(structural=False)

    def player_is_playing(self, index: int) -> bool:
        p = self.get_player(index)
        v = self._player_voice(p) if p else None
        return bool(v and not v.paused and not v.finished)

    def player_status(self, index: int) -> dict[str, Any]:
        p = self.get_player(index)
        f = self._files.get(p.file_id) if p else None
        v = self._player_voice(p) if p else None
        return {
            "loaded": v is not None,
            "playing": bool(v and not v.paused),
            "position": (v.pos / SAMPLE_RATE) if v else 0.0,
            "duration": f.duration if f else 0.0,
            "name": f.name if f else "",
            "missing": bool(f and f.missing),
        }

    def player_snapshot(self, index: int) -> dict[str, Any] | None:
        p = self.get_player(index)
        if p is None:
            return None
        st = self.player_status(index)
        return {"file_id": p.file_id, "position": st["position"],
                "volume": p.volume, "playing": st["playing"],
                "loop_mode": p.loop_mode, "loaded": st["loaded"]}

    def player_restore(self, index: int, snap: dict[str, Any] | None) -> None:
        p = self.get_player(index)
        if p is None or not snap:
            return
        if snap["file_id"] != p.file_id or (snap["loaded"] and p.voice_id is None):
            if snap["file_id"] and snap["loaded"]:
                self.player_load(index, snap["file_id"])
            else:
                self.player_unload(index)
                p.file_id = snap["file_id"]
        p.loop_mode = snap["loop_mode"]
        self._apply_region(p)
        p.volume = snap["volume"]
        v = self._player_voice(p)
        if v is not None:
            with self.mixer.lock:
                v.seek(int(snap["position"] * SAMPLE_RATE))
                if snap["playing"]:
                    if v.paused:
                        v.gain, v.paused = 0.0, False
                    v.fade_to(_vol(p.volume), MIN_FADE)
                elif not v.paused:
                    v.fade_to(0.0, MIN_FADE, after="pause")
        self._emit_changed()

    # --- Sons directs ------------------------------------------------

    @property
    def sounds(self) -> list[Sound]:
        return [self._sounds[n] for n in sorted(self._sounds)]

    def get_sound(self, number: int) -> Sound | None:
        return self._sounds.get(number)

    def create_sound(self, number: int | None = None, name: str = "",
                     file_id: int = 0) -> Sound | None:
        if number is None:
            number = next((n for n in range(1, MAX_SOUNDS + 1)
                           if n not in self._sounds), 0)
        if not 1 <= number <= MAX_SOUNDS or number in self._sounds:
            return None
        f = self._files.get(file_id)
        s = Sound(number=number, file_id=file_id,
                  name=name or (os.path.splitext(f.name)[0] if f else f"Son {number}"))
        self._sounds[number] = s
        self._emit_changed()
        return s

    def delete_sound(self, number: int) -> bool:
        if number not in self._sounds:
            return False
        self.stop_sound(number, 0)
        del self._sounds[number]
        self._emit_changed()
        return True

    def update_sound(self, number: int, **kwargs: Any) -> None:
        s = self._sounds.get(number)
        if s is None:
            return
        for key in ("name", "file_id", "volume", "fade_in", "fade_out",
                    "loop", "retrigger"):
            if key in kwargs:
                setattr(s, key, kwargs[key])
        s.volume = max(0.0, min(100.0, float(s.volume)))
        s.fade_in = max(0.0, float(s.fade_in))
        s.fade_out = max(0.0, float(s.fade_out))
        self._emit_changed(structural="file_id" in kwargs)

    def play_sound(self, number: int) -> bool:
        s = self._sounds.get(number)
        f = self._files.get(s.file_id) if s else None
        if s is None or f is None or f.missing:
            logger.warning("Son %s : pas de fichier jouable", number)
            return False
        if s.retrigger == "restart":
            self.stop_sound(number, MIN_FADE)
        try:
            source = self._make_source(f)
        except Exception:
            logger.exception("Son %d : ouverture impossible", number)
            return False
        start, end = f.region_frames()
        voice = Voice(source, start=start, end=end, loop=s.loop,
                      loop_start=start, on_end="finish")
        voice.paused = False
        voice.fade_to(_vol(s.volume), s.fade_in)
        vid = self.mixer.add(voice)
        self._sound_voices.setdefault(number, []).append(vid)
        self._bus.emit("audio.state_changed")
        return True

    def stop_sound(self, number: int, fade: float | None = None) -> None:
        s = self._sounds.get(number)
        seconds = (s.fade_out if s else 0.0) if fade is None or fade < 0 else fade
        with self.mixer.lock:
            for vid in self._sound_voices.get(number, []):
                v = self.mixer.get(vid)
                if v is not None and not v.finished:
                    v.fade_to(0.0, seconds, after="finish")

    def stop_all(self, fade: float | None = None) -> None:
        """Arrête tous les sons et met tous les lecteurs en pause (rembobinés)."""
        for number in list(self._sound_voices):
            self.stop_sound(number, fade)
        for p in self._players:
            self.player_stop(p.index, fade)

    def sound_is_playing(self, number: int) -> bool:
        return any(self.mixer.get(v) is not None
                   for v in self._sound_voices.get(number, []))

    def _prune_sound_voices(self) -> None:
        for number in list(self._sound_voices):
            alive = [v for v in self._sound_voices[number]
                     if self.mixer.get(v) is not None]
            if alive:
                self._sound_voices[number] = alive
            else:
                del self._sound_voices[number]

    # --- Divers ------------------------------------------------------

    def _emit_changed(self, structural: bool = True) -> None:
        self._bus.emit("audio.changed", structural=structural)

    def _release_all_voices(self) -> None:
        for p in self._players:
            self.mixer.remove(p.voice_id)
            p.voice_id = None
        for vids in self._sound_voices.values():
            for vid in vids:
                self.mixer.remove(vid)
        self._sound_voices.clear()

    # --- Sérialisation ----------------------------------------------

    def clear(self) -> None:
        self._release_all_voices()
        self._files.clear()
        self._cache.clear()
        self._sounds.clear()
        self._next_file_id = 1
        self._players = [Player(i) for i in range(1, 5)]
        self._emit_changed()

    def to_dict(self, base_dir: str | Path | None = None) -> dict[str, Any]:
        def portable(path: str) -> str:
            if base_dir:
                try:
                    return Path(os.path.relpath(path, base_dir)).as_posix()
                except ValueError:  # autre lecteur (Windows)
                    pass
            return path

        return {
            "files": [{"id": f.id, "path": portable(f.path), "cue_in": f.cue_in,
                       "cue_out": f.cue_out, "preload": f.preload}
                      for f in self.files],
            "players": [p.to_dict() for p in self._players],
            "sounds": [s.to_dict() for s in self.sounds],
        }

    def from_dict(self, data: dict[str, Any], base_dir: str | Path | None = None) -> None:
        self._release_all_voices()
        self._files.clear()
        self._cache.clear()
        self._sounds.clear()
        self._next_file_id = 1
        for fd in data.get("files", []) or []:
            try:
                path = fd["path"]
                if not os.path.isabs(path) and base_dir:
                    path = os.path.join(base_dir, path)
                self.add_file(path, file_id=int(fd["id"]),
                              cue_in=float(fd.get("cue_in", 0.0)),
                              cue_out=float(fd.get("cue_out", 0.0)),
                              preload=bool(fd.get("preload", True)))
            except (KeyError, TypeError, ValueError):
                logger.warning("Entrée audio invalide ignorée : %r", fd)
        players = data.get("players") or [{} for _ in range(4)]
        self._players = []
        for i, pd in enumerate(players, start=1):
            p = Player(i, file_id=int(pd.get("file_id", 0) or 0),
                       volume=float(pd.get("volume", 100.0)),
                       loop_mode=pd.get("loop_mode", "off"),
                       fade_in=float(pd.get("fade_in", 0.0)),
                       fade_out=float(pd.get("fade_out", 0.0)))
            if p.loop_mode not in dict(LOOP_MODES):
                p.loop_mode = "off"
            self._players.append(p)
            if p.file_id:
                self.player_load(i, p.file_id)
        for sd in data.get("sounds", []) or []:
            try:
                s = Sound(number=int(sd["number"]), name=str(sd.get("name", "")),
                          file_id=int(sd.get("file_id", 0) or 0),
                          volume=float(sd.get("volume", 100.0)),
                          fade_in=float(sd.get("fade_in", 0.0)),
                          fade_out=float(sd.get("fade_out", 0.0)),
                          loop=bool(sd.get("loop", False)),
                          retrigger=sd.get("retrigger", "restart"))
            except (KeyError, TypeError, ValueError):
                logger.warning("Son invalide ignoré : %r", sd)
                continue
            if s.retrigger not in dict(RETRIGGER_MODES):
                s.retrigger = "restart"
            self._sounds[s.number] = s
        self._emit_changed()


# ═══════════════════════════════════════════════════════════════════
#  Actions de banger (famille Audio)
# ═══════════════════════════════════════════════════════════════════

def _audio(m: Any) -> AudioManager | None:
    a = getattr(m, "audio", None)
    if a is None:
        logger.warning("Action audio ignorée : module audio absent")
    return a


def _snap_then(fn):
    """Exécute fn(audio, params) après avoir pris un snapshot du lecteur."""
    def execute(m: Any, p: dict[str, Any]) -> Any:
        a = _audio(m)
        if a is None:
            return None
        snap = a.player_snapshot(p["player"])
        fn(a, p)
        return snap
    return execute


def _restore_player(m: Any, p: dict[str, Any], snap: Any) -> None:
    a = _audio(m)
    if a is not None:
        a.player_restore(p["player"], snap)


def _fade(p: dict[str, Any]) -> float | None:
    return None if p["fade"] < 0 else p["fade"]


_PLAYER = bg.ActionParam("player", "Lecteur", "player", 1, 1, 16)
_FILE = bg.ActionParam("file", "Fichier", "file", 0, 0, None)
_FADE = bg.ActionParam("fade", "Fondu (s, -1 = defaut)", "float", -1.0, -1.0, 600.0)

bg.FAMILIES["audio"] = "Audio"

bg.register_action(bg.ActionSpec(
    "audio", "sound_play", "Jouer son",
    [bg.ActionParam("sound", "Son", "sound", 1, 1, MAX_SOUNDS)],
    lambda m, p: (_audio(m) and _audio(m).play_sound(p["sound"])) and None,
    lambda m, p, s: _audio(m) and _audio(m).stop_sound(p["sound"], MIN_FADE)))
bg.register_action(bg.ActionSpec(
    "audio", "sound_stop", "Stopper son",
    [bg.ActionParam("sound", "Son", "sound", 1, 1, MAX_SOUNDS), _FADE],
    lambda m, p: _audio(m) and _audio(m).stop_sound(p["sound"], _fade(p))))
bg.register_action(bg.ActionSpec(
    "audio", "stop_all", "Tout stopper", [_FADE],
    lambda m, p: _audio(m) and _audio(m).stop_all(_fade(p))))
bg.register_action(bg.ActionSpec(
    "audio", "player_load", "Lecteur : charger", [_PLAYER, _FILE],
    _snap_then(lambda a, p: a.player_load(p["player"], p["file"])),
    _restore_player, wc_codes=[(5, 1)]))
bg.register_action(bg.ActionSpec(
    "audio", "player_load_play", "Lecteur : charger + play", [_PLAYER, _FILE],
    _snap_then(lambda a, p: a.player_load(p["player"], p["file"])
               and a.player_play(p["player"])),
    _restore_player, wc_codes=[(5, 3)]))
bg.register_action(bg.ActionSpec(
    "audio", "player_play", "Lecteur : play", [_PLAYER],
    _snap_then(lambda a, p: a.player_play(p["player"])),
    _restore_player, wc_codes=[(5, 2)]))
bg.register_action(bg.ActionSpec(
    "audio", "player_pause", "Lecteur : pause", [_PLAYER, _FADE],
    _snap_then(lambda a, p: a.player_pause(p["player"], _fade(p))),
    _restore_player, wc_codes=[(5, 2)]))
bg.register_action(bg.ActionSpec(
    "audio", "player_stop", "Lecteur : stop", [_PLAYER, _FADE],
    _snap_then(lambda a, p: a.player_stop(p["player"], _fade(p))),
    _restore_player))
bg.register_action(bg.ActionSpec(
    "audio", "player_seek", "Lecteur : seek",
    [_PLAYER, bg.ActionParam("position", "Position (s)", "float", 0.0, 0.0, None)],
    _snap_then(lambda a, p: a.player_seek(p["player"], p["position"])),
    _restore_player, wc_codes=[(5, 5)]))
bg.register_action(bg.ActionSpec(
    "audio", "player_volume", "Lecteur : volume",
    [_PLAYER, bg.ActionParam("value", "Volume %", "float", 100.0, 0.0, 100.0),
     bg.ActionParam("time", "en (s)", "float", 0.0, 0.0, 600.0)],
    _snap_then(lambda a, p: a.player_set_volume(p["player"], p["value"], p["time"])),
    _restore_player, wc_codes=[(5, 6)]))
bg.register_action(bg.ActionSpec(
    "audio", "player_loop", "Lecteur : loop",
    [_PLAYER, bg.ActionParam("mode", "Mode", "choice", "off", choices=LOOP_MODES)],
    _snap_then(lambda a, p: a.player_set_loop(p["player"], p["mode"])),
    _restore_player, wc_codes=[(5, 4)]))
