"""语音信号处理核心（纯 numpy，不依赖 librosa / scipy）。

包含：
- f0_track：基频跟踪（YIN 类似算法 + 八度纠错 + 中值平滑）
- psola：TD-PSOLA 变调，只改音高不改语速，保留共振峰（比直接改采样率自然得多）
- resample_formants：频谱包络搬迁（音色/声道长度变换）
- spectral_profile / eq_matches：把长时频谱包络拉向参考音色
- peaking / shelf / highpass：STFT 域实现的滤波器，速度快且无块状感
- de_ess / compress / limit_soft / add_breath / add_room：成片级后处理

输入输出统一为 float32 单声道。
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "frames",
    "f0_track",
    "harmonic_intervals",
    "psola",
    "resample_linear",
    "resample_formants",
    "spectral_profile",
    "eq_matches",
    "peaking",
    "shelf",
    "highpass",
    "bandpass_energy",
    "de_ess",
    "compress",
    "limit_soft",
    "add_breath",
    "add_room",
    "pitch_shift",
    "boost_clarity",
    "stft_filter",
]

_EPS = 1e-9


def frames(samples: np.ndarray, size: int, hop: int) -> np.ndarray:
    count = max(1, 1 + (len(samples) - size) // hop)
    return np.lib.stride_tricks.as_strided(
        samples,
        shape=(count, size),
        strides=(samples.strides[0] * hop, samples.strides[0]),
        writeable=False,
    )


def _frame_rms(fr: np.ndarray) -> np.ndarray:
    return np.sqrt(np.mean(np.square(fr, dtype=np.float64), axis=1))


# --------------------------------------------------------------------------- #
# STFT 工具
# --------------------------------------------------------------------------- #
def _stft_process(samples: np.ndarray, gain_fn, n_fft: int = 1024, hop: int = 256) -> np.ndarray:
    """重叠相加：对每一帧的频谱乘以 gain_fn(freqs) 给出的增益。"""
    data = np.asarray(samples, dtype=np.float32)
    if data.size == 0:
        return data
    if data.size < n_fft:
        data = np.pad(data, (0, n_fft - data.size))
    pad = n_fft // 2
    padded = np.pad(data, (pad, pad))
    count = 1 + (len(padded) - n_fft) // hop
    window = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / 1.0)  # 归一化频率占位，实际用 rate 归一化
    del freqs
    gain = np.asarray(gain_fn(n_fft), dtype=np.complex64)
    out = np.zeros(len(padded), dtype=np.float32)
    weight = np.zeros(len(padded), dtype=np.float32)
    for i in range(count):
        start = i * hop
        spectrum = np.fft.rfft(padded[start : start + n_fft] * window)
        rebuilt = np.fft.irfft(spectrum * gain, n=n_fft).astype(np.float32)
        out[start : start + n_fft] += rebuilt * window
        weight[start : start + n_fft] += window ** 2
    valid = weight > 1e-5
    out[valid] /= weight[valid]
    return out[pad : pad + data.size].astype(np.float32)


def stft_filter(samples: np.ndarray, rate: int, response: np.ndarray | None) -> np.ndarray:
    if response is None:
        return np.asarray(samples, dtype=np.float32).copy()
    return _stft_process(samples, lambda n_fft: np.asarray(response, dtype=np.complex64))


# --------------------------------------------------------------------------- #
# 基频
# --------------------------------------------------------------------------- #
def _yin_frame(frame: np.ndarray, rate: int, fmin: float, fmax: float, threshold: float) -> float:
    size = len(frame)
    frame = frame - frame.mean()
    energy = float(np.dot(frame, frame))
    if energy < 1e-6:
        return 0.0
    max_lag = int(rate / fmin)
    min_lag = max(2, int(rate / fmax))
    max_lag = min(max_lag, size // 2)
    if max_lag <= min_lag + 1:
        return 0.0
    corr = np.correlate(frame, frame, mode="full")[size - 1 :]
    sq = np.concatenate(([0.0], np.cumsum(frame * frame)))
    lags = np.arange(min_lag, max_lag + 1)
    energy0 = np.maximum(sq[size - lags], _EPS)
    energy1 = np.maximum(sq[size] - sq[lags], _EPS)
    cmnd = 1.0 - corr[lags] / np.sqrt(energy0 * energy1)
    count = len(cmnd)
    minima = [
        i for i in range(1, count - 1)
        if cmnd[i] <= cmnd[i - 1] and cmnd[i] < cmnd[i + 1]
    ]
    below = [i for i in minima if cmnd[i] < threshold]
    if below:
        index = below[0]  # 最靠前的低谷 = 最高且可信的音高，避免低八度错误
    else:
        index = int(np.argmin(cmnd))
        if cmnd[index] > 0.55:
            return 0.0
    # 只有 2 倍周期处的低谷「明显更深」时才认为真实基频是低八度
    double = 2 * index
    if double < count and cmnd[double] < cmnd[index] - 0.06 and cmnd[double] < threshold:
        index = double
    candidate = int(lags[index])
    if candidate <= min_lag or candidate > max_lag:
        return 0.0
    index = candidate - min_lag
    lag = float(candidate)
    if 0 < index < len(lags) - 1:
        y0, y1, y2 = cmnd[index - 1], cmnd[index], cmnd[index + 1]
        denom = y0 - 2 * y1 + y2
        if abs(denom) > _EPS:
            lag += float(np.clip(0.5 * (y0 - y2) / denom, -0.5, 0.5))
    return rate / lag


def _median_filter(values: np.ndarray, width: int = 3) -> np.ndarray:
    if values.size < width or width < 2:
        return values
    padded = np.pad(values, (width // 2, width - 1 - width // 2), mode="edge")
    stack = np.stack([padded[i : i + values.size] for i in range(width)], axis=1)
    result = np.zeros(values.size)
    for i in range(values.size):
        if values[i] <= 0:
            continue  # 清音/静音不制造假基频
        row = stack[i][stack[i] > 0]
        result[i] = float(np.median(row)) if row.size else values[i]
    return result


def f0_track(
    samples: np.ndarray,
    rate: int,
    hop: float = 0.005,
    size: float = 0.04,
    fmin: float = 55.0,
    fmax: float = 420.0,
    threshold: float = 0.30,
) -> tuple[np.ndarray, np.ndarray]:
    """返回 (时间轴秒, 基频 Hz)；无声或清音帧为 0。"""
    data = np.asarray(samples, dtype=np.float32)
    win = max(64, int(round(size * rate)))
    step = max(16, int(round(hop * rate)))
    if data.size < win:
        return np.zeros(0), np.zeros(0)
    energy = _frame_rms(frames(data, win, step))
    times = (np.arange(len(energy)) * step + win / 2.0) / rate
    values = np.zeros(len(energy))
    active = energy > max(1e-4, float(np.percentile(energy, 90)) * 0.06)
    for index in np.flatnonzero(active):
        chunk = data[index * step : index * step + win]
        if not np.any(chunk):
            continue
        values[index] = _yin_frame(chunk.astype(np.float64), rate, fmin, fmax, threshold)
    values = _median_filter(values, 3)
    voiced = values > 0
    if np.count_nonzero(voiced) >= 3:
        median = float(np.median(values[voiced]))
        keep = np.abs(np.log2(np.maximum(values, median) / max(median, _EPS))) < 0.6
        values = np.where(voiced & keep, values, 0.0)
    return times, values


def harmonic_intervals(times: np.ndarray, f0: np.ndarray, rate: int, length: int):
    """把帧级基频铺成逐采样点的音高标记（PSOLA 用）。"""
    if f0.size == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    positions: list[int] = []
    periods: list[float] = []
    cursor = 0.0
    index = 0
    while cursor < length:
        while index + 1 < len(times) and times[index + 1] <= cursor / rate:
            index += 1
        freq = float(f0[index]) if index < len(f0) else 0.0
        if freq <= 0:
            cursor += rate * 0.005
            continue
        period = rate / freq
        positions.append(int(round(cursor)))
        periods.append(period)
        cursor += period
    return np.asarray(positions, dtype=np.int64), np.asarray(periods, dtype=np.float64)


# --------------------------------------------------------------------------- #
# 变调 / 共振峰
# --------------------------------------------------------------------------- #
def psola(samples: np.ndarray, rate: int, ratio: float = 1.0) -> np.ndarray:
    """时域基音同步叠加（TD-PSOLA）：只改音高，时长不变，共振峰不被搬走。

    经典实现：每隔「源周期」取一段以基音脉冲（epoch）为中心、长度 2 个周期的
    分析窗，按「源周期 / ratio」的间隔叠加到输出上。ratio > 1 变高，< 1 变低。
    因为每一帧都对齐到真实基音脉冲，相位是连续的，听起来不会像机器人或水声。
    """
    data = np.asarray(samples, dtype=np.float32)
    if abs(ratio - 1.0) < 1e-3 or data.size == 0:
        return data.astype(np.float32, copy=True)
    times, f0 = f0_track(data, rate)
    if f0.size == 0 or not np.any(f0 > 0):
        return resample_linear(data, ratio)
    marks, periods = harmonic_intervals(times, f0, rate, data.size)
    if marks.size == 0:
        return resample_linear(data, ratio)
    period_at = np.zeros(data.size, dtype=np.float32)
    period_at = np.interp(
        np.arange(data.size, dtype=np.float32),
        (times * rate).astype(np.float32),
        np.where(f0 > 0, rate / np.maximum(f0, _EPS), 0.0).astype(np.float32),
    ).astype(np.float32)
    sorted_marks = np.sort(marks)
    max_period = float(periods.max())
    out = np.zeros(data.size + int(max_period * 2) + 8, dtype=np.float32)
    weight = np.zeros(out.shape[0], dtype=np.float32)
    in_pos = 0.0
    out_pos = 0.0
    guard = 0
    while out_pos < data.size and guard < data.size:
        guard += 1
        position = int(np.clip(in_pos, 0, data.size - 1))
        period = float(period_at[position])
        if period <= 1.5:
            in_pos += rate * 0.004
            out_pos += rate * 0.004
            continue
        index = int(np.searchsorted(sorted_marks, in_pos))
        candidates = [i for i in (index - 1, index) if 0 <= i < sorted_marks.size]
        if candidates:
            epoch = int(sorted_marks[min(candidates, key=lambda i: abs(int(sorted_marks[i]) - in_pos))])
        else:
            epoch = int(round(in_pos))
        half = period
        src_left = max(0, epoch - int(round(half)))
        src_right = min(data.size, epoch + int(round(half)))
        if src_right - src_left < 6:
            in_pos += period
            out_pos += period / ratio
            continue
        segment = data[src_left:src_right]
        window = np.hanning(segment.size + 2)[1:-1].astype(np.float32)
        dst_center = out_pos - (epoch - src_left)
        dst_left = int(round(dst_center))
        low = max(0, -dst_left)
        dst_left = max(0, dst_left)
        dst_right = min(out.shape[0], dst_left + segment.size - low)
        if dst_right <= dst_left:
            break
        piece = segment[low : low + (dst_right - dst_left)] * window[low : low + (dst_right - dst_left)]
        out[dst_left:dst_right] += piece
        weight[dst_left:dst_right] += window[low : low + (dst_right - dst_left)]
        in_pos += period
        out_pos += period / ratio
    valid = weight > 1e-3
    out[valid] /= weight[valid]
    tail = min(int(max_period), out.size)
    out[: min(tail, data.size)] = np.where(
        valid[: min(tail, data.size)], out[: min(tail, data.size)], data[: min(tail, data.size)]
    )
    return out[: data.size].astype(np.float32)


def resample_linear(samples: np.ndarray, ratio: float) -> np.ndarray:
    """变速变调（ratio>1 变快变高）。"""
    data = np.asarray(samples, dtype=np.float32)
    if abs(ratio - 1.0) < 1e-4 or data.size == 0:
        return data.copy()
    index = np.arange(0, data.size, ratio)
    return np.interp(index, np.arange(data.size), data).astype(np.float32)


def resample_formants(samples: np.ndarray, rate: int, scale: float, n_fft: int = 1024, hop: int = 256) -> np.ndarray:
    """频谱包络搬迁：共振峰位置乘以 scale（>1 更亮更「小声道」，<1 更低沉）。"""
    data = np.asarray(samples, dtype=np.float32)
    if abs(scale - 1.0) < 1e-3 or data.size < n_fft:
        return data.copy()
    pad = n_fft // 2
    padded = np.pad(data, (pad, pad))
    count = 1 + (len(padded) - n_fft) // hop
    window = np.hanning(n_fft).astype(np.float32)
    out = np.zeros(len(padded), dtype=np.float32)
    weight = np.zeros(len(padded), dtype=np.float32)
    bins = np.arange(n_fft // 2 + 1, dtype=np.float64)
    source = bins / max(scale, _EPS)
    for i in range(count):
        start = i * hop
        spectrum = np.fft.rfft(padded[start : start + n_fft] * window)
        magnitude = np.abs(spectrum)
        phase = spectrum / np.maximum(magnitude, _EPS)
        warped = np.interp(source, bins, magnitude, left=magnitude[0], right=magnitude[-1])
        rebuilt = np.fft.irfft(phase * warped, n=n_fft).astype(np.float32)
        out[start : start + n_fft] += rebuilt * window
        weight[start : start + n_fft] += window ** 2
    valid = weight > 1e-5
    out[valid] /= weight[valid]
    return out[pad : pad + data.size].astype(np.float32)


# --------------------------------------------------------------------------- #
# 频谱包络匹配
# --------------------------------------------------------------------------- #
def _band_matrix(rate: int, n_fft: int, bands: int = 26, fmin: float = 80.0, fmax: float = 11000.0) -> np.ndarray:
    freqs = np.fft.rfftfreq(n_fft, 1.0 / rate)
    edges = np.geomspace(fmin, min(fmax, rate / 2.0 * 0.95), bands + 1)
    matrix = np.zeros((bands, freqs.size), dtype=np.float32)
    for b in range(bands):
        mask = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if not np.any(mask):
            matrix[b, int(np.argmin(np.abs(freqs - (edges[b] + edges[b + 1]) / 2)))] = 1.0
        else:
            matrix[b, mask] = 1.0 / np.count_nonzero(mask)
    return matrix


def spectral_profile(samples: np.ndarray, rate: int, n_fft: int = 1024, bands: int = 26) -> np.ndarray:
    """长时平均频谱（分频段对数能量 dB），用来描述音色。"""
    data = np.asarray(samples, dtype=np.float32)
    if data.size == 0:
        return np.zeros(bands, dtype=np.float32)
    if data.size < n_fft:
        data = np.pad(data, (0, n_fft - data.size))
    hop = n_fft // 2
    count = 1 + (len(data) - n_fft) // hop
    window = np.hanning(n_fft).astype(np.float32)
    matrix = _band_matrix(rate, n_fft, bands)
    acc = np.zeros(bands, dtype=np.float64)
    used = 0
    for i in range(count):
        segment = data[i * hop : i * hop + n_fft] * window
        if float(np.sqrt(np.mean(segment ** 2))) < 1e-4:
            continue
        acc += matrix @ np.abs(np.fft.rfft(segment))
        used += 1
    if used == 0:
        return np.zeros(bands, dtype=np.float32)
    return (20 * np.log10(np.maximum(acc / used, 1e-6))).astype(np.float32)


def eq_matches(
    samples: np.ndarray,
    rate: int,
    target: np.ndarray,
    strength: float = 0.85,
    bands: int = 26,
    max_gain_db: float = 12.0,
    n_fft: int = 1024,
    hop: int = 256,
) -> np.ndarray:
    """把样本的长时频谱包络拉向 target（参考音色的频谱轮廓）。"""
    data = np.asarray(samples, dtype=np.float32)
    target = np.asarray(target, dtype=np.float32)
    if data.size < n_fft or target.size == 0 or strength <= 0:
        return data.copy()
    current = spectral_profile(data, rate, n_fft, bands)
    if not np.any(current):
        return data.copy()
    diff = np.clip((target - current) * strength, -max_gain_db, max_gain_db)
    gains = 10 ** (diff / 20.0)
    matrix = _band_matrix(rate, n_fft, bands)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / rate)
    centers = np.array([
        float(freqs[matrix[b] > 0].mean()) if np.any(matrix[b] > 0) else float(freqs[0])
        for b in range(bands)
    ])
    curve = np.interp(freqs, centers, gains).astype(np.float32)
    return _stft_process(data, lambda n: curve, n_fft, hop)


# --------------------------------------------------------------------------- #
# 滤波器（STFT 域，保证速度）
# --------------------------------------------------------------------------- #
def _biquad(samples: np.ndarray, rate: int, b, a, n_fft: int = 1024) -> np.ndarray:
    freqs = np.fft.rfftfreq(n_fft, 1.0 / rate)
    z = np.exp(-2j * np.pi * freqs / rate)
    numerator = b[0] + b[1] * z + b[2] * z ** 2
    denominator = a[0] + a[1] * z + a[2] * z ** 2
    response = (numerator / denominator).astype(np.complex64)
    return _stft_process(samples, lambda n: response, n_fft)


def peaking(samples: np.ndarray, rate: int, freq: float, gain_db: float, q: float = 1.0) -> np.ndarray:
    if abs(gain_db) < 0.05:
        return samples
    amp = 10 ** (gain_db / 40.0)
    w0 = 2 * np.pi * min(freq, rate * 0.45) / rate
    alpha = np.sin(w0) / (2 * max(q, 0.1))
    cos = np.cos(w0)
    return _biquad(samples, rate, (1 + alpha * amp, -2 * cos, 1 - alpha * amp), (1 + alpha / amp, -2 * cos, 1 - alpha / amp))


def shelf(samples: np.ndarray, rate: int, freq: float, gain_db: float, high: bool = True) -> np.ndarray:
    if abs(gain_db) < 0.05:
        return samples
    amp = 10 ** (gain_db / 40.0)
    w0 = 2 * np.pi * min(freq, rate * 0.45) / rate
    cos, sin = np.cos(w0), np.sin(w0)
    beta = np.sqrt(max(amp, 1e-6)) * sin * np.sqrt(2.0)
    if high:
        b = (amp * ((amp + 1) + (amp - 1) * cos + beta), -2 * amp * ((amp - 1) + (amp + 1) * cos), amp * ((amp + 1) + (amp - 1) * cos - beta))
        a = ((amp + 1) - (amp - 1) * cos + beta, 2 * ((amp - 1) - (amp + 1) * cos), (amp + 1) - (amp - 1) * cos - beta)
    else:
        b = (amp * ((amp + 1) - (amp - 1) * cos + beta), 2 * amp * ((amp - 1) - (amp + 1) * cos), amp * ((amp + 1) - (amp - 1) * cos - beta))
        a = ((amp + 1) + (amp - 1) * cos + beta, -2 * ((amp - 1) + (amp + 1) * cos), (amp + 1) + (amp - 1) * cos - beta)
    return _biquad(samples, rate, b, a)


def highpass(samples: np.ndarray, rate: int, freq: float = 70.0, q: float = 0.7) -> np.ndarray:
    if freq <= 0:
        return np.asarray(samples, dtype=np.float32).copy()
    w0 = 2 * np.pi * min(freq, rate * 0.45) / rate
    alpha = np.sin(w0) / (2 * max(q, 0.1))
    cos = np.cos(w0)
    return _biquad(samples, rate, ((1 + cos) / 2, -(1 + cos), (1 + cos) / 2), (1 + alpha, -2 * cos, 1 - alpha))


def bandpass_energy(samples: np.ndarray, rate: int, freq: float, q: float = 0.8) -> float:
    """某个频带内的能量（分析用，不改变音频）。"""
    n_fft = 1024
    size = min(len(samples), max(n_fft, int(rate * 2)))
    data = np.asarray(samples[:size], dtype=np.float32)
    if data.size < n_fft:
        data = np.pad(data, (0, n_fft - data.size))
    spectrum = np.abs(np.fft.rfft(data * np.hanning(data.size)))
    freqs = np.fft.rfftfreq(data.size, 1.0 / rate)
    width = freq / max(q, 0.1)
    mask = np.abs(freqs - freq) <= width
    if not np.any(mask):
        return 0.0
    return float(np.sqrt(np.mean(spectrum[mask] ** 2)))


def de_ess(samples: np.ndarray, rate: int, strength: float = 0.35) -> np.ndarray:
    """齿音动态抑制：只在 5–9k 能量过强时压一点，避免嘶嘶刺耳。"""
    if strength <= 0:
        return np.asarray(samples, dtype=np.float32)
    data = np.asarray(samples, dtype=np.float32)
    band = highpass(data, rate, 5200.0, 0.6)
    level = np.abs(band)
    smoothed = np.convolve(level, np.ones(256) / 256.0, mode="same")
    reference = float(np.percentile(smoothed, 85)) if smoothed.size else 0.0
    if reference <= 1e-6:
        return data
    excess = np.clip(smoothed / reference - 1.0, 0.0, 1.0)
    return (data - band * (excess * strength)).astype(np.float32)


def compress(
    samples: np.ndarray,
    rate: int,
    threshold_db: float = -20.0,
    ratio: float = 2.6,
    attack: float = 0.005,
    release: float = 0.12,
    makeup: float = 1.6,
) -> np.ndarray:
    """平滑动态压缩：朗读的音量更均匀，更像成片解说。"""
    data = np.asarray(samples, dtype=np.float32)
    if data.size == 0:
        return data
    level = np.abs(data)
    window = max(1, int(rate * 0.01))
    padded = np.pad(level, (0, (-level.size) % window))
    envelope = padded.reshape(-1, window).max(axis=1)
    attack_coef = float(np.exp(-1.0 / max(1.0, attack * rate / window)))
    release_coef = float(np.exp(-1.0 / max(1.0, release * rate / window)))
    smoothed = np.empty_like(envelope)
    value = 0.0
    for i, item in enumerate(envelope):
        coef = attack_coef if item > value else release_coef
        value = coef * value + (1 - coef) * item
        smoothed[i] = value
    threshold = 10 ** (threshold_db / 20.0)
    gain = np.ones_like(smoothed)
    above = smoothed > threshold
    gain[above] = (threshold / np.maximum(smoothed[above], _EPS)) ** (1.0 - 1.0 / ratio)
    gain = np.repeat(gain, window)[: data.size]
    return (data * gain * makeup).astype(np.float32)


def limit_soft(samples: np.ndarray, ceiling: float = 0.97) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float32)
    return (np.tanh(data / ceiling) * ceiling).astype(np.float32)


def add_breath(samples: np.ndarray, rate: int, amount: float = 0.0, seed: int = 7) -> np.ndarray:
    """在谐波间隙填一点高频噪声，减少「合成感 / 金属感」。"""
    data = np.asarray(samples, dtype=np.float32)
    if amount <= 0 or data.size == 0:
        return data
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(data.size).astype(np.float32)
    noise = highpass(noise, rate, 2200.0, 0.7)
    envelope = np.convolve(np.abs(data), np.ones(64) / 64.0, mode="same")
    return (data + noise * envelope * amount).astype(np.float32)


def add_room(samples: np.ndarray, rate: int, amount: float = 0.0, decay: float = 0.25) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float32)
    if amount <= 0 or data.size == 0:
        return data
    out = data.copy()
    for index, delay_s in enumerate((0.011, 0.019, 0.031, 0.047)):
        delay = int(rate * delay_s)
        if delay >= data.size:
            continue
        gain = amount * float(np.exp(-delay_s / max(decay, 0.01))) * 0.9
        out[delay:] += data[:-delay] * gain * (1.0 - index * 0.18)
    return limit_soft(out)


def pitch_shift(samples: np.ndarray, rate: int, semitones: float) -> np.ndarray:
    if abs(semitones) < 0.05:
        return np.asarray(samples, dtype=np.float32).copy()
    return psola(samples, rate, 2 ** (semitones / 12.0))


def boost_clarity(samples: np.ndarray, rate: int, amount: float = 1.0) -> np.ndarray:
    """存在感 / 清晰度：抬 1.7k，轻削 320 Hz 的浑浊，补一点 7k 空气感。"""
    data = highpass(samples, rate, 65.0)
    data = peaking(data, rate, 320.0, -1.5 * amount, 0.9)
    data = peaking(data, rate, 1750.0, 2.2 * amount, 1.1)
    data = peaking(data, rate, 7200.0, 1.2 * amount, 0.8)
    return data
