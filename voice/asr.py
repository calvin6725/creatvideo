"""参考音频自动转写（ASR）。

克隆模型需要知道参考音频「说的是什么」——参考文字和音频对不上时，音色会跑偏，
这是很多在线克隆「听着像换了个人」的常见原因之一。

默认用 faster-whisper（CTranslate2 实现，CPU 就能跑，int8 量化后很快）；
没装时自动尝试 openai-whisper；两者都没有时，接口会提示用户手动填写参考文字。
"""

from __future__ import annotations

import logging
import threading
import numpy as np

from .audio_io import resample, to_mono

log = logging.getLogger("voice.asr")

_LOCK = threading.Lock()
_CACHE: dict = {}


def available() -> tuple[bool, str]:
    from importlib.util import find_spec

    if find_spec("faster_whisper") is not None:
        return True, "faster-whisper（CTranslate2，CPU/GPU 均可）"
    if find_spec("whisper") is not None:
        return True, "openai-whisper"
    return False, "未安装转写模型，可执行 pip install faster-whisper，或手动填写参考文字"


def transcribe(samples: np.ndarray, rate: int, language: str = "zh", model_size: str = "small") -> str:
    """把参考音频转成文字。结果会缓存在内存里（同一段录音只转一次）。"""
    mono = to_mono(samples)
    key = (len(mono), rate, model_size, float(np.abs(mono[:2000]).sum()))
    if key in _CACHE:
        return _CACHE[key]
    with _LOCK:
        text = _transcribe_locked(mono, rate, language, model_size)
    _CACHE[key] = text
    return text


def _transcribe_locked(samples: np.ndarray, rate: int, language: str, model_size: str) -> str:
    audio_16k = resample(samples, rate, 16000)
    try:
        from faster_whisper import WhisperModel

        cache_key = f"faster-whisper-{model_size}"
        model = _CACHE.get(cache_key)
        if model is None:
            try:  # 有 CUDA 时优先 GPU，显存不足更快失败
                model = WhisperModel(model_size, device="cuda", compute_type="float16")
            except Exception:
                model = WhisperModel(model_size, device="cpu", compute_type="int8")
            _CACHE[cache_key] = model
        segments, _ = model.transcribe(
            audio_16k.astype(np.float32),
            language=language,
            vad_filter=False,
            beam_size=1,
            condition_on_previous_text=False,
        )
        return "".join(segment.text for segment in segments).strip()
    except ImportError:
        pass
    try:
        import whisper

        cache_key = f"whisper-{model_size}"
        model = _CACHE.get(cache_key)
        if model is None:
            model = whisper.load_model(model_size)
            _CACHE[cache_key] = model
        result = model.transcribe(audio_16k.astype(np.float32), language=language, fp16=False)
        return str(result.get("text", "")).strip()
    except ImportError:
        raise RuntimeError(
            "没有可用的语音转写模型。请执行 pip install faster-whisper，"
            "或在页面里手动填写参考音频的文字内容。"
        )


def cleanup_reference_text(text: str) -> str:
    """清理转写结果：去掉首尾空白、多余标点。"""
    text = (text or "").strip()
    for pair in ("“”", "「」", "\"\"", "''", "《》"):
        text = text.strip(pair)
    return text.strip()
