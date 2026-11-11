"""服务层流程测试：分析 → 建音色 → 合成（用假引擎，不需要 GPU/模型）。

验证的是「导入音频 → 标记音 → 更新音色 → 朗读」这条链路本身：
选段、参考文字校验、音色落盘、合成后处理、WAV 输出。
"""

from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice import service, store as store_module  # noqa: E402
from voice.audio_io import read_audio_bytes, wav_bytes  # noqa: E402


def speech_like(seconds=4.0, rate=16000, f0=150.0):
    t = np.arange(int(seconds * rate)) / rate
    signal = sum(0.4 / h * np.sin(2 * np.pi * f0 * h * t) for h in range(1, 12))
    signal = signal * (0.55 + 0.45 * np.sin(2 * np.pi * 3.2 * t - np.pi / 2))
    signal = signal / max(1e-6, float(np.max(np.abs(signal)))) * 0.6
    return signal.astype(np.float32), rate


def sample_audio(rate=16000) -> bytes:
    speech, _ = speech_like(3.0, rate)
    silence = np.zeros(int(rate * 1.2), dtype=np.float32)
    tail = np.zeros(int(rate * 1.0), dtype=np.float32)
    return wav_bytes(np.concatenate([silence, speech, tail]).astype(np.float32), rate)


class FakeEngine:
    """冒充 ZipVoice：返回一段带「音色」的正弦谐波，验证后处理链路。"""

    def __init__(self):
        self.calls = []

    def synthesize(self, text, clone, settings, progress=None, engine=None):
        from voice.processor import finish_audio, prepare_clone_profile

        self.calls.append({"text": text, "clone": clone, "settings": dict(settings or {}), "engine": engine})
        if progress:
            progress(0.2, "假引擎合成中")
        rate = 24000
        seconds = max(1.0, min(6.0, len(text) / 4.5))
        t = np.arange(int(seconds * rate)) / rate
        audio = sum(0.3 / h * np.sin(2 * np.pi * 165 * h * t) for h in range(1, 10)).astype(np.float32)
        profile = prepare_clone_profile(clone, audio, rate, settings or {})
        audio, rate = finish_audio(audio, rate, profile, settings or {})
        if progress:
            progress(0.95, "假引擎完成")
        return audio, rate, {"engine": "ai", "detail": "测试引擎"}

    def status(self):
        return {"engine": "ai", "detail": "测试引擎"}


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    clone_dir = tmp_path / "clones"
    upload_dir = tmp_path / "uploads"
    job_dir = tmp_path / "jobs"
    upload_dir.mkdir(parents=True)
    job_dir.mkdir(parents=True)
    fake = FakeEngine()
    monkeypatch.setattr(service, "STORE", store_module.CloneStore(clone_dir))
    monkeypatch.setattr(service, "ENGINE", fake)
    monkeypatch.setattr(service, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(service, "JOB_DIR", job_dir)
    return fake


def test_analyze_and_create_clone(isolated):
    raw = sample_audio()
    info = service.handle_analyze(raw, "录音.wav", want_text=False)
    assert info["token"]
    assert info["best"]["stop"] - info["best"]["start"] >= 2.0
    assert len(info["segments"]) >= 1
    assert info["suggestedName"]

    clone = service.handle_create_clone({
        "token": info["token"],
        "name": "测试克隆",
        "text": "这是参考音频里说的话。",
        "start": info["best"]["start"],
        "stop": info["best"]["stop"],
    })
    assert clone["name"] == "测试克隆"
    assert clone["rate"] == service.REFERENCE_RATE
    assert clone["features"]["f0"] > 60
    assert 1.0 <= clone["seconds"] <= 12.0
    assert len(service.STORE.list()) == 1

    saved = service.STORE.get(clone["id"], load_audio=True)
    assert saved["audio"] is not None
    assert saved["audio"].ndim == 1


def test_clone_requires_reference_text(isolated):
    info = service.handle_analyze(sample_audio(), "录音.wav", want_text=False)
    with pytest.raises(service.HttpError) as excinfo:
        service.handle_create_clone({
            "token": info["token"], "name": "缺文字", "text": "",
            "start": info["best"]["start"], "stop": info["best"]["stop"],
        })
    assert excinfo.value.status == 400
    assert "参考" in str(excinfo.value)


def test_synthesize_uses_clone_and_returns_wav(isolated):
    info = service.handle_analyze(sample_audio(), "录音.wav", want_text=False)
    clone = service.handle_create_clone({
        "token": info["token"], "name": "测试克隆", "text": "参考文字。",
        "start": info["best"]["start"], "stop": info["best"]["stop"],
    })
    payload, meta = service.synthesize(
        "你好，这是用克隆音色朗读的一整句话。", clone["id"],
        {"pitch": 2, "formant": 15, "style": "broadcast", "deess": 40}, engine="ai",
    )
    assert meta["engine"] == "ai"
    assert payload[:4] == b"RIFF"
    audio, rate = read_audio_bytes(payload, "out.wav")
    assert rate == 24000
    assert audio.shape[0] / rate > 0.5
    assert float(np.max(np.abs(audio))) <= 1.0
    assert isolated.calls and isolated.calls[-1]["clone"]["id"] == clone["id"]
    # 参考音频与参考文字必须一起传给引擎（这是「像不像」的关键）
    assert isolated.calls[-1]["clone"]["text"] == "参考文字。"
    assert isolated.calls[-1]["settings"]["pitch"] == 2


def test_synthesize_rejects_bad_input(isolated):
    with pytest.raises(service.HttpError):
        service.synthesize("", None, {}, engine="ai")
    with pytest.raises(service.HttpError) as excinfo:
        service.synthesize("你好", "不存在的音色", {}, engine="ai")
    assert excinfo.value.status == 404
    with pytest.raises(service.HttpError):
        service.synthesize("字" * (service.CONFIG.max_text_chars + 1), None, {}, engine="ai")


def test_async_job_flow(isolated):
    info = service.handle_analyze(sample_audio(), "录音.wav", want_text=False)
    clone = service.handle_create_clone({
        "token": info["token"], "name": "任务音色", "text": "参考。",
        "start": info["best"]["start"], "stop": info["best"]["stop"],
    })
    job = service.start_job("第一句。第二句。", clone["id"], {"pitch": 0}, "ai")
    for _ in range(200):
        state = service.JOBS[job["id"]]
        if state["status"] in ("done", "error"):
            break
        import time

        time.sleep(0.05)
    state = service.JOBS[job["id"]]
    assert state["status"] == "done", state
    wav_path = service.JOB_DIR / f"{job['id']}.wav"
    assert wav_path.is_file() and wav_path.stat().st_size > 44
    with wave.open(io.BytesIO(wav_path.read_bytes()), "rb") as handle:
        assert handle.getframerate() == 24000
        assert handle.getnframes() > 0


def test_static_file_guard():
    """静态文件服务不能越出项目目录。"""
    assert service.APP_DIR == ROOT or str(service.APP_DIR) == str(ROOT)
