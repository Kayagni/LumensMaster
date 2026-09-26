"""Tests du module audio (mixeur rendu à la main, sans carte son)."""

import wave

import numpy as np
import pytest

from lumensmaster.core.events import EventBus
from lumensmaster.modules import audio as au
from lumensmaster.modules.audio import (
    SAMPLE_RATE, AudioManager, DecoderSource, MemorySource, Mixer, Voice,
)
from lumensmaster.modules.bangers import BangerEvent, BangerManager, describe_event


def write_wav(path, seconds, rate=SAMPLE_RATE, value=0.5):
    n = int(seconds * rate)
    data = np.full((n, 2), int(value * 32767), dtype=np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return str(path)


def const(seconds, value=1.0):
    return np.full((int(seconds * SAMPLE_RATE), 2), value, dtype=np.float32)


def render_seconds(mixer, seconds, block=480):
    out = []
    for _ in range(int(seconds * SAMPLE_RATE) // block):
        out.append(mixer.render(block))
    return np.concatenate(out) if out else np.zeros((0, 2), np.float32)


# --- Voix / mixeur -----------------------------------------------------

def test_voice_fin_et_gain():
    m = Mixer()
    v = Voice(MemorySource(const(0.1)), on_end="finish")
    v.paused = False
    v.gain = 0.5
    m.add(v)
    out = render_seconds(m, 0.2)
    assert out[:100].max() == pytest.approx(0.25)   # amplitude = gain²
    assert out[-100:].max() == 0.0
    assert m.active_count() == 0 and not m._voices


def test_fondu_lineaire_sans_saut():
    m = Mixer()
    v = Voice(MemorySource(const(2.0)))
    v.paused = False
    v.fade_to(1.0, 1.0)
    m.add(v)
    out = render_seconds(m, 1.2)[:, 0]
    assert out[0] < 0.001
    assert np.max(np.abs(np.diff(out))) < 0.001      # pas de clic
    assert out[-1] == pytest.approx(1.0)


def test_loop_region():
    samples = np.arange(SAMPLE_RATE, dtype=np.float32).repeat(2).reshape(-1, 2)
    v = Voice(MemorySource(samples / SAMPLE_RATE), start=1000, end=2000,
              loop=True, loop_start=1000)
    v.paused, v.gain = False, 1.0
    out = v.render(3000)
    assert v.pos < 2000 and not v.finished
    assert out[999, 0] == pytest.approx(1999 / SAMPLE_RATE)
    assert out[1000, 0] == pytest.approx(1000 / SAMPLE_RATE)


# --- Lecteurs ------------------------------------------------------------

@pytest.fixture
def mgr(tmp_path):
    a = AudioManager(EventBus())
    a.tmp = tmp_path
    yield a
    a.stop()


def test_lecteur_load_play_stop_fade(mgr):
    f = mgr.add_file(write_wav(mgr.tmp / "a.wav", 2.0))
    assert f.preload and not f.missing and f.duration == pytest.approx(2.0)
    mgr.player_update(1, fade_out=0.5)
    assert mgr.player_load(1, f.id)
    assert not mgr.player_is_playing(1)
    mgr.player_play(1)
    render_seconds(mgr.mixer, 0.3)
    assert mgr.player_status(1)["position"] == pytest.approx(0.3, abs=0.01)
    mgr.player_stop(1)
    render_seconds(mgr.mixer, 0.4)
    assert mgr.player_is_playing(1)                    # fondu en cours
    render_seconds(mgr.mixer, 0.2)
    assert not mgr.player_is_playing(1)
    assert mgr.player_status(1)["position"] == 0.0     # rembobiné


def test_lecteur_fin_naturelle_rembobine_au_cue_in(mgr):
    f = mgr.add_file(write_wav(mgr.tmp / "a.wav", 1.0))
    mgr.set_cues(f.id, cue_in=0.2, cue_out=0.5)
    mgr.player_load(1, f.id)
    mgr.player_play(1)
    render_seconds(mgr.mixer, 0.5)
    st = mgr.player_status(1)
    assert not st["playing"] and st["position"] == pytest.approx(0.2)


def test_lecteur_loop_cue(mgr):
    f = mgr.add_file(write_wav(mgr.tmp / "a.wav", 1.0))
    mgr.set_cues(f.id, cue_in=0.1, cue_out=0.3)
    mgr.player_load(1, f.id)
    mgr.player_set_loop(1, "cue")
    mgr.player_play(1)
    render_seconds(mgr.mixer, 1.0)
    st = mgr.player_status(1)
    assert st["playing"] and 0.1 <= st["position"] < 0.3


def test_lecteur_streaming_non_precharge(mgr):
    f = mgr.add_file(write_wav(mgr.tmp / "b.wav", 1.0, rate=44100), preload=False)
    assert not f.preload
    mgr.player_load(1, f.id)
    assert isinstance(mgr.mixer.get(mgr.get_player(1).voice_id).source, DecoderSource)
    mgr.player_play(1)
    out = render_seconds(mgr.mixer, 0.5)
    assert out[SAMPLE_RATE // 10:, 0].min() > 0.2    # 44,1 kHz rééchantillonné
    mgr.player_seek(1, 0.9)
    render_seconds(mgr.mixer, 0.2)
    assert not mgr.player_is_playing(1)


def test_fichier_manquant(mgr, tmp_path):
    f = mgr.add_file(str(tmp_path / "absent.wav"))
    assert f.missing
    assert not mgr.player_load(1, f.id)


# --- Sons directs --------------------------------------------------------

def test_son_restart_vs_overlap(mgr):
    f = mgr.add_file(write_wav(mgr.tmp / "a.wav", 1.0))
    s = mgr.create_sound(file_id=f.id)
    assert s.name == "a"
    mgr.play_sound(s.number)
    render_seconds(mgr.mixer, 0.1)
    mgr.play_sound(s.number)                           # restart
    render_seconds(mgr.mixer, 0.1)
    mgr.poll_ui()
    assert len(mgr._sound_voices[s.number]) == 1
    mgr.update_sound(s.number, retrigger="overlap")
    mgr.play_sound(s.number)
    render_seconds(mgr.mixer, 0.05)
    mgr.poll_ui()
    assert len(mgr._sound_voices[s.number]) == 2
    render_seconds(mgr.mixer, 1.2)
    mgr.poll_ui()
    assert not mgr.sound_is_playing(s.number)


def test_son_stop_avec_fondu(mgr):
    f = mgr.add_file(write_wav(mgr.tmp / "a.wav", 3.0))
    s = mgr.create_sound(file_id=f.id)
    mgr.update_sound(s.number, fade_out=0.5, loop=True)
    mgr.play_sound(s.number)
    render_seconds(mgr.mixer, 0.2)
    mgr.stop_sound(s.number)
    render_seconds(mgr.mixer, 0.3)
    assert mgr.sound_is_playing(s.number)
    render_seconds(mgr.mixer, 0.3)
    mgr.poll_ui()
    assert not mgr.sound_is_playing(s.number)


# --- Sérialisation -------------------------------------------------------

def test_chemins_relatifs(mgr, tmp_path):
    (tmp_path / "sons").mkdir()
    f = mgr.add_file(write_wav(tmp_path / "sons" / "pluie.wav", 0.5))
    mgr.set_cues(f.id, cue_in=0.1)
    mgr.player_load(2, f.id)
    s = mgr.create_sound(file_id=f.id)
    data = mgr.to_dict(base_dir=tmp_path)
    assert data["files"][0]["path"] == "sons/pluie.wav"
    other = AudioManager(EventBus())
    other.from_dict(data, base_dir=tmp_path)
    g = other.get_file(f.id)
    assert not g.missing and g.cue_in == pytest.approx(0.1)
    assert other.get_player(2).voice_id is not None
    assert other.get_sound(s.number).file_id == f.id


# --- Bangers ------------------------------------------------------------

def test_banger_audio_rollback_lecteur(mgr):
    f = mgr.add_file(write_wav(mgr.tmp / "a.wav", 2.0))
    s = mgr.create_sound(file_id=f.id)
    bus = EventBus()
    bm = BangerManager(bus, clock=lambda: 0.0)
    bm.audio = mgr
    bm.create(1)
    bm.add_event(1, BangerEvent("audio", "player_load_play",
                                {"player": 1, "file": f.id}))
    bm.add_event(1, BangerEvent("audio", "sound_play", {"sound": s.number}))
    assert "a.wav" in describe_event(bm.get(1).events[0])
    bm.start(1)
    bm.poll()
    assert mgr.player_is_playing(1) and mgr.sound_is_playing(s.number)
    bm.rollback(1)
    render_seconds(mgr.mixer, 0.05)
    mgr.poll_ui()
    assert not mgr.player_is_playing(1)
    assert mgr.get_player(1).file_id == 0
    assert not mgr.sound_is_playing(s.number)


def test_engine_save_load_audio(tmp_path):
    from lumensmaster.core.engine import Engine
    (tmp_path / "show" / "sons").mkdir(parents=True)
    wav = write_wav(tmp_path / "show" / "sons" / "vent.wav", 0.5)
    eng = Engine()
    f = eng.audio.add_file(wav)
    eng.audio.create_sound(file_id=f.id)
    show = tmp_path / "show" / "test.lms"
    assert eng.save_current_show(str(show))
    # Le show est déplacé avec son dossier : les chemins suivent
    moved = tmp_path / "ailleurs"
    (tmp_path / "show").rename(moved)
    eng2 = Engine()
    assert eng2.load_existing_show(str(moved / "test.lms"))
    g = eng2.audio.get_file(f.id)
    assert not g.missing and g.path.startswith(str(moved))
    assert not eng2.is_dirty
    eng.sequencer.stop(); eng2.sequencer.stop()
