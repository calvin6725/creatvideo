"""合成结果的后处理：音色匹配 + 成片级润色。

为什么需要它：
AI 克隆模型给出的音色已经接近参考人，但（1）它的频率响应和你的参考录音不完全一致，
（2）直接输出常常偏「干」、音量忽大忽小。这里做三件事：
- 频谱包络匹配：把生成的频响曲线拉向参考录音，去掉「不像」的频段差异；
- 共振峰微调：需要更亮/更低沉时用频谱包络搬迁，而不是简单变调（不会变机器人）；
- 动态与润色：压缩、齿音抑制、轻柔限幅，让结果听起来像成片解说。

所有参数都来自页面上的滑块，默认值对「尽量像原说话人」最优。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import dsp
from .audio_io import db_to_gain, normalize, peak, rms, to_mono

__all__ = ["analyze_clone", "prepare_clone_profile", "finish_audio", "LegacySettings", "speed_factor"]

SPEED_MIN = 0.5
SPEED_MAX = 2.0


def speed_factor(percent: float) -> float:
    """语速百分比 → 倍率（+25% 就是 1.25 倍），并夹到安全区间。"""
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return 1.0
    return float(min(SPEED_MAX, max(SPEED_MIN, 1.0 + value / 100.0)))


@dataclass
class LegacySettings:
    """页面上的滑块（0 表示不改变），沿用旧版界面。"""

    pitch: float = 0.0          # 半音 -12..12
    brightness: float = 0.0     # -100..100
    warmth: float = 0.0         # -100..100
    presence: float = 0.0       # -100..100
    room: float = 0.0           # 0..60 (%)
    breath: float = 0.0         # 0..100
    speed: float = 0.0          # -25..25 (%)
    resonance: float = 0.0      # -100..100（共振峰搬迁）
    formant: float = 0.0        # -100..100（显式共振峰，优先于 resonance）
    deess: float = 35.0         # 0..100 齿音抑制强度
    style: str = "natural"      # natural / broadcast / warm / deep / soft
    sapiVoice: str = ""
    volume: float = 0.0         # -12..12 dB

    @classmethod
    def from_dict(cls, data: dict | None) -> "LegacySettings":
        data = data or {}
        base = cls()
        for field_name in base.__dataclass_fields__:  # type: ignore[attr-defined]
            if field_name in data and data[field_name] is not None:
                value = data[field_name]
                current = getattr(base, field_name)
                if isinstance(current, str):
                    setattr(base, field_name, str(value))
                else:
                    try:
                        setattr(base, field_name, float(value))
                    except (TypeError, ValueError):
                        pass
        return base


def analyze_clone(audio: np.ndarray, rate: int) -> dict:
    """分析参考音频，得到描述音色的一组特征（会存进音色库）。"""
    mono = to_mono(audio).astype(np.float32)
    if mono.size == 0:
        return {"duration": 0.0, "f0": 0.0, "level": 0.0, "profile": []}
    times, f0 = dsp.f0_track(mono, rate)
    voiced = f0[f0 > 0]
    median = float(np.median(voiced)) if voiced.size else 0.0
    low = float(np.percentile(voiced, 10)) if voiced.size else 0.0
    high = float(np.percentile(voiced, 90)) if voiced.size else 0.0
    profile = dsp.spectral_profile(mono, rate).tolist()
    level = rms(mono)
    voiced_ratio = float(np.count_nonzero(f0 > 0)) / max(1, f0.size)
    return {
        "duration": round(mono.size / rate, 3),
        "rate": rate,
        "f0": round(median, 2),
        "f0_low": round(low, 2),
        "f0_high": round(high, 2),
        "f0_range": round(high - low, 2),
        "level": round(level, 5),
        "voiced_ratio": round(voiced_ratio, 3),
        "profile": [round(v, 2) for v in profile],
    }


def prepare_clone_profile(clone: Optional[dict], audio: np.ndarray, rate: int, settings: dict) -> dict:
    """整理出后处理要用的目标特征。

    clone 存在时用参考音色的特征；否则退化为「自身特征」，只做润色不做匹配。
    """
    target = {}
    if clone:
        features = clone.get("features") or {}
        target["f0"] = float(features.get("f0") or 0.0)
        target["profile"] = np.asarray(features.get("profile") or [], dtype=np.float32)
        target["level"] = float(features.get("level") or 0.0)
    if target.get("profile") is None or np.asarray(target["profile"]).size == 0:
        own = analyze_clone(audio, rate)
        target["f0"] = own["f0"]
        target["profile"] = np.asarray(own["profile"], dtype=np.float32)
        target["level"] = own["level"]
        target["self"] = True
    else:
        target["self"] = False
    for key in ("eqStrength", "formantScale"):
        if key in settings and settings[key] is not None:
            target[key] = float(settings[key])
    return target


def finish_audio(
    audio: np.ndarray,
    rate: int,
    profile: dict,
    settings: dict,
    use_formant_shift: bool = True,
    gentle: bool = False,
) -> tuple[np.ndarray, int]:
    """对合成结果做音色匹配与润色，返回 (音频, 采样率)。

    gentle=True 用于 AI 克隆结果：克隆模型本身已经贴合参考人的音色，
    再叠加共振峰搬迁 / 重压缩容易发闷、发死板，所以只做很轻的润色。
    """
    cfg = LegacySettings.from_dict(settings)
    data = to_mono(audio).astype(np.float32)
    if data.size == 0:
        return data, rate
    data = normalize(data, target_db=-6.0)

    # 0) 语速（变速不变调）：放在最前面，后面的 EQ / 压缩都在最终时长上做
    factor = speed_factor(cfg.speed)
    if abs(factor - 1.0) > 0.005:
        data = dsp.time_stretch(data, rate, factor)

    # 1) 音高微调（PSOLA，时长不变）
    if abs(cfg.pitch) > 0.05:
        data = dsp.psola(data, rate, 2 ** (cfg.pitch / 12.0))

    # 2) 共振峰 / 音色明暗：优先用显式共振峰，否则用 resonance 滑块
    raw_scale = 1.0
    if abs(cfg.formant) > 0.5:
        raw_scale = 2 ** (cfg.formant / 100.0 * 0.6)
    elif abs(cfg.resonance) > 0.5:
        raw_scale = 2 ** (cfg.resonance / 100.0 * 0.5)
    target_formant = float(profile.get("formantScale") or 0.0)
    if target_formant > 0:
        raw_scale *= target_formant
    auto_scale = 1.0
    if use_formant_shift and not profile.get("self"):
        f0_target = float(profile.get("f0") or 0.0)
        times, f0 = dsp.f0_track(data, rate)
        voiced = f0[f0 > 0]
        own = float(np.median(voiced)) if voiced.size else 0.0
        if f0_target > 0 and own > 0:
            ratio = f0_target / own
            # 只做「温和」补偿：把共振峰往参考人的声道长度方向挪一点
            auto_scale = float(np.clip(ratio ** 0.35, 0.82, 1.22))
    scale = float(np.clip(raw_scale * auto_scale if use_formant_shift else raw_scale, 0.65, 1.55))
    if abs(scale - 1.0) > 0.01:
        data = dsp.resample_formants(data, rate, scale)

    # 3) 频率响应匹配目标音色 + 明暗 / 冷暖 / 存在感
    target_profile = profile.get("profile")
    if target_profile is not None and np.asarray(target_profile).size:
        default_strength = (0.35 if profile.get("self") else 0.55)
        if gentle and not profile.get("self"):
            default_strength = 0.3  # 克隆结果只做轻微频响对齐
        strength = float(profile.get("eqStrength") or default_strength)
        data = dsp.eq_matches(data, rate, np.asarray(target_profile, dtype=np.float32), strength=strength)
    if abs(cfg.warmth) > 1:
        data = dsp.shelf(data, rate, 220.0, cfg.warmth / 100.0 * 5.0, high=False)
    if abs(cfg.brightness) > 1:
        data = dsp.shelf(data, rate, 3600.0, cfg.brightness / 100.0 * 5.0, high=True)
    if abs(cfg.presence) > 1:
        data = dsp.peaking(data, rate, 1750.0, cfg.presence / 100.0 * 4.0, 1.1)
    if cfg.style == "broadcast":
        data = dsp.boost_clarity(data, rate, 1.0)
    elif cfg.style == "warm":
        data = dsp.shelf(data, rate, 250.0, 2.0, high=False)
    elif cfg.style == "deep":
        data = dsp.shelf(data, rate, 180.0, 2.6, high=False)
        data = dsp.peaking(data, rate, 3200.0, -1.4, 0.9)
    elif cfg.style == "soft":
        data = dsp.shelf(data, rate, 5200.0, -2.2, high=True)
        data = dsp.add_breath(data, rate, 0.03)

    # 4) 齿音抑制 + 动态 + 气声 + 空间
    deess_scale = 0.55
    if factor > 1.15:  # 念得快时齿音本来就更短，压狠了会发闷
        deess_scale *= max(0.55, 1.0 - (factor - 1.15) * 0.9)
    data = dsp.de_ess(data, rate, strength=float(np.clip(cfg.deess / 100.0, 0.0, 1.0)) * deess_scale)
    if gentle:
        # 轻压缩：保留说话人的自然起伏，只把忽大忽小收一收
        data = dsp.compress(data, rate, threshold_db=-24.0, ratio=1.6, makeup=1.1)
    else:
        data = dsp.compress(data, rate)
    if cfg.breath > 1:
        data = dsp.add_breath(data, rate, cfg.breath / 100.0 * 0.06)
    if cfg.room > 0.5:
        data = dsp.add_room(data, rate, cfg.room / 100.0 * 0.5)

    # 5) 收尾：音量归一 + 轻柔限幅
    if abs(cfg.volume) > 0.05:
        data = data * db_to_gain(cfg.volume)
    data = normalize(data, target_db=-1.5)
    if peak(data) > 0.985:
        data = dsp.limit_soft(data, 0.985)
    return data.astype(np.float32), rate
