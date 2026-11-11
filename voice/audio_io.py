"""音频读写、重采样与基础度量（只依赖 numpy）。"""

from __future__ import annotations

import io
import struct
import subprocess
import wave
from pathlib import Path
from typing import Tuple

import numpy as np

from .config import ffmpeg_path

__all__ = [
    "read_audio",
    "read_audio_bytes",
    "write_wav",
    "wav_bytes",
    "resample",
    "to_mono",
    "rms",
    "peak",
    "normalize",
    "db_to_gain",
    "trim_silence",
]


def db_to_gain(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def _decode_pcm(raw: bytes, width: int, channels: int) -> np.ndarray:
    if width == 1:  # 8-bit 无符号
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        data = (data - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        buf = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        value = buf[:, 0] | (buf[:, 1] << 8) | (buf[:, 2] << 16)
        value = np.where(value & 0x800000, value - (1 << 24), value)
        data = value.astype(np.float32) / float(1 << 23)
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(f"不支持的位深：{width * 8} bit")
    if channels > 1:
        data = data.reshape(-1, channels)
    return data


def _read_wav_bytes(raw: bytes) -> Tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(raw), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    return _decode_pcm(frames, width, channels), rate


def _read_wav_float(raw: bytes) -> Tuple[np.ndarray, int] | None:
    """处理 wave 模块不认识的 IEEE float / 24bit 等 WAV。"""
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None
    pos = 12
    fmt = None
    data = None
    while pos + 8 <= len(raw):
        tag = raw[pos : pos + 4]
        size = struct.unpack("<I", raw[pos + 4 : pos + 8])[0]
        body = raw[pos + 8 : pos + 8 + size]
        if tag == b"fmt ":
            fmt = body
        elif tag == b"data":
            data = body
        pos += 8 + size + (size % 2)
    if fmt is None or data is None or len(fmt) < 16:
        return None
    audio_format, channels, rate = struct.unpack("<HH I", fmt[:8])
    bits = struct.unpack("<H", fmt[14:16])[0]
    if audio_format == 3 and bits == 32:
        samples = np.frombuffer(data, dtype="<f4").astype(np.float32)
        if channels > 1:
            samples = samples.reshape(-1, channels)
        return samples, rate
    if audio_format == 1:
        return _decode_pcm(data, bits // 8, channels), rate
    return None


def _ffmpeg_decode(raw: bytes, suffix: str = ".bin") -> Tuple[np.ndarray, int]:
    exe = ffmpeg_path()
    if not exe:
        raise RuntimeError(
            "这个音频格式需要 ffmpeg 才能读取（mp3 / m4a / mp4 / webm 等）。"
            "请安装 ffmpeg，或执行 pip install imageio-ffmpeg"
        )
    cmd = [exe, "-v", "error", "-i", "pipe:0", "-f", "wav", "-acodec", "pcm_s16le", "pipe:1"]
    result = subprocess.run(cmd, input=raw, capture_output=True, timeout=300)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(result.stderr.decode("utf-8", "ignore")[:400] or "无法解码音频")
    return _read_wav_bytes(result.stdout)


def read_audio_bytes(raw: bytes, name: str = "") -> Tuple[np.ndarray, int]:
    """把任意音频字节解码成 (样本, 采样率)，样本保留多声道（形状 frames x channels）。"""
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        try:
            return _read_wav_bytes(raw)
        except Exception:
            parsed = _read_wav_float(raw)
            if parsed is not None:
                return parsed
    suffix = Path(name).suffix.lower() if name else ""
    return _ffmpeg_decode(raw, suffix)


def read_audio(path: str | Path) -> Tuple[np.ndarray, int]:
    return read_audio_bytes(Path(path).read_bytes(), str(path))


def to_mono(samples: np.ndarray) -> np.ndarray:
    if samples.ndim == 1:
        return np.ascontiguousarray(samples, dtype=np.float32)
    return np.ascontiguousarray(samples.mean(axis=1), dtype=np.float32)


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def peak(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.max(np.abs(samples)))


def normalize(samples: np.ndarray, target_db: float = -1.0, floor: float = 1e-6) -> np.ndarray:
    current = peak(samples)
    if current < floor:
        return samples.astype(np.float32, copy=True)
    return (samples * (db_to_gain(target_db) / current)).astype(np.float32)


def wav_bytes(samples: np.ndarray, sample_rate: int, bit_depth: int = 16) -> bytes:
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 1:
        data = data[:, None]
    channels = data.shape[1]
    clipped = np.clip(data, -1.0, 1.0)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(bit_depth // 8)
        handle.setframerate(int(sample_rate))
        if bit_depth == 16:
            handle.writeframes((clipped * 32767.0).astype("<i2").tobytes())
        elif bit_depth == 24:
            value = (clipped * float(1 << 23)).astype(np.int32).reshape(-1)
            packed = np.empty((value.size, 3), dtype=np.uint8)
            packed[:, 0] = value & 0xFF
            packed[:, 1] = (value >> 8) & 0xFF
            packed[:, 2] = (value >> 16) & 0xFF
            handle.writeframes(packed.tobytes())
        else:
            handle.writeframes((clipped * float(1 << 31)).astype("<i4").tobytes())
    return buffer.getvalue()


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int, bit_depth: int = 16) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(wav_bytes(samples, sample_rate, bit_depth))
    return target


def _lowpass(samples: np.ndarray, cutoff_ratio: float, taps: int = 101) -> np.ndarray:
    cutoff_ratio = float(np.clip(cutoff_ratio, 0.02, 0.995))
    n = np.arange(taps) - (taps - 1) / 2.0
    kernel = 2 * cutoff_ratio * np.sinc(2 * cutoff_ratio * n) * np.kaiser(taps, 8.0)
    kernel /= kernel.sum()
    padded = np.pad(samples, (taps // 2, taps // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid").astype(np.float32)


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """带宽有保障的线性插值重采样（降采样前先做凯泽窗低通）。"""
    data = np.asarray(samples, dtype=np.float32)
    if source_rate == target_rate or data.size == 0:
        return np.ascontiguousarray(data, dtype=np.float32)
    ratio = target_rate / float(source_rate)
    if ratio < 1.0:
        data = _lowpass(data, 0.45 * ratio)
    length = max(1, int(round(data.shape[0] * ratio)))
    positions = np.linspace(0.0, data.shape[0] - 1.0, length, dtype=np.float64)
    left = np.floor(positions).astype(np.int64)
    right = np.minimum(left + 1, data.shape[0] - 1)
    frac = (positions - left).astype(np.float32)
    if data.ndim == 1:
        return (data[left] * (1 - frac) + data[right] * frac).astype(np.float32)
    frac = frac[:, None]
    return (data[left] * (1 - frac) + data[right] * frac).astype(np.float32)


def trim_silence(
    samples: np.ndarray,
    sample_rate: int,
    threshold_db: float = -42.0,
    edge: float = 0.05,
) -> np.ndarray:
    """去掉首尾静音，保留一点点余量。"""
    mono = to_mono(samples)
    if mono.size == 0:
        return samples
    window = max(1, int(sample_rate * 0.02))
    frames = mono.size // window
    if frames < 3:
        return samples
    energy = np.sqrt(np.mean(np.square(mono[: frames * window].reshape(frames, window)), axis=1))
    keep = energy > rms(mono) * db_to_gain(threshold_db - (-20.0))
    active = np.flatnonzero(keep)
    if active.size == 0:
        return samples
    start = max(0, active[0] * window - int(sample_rate * edge))
    stop = min(samples.shape[0], (active[-1] + 1) * window + int(sample_rate * edge))
    return samples[start:stop]
