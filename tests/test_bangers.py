"""Tests du module bangers (horloge simulée, sans UI)."""

import pytest

from lumensmaster.core.events import EventBus
from lumensmaster.modules.bangers import (
    ACTIONS, MAX_CHAIN_DEPTH, Banger, BangerEvent, BangerManager, describe_event,
)
from lumensmaster.modules.circuits import Circuits
from lumensmaster.modules.faders import Faders
from lumensmaster.modules.sequencer import Cue, Sequencer


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    bus = EventBus()
    clock = Clock()
    faders = Faders(bus, count=8)
    circuits = Circuits(bus)
    seq = Sequencer(bus)
    mgr = BangerManager(bus, clock=clock)
    mgr.attach(faders, circuits, seq)
    yield mgr, clock, faders, circuits, seq, bus
    seq.stop()


def ev(family, action, delay=0.0, **params):
    return BangerEvent(family=family, action=action, params=params, delay=delay)


def test_delais_absolus_et_tir_unique(env):
    mgr, clock, faders, *_ = env
    b = mgr.create(1)
    mgr.add_event(1, ev("faders", "set_level", 0.0, fader=1, value=100, unit="percent"))
    mgr.add_event(1, ev("faders", "set_level", 2.0, fader=2, value=128, unit="dmx"))
    assert mgr.start(1)
    mgr.poll()
    assert faders.get_level(1) == 255
    assert faders.get_level(2) == 0
    clock.t += 1.9
    mgr.poll()
    assert faders.get_level(2) == 0
    clock.t += 0.2
    mgr.poll()
    assert faders.get_level(2) == 128
    # Ne repart pas
    faders.set_level(1, 10)
    mgr.poll()
    assert faders.get_level(1) == 10
    assert not mgr.is_running(1)  # durée = max(2.0, 1.0)


def test_duree_minimale(env):
    mgr, clock, *_ = env
    mgr.create(1)
    mgr.add_event(1, ev("faders", "all_down"))
    mgr.start(1)
    mgr.poll()
    assert mgr.is_running(1)
    clock.t += 1.0
    mgr.poll()
    assert not mgr.is_running(1)


def test_rollback_ordre_inverse_meme_termine(env):
    mgr, clock, faders, circuits, *_ = env
    faders.set_level(3, 50)
    circuits.set_level(10, 20)
    mgr.create(1)
    mgr.add_event(1, ev("faders", "set_level", 0.0, fader=3, value=200, unit="dmx"))
    mgr.add_event(1, ev("faders", "set_level", 0.5, fader=3, value=10, unit="dmx"))
    mgr.add_event(1, ev("circuits", "add", 0.5, circuit=10, value=50, unit="dmx"))
    mgr.start(1)
    mgr.poll()
    clock.t += 5
    mgr.poll()
    assert faders.get_level(3) == 10
    assert circuits.get_level(10) == 70
    assert not mgr.is_running(1)
    mgr.rollback(1)
    assert faders.get_level(3) == 50
    assert circuits.get_level(10) == 20
    assert not mgr.has_rollback(1)


def test_rollback_ne_concerne_que_les_evenements_partis(env):
    mgr, clock, faders, *_ = env
    mgr.create(1)
    mgr.add_event(1, ev("faders", "set_level", 0.0, fader=1, value=255, unit="dmx"))
    mgr.add_event(1, ev("faders", "set_level", 3.0, fader=2, value=255, unit="dmx"))
    faders.set_level(2, 40)
    mgr.start(1)
    mgr.poll()
    mgr.rollback(1)
    assert faders.get_level(1) == 0
    assert faders.get_level(2) == 40
    clock.t += 5
    mgr.poll()
    assert faders.get_level(2) == 40  # stoppé : l'événement 2 ne part plus


def test_circuits_bornes(env):
    mgr, clock, faders, circuits, *_ = env
    circuits.set_level(1, 250)
    mgr.create(1)
    mgr.add_event(1, ev("circuits", "add", circuit=1, value=50, unit="percent"))
    mgr.add_event(1, ev("circuits", "sub", circuit=2, value=10, unit="dmx"))
    mgr.start(1)
    mgr.poll()
    assert circuits.get_level(1) == 255
    assert circuits.get_level(2) == 0


def test_loop_et_rollback_vers_etat_initial(env):
    mgr, clock, faders, circuits, *_ = env
    mgr.create(1)
    mgr.update(1, loop=True, loop_interval=2.0)
    mgr.add_event(1, ev("circuits", "add", 0.0, circuit=5, value=10, unit="dmx"))
    mgr.start(1)
    mgr.poll()
    assert circuits.get_level(5) == 10
    for _ in range(3):
        clock.t += 2.0
        mgr.poll()  # redémarrage
        mgr.poll()  # tir de l'itération
    assert circuits.get_level(5) == 40
    assert mgr.is_running(1)
    mgr.rollback(1)
    assert circuits.get_level(5) == 0
    assert not mgr.is_running(1)


def test_loop_period_jamais_inferieure_au_dernier_delai(env):
    mgr, *_ = env
    b = mgr.create(1)
    mgr.update(1, loop=True, loop_interval=0.5)
    mgr.add_event(1, ev("faders", "all_down", 3.0))
    assert b.loop_period == 3.0


def test_declenchement_depuis_cue_et_go_back(env):
    mgr, clock, faders, circuits, seq, bus = env
    seq.add_cue(Cue(number=0.0, name="noir", fade_in=0, fade_out=0))
    seq.add_cue(Cue(number=1.0, name="A", fade_in=0, fade_out=0, banger=1))
    seq._current_index = 0
    mgr.create(1)
    mgr.add_event(1, ev("faders", "set_level", fader=4, value=100, unit="percent"))
    seq.go()
    mgr.poll()
    assert faders.get_level(4) == 255
    seq._complete_crossfade_locked()
    clock.t += 5
    mgr.poll()
    assert not mgr.is_running(1)
    seq.go_back()
    assert faders.get_level(4) == 0  # GO BACK annule même terminé


def test_mode_global_off(env):
    mgr, clock, faders, circuits, seq, bus = env
    seq.add_cue(Cue(number=0.0, name="noir", fade_in=0, fade_out=0))
    seq.add_cue(Cue(number=1.0, name="A", fade_in=0, fade_out=0, banger=1))
    seq._current_index = 0
    mgr.create(1)
    mgr.add_event(1, ev("faders", "set_level", fader=4, value=100, unit="percent"))
    mgr.enabled = False
    seq.go()
    mgr.poll()
    assert faders.get_level(4) == 0
    # Le déclenchement manuel reste possible
    mgr.start(1)
    mgr.poll()
    assert faders.get_level(4) == 255


def test_garde_fou_boucle_infinie(env):
    mgr, clock, *_ = env
    mgr.create(1)
    mgr.add_event(1, ev("bangers", "start", banger=1))
    mgr.start(1)
    starts = 0
    for _ in range(50):
        mgr.poll()
        if mgr.is_running(1):
            starts += 1
    # La chaîne s'arrête d'elle-même après MAX_CHAIN_DEPTH relances
    clock.t += 5
    mgr.poll()
    assert not mgr.is_running(1)
    assert mgr._runs[1].depth == MAX_CHAIN_DEPTH


def test_chaine_cues_via_go_limitee(env):
    """Cue A → banger GO → cue B → banger GO… ne boucle pas à l'infini."""
    mgr, clock, faders, circuits, seq, bus = env
    seq.add_cue(Cue(number=0.0, name="noir", fade_in=0, fade_out=0))
    for n in range(1, 21):
        seq.add_cue(Cue(number=float(n), name=f"C{n}", fade_in=0, fade_out=0, banger=1))
    seq._current_index = 0
    mgr.create(1)
    mgr.add_event(1, ev("sequencer", "go"))
    seq.go()
    for _ in range(40):
        mgr.poll()
    assert seq.current_index <= MAX_CHAIN_DEPTH + 2


def test_links_action_et_rollback(env):
    mgr, clock, faders, circuits, seq, bus = env
    mgr.create(1)
    mgr.add_event(1, ev("sequencer", "links", state="off"))
    mgr.start(1)
    mgr.poll()
    assert seq.links_enabled is False
    mgr.rollback(1)
    assert seq.links_enabled is True


def test_banger_start_rollback_en_cascade(env):
    mgr, clock, faders, *_ = env
    mgr.create(1)
    mgr.add_event(1, ev("bangers", "start", banger=2))
    mgr.create(2)
    mgr.add_event(2, ev("faders", "set_level", fader=1, value=255, unit="dmx"))
    mgr.start(1)
    mgr.poll()   # banger 1 lance banger 2
    mgr.poll()   # banger 2 tire
    assert faders.get_level(1) == 255
    mgr.rollback(1)
    assert faders.get_level(1) == 0


def test_stop_ne_restaure_pas(env):
    mgr, clock, faders, *_ = env
    mgr.create(1)
    mgr.add_event(1, ev("faders", "set_level", fader=1, value=255, unit="dmx"))
    mgr.start(1)
    mgr.poll()
    mgr.stop(1)
    assert faders.get_level(1) == 255
    assert mgr.has_rollback(1)


def test_params_manquants_et_invalides():
    spec = ACTIONS[("faders", "set_level")]
    p = spec.resolve_params({"fader": "x", "value": 999, "unit": "bidon"})
    assert p == {"fader": 1, "value": 255, "unit": "percent"}


def test_serialisation_aller_retour(env):
    mgr, *_ = env
    mgr.create(3, "Orage")
    mgr.update(3, loop=True, loop_interval=4.0)
    mgr.add_event(3, ev("circuits", "set", 1.5, circuit=12, value=80, unit="percent"))
    mgr.enabled = False
    data = mgr.to_dict()
    mgr.clear()
    mgr.from_dict(data)
    b = mgr.get(3)
    assert b.name == "Orage" and b.loop and b.loop_interval == 4.0
    assert b.events[0].params == {"circuit": 12, "value": 80, "unit": "percent"}
    assert mgr.enabled is False
    assert "Circuit" in describe_event(b.events[0])


def test_cue_banger_serialise_et_compatible_ancien_format():
    c = Cue.from_dict({"number": 2, "name": "x"})
    assert c.banger == 0
    c2 = Cue.from_dict(Cue(number=2, name="x", banger=7).to_dict())
    assert c2.banger == 7


def test_changement_famille_reinitialise_action(env):
    mgr, *_ = env
    mgr.create(1)
    mgr.add_event(1)
    mgr.update_event(1, 0, family="sequencer")
    e = mgr.get(1).events[0]
    assert e.family == "sequencer" and e.action == "go" and e.params == {}
