"""Intégration engine : sauvegarde/chargement des bangers et du lien cue→banger."""

from lumensmaster.core.engine import Engine
from lumensmaster.modules.bangers import BangerEvent


def test_save_load_bangers(tmp_path):
    eng = Engine()
    eng.sequencer.ensure_default_cue()
    eng.sequencer.record_cue(1.0, "A", {1: 255}, banger=2)
    eng.bangers.create(2, "Tonnerre")
    eng.bangers.add_event(2, BangerEvent("faders", "set_level",
                                         {"fader": 1, "value": 50}, 0.5))
    path = tmp_path / "show.lms"
    assert eng.save_current_show(str(path))

    eng2 = Engine()
    assert eng2.load_existing_show(str(path))
    assert eng2.bangers.get(2).name == "Tonnerre"
    assert eng2.sequencer.get_cue(1.0).banger == 2
    assert not eng2.is_dirty
    eng.sequencer.stop(); eng2.sequencer.stop()


def test_record_cue_conserve_le_banger():
    eng = Engine()
    eng.sequencer.record_cue(1.0, "A", {1: 255}, banger=4)
    eng.sequencer.record_cue(1.0, "A bis", {1: 100})
    assert eng.sequencer.get_cue(1.0).banger == 4
