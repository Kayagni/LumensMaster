"""
Module Bangers : déclencheurs multi-événements temporisés.

Principe (repris de WhiteCat, banger_core_8.cpp) :
    - Un banger est une liste d'événements. Chaque événement porte une
      action (famille + action + paramètres) et un délai en secondes.
    - Les délais sont ABSOLUS : comptés depuis le départ du banger,
      ils ne s'additionnent pas.
    - Chaque événement part une seule fois par exécution.
    - Un banger peut boucler (loop) avec une période donnée.
    - Déclencheurs : GO du séquenceur vers une cue qui porte un banger
      (si le mode global est actif), manuel, ou depuis un autre banger.

Différences assumées avec WhiteCat :
    - Nombre d'événements libre (WhiteCat : 6 fixes).
    - Rollback par snapshot : chaque action mémorise l'état qu'elle
      écrase et le restaure. Le rollback annule les événements déjà partis,
      dans l'ordre inverse, même si le banger est terminé.
    - GO BACK annule le banger de la cue quittée, qu'il soit en cours
      ou terminé (WhiteCat : uniquement s'il tourne encore).

Threading :
    Le BangerManager est MONO-THREAD : toutes ses méthodes doivent être
    appelées depuis le thread principal (boucle de rendu). poll() est
    appelé à chaque frame par app.py. Les actions appellent directement
    les modules (faders, circuits, séquenceur), qui émettent leurs
    événements bus depuis ce même thread : compatible Dear PyGui.

Garde-fou anti-boucle :
    Un banger démarré par une action (directement ou via un GO du
    séquenceur) hérite d'une profondeur de chaîne = parent + 1.
    Au-delà de MAX_CHAIN_DEPTH, le démarrage est refusé.
    Les démarrages déclenchés pendant poll() ne sont traités qu'à la
    frame suivante : aucune récursion possible dans un même appel.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from lumensmaster.core.events import EventBus

logger = logging.getLogger(__name__)

MAX_BANGERS = 128
MAX_CHAIN_DEPTH = 8
# Durée minimale d'exécution (WhiteCat : default_time_of_the_bang = 1 s).
# Sert au retour visuel « banger actif » et comme période mini de loop.
MIN_RUN_DURATION = 1.0


# ═══════════════════════════════════════════════════════════════════
#  Modèle de données
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BangerEvent:
    """Un événement : une action exécutée à `delay` secondes du départ."""
    family: str = ""
    action: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    delay: float = 0.0
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "action": self.action,
            "params": dict(self.params),
            "delay": self.delay,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BangerEvent":
        return cls(
            family=str(data.get("family", "")),
            action=str(data.get("action", "")),
            params=dict(data.get("params", {}) or {}),
            delay=max(0.0, float(data.get("delay", 0.0))),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class Banger:
    """Un banger : liste d'événements temporisés."""
    number: int
    name: str = ""
    events: list[BangerEvent] = field(default_factory=list)
    loop: bool = False
    loop_interval: float = 0.0  # 0 = la durée du banger

    @property
    def max_delay(self) -> float:
        delays = [e.delay for e in self.events if e.enabled]
        return max(delays) if delays else 0.0

    @property
    def duration(self) -> float:
        return max(self.max_delay, MIN_RUN_DURATION)

    @property
    def loop_period(self) -> float:
        """Période de boucle : jamais plus courte que le dernier délai."""
        return max(self.loop_interval, self.duration)

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "name": self.name,
            "events": [e.to_dict() for e in self.events],
            "loop": self.loop,
            "loop_interval": self.loop_interval,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Banger":
        events = []
        for ev in data.get("events", []) or []:
            try:
                events.append(BangerEvent.from_dict(ev))
            except (TypeError, ValueError):
                logger.warning("Événement de banger invalide ignoré : %r", ev)
        return cls(
            number=int(data["number"]),
            name=str(data.get("name", "")),
            events=events,
            loop=bool(data.get("loop", False)),
            loop_interval=max(0.0, float(data.get("loop_interval", 0.0))),
        )


# ═══════════════════════════════════════════════════════════════════
#  Registre d'actions
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ActionParam:
    """
    Description d'un paramètre d'action (sert aussi à générer l'UI).

    kind : "int" | "float" | "choice" | "cue" | "banger" | "fader" | "circuit"
           | "player" | "sound" | "file" (id de fichier de la bibliothèque audio)
    """
    key: str
    label: str
    kind: str = "int"
    default: Any = 0
    min_value: float | None = None
    max_value: float | None = None
    choices: list[tuple[str, str]] = field(default_factory=list)  # (valeur, libellé)


# execute(manager, params) -> snapshot (quelconque) pour le rollback
ExecuteFn = Callable[["BangerManager", dict[str, Any]], Any]
# rollback(manager, params, snapshot)
RollbackFn = Callable[["BangerManager", dict[str, Any], Any], None]


@dataclass
class ActionSpec:
    family: str
    action: str
    label: str
    params: list[ActionParam]
    execute: ExecuteFn
    rollback: RollbackFn | None = None
    # Correspondance WhiteCat (famille, action) — indicative, pour le futur
    # convertisseur .whc. La sémantique des paramètres reste à valider
    # au moment de l'écriture du convertisseur.
    wc_codes: list[tuple[int, int]] = field(default_factory=list)

    def resolve_params(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Complète les paramètres manquants et convertit les types."""
        resolved: dict[str, Any] = {}
        for p in self.params:
            value = raw.get(p.key, p.default)
            try:
                if p.kind in ("int", "banger", "fader", "circuit",
                              "player", "sound", "file"):
                    value = int(value)
                elif p.kind in ("float", "cue"):
                    value = float(value)
                elif p.kind == "choice":
                    value = str(value)
                    if p.choices and value not in [c[0] for c in p.choices]:
                        value = p.default
            except (TypeError, ValueError):
                value = p.default
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if p.min_value is not None and value < p.min_value:
                    value = type(value)(p.min_value)
                if p.max_value is not None and value > p.max_value:
                    value = type(value)(p.max_value)
            resolved[p.key] = value
        return resolved


FAMILIES: dict[str, str] = {
    "faders": "Faders",
    "circuits": "Circuits",
    "sequencer": "Séquenceur",
    "bangers": "Bangers",
}

ACTIONS: dict[tuple[str, str], ActionSpec] = {}


def register_action(spec: ActionSpec) -> None:
    """Enregistre une action (point d'extension : audio, MIDI…)."""
    if spec.family not in FAMILIES:
        FAMILIES[spec.family] = spec.family
    ACTIONS[(spec.family, spec.action)] = spec


def get_action(family: str, action: str) -> ActionSpec | None:
    return ACTIONS.get((family, action))


def actions_for(family: str) -> list[ActionSpec]:
    return [s for (f, _), s in ACTIONS.items() if f == family]


# Formatage lisible de certains types de paramètres, enregistré par les
# modules qui en connaissent le sens (ex. audio : id de fichier -> nom).
PARAM_FORMATTERS: dict[str, Callable[[Any], str]] = {}


def describe_event(event: BangerEvent) -> str:
    """Résumé lisible d'un événement (liste, tooltips)."""
    spec = get_action(event.family, event.action)
    if spec is None:
        return f"? {event.family}/{event.action}"
    params = spec.resolve_params(event.params)
    parts = []
    for p in spec.params:
        v = params[p.key]
        if p.kind == "choice":
            v = dict(p.choices).get(v, v)
        elif p.kind == "cue":
            v = f"{v:.1f}"
        elif p.kind in PARAM_FORMATTERS:
            try:
                v = PARAM_FORMATTERS[p.kind](v)
            except Exception:
                pass
        parts.append(f"{p.label} {v}")
    fam = FAMILIES.get(spec.family, spec.family)
    return f"{fam} : {spec.label}" + (f" ({', '.join(parts)})" if parts else "")


# --- Helpers de conversion -----------------------------------------

UNIT_CHOICES = [("percent", "%"), ("dmx", "/255")]


def _to_dmx(value: float, unit: str) -> int:
    if unit == "percent":
        return max(0, min(255, round(value * 255 / 100)))
    return max(0, min(255, int(value)))


# --- Famille Faders --------------------------------------------------

def _fader_set_level(m: "BangerManager", p: dict[str, Any]) -> Any:
    fid = p["fader"]
    previous = m.faders.get_level(fid)
    m.faders.set_level(fid, _to_dmx(p["value"], p["unit"]))
    return {fid: previous}


def _faders_all_down(m: "BangerManager", p: dict[str, Any]) -> Any:
    snapshot = {fid: m.faders.get_level(fid)
                for fid in range(1, m.faders.count + 1)
                if m.faders.get_level(fid) > 0}
    m.faders.all_down()
    return snapshot


def _faders_restore(m: "BangerManager", p: dict[str, Any], snap: Any) -> None:
    for fid, level in (snap or {}).items():
        m.faders.set_level(fid, level)


register_action(ActionSpec(
    "faders", "set_level", "Fader à",
    [ActionParam("fader", "Fader", "fader", 1, 1, None),
     ActionParam("value", "Niveau", "int", 100, 0, 255),
     ActionParam("unit", "Unité", "choice", "percent", choices=UNIT_CHOICES)],
    _fader_set_level, _faders_restore, wc_codes=[(1, 11)]))

register_action(ActionSpec(
    "faders", "all_down", "Tous à 0", [],
    _faders_all_down, _faders_restore, wc_codes=[(1, 16)]))


# --- Famille Circuits (SetChannel WhiteCat) ---------------------------

def _circuit_apply(m: "BangerManager", p: dict[str, Any], mode: str) -> Any:
    circuit = p["circuit"]
    previous = m.circuits.get_level(circuit)
    delta = _to_dmx(p["value"], p["unit"])
    if mode == "set":
        new = delta
    elif mode == "add":
        new = previous + delta
    else:
        new = previous - delta
    m.circuits.set_level(circuit, max(0, min(255, new)))
    return {circuit: previous}


def _circuits_restore(m: "BangerManager", p: dict[str, Any], snap: Any) -> None:
    for circuit, level in (snap or {}).items():
        m.circuits.set_level(circuit, level)


_CIRCUIT_PARAMS = [
    ActionParam("circuit", "Circuit", "circuit", 1, 1, 512),
    ActionParam("value", "Valeur", "int", 100, 0, 255),
    ActionParam("unit", "Unité", "choice", "percent", choices=UNIT_CHOICES),
]

register_action(ActionSpec(
    "circuits", "set", "Circuit à", _CIRCUIT_PARAMS,
    lambda m, p: _circuit_apply(m, p, "set"), _circuits_restore,
    wc_codes=[(11, 0), (11, 3)]))
register_action(ActionSpec(
    "circuits", "add", "Circuit +", _CIRCUIT_PARAMS,
    lambda m, p: _circuit_apply(m, p, "add"), _circuits_restore,
    wc_codes=[(11, 1), (11, 4)]))
register_action(ActionSpec(
    "circuits", "sub", "Circuit -", _CIRCUIT_PARAMS,
    lambda m, p: _circuit_apply(m, p, "sub"), _circuits_restore,
    wc_codes=[(11, 2), (11, 5)]))


# --- Famille Séquenceur ------------------------------------------------
# Un GO n'est pas annulable proprement : pas de rollback pour GO/BACK/GOTO.

def _seq_links(m: "BangerManager", p: dict[str, Any]) -> Any:
    previous = m.sequencer.links_enabled
    m.sequencer.links_enabled = (p["state"] == "on")
    return previous


def _seq_links_restore(m: "BangerManager", p: dict[str, Any], snap: Any) -> None:
    m.sequencer.links_enabled = bool(snap)


_ON_OFF = [("on", "ON"), ("off", "OFF")]

register_action(ActionSpec(
    "sequencer", "go", "GO", [],
    lambda m, p: m.sequencer.go(), wc_codes=[(6, 7)]))
register_action(ActionSpec(
    "sequencer", "go_back", "GO BACK", [],
    lambda m, p: m.sequencer.go_back()))
register_action(ActionSpec(
    "sequencer", "goto", "GOTO cue",
    [ActionParam("cue", "Cue", "cue", 1.0, 0.0, None),
     ActionParam("mode", "Mode", "choice", "fade",
                 choices=[("fade", "avec fondu"), ("instant", "instantané")])],
    lambda m, p: (m.sequencer.goto_cue_instant(p["cue"])
                  if p["mode"] == "instant" else m.sequencer.go_to_cue(p["cue"])),
    wc_codes=[(6, 0)]))
register_action(ActionSpec(
    "sequencer", "pause", "PAUSE", [],
    lambda m, p: m.sequencer.pause()))
register_action(ActionSpec(
    "sequencer", "links", "Links",
    [ActionParam("state", "État", "choice", "on", choices=_ON_OFF)],
    _seq_links, _seq_links_restore, wc_codes=[(6, 3)]))


# --- Famille Bangers ---------------------------------------------------

def _bg_start(m: "BangerManager", p: dict[str, Any]) -> Any:
    m.start(p["banger"], _from_action=True)
    return None


def _bg_start_rollback(m: "BangerManager", p: dict[str, Any], snap: Any) -> None:
    m.rollback(p["banger"])


def _bg_stop(m: "BangerManager", p: dict[str, Any]) -> Any:
    m.stop(p["banger"])
    return None


def _bg_rollback(m: "BangerManager", p: dict[str, Any]) -> Any:
    m.rollback(p["banger"])
    return None


def _bg_loop(m: "BangerManager", p: dict[str, Any], on: bool) -> Any:
    b = m.get(p["banger"])
    if b is None:
        return None
    previous = b.loop
    m.set_loop(b.number, on)
    if on:
        m.start(b.number, _from_action=True)  # WhiteCat : Loop ON relance
    return previous


def _bg_loop_restore(m: "BangerManager", p: dict[str, Any], snap: Any) -> None:
    if snap is not None:
        m.set_loop(p["banger"], bool(snap))
        if not snap:
            m.stop(p["banger"])


def _bg_enable(m: "BangerManager", p: dict[str, Any]) -> Any:
    previous = m.enabled
    m.enabled = (p["state"] == "on")
    return previous


def _bg_enable_restore(m: "BangerManager", p: dict[str, Any], snap: Any) -> None:
    m.enabled = bool(snap)


_BANGER_PARAM = [ActionParam("banger", "Banger", "banger", 1, 1, MAX_BANGERS)]

register_action(ActionSpec(
    "bangers", "start", "Lancer", _BANGER_PARAM,
    _bg_start, _bg_start_rollback, wc_codes=[(12, 0)]))
register_action(ActionSpec(
    "bangers", "stop", "Stopper", _BANGER_PARAM,
    _bg_stop, wc_codes=[(12, 0)]))
register_action(ActionSpec(
    "bangers", "rollback", "Rollback", _BANGER_PARAM,
    _bg_rollback, wc_codes=[(12, 1)]))
register_action(ActionSpec(
    "bangers", "loop_on", "Loop ON", _BANGER_PARAM,
    lambda m, p: _bg_loop(m, p, True), _bg_loop_restore, wc_codes=[(12, 2)]))
register_action(ActionSpec(
    "bangers", "loop_off", "Loop OFF", _BANGER_PARAM,
    lambda m, p: _bg_loop(m, p, False), _bg_loop_restore, wc_codes=[(12, 3)]))
register_action(ActionSpec(
    "bangers", "enable", "Mode bangers (cues)",
    [ActionParam("state", "État", "choice", "on", choices=_ON_OFF)],
    _bg_enable, _bg_enable_restore, wc_codes=[(6, 4)]))


# ═══════════════════════════════════════════════════════════════════
#  Exécution
# ═══════════════════════════════════════════════════════════════════

@dataclass
class _Fired:
    index: int
    event: BangerEvent
    params: dict[str, Any]
    snapshot: Any


@dataclass
class BangerRun:
    """État d'une exécution de banger."""
    number: int
    start_time: float
    depth: int = 0
    fired_indices: set[int] = field(default_factory=set)
    fired: list[_Fired] = field(default_factory=list)
    # Événements de la 1re itération d'une boucle (snapshots d'origine)
    base_fired: list[_Fired] = field(default_factory=list)
    running: bool = True


class BangerManager:
    """Gestionnaire central des bangers (thread principal uniquement)."""

    def __init__(self, bus: EventBus,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        self._bus = bus
        self._clock = clock
        self._bangers: dict[int, Banger] = {}
        # Dernière exécution de chaque banger (en cours ou terminée)
        self._runs: dict[int, BangerRun] = {}
        self._enabled: bool = True
        self._current_depth: int | None = None  # profondeur de l'action en cours

        # Modules pilotés (injectés par l'engine)
        self.faders: Any = None
        self.circuits: Any = None
        self.sequencer: Any = None

    def attach(self, faders: Any, circuits: Any, sequencer: Any) -> None:
        self.faders = faders
        self.circuits = circuits
        self.sequencer = sequencer
        self._bus.on("sequencer.go_started", self._on_sequencer_go)

    # --- Mode global -----------------------------------------------

    @property
    def enabled(self) -> bool:
        """Déclenchement des bangers depuis les cues (WhiteCat : Banger ON)."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        value = bool(value)
        if value != self._enabled:
            self._enabled = value
            self._bus.emit("bangers.enabled_changed", enabled=value)

    # --- Définitions -------------------------------------------------

    @property
    def bangers(self) -> list[Banger]:
        return [self._bangers[n] for n in sorted(self._bangers)]

    def get(self, number: int) -> Banger | None:
        return self._bangers.get(number)

    def next_free_number(self) -> int:
        for n in range(1, MAX_BANGERS + 1):
            if n not in self._bangers:
                return n
        return 0

    def create(self, number: int | None = None, name: str = "") -> Banger | None:
        if number is None:
            number = self.next_free_number()
        if not 1 <= number <= MAX_BANGERS or number in self._bangers:
            return None
        banger = Banger(number=number, name=name or f"Banger {number}")
        self._bangers[number] = banger
        self._notify_changed(number)
        return banger

    def delete(self, number: int) -> bool:
        if number not in self._bangers:
            return False
        self._runs.pop(number, None)
        del self._bangers[number]
        self._notify_changed(number)
        return True

    def copy(self, source: int, target: int) -> Banger | None:
        src = self._bangers.get(source)
        if src is None or not 1 <= target <= MAX_BANGERS:
            return None
        data = src.to_dict()
        data["number"] = target
        self._runs.pop(target, None)
        self._bangers[target] = Banger.from_dict(data)
        self._notify_changed(target)
        return self._bangers[target]

    def update(self, number: int, **kwargs: Any) -> bool:
        b = self._bangers.get(number)
        if b is None:
            return False
        for key in ("name", "loop", "loop_interval"):
            if key in kwargs:
                setattr(b, key, kwargs[key])
        b.loop_interval = max(0.0, float(b.loop_interval))
        self._notify_changed(number)
        return True

    def set_loop(self, number: int, on: bool) -> None:
        self.update(number, loop=bool(on))

    def add_event(self, number: int, event: BangerEvent | None = None) -> int:
        """Ajoute un événement, retourne son index (-1 si échec)."""
        b = self._bangers.get(number)
        if b is None:
            return -1
        if event is None:
            event = BangerEvent(family="faders", action="set_level",
                                params={}, delay=b.max_delay)
        b.events.append(event)
        self._notify_changed(number)
        return len(b.events) - 1

    def update_event(self, number: int, index: int, **kwargs: Any) -> bool:
        b = self._bangers.get(number)
        if b is None or not 0 <= index < len(b.events):
            return False
        ev = b.events[index]
        if "family" in kwargs and kwargs["family"] != ev.family:
            # Changement de famille : première action de la famille
            ev.family = kwargs["family"]
            specs = actions_for(ev.family)
            ev.action = specs[0].action if specs else ""
            ev.params = {}
        if "action" in kwargs and kwargs["action"] != ev.action:
            ev.action = kwargs["action"]
            ev.params = {}
        if "params" in kwargs:
            ev.params.update(kwargs["params"])
        if "delay" in kwargs:
            ev.delay = max(0.0, float(kwargs["delay"]))
        if "enabled" in kwargs:
            ev.enabled = bool(kwargs["enabled"])
        self._notify_changed(number)
        return True

    def remove_event(self, number: int, index: int) -> bool:
        b = self._bangers.get(number)
        if b is None or not 0 <= index < len(b.events):
            return False
        b.events.pop(index)
        self._notify_changed(number)
        return True

    def move_event(self, number: int, index: int, direction: int) -> bool:
        b = self._bangers.get(number)
        if b is None:
            return False
        target = index + direction
        if not (0 <= index < len(b.events) and 0 <= target < len(b.events)):
            return False
        b.events[index], b.events[target] = b.events[target], b.events[index]
        self._notify_changed(number)
        return True

    def _notify_changed(self, number: int) -> None:
        self._bus.emit("bangers.changed", number=number)

    # --- Exécution ---------------------------------------------------

    def is_running(self, number: int) -> bool:
        run = self._runs.get(number)
        return bool(run and run.running)

    def has_rollback(self, number: int) -> bool:
        run = self._runs.get(number)
        return bool(run and (run.fired or run.base_fired))

    def running_numbers(self) -> list[int]:
        return [n for n, r in self._runs.items() if r.running]

    def start(self, number: int, *, _from_action: bool = False) -> bool:
        """
        Démarre (ou redémarre) un banger. Les événements partent au
        prochain poll(). Un redémarrage conserve les snapshots d'origine
        pour que le rollback revienne à l'état d'avant le 1er départ.
        """
        b = self._bangers.get(number)
        if b is None or not b.events:
            return False

        depth = 0
        if _from_action or self._current_depth is not None:
            depth = (self._current_depth or 0) + 1
        if depth > MAX_CHAIN_DEPTH:
            logger.warning("Banger %d refusé : chaîne de déclenchement trop "
                           "longue (> %d)", number, MAX_CHAIN_DEPTH)
            return False

        previous = self._runs.get(number)
        run = BangerRun(number=number, start_time=self._clock(), depth=depth)
        if previous is not None and previous.running:
            run.base_fired = previous.base_fired or previous.fired
        self._runs[number] = run
        logger.info("Banger %d '%s' lancé", number, b.name)
        self._bus.emit("banger.state_changed", number=number, running=True)
        return True

    def stop(self, number: int) -> None:
        """Arrête un banger sans annuler ce qui est déjà parti."""
        run = self._runs.get(number)
        if run and run.running:
            run.running = False
            self._bus.emit("banger.state_changed", number=number, running=False)

    def rollback(self, number: int) -> None:
        """Annule les événements déjà partis (ordre inverse) et arrête."""
        run = self._runs.pop(number, None)
        if run is None:
            return
        was_running = run.running
        run.running = False
        entries = list(reversed(run.fired))
        if run.base_fired and run.base_fired is not run.fired:
            entries += list(reversed(run.base_fired))
        for entry in entries:
            spec = get_action(entry.event.family, entry.event.action)
            if spec is None or spec.rollback is None:
                continue
            self._guarded(run.depth, spec.rollback, entry.params, entry.snapshot)
        logger.info("Banger %d : rollback (%d événements)", number, len(entries))
        if was_running or entries:
            self._bus.emit("banger.state_changed", number=number, running=False)

    def trigger_or_rollback(self, number: int) -> None:
        """Comportement du clic dans la grille WhiteCat : lance / annule."""
        if self.is_running(number):
            self.rollback(number)
        else:
            self.start(number)

    def poll(self) -> None:
        """Fait avancer tous les bangers actifs. Thread principal, chaque frame."""
        if not self._runs:
            return
        now = self._clock()
        for number, run in list(self._runs.items()):
            if not run.running:
                continue
            b = self._bangers.get(number)
            if b is None:
                self._runs.pop(number, None)
                continue
            elapsed = now - run.start_time

            due = sorted(
                (i for i, ev in enumerate(b.events)
                 if ev.enabled and i not in run.fired_indices
                 and ev.delay <= elapsed),
                key=lambda i: (b.events[i].delay, i))
            for i in due:
                if self._runs.get(number) is not run or not run.running:
                    break  # stoppé/annulé par une de ses propres actions
                self._fire(run, i, b.events[i])

            if self._runs.get(number) is not run or not run.running:
                continue

            if b.loop:
                if elapsed >= b.loop_period:
                    base = run.base_fired or run.fired
                    new_run = BangerRun(number=number,
                                        start_time=run.start_time + b.loop_period,
                                        depth=run.depth, base_fired=base)
                    self._runs[number] = new_run
            elif elapsed >= b.duration:
                run.running = False
                self._bus.emit("banger.state_changed", number=number, running=False)

    def _fire(self, run: BangerRun, index: int, event: BangerEvent) -> None:
        run.fired_indices.add(index)
        spec = get_action(event.family, event.action)
        if spec is None:
            logger.warning("Banger %d : action inconnue %s/%s",
                           run.number, event.family, event.action)
            return
        params = spec.resolve_params(event.params)
        snapshot = self._guarded(run.depth, spec.execute, params)
        run.fired.append(_Fired(index, event, params, snapshot))
        self._bus.emit("banger.event_fired", number=run.number, index=index)

    def _guarded(self, depth: int, fn: Callable[..., Any], *args: Any) -> Any:
        """Exécute une action en positionnant la profondeur de chaîne."""
        saved = self._current_depth
        self._current_depth = depth
        try:
            return fn(self, *args)
        except Exception:
            logger.exception("Erreur dans une action de banger")
            return None
        finally:
            self._current_depth = saved

    def stop_all(self) -> None:
        for number in self.running_numbers():
            self.stop(number)

    # --- Intégration séquenceur -----------------------------------

    def _on_sequencer_go(self, cue_number: float | None = None,
                         from_cue: float | None = None,
                         direction: str = "forward", **kwargs: Any) -> None:
        seq = self.sequencer
        if seq is None:
            return
        if direction == "back":
            # GO BACK : rollback du banger de la cue quittée
            cue = seq.get_cue(from_cue) if from_cue is not None else None
            if cue and cue.banger:
                self.rollback(cue.banger)
            return
        if not self._enabled:
            return
        cue = seq.get_cue(cue_number) if cue_number is not None else None
        if cue and cue.banger:
            self.start(cue.banger)

    # --- Sérialisation ------------------------------------------------

    def clear(self) -> None:
        self._runs.clear()
        self._bangers.clear()
        self._enabled = True
        self._bus.emit("bangers.changed", number=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "bangers": [b.to_dict() for b in self.bangers],
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        self._runs.clear()
        self._bangers.clear()
        self._enabled = bool(data.get("enabled", True))
        for bd in data.get("bangers", []) or []:
            try:
                b = Banger.from_dict(bd)
            except (KeyError, TypeError, ValueError):
                logger.warning("Banger invalide ignoré : %r", bd)
                continue
            if 1 <= b.number <= MAX_BANGERS:
                self._bangers[b.number] = b
        self._bus.emit("bangers.changed", number=0)
