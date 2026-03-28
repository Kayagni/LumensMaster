"""
Module Séquenceur : gestion des mémoires (cues) et crossfades.

Le séquenceur gère une liste ordonnée de cues, chacune contenant :
    - Un numéro (float, ex: 1.0, 1.5, 2.0) pour insertion libre
    - Un nom (ex: "Entrée Hamlet")
    - Un contenu : {circuit: valeur}
    - 4 temps : fade_in, fade_out, delay_in, delay_out (en secondes)
    - Un temps de link (enchainement auto, 0 = désactivé)

Crossfade :
    - Mode temporisé : GO déclenche un fondu selon les temps de la cue cible
    - Mode manuel : un slider (0.0 à 1.0) contrôle la progression
      En mode manuel, les temps de fade et delay sont ignorés.
    - Pause : fige la transition en cours (mode temporisé uniquement)

    Canaux montants (UP) : delay_in → fade_in
    Canaux descendants (DOWN) : delay_out → fade_out

Thread safety :
    Un verrou (_lock) protège toutes les transitions d'état du crossfade
    pour éviter les race conditions entre le thread crossfade et le thread UI.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

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
    contents: dict[int, int] = field(default_factory=dict)  # circuit -> level
    fade_in: float = 3.0      # seconds
    fade_out: float = 3.0     # seconds
    delay_in: float = 0.0     # seconds
    delay_out: float = 0.0    # seconds
    link_time: float = 0.0    # seconds, 0 = pas de link

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
    """
    Séquenceur de cues avec crossfade thread-safe.

    Utilisation :
        seq = Sequencer(bus)
        seq.add_cue(Cue(number=1.0, name="Noir", contents={}))
        seq.add_cue(Cue(number=2.0, name="Plein feux", contents={1: 255}))
        seq.go()
        output = seq.get_output()
    """

    CROSSFADE_FPS = 40

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._cues: list[Cue] = []
        self._current_index: int = -1

        # Verrou réentrant pour protéger les transitions d'état
        # RLock car les callbacks d'événements peuvent rappeler get_output()
        # sur le même thread qui tient déjà le verrou
        self._lock = threading.RLock()

        # État du crossfade
        self._mode = CrossfadeMode.IDLE
        self._crossfade_thread: threading.Thread | None = None
        self._crossfade_running = False

        # Niveaux onstage (état actuel de la sortie)
        self._onstage_levels: dict[int, int] = {}

        # Cible du crossfade
        self._target_index: int = -1
        self._target_levels: dict[int, int] = {}

        # Timing du crossfade (mode temporisé)
        self._crossfade_start_time: float = 0.0
        self._crossfade_pause_elapsed: float = 0.0
        self._crossfade_target_cue: Cue | None = None

        # Mode manuel
        self._manual_progress: float = 0.0

        # Progression globale affichée (0.0 à 1.0)
        self._global_progress: float = 0.0

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

    def go(self) -> None:
        """Lance le crossfade vers la cue suivante."""
        if not self._cues:
            return

        with self._lock:
            if self.is_crossfading:
                self._complete_crossfade_locked()

            next_index = self._current_index + 1
            if next_index >= len(self._cues):
                logger.info("Fin de séquence, pas de cue suivante")
                return

            self._start_crossfade_locked(next_index)

    def go_back(self) -> None:
        """Lance le crossfade vers la cue précédente."""
        if not self._cues:
            return

        with self._lock:
            if self.is_crossfading:
                self._complete_crossfade_locked()

            prev_index = self._current_index - 1
            if prev_index < 0:
                logger.info("Début de séquence, pas de cue précédente")
                return

            self._start_crossfade_locked(prev_index)

    def go_to_cue(self, cue_number: float) -> None:
        """Saute directement à une cue spécifique."""
        for i, cue in enumerate(self._cues):
            if abs(cue.number - cue_number) < 0.001:
                with self._lock:
                    if self.is_crossfading:
                        self._complete_crossfade_locked()
                    self._start_crossfade_locked(i)
                return

    def pause(self) -> None:
        """Met en pause ou reprend le crossfade temporisé."""
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

        self._bus.emit("sequencer.state_changed")

    # --- Crossfade manuel ---

    def set_manual_mode(self, enabled: bool) -> None:
        """Active ou désactive le mode crossfade manuel."""
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

        self._bus.emit("sequencer.state_changed")
        self._bus.emit("sequencer.output_changed")

    def set_manual_progress(self, progress: float) -> None:
        """
        Définit la progression du crossfade manuel (0.0 à 1.0).
        En mode manuel, les temps de fade et delay sont ignorés :
        la progression est appliquée linéairement à tous les circuits.
        """
        with self._lock:
            if self._mode != CrossfadeMode.MANUAL:
                return
            self._manual_progress = max(0.0, min(1.0, progress))
            self._global_progress = self._manual_progress

        # Émissions hors du lock pour éviter deadlocks
        self._bus.emit("sequencer.output_changed")
        self._bus.emit("sequencer.progress_changed",
                       progress=self._global_progress)

    def complete_manual(self) -> None:
        """Complète le crossfade manuel (appelé quand le slider atteint 100%)."""
        with self._lock:
            if self._mode == CrossfadeMode.MANUAL:
                self._complete_crossfade_locked()

    # --- Crossfade temporisé (interne) ---

    def _start_crossfade_locked(self, target_index: int) -> None:
        """Démarre un crossfade. Doit être appelé avec le lock acquis."""
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
        self._bus.emit("sequencer.state_changed")

    def _start_crossfade_thread(self) -> None:
        self._crossfade_running = True
        self._crossfade_thread = threading.Thread(
            target=self._crossfade_loop,
            name="Crossfade",
            daemon=True,
        )
        self._crossfade_thread.start()

    def _crossfade_loop(self) -> None:
        """Boucle du thread crossfade."""
        interval = 1.0 / self.CROSSFADE_FPS

        while self._crossfade_running and self._mode == CrossfadeMode.TIMED:
            start = time.perf_counter()

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
                    # Émettre hors du lock
                    break

            # Émissions hors du lock
            self._bus.emit("sequencer.output_changed")
            self._bus.emit("sequencer.progress_changed",
                           progress=self._global_progress)

            sleep_time = interval - (time.perf_counter() - start)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _compute_channel_progress(self, circuit: int, elapsed: float,
                                  target_cue: Cue) -> float:
        """Calcule la progression d'un circuit (mode temporisé uniquement)."""
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
        Complète instantanément le crossfade en cours.
        DOIT être appelé avec self._lock acquis.

        L'ordre est critique :
            1. Copier les niveaux cible dans onstage
            2. Passer en IDLE (pour que get_output retourne onstage)
            3. Nettoyer l'état
            4. Émettre les événements (safe car RLock réentrant)
        """
        self._crossfade_running = False

        # 1. Copier les niveaux cible
        if self._target_index >= 0:
            self._current_index = self._target_index
            self._onstage_levels = dict(self._target_levels)

        # 2. Passer en IDLE AVANT de nettoyer
        self._mode = CrossfadeMode.IDLE
        self._global_progress = 0.0
        self._manual_progress = 0.0

        # 3. Nettoyer (maintenant que mode=IDLE, get_output ne lit plus target)
        self._target_index = -1
        self._target_levels.clear()
        self._crossfade_target_cue = None

        # 4. Émettre et gérer le link
        self._bus.emit("sequencer.state_changed")
        self._bus.emit("sequencer.output_changed")
        self._bus.emit("sequencer.progress_changed", progress=0.0)

        current = self.current_cue
        if current:
            logger.info("Cue %.1f '%s' onstage", current.number, current.name)
            if current.link_time > 0:
                logger.info("Link actif : GO auto dans %.1fs",
                            current.link_time)
                threading.Timer(current.link_time, self._link_go).start()

    def _link_go(self) -> None:
        if self._mode == CrossfadeMode.IDLE:
            self.go()

    # --- Sortie ---

    def get_output(self) -> dict[int, int]:
        """
        Calcule la sortie actuelle du séquenceur.
        Thread-safe grâce au lock.
        """
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
                    # Mode manuel : interpolation linéaire globale
                    # Les temps de fade/delay sont IGNORÉS
                    progress = self._manual_progress

                elif self._mode in (CrossfadeMode.TIMED, CrossfadeMode.PAUSED):
                    # Mode temporisé : progression par canal avec fade/delay
                    target_cue = self._crossfade_target_cue
                    if target_cue is None:
                        progress = 0.0
                    elif self._mode == CrossfadeMode.TIMED:
                        elapsed = time.perf_counter() - self._crossfade_start_time
                        progress = self._compute_channel_progress(
                            circuit, elapsed, target_cue)
                    else:  # PAUSED
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
        """Arrête le séquenceur proprement."""
        self._crossfade_running = False
        if self._crossfade_thread and self._crossfade_thread.is_alive():
            self._crossfade_thread.join(timeout=1.0)
        self._mode = CrossfadeMode.IDLE