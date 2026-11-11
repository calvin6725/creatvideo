"""断句、VAD 与「自动挑选最适合做克隆的参考片段」。

参考音频的质量几乎决定了克隆相似度：
- 1.5~8 秒、单人、没有背景音乐、没有明显底噪；
- 包含完整的句子（不能从词中间切）；
- 音量足够、清音比例不过高（气声太多会让模型学不到稳定音色）。

导入整段视频 / 录音（比如 84 秒的「标记音.mp4」）时，这里会自动切成候选片段，
按上述规则打分，挑出最高分的一段作为克隆参考。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from . import dsp
from .audio_io import db_to_gain, resample, rms, to_mono

__all__ = [
    "split_sentences",
    "split_text_for_tts",
    "voice_activity",
    "analyze_reference",
    "pick_best_segment",
    "best_reference_segment",
]

_PUNCT = "。！？；…，、,.!?;:："
_SENTENCE_END = "。！？；…!?;"


def split_sentences(text: str, max_chars: int = 60) -> List[str]:
    """按中文标点切句；过长的句子再按逗号切，仍过长就硬切。"""
    text = (text or "").strip()
    if not text:
        return []
    text = re.sub(r"[ \t\r\n]+", " ", text)
    pieces = re.findall(r"[^。！？；…!?;\n]+[。！？；…!?;]*", text)
    result: List[str] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        result.extend(_chunk_long(piece, max_chars))
    return result


def _chunk_long(piece: str, max_chars: int) -> List[str]:
    if len(piece) <= max_chars:
        return [piece]
    parts = re.findall(r"[^，、,：:]+[，、,：:]*", piece)
    out: List[str] = []
    current = ""
    for part in parts:
        if len(current) + len(part) <= max_chars:
            current += part
        else:
            if current:
                out.append(current)
            current = part
            while len(current) > max_chars:
                out.append(current[:max_chars])
                current = current[max_chars:]
    if current:
        out.append(current)
    return out


def split_text_for_tts(text: str, max_chars: int = 55) -> List[str]:
    """解说用的断句：长句拆短，合成更快也更稳（显存友好的分块依据）。"""
    sentences = split_sentences(text, max_chars=max_chars)
    out: List[str] = []
    for sentence in sentences:
        if not sentence.strip():
            continue
        if not sentence[-1] in _SENTENCE_END:
            sentence = sentence + "。"
        out.append(sentence)
    return out


def voice_activity(samples: np.ndarray, rate: int, frame: float = 0.03, threshold_db: float = -38.0) -> np.ndarray:
    """返回每个帧是否有人声（能量 VAD，够用且快）。"""
    mono = to_mono(samples)
    win = max(1, int(rate * frame))
    count = max(1, len(mono) // win)
    trimmed = mono[: count * win]
    energy = np.sqrt(np.mean(trimmed.reshape(count, win) ** 2, axis=1))
    reference = float(np.percentile(energy, 95)) if energy.size else 0.0
    if reference <= 1e-6:
        return np.zeros(count, dtype=bool)
    threshold = max(reference * db_to_gain(threshold_db - (-20.0)), 1e-5)
    return energy > threshold


@dataclass
class SegmentScore:
    start: float
    stop: float
    score: float
    detail: dict = field(default_factory=dict)


def _segments_from_mask(mask: np.ndarray, frame: float, min_len: float, max_len: float) -> List[Tuple[float, float]]:
    segments: List[Tuple[float, float]] = []
    active = np.flatnonzero(mask)
    if active.size == 0:
        return segments
    start = active[0]
    previous = active[0]
    for index in active[1:]:
        if index - previous > 6:  # 超过 ~0.18 秒静音就断开
            segments.append((start * frame, (previous + 1) * frame))
            start = index
        previous = index
    segments.append((start * frame, (previous + 1) * frame))
    # 太长或太短的段做修正
    out: List[Tuple[float, float]] = []
    for begin, end in segments:
        length = end - begin
        if length < min_len:
            continue
        if length > max_len:
            cursor = begin
            while cursor + max_len <= end:
                out.append((cursor, cursor + max_len))
                cursor += max_len
            if end - cursor >= min_len:
                out.append((cursor, end))
        else:
            out.append((begin, end))
    return out


def analyze_reference(
    samples: np.ndarray,
    rate: int,
    min_len: float = 2.5,
    max_len: float = 9.0,
) -> dict:
    """整段参考音频的分析结果：VAD 段、整体特征、推荐片段。"""
    mono = to_mono(samples)
    mask = voice_activity(mono, rate)
    segments = _segments_from_mask(mask, 0.03, min_len, max_len)
    total = mono.size / max(1, rate)
    if not segments:
        # 没有明显的停顿，直接按长度切
        segments = []
        cursor = 0.0
        while cursor < total - 0.5:
            stop = min(total, cursor + max_len)
            segments.append((cursor, stop))
            cursor = stop
    scored = [score_segment(mono, rate, begin, stop) for begin, stop in segments]
    best = max(scored, key=lambda item: item.score) if scored else None
    return {
        "duration": round(total, 3),
        "rate": rate,
        "segments": [{"start": round(s.start, 2), "stop": round(s.stop, 2), "score": round(s.score, 1), "detail": s.detail} for s in scored],
        "best": None if best is None else {"start": round(best.start, 2), "stop": round(best.stop, 2), "score": round(best.score, 1), "detail": best.detail},
        "voiced_ratio": round(float(np.count_nonzero(mask)) / max(1, mask.size), 3),
        "level": round(rms(mono), 4),
    }


def score_segment(mono: np.ndarray, rate: int, start: float, stop: float) -> SegmentScore:
    """给候选片段打分：长度、音量、稳定性、清音比例、是否有削波。"""
    begin = int(start * rate)
    end = int(stop * rate)
    chunk = mono[begin:end]
    length = chunk.size / max(1, rate)
    if chunk.size < rate * 0.5:
        return SegmentScore(start, stop, 0.0, {"reason": "太短"})
    level = rms(chunk)
    # 基频分析降到 16k：足够判断音高/稳定性，速度提升 2~3 倍
    analysis_rate = 16000
    probe = resample(chunk, rate, analysis_rate) if rate != analysis_rate else chunk
    times, f0 = dsp.f0_track(probe, analysis_rate, hop=0.02)
    voiced = f0[f0 > 0]
    voiced_ratio = float(np.count_nonzero(f0 > 0)) / max(1, f0.size)
    stability = 0.0
    if voiced.size >= 4:
        median = float(np.median(voiced))
        jitter = float(np.median(np.abs(np.diff(np.log2(np.maximum(voiced, 1e-3)))))) if voiced.size > 1 else 0.0
        stability = float(np.clip(1.0 - jitter * 6.0, 0.0, 1.0))
    else:
        median = 0.0
    clipping = float(np.count_nonzero(np.abs(chunk) > 0.985)) / max(1, chunk.size)
    score = 0.0
    # 长度：3~7 秒最理想
    score += 30.0 * float(np.clip(1.0 - abs(length - 5.0) / 5.0, 0.0, 1.0))
    # 音量
    score += 25.0 * float(np.clip((level - 0.01) / 0.12, 0.0, 1.0))
    # 清音比例（越接近自然人声越好，太高说明气声噪声多）
    score += 20.0 * float(np.clip(1.0 - abs(voiced_ratio - 0.62) / 0.5, 0.0, 1.0))
    # 稳定度
    score += 15.0 * stability
    # 削波惩罚
    score -= 40.0 * float(np.clip(clipping * 200.0, 0.0, 1.0))
    # 没有基频（纯噪声/纯音乐）直接判低
    if median <= 0:
        score *= 0.3
    return SegmentScore(
        start,
        stop,
        score,
        {
            "length": round(length, 2),
            "level": round(level, 4),
            "f0": round(median, 1),
            "voiced_ratio": round(voiced_ratio, 2),
            "stability": round(stability, 2),
            "clipping": round(clipping, 4),
        },
    )


def pick_best_segment(analysis: dict) -> Optional[dict]:
    return analysis.get("best")


def best_reference_segment(
    samples: np.ndarray,
    rate: int,
    min_len: float = 2.5,
    max_len: float = 9.0,
) -> tuple[np.ndarray, dict]:
    """返回（最佳片段音频, 分析信息）。找不到合适片段时返回整段。"""
    analysis = analyze_reference(samples, rate, min_len, max_len)
    best = analysis.get("best")
    mono = to_mono(samples)
    if not best:
        return mono, analysis
    begin = int(best["start"] * rate)
    end = int(best["stop"] * rate)
    if end - begin < rate * 0.5:
        return mono, analysis
    return mono[begin:end], analysis
