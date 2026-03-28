"""
Module Séquenceur : gestion des mémoires (cues) et crossfades.

Architecture thread :
    Le thread crossfade met à jour l'état interne et appelle directement
    le callback DMX (engine.update_dmx). Il n'émet JAMAIS d'événements
    sur le bus car les callbacks Dear PyGui ne sont pas thread-safe.

    Les mises à jour UI passent par poll_ui(), appelé depuis le thread
    principal via un render callback Dear PyGui. poll_ui() vérifie des
    flags "dirty" et émet les événements nécessaires depuis le thread UI.

Mode manuel :
    Les temps de fade et delay sont ignorés — la progression est appliquée
    linéairement à tous les circuits.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from lumensmaster.core.events import EventBus

logger = logging.getLogger(__name__)


class CrossfadeMode(Enum):
    IDLE = "idle"
    TIMED = "timed"
    MANUAL = "manual"
    PAUSED = "paused"


@dataclass
class Cue:
    """Une mémoire d'éclairage."""
    number: float
    name: str
    contents: dict[int, int] = field(default_factory=dict)
    fade_in: float = 3.0
    fade_out: float = 3.0
    delay_in: float = 0.0
    delay_out: float = 0.0
    link_time: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "name": self.name,
            "contents": {str(k): v for k, v in self.contents.items()},
            "fade_in": self.fade_in,
            "fade_out": self.fade_out,
            "delay_in": self.delay_in,
            "delay_out": self.delay_out,
            "link_time": self.link_time,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cue":
        contents = {}
        for k, v in data.get("contents", {}).items():
            try:
                contents[int(k)] = max(0, min(255, int(v)))
            except (ValueError, TypeError):
                pass
        return cls(
            number=float(data.get("number", 0)),
            name=data.get("name", ""),
            contents=contents,
            fade_in=float(data.get("fade_in", 3.0)),
            fade_out=float(data.get("fade_out", 3.0)),
            delay_in=float(data.get("delay_in", 0.0)),
            delay_out=float(data.get("delay_out", 0.0)),
            link_time=float(data.get("link_time", 0.0)),
        )


class Sequencer:
    """Séquenceur de cues avec crossfade thread-safe."""

    CROSSFADE_FPS = 25

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._cues: list[Cue] = []
        self._current_index: int = -1

        # Verrou réentrant pour l'état interne
        self._lock = threading.RLock()

        # Callback DMX : appelé directement par le thread crossfade
        self._dmx_callback: Callable[[], None] | None = None

        # État du crossfade
        self._mode = CrossfadeMode.IDLE
        self._crossfade_thread: threading.Thread | None = None
        self._crossfade_running = False

        # Niveaux onstage
        self._onstage_levels: dict[int, int] = {}

        # Cible du crossfade
        self._target_index: int = -1
        self._target_levels: dict[int, int] = {}

        # Timing
        self._crossfade_start_time: float = 0.0
        self._crossfade_pause_elapsed: float = 0.0
        self._crossfade_target_cue: Cue | None = None

        # Mode manuel
        self._manual_progress: float = 0.0

        # Progression globale
        self._global_progress: float = 0.0

        # Flags "dirty" pour le polling UI (thread-safe via _lock)
        self._ui_state_dirty: bool = False
        self._ui_progress_dirty: bool = False
        self._ui_last_progress: float = 0.0
        self._ui_link_pending: bool = False

    def ensure_default_cue(self) -> None:
        """Crée la cue 'Noir début' si la séquence est vide."""
        if not self._cues:
            self.add_cue(Cue(
                number=0.0,
                name="Noir début",
                contents={},
                fade_in=0.0,
                fade_out=0.0,
                delay_in=0.0,
                delay_out=0.0,
                link_time=0.0,
            ))
            self._current_index = 0
            self._onstage_levels = {}

    # --- Configuration ---

    def set_dmx_callback(self, callback: Callable[[], None]) -> None:
        """Enregistre le callback appelé pour rafraîchir le DMX."""
        self._dmx_callback = callback

    def _notify_dmx(self) -> None:
        """Appelle le callback DMX (thread-safe, pas d'événement bus)."""
        if self._dmx_callback:
            self._dmx_callback()

    # --- Polling UI (appelé depuis le thread principal) ---

    def poll_ui(self) -> None:
        """
        Vérifie les flags dirty et émet les événements UI.
        DOIT être appelé depuis le thread principal (boucle de rendu).
        """
        # Vérifier le link en premier (déclenche un go() depuis le thread UI)
        with self._lock:
            link_pending = self._ui_link_pending
            self._ui_link_pending = False
        if link_pending:
            self.go()  # Safe : on est sur le thread UI
            return  # go() émet déjà ses propres événements

        with self._lock:
            state_dirty = self._ui_state_dirty
            progress_dirty = self._ui_progress_dirty
            progress = self._ui_last_progress
            self._ui_state_dirty = False
            self._ui_progress_dirty = False

        if state_dirty:
            self._bus.emit("sequencer.state_changed")
            self._bus.emit("sequencer.output_changed")

        if progress_dirty:
            self._bus.emit("sequencer.progress_changed", progress=progress)
            if not state_dirty:
                self._bus.emit("sequencer.output_changed")

    # --- Propriétés ---

    @property
    def cues(self) -> list[Cue]:
        return self._cues

    @property
    def cue_count(self) -> int:
        return len(self._cues)

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def current_cue(self) -> Cue | None:
        if 0 <= self._current_index < len(self._cues):
            return self._cues[self._current_index]
        return None

    @property
    def next_cue(self) -> Cue | None:
        next_idx = self._current_index + 1
        if 0 <= next_idx < len(self._cues):
            return self._cues[next_idx]
        return None

    @property
    def mode(self) -> CrossfadeMode:
        return self._mode

    @property
    def global_progress(self) -> float:
        return self._global_progress

    @property
    def is_crossfading(self) -> bool:
        return self._mode in (CrossfadeMode.TIMED, CrossfadeMode.MANUAL,
                              CrossfadeMode.PAUSED)

    # --- Gestion des cues ---

    def add_cue(self, cue: Cue) -> None:
        for existing in self._cues:
            if abs(existing.number - cue.number) < 0.001:
                logger.warning("Cue %.1f existe déjà", cue.number)
                return
        self._cues.append(cue)
        self._cues.sort(key=lambda c: c.number)
        self._bus.emit("sequencer.cues_changed")
        logger.info("Cue ajoutée : %.1f '%s' (%d circuits)",
                     cue.number, cue.name, len(cue.contents))

    def update_cue(self, cue_number: float, **kwargs) -> bool:
        cue = self.get_cue(cue_number)
        if cue is None:
            return False
        for key, value in kwargs.items():
            if hasattr(cue, key):
                setattr(cue, key, value)
        if "number" in kwargs:
            self._cues.sort(key=lambda c: c.number)
        self._bus.emit("sequencer.cues_changed")
        return True

    def delete_cue(self, cue_number: float) -> bool:
        for i, cue in enumerate(self._cues):
            if abs(cue.number - cue_number) < 0.001:
                self._cues.pop(i)
                if self._current_index >= len(self._cues):
                    self._current_index = len(self._cues) - 1
                elif self._current_index > i:
                    self._current_index -= 1
                self._bus.emit("sequencer.cues_changed")
                logger.info("Cue supprimée : %.1f", cue_number)
                return True
        return False

    def get_cue(self, cue_number: float) -> Cue | None:
        for cue in self._cues:
            if abs(cue.number - cue_number) < 0.001:
                return cue
        return None

    def get_next_free_number(self) -> float:
        if not self._cues:
            return 1.0
        return float(int(self._cues[-1].number) + 1)

    def get_insert_number(self, before: float, after: float) -> float:
        return round((before + after) / 2, 1)

    # --- Navigation / GO ---
    # Ces méthodes sont appelées depuis le thread UI (boutons, clavier)
    # Elles peuvent émettre des événements en toute sécurité

    def go(self) -> None:
        if not self._cues:
            return
        with self._lock:
            if self.is_crossfading:
                self._complete_crossfade_locked()
            next_index = self._current_index + 1
            if next_index >= len(self._cues):
                next_index = 0  # Boucle vers le début
            self._start_crossfade_locked(next_index)

        # Émettre depuis le thread UI (safe)
        self._bus.emit("sequencer.state_changed")
        self._bus.emit("sequencer.output_changed")
        self._notify_dmx()

    def go_back(self) -> None:
        if not self._cues:
            return
        with self._lock:
            if self.is_crossfading:
                self._complete_crossfade_locked()
            prev_index = self._current_index - 1
            if prev_index < 0:
                logger.info("Début de séquence")
                return
            self._start_crossfade_locked(prev_index)

        self._bus.emit("sequencer.state_changed")
        self._bus.emit("sequencer.output_changed")
        self._notify_dmx()

    def go_to_cue(self, cue_number: float) -> None:
        for i, cue in enumerate(self._cues):
            if abs(cue.number - cue_number) < 0.001:
                with self._lock:
                    if self.is_crossfading:
                        self._complete_crossfade_locked()
                    self._start_crossfade_locked(i)
                self._bus.emit("sequencer.state_changed")
                self._bus.emit("sequencer.output_changed")
                self._notify_dmx()
                return
            
    def goto_cue_instant(self, cue_number: float) -> None:
        """Saute directement à une cue sans crossfade (instantané)."""
        for i, cue in enumerate(self._cues):
            if abs(cue.number - cue_number) < 0.001:
                with self._lock:
                    if self.is_crossfading:
                        self._complete_crossfade_locked()
                    # Application instantanée
                    self._current_index = i
                    self._onstage_levels = dict(cue.contents)
                    self._mode = CrossfadeMode.IDLE
                    self._global_progress = 0.0
 
                self._bus.emit("sequencer.state_changed")
                self._bus.emit("sequencer.output_changed")
                self._notify_dmx()
                logger.info("GOTO instantané : cue %.1f '%s'",
                            cue.number, cue.name)
                return

    def pause(self) -> None:
        with self._lock:
            if self._mode == CrossfadeMode.TIMED:
                self._crossfade_pause_elapsed = (
                    time.perf_counter() - self._crossfade_start_time)
                self._mode = CrossfadeMode.PAUSED
                self._crossfade_running = False
                logger.info("Crossfade en pause")
            elif self._mode == CrossfadeMode.PAUSED:
                self._crossfade_start_time = (
                    time.perf_counter() - self._crossfade_pause_elapsed)
                self._mode = CrossfadeMode.TIMED
                self._start_crossfade_thread()
                logger.info("Crossfade repris")
            else:
                return

        self._bus.emit("sequencer.state_changed")

    # --- Crossfade manuel ---

    def set_manual_mode(self, enabled: bool) -> None:
        with self._lock:
            if enabled and self._mode == CrossfadeMode.IDLE:
                next_index = self._current_index + 1
                if next_index >= len(self._cues):
                    return
                self._target_index = next_index
                target_cue = self._cues[next_index]
                self._target_levels = dict(target_cue.contents)
                self._crossfade_target_cue = target_cue
                self._manual_progress = 0.0
                self._global_progress = 0.0
                self._mode = CrossfadeMode.MANUAL
                logger.info("Mode crossfade manuel activé")
            elif not enabled and self._mode == CrossfadeMode.MANUAL:
                self._mode = CrossfadeMode.IDLE
                self._global_progress = 0.0
            else:
                return

        self._bus.emit("sequencer.state_changed")
        self._bus.emit("sequencer.output_changed")
        self._notify_dmx()

    def set_manual_progress(self, progress: float) -> None:
        """En mode manuel, les temps de fade/delay sont ignorés."""
        with self._lock:
            if self._mode != CrossfadeMode.MANUAL:
                return
            self._manual_progress = max(0.0, min(1.0, progress))
            self._global_progress = self._manual_progress

        # Appel DMX direct (pas d'événement bus)
        self._notify_dmx()

        # Marquer dirty pour que poll_ui rafraîchisse l'UI
        with self._lock:
            self._ui_progress_dirty = True
            self._ui_last_progress = self._global_progress

    def complete_manual(self) -> None:
        """Complète le crossfade manuel."""
        with self._lock:
            if self._mode == CrossfadeMode.MANUAL:
                self._complete_crossfade_locked()

        # Émettre depuis le thread UI (safe — appelé par la vue)
        self._bus.emit("sequencer.state_changed")
        self._bus.emit("sequencer.output_changed")
        self._bus.emit("sequencer.progress_changed", progress=0.0)
        self._notify_dmx()

    # --- Crossfade temporisé (interne) ---

    def _start_crossfade_locked(self, target_index: int) -> None:
        """Démarre un crossfade. Appelé avec le lock."""
        self._target_index = target_index
        target_cue = self._cues[target_index]
        self._target_levels = dict(target_cue.contents)
        self._crossfade_target_cue = target_cue
        self._crossfade_start_time = time.perf_counter()
        self._crossfade_pause_elapsed = 0.0
        self._global_progress = 0.0
        self._mode = CrossfadeMode.TIMED

        logger.info(
            "Crossfade vers cue %.1f '%s' (in=%.1f+%.1f, out=%.1f+%.1f)",
            target_cue.number, target_cue.name,
            target_cue.delay_in, target_cue.fade_in,
            target_cue.delay_out, target_cue.fade_out,
        )
        self._start_crossfade_thread()

    def _start_crossfade_thread(self) -> None:
        self._crossfade_running = True
        self._crossfade_thread = threading.Thread(
            target=self._crossfade_loop,
            name="Crossfade",
            daemon=True,
        )
        self._crossfade_thread.start()

    def _crossfade_loop(self) -> None:
        """
        Boucle du thread crossfade.
        N'émet JAMAIS d'événements bus (Dear PyGui n'est pas thread-safe).
        Met à jour l'état interne et appelle le callback DMX directement.
        Les flags dirty sont vérifiés par poll_ui() depuis le thread principal.
        """
        interval = 1.0 / self.CROSSFADE_FPS

        while self._crossfade_running and self._mode == CrossfadeMode.TIMED:
            start = time.perf_counter()

            completed = False
            link_time = 0.0

            with self._lock:
                if self._mode != CrossfadeMode.TIMED:
                    break

                elapsed = time.perf_counter() - self._crossfade_start_time
                target_cue = self._crossfade_target_cue
                if target_cue is None:
                    break

                max_time = max(
                    target_cue.delay_in + target_cue.fade_in,
                    target_cue.delay_out + target_cue.fade_out,
                    0.01,
                )
                self._global_progress = min(1.0, elapsed / max_time)

                # Vérifier si terminé
                all_done = True
                all_circuits = (set(self._onstage_levels.keys())
                                | set(self._target_levels.keys()))
                for circuit in all_circuits:
                    progress = self._compute_channel_progress(
                        circuit, elapsed, target_cue)
                    if progress < 1.0:
                        all_done = False
                        break

                if all_done:
                    self._complete_crossfade_locked()
                    completed = True
                    # Vérifier le link
                    current = self.current_cue
                    if current and current.link_time > 0:
                        link_time = current.link_time

                # Marquer dirty pour l'UI
                self._ui_progress_dirty = True
                self._ui_last_progress = self._global_progress
                if completed:
                    self._ui_state_dirty = True

            # Appel DMX direct (hors lock, thread-safe via _dmx_lock)
            self._notify_dmx()

            if completed:
                # Programmer le link si nécessaire
                if link_time > 0:
                    logger.info("Link actif : GO auto dans %.1fs", link_time)
                    threading.Timer(link_time, self._link_go).start()
                break

            sleep_time = interval - (time.perf_counter() - start)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _compute_channel_progress(self, circuit: int, elapsed: float,
                                  target_cue: Cue) -> float:
        current_level = self._onstage_levels.get(circuit, 0)
        target_level = self._target_levels.get(circuit, 0)

        if current_level == target_level:
            return 1.0

        if target_level > current_level:
            delay = target_cue.delay_in
            fade = target_cue.fade_in
        else:
            delay = target_cue.delay_out
            fade = target_cue.fade_out

        if elapsed < delay:
            return 0.0
        elif fade <= 0:
            return 1.0
        else:
            return min(1.0, (elapsed - delay) / fade)

    def _complete_crossfade_locked(self) -> None:
        """
        Complète le crossfade. Appelé avec le lock.
        N'émet AUCUN événement (le thread appelant s'en charge ou poll_ui).
        """
        self._crossfade_running = False

        # 1. Copier target dans onstage
        if self._target_index >= 0:
            self._current_index = self._target_index
            self._onstage_levels = dict(self._target_levels)

        # 2. Passer en IDLE AVANT de clear target
        self._mode = CrossfadeMode.IDLE
        self._global_progress = 0.0
        self._manual_progress = 0.0

        # 3. Nettoyer
        self._target_index = -1
        self._target_levels.clear()
        self._crossfade_target_cue = None

        current = self.current_cue
        if current:
            logger.info("Cue %.1f '%s' onstage", current.number, current.name)

    def _link_go(self) -> None:
        """Appelé par le timer de link. Marque un flag pour poll_ui."""
        # Ne PAS appeler go() directement — on est sur un thread Timer
        with self._lock:
            if self._mode == CrossfadeMode.IDLE:
                self._ui_link_pending = True

    # --- Sortie ---

    def get_output(self) -> dict[int, int]:
        with self._lock:
            if self._mode == CrossfadeMode.IDLE:
                return dict(self._onstage_levels)

            output: dict[int, int] = {}
            all_circuits = (set(self._onstage_levels.keys())
                            | set(self._target_levels.keys()))

            for circuit in all_circuits:
                current_level = self._onstage_levels.get(circuit, 0)
                target_level = self._target_levels.get(circuit, 0)

                if self._mode == CrossfadeMode.MANUAL:
                    # Mode manuel : interpolation linéaire, ignore fade/delay
                    progress = self._manual_progress
                elif self._mode in (CrossfadeMode.TIMED, CrossfadeMode.PAUSED):
                    target_cue = self._crossfade_target_cue
                    if target_cue is None:
                        progress = 0.0
                    elif self._mode == CrossfadeMode.TIMED:
                        elapsed = time.perf_counter() - self._crossfade_start_time
                        progress = self._compute_channel_progress(
                            circuit, elapsed, target_cue)
                    else:
                        elapsed = self._crossfade_pause_elapsed
                        progress = self._compute_channel_progress(
                            circuit, elapsed, target_cue)
                else:
                    progress = 0.0

                interpolated = int(
                    current_level + progress * (target_level - current_level))
                interpolated = max(0, min(255, interpolated))

                if interpolated > 0:
                    output[circuit] = interpolated

            return output

    # --- Enregistrement ---

    def record_cue(self, number: float, name: str, contents: dict[int, int],
                   fade_in: float = 3.0, fade_out: float = 3.0,
                   delay_in: float = 0.0, delay_out: float = 0.0,
                   link_time: float = 0.0) -> Cue:
        existing = self.get_cue(number)
        if existing:
            existing.name = name
            existing.contents = dict(contents)
            existing.fade_in = fade_in
            existing.fade_out = fade_out
            existing.delay_in = delay_in
            existing.delay_out = delay_out
            existing.link_time = link_time
            self._bus.emit("sequencer.cues_changed")
            logger.info("Cue %.1f mise à jour : '%s'", number, name)
            return existing

        cue = Cue(
            number=number, name=name, contents=dict(contents),
            fade_in=fade_in, fade_out=fade_out,
            delay_in=delay_in, delay_out=delay_out,
            link_time=link_time,
        )
        self.add_cue(cue)
        return cue

    # --- Sérialisation ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "cues": [cue.to_dict() for cue in self._cues],
            "current_index": self._current_index,
            "onstage_levels": {
                str(k): v for k, v in self._onstage_levels.items()},
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        self._cues.clear()
        self._mode = CrossfadeMode.IDLE
        self._onstage_levels.clear()
        self._current_index = -1

        for cue_data in data.get("cues", []):
            try:
                self._cues.append(Cue.from_dict(cue_data))
            except Exception:
                logger.warning("Données de cue invalides ignorées")

        self._cues.sort(key=lambda c: c.number)
        self._current_index = data.get("current_index", -1)

        for k, v in data.get("onstage_levels", {}).items():
            try:
                self._onstage_levels[int(k)] = int(v)
            except (ValueError, TypeError):
                pass

    def stop(self) -> None:
        self._crossfade_running = False
        if self._crossfade_thread and self._crossfade_thread.is_alive():
            self._crossfade_thread.join(timeout=1.0)
        self._mode = CrossfadeMode.IDLE