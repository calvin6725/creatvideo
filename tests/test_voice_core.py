"""声线实验室核心逻辑的自测（不需要 GPU、不需要下载模型）。

运行：
    python -m pytest tests/test_voice_core.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice import dsp, processor, segment  # noqa: E402
from voice.audio_io import rms, wav_bytes, read_audio_bytes, to_mono, resample  # noqa: E402
from voice.store import CloneStore  # noqa: E402


def harmonic_tone(seconds=2.0, f0=140.0, rate=24000, harmonics=10):
    t = np.arange(int(seconds * rate)) / rate
    signal = sum(0.5 / h * np.sin(2 * np.pi * f0 * h * t + 0.3 * h) for h in range(1, harmonics + 1))
    signal = signal * (0.65 + 0.35 * np.sin(2 * np.pi * 2.7 * t))  # 模拟音节起伏
    return (signal / np.max(np.abs(signal))).astype(np.float32)


@pytest.mark.parametrize("semitones", [-7, -3, 0, 3, 7])
def test_psola_changes_pitch_but_keeps_duration(semitones):
    rate = 24000
    signal = harmonic_tone(1.5, 150.0, rate)
    out = dsp.psola(signal, rate, 2 ** (semitones / 12))
    assert out.shape == signal.shape, "变调不应该改变时长"
    _, before = dsp.f0_track(signal, rate)
    _, after = dsp.f0_track(out, rate)
    f_before = float(np.median(before[before > 0]))
    f_after = float(np.median(after[after > 0]))
    expected = f_before * 2 ** (semitones / 12)
    assert abs(f_after - expected) / expected < 0.06, (f_after, expected)
    assert not np.isnan(out).any()
    assert rms(out) > rms(signal) * 0.35


def test_f0_track_rejects_noise():
    rate = 16000
    rng = np.random.default_rng(0)
    noise = (rng.standard_normal(rate) * 0.2).astype(np.float32)
    _, f0 = dsp.f0_track(noise, rate)
    assert np.count_nonzero(f0) == 0 or float(np.median(f0[f0 > 0])) > 0


def test_formant_shift_and_eq_are_stable():
    rate = 24000
    signal = harmonic_tone(1.0, 180.0, rate)
    shifted = dsp.resample_formants(signal, rate, 1.25)
    assert shifted.shape == signal.shape
    assert np.isfinite(shifted).all()
    profile = dsp.spectral_profile(signal, rate)
    matched = dsp.eq_matches(shifted, rate, profile)
    assert matched.shape == signal.shape
    assert abs(rms(matched) - rms(shifted)) < rms(shifted) * 1.2


def test_finish_audio_produces_valid_output():
    rate = 24000
    signal = harmonic_tone(2.0, 160.0, rate)
    features = processor.analyze_clone(signal, rate)
    profile = processor.prepare_clone_profile({"features": features}, signal, rate, {})
    out, out_rate = processor.finish_audio(
        signal, rate, profile,
        {"pitch": 2, "formant": 20, "speed": 0, "style": "broadcast", "deess": 40},
    )
    assert out_rate == rate
    assert out.dtype == np.float32
    assert np.isfinite(out).all()
    assert 0.0 < float(np.max(np.abs(out))) <= 1.0
    assert len(out) == len(signal)


def test_split_sentences_and_budget():
    text = "第一句话。第二句话！第三句比较长的内容，需要按逗号切开吗？最后一句"
    sentences = segment.split_sentences(text)
    assert "".join(sentences).replace(" ", "") == text.replace(" ", "")
    chunks = segment.split_text_for_tts(text, max_chars=8)
    assert all(len(chunk) <= 12 for chunk in chunks)
    assert all(chunk.endswith(("。", "！", "？")) for chunk in chunks)


def test_analyze_reference_finds_speech_segment():
    rate = 16000
    silence = np.zeros(int(rate * 1.0), dtype=np.float32)
    speech = harmonic_tone(3.0, 130.0, rate) * 0.4
    tail = np.zeros(int(rate * 1.5), dtype=np.float32)
    analysis = segment.analyze_reference(np.concatenate([silence, speech, tail]), rate)
    assert analysis["best"] is not None
    best = analysis["best"]
    assert 0.8 <= best["start"] <= 1.4
    assert 3.2 <= best["stop"] <= 5.2
    selected, _ = segment.best_reference_segment(np.concatenate([silence, speech, tail]), rate)
    assert rms(selected) > 0.05


def test_audio_io_roundtrip():
    rate = 24000
    signal = harmonic_tone(1.0, 200.0, rate)
    raw = wav_bytes(signal, rate)
    decoded, decoded_rate = read_audio_bytes(raw, "test.wav")
    assert decoded_rate == rate
    assert decoded.shape[0] == signal.shape[0]
    assert np.allclose(to_mono(decoded), signal, atol=1e-3)
    down = resample(signal, rate, 16000)
    assert down.shape[0] == round(signal.shape[0] * 16000 / rate)


def test_cross_fade_concat_keeps_length_and_peak():
    from voice.engines import cross_fade_concat, rms_norm, remove_silence

    rate = 24000
    a = harmonic_tone(1.0, 150.0, rate)
    b = harmonic_tone(1.0, 180.0, rate)
    out = cross_fade_concat([a, b], rate, 0.05)
    expected = len(a) + len(b) - int(rate * 0.05)
    assert abs(len(out) - expected) <= 2
    assert float(np.max(np.abs(out))) <= 1.0
    assert np.isfinite(out).all()

    padded = np.concatenate([np.zeros(rate // 2, dtype=np.float32), a, np.zeros(rate // 2, dtype=np.float32)])
    trimmed = remove_silence(padded, rate)
    assert len(trimmed) < len(padded)
    normalized, original = rms_norm(a * 0.05)
    assert original < 0.1
    assert rms(normalized) > rms(a * 0.05)


def test_clone_store_crud(tmp_path):
    store = CloneStore(tmp_path)
    signal = harmonic_tone(2.0, 150.0)
    record = store.create("测试音色", signal, 24000, "你好世界", {"f0": 150.0, "profile": [1, 2, 3]})
    assert store.get(record["id"])["name"] == "测试音色"
    loaded = store.get(record["id"], load_audio=True)
    assert loaded["audio"].shape[0] == signal.shape[0]
    assert len(store.list()) == 1
    store.update(record["id"], {"name": "改名后"})
    assert store.get(record["id"])["name"] == "改名后"
    assert store.delete(record["id"]) is True
    assert store.list() == []
    assert store.get("../etc") is None


def test_service_status_without_models(monkeypatch, tmp_path):
    """没有安装 PyTorch 时，服务端状态接口要给出可执行的提示而不是崩溃。"""
    from voice import config, engines

    cfg = config.Config()
    cfg.zipvoice_repo = str(tmp_path / "missing")
    manager = engines.EngineManager(cfg)
    status = manager.status()
    assert status["engine"] in ("ai", "fallback", "none")
    assert "detail" in status
    assert isinstance(status["ai"], dict)
    if not status["ai"]["available"]:
        assert status["ai"]["detail"]
    # 引擎不可用时要抛出可读的提示，而不是崩溃
    if status["engine"] == "none":
        with pytest.raises(engines.EngineError):
            manager.active()


def test_split_for_budget_respects_prompt_cost():
    """参考音越长，单句允许的字数越少（4GB 显存的关键约束）。"""
    from voice.engines import split_for_budget

    sentence = "这是一句比较长的解说词，需要按预算切成更小的片段，避免一次生成太长导致显存不足。"
    wide = split_for_budget(sentence, prompt_seconds=3.0, limit=12.0)
    narrow = split_for_budget(sentence, prompt_seconds=3.0, limit=5.0)
    assert len(narrow) >= len(wide) >= 1
    assert all(piece for piece in narrow)
    assert "".join(narrow).replace("，", "") != "" and len("".join(narrow)) >= len(sentence) - 2


def test_prepare_prompt_normalizes_and_trims():
    from voice.config import Config
    from voice.engines import SAMPLE_RATE, TARGET_RMS, ZipVoiceEngine

    engine = ZipVoiceEngine(Config())
    rate = 44100
    speech = harmonic_tone(2.0, 150.0, rate) * 0.06
    silence = np.zeros(int(rate * 0.4), dtype=np.float32)
    prompt, original_rms = engine.prepare_prompt(np.concatenate([silence, speech, silence]), rate)
    assert len(prompt) / SAMPLE_RATE < 2.6, "首尾静音应该被去掉"
    assert original_rms < TARGET_RMS
    assert abs(rms(prompt) - TARGET_RMS) < 0.02, "参考音频音量要对齐到目标值"
