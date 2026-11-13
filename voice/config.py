"""运行配置：目录、引擎选择、显存策略。

配置文件 voice/config.json（可选）的键与下面 Config 的字段一致，环境变量可以覆盖：

    CREATVIDEO_ENGINE      auto | ai | fallback
    CREATVIDEO_DEVICE      auto | cuda | cpu
    CREATVIDEO_ZIPVOICE    ZipVoice 仓库目录
    CREATVIDEO_MODEL       ZipVoice 模型目录（含 model.pt / model.json / tokens.txt）

默认按「4GB 显存显卡（GTX 1050 Ti）」做了保守设置：
模型用蒸馏版（8 步、更省显存更快），单句最长 12 秒，fp32 保证稳定，
显存不足时自动降级到更小的分块而不是直接失败。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
CLONE_DIR = DATA_DIR / "clones"
JOB_DIR = DATA_DIR / "jobs"
MODEL_DIR = APP_DIR / "models"
THIRD_PARTY_DIR = APP_DIR / "third_party"
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"


@dataclass
class Config:
    # 引擎：auto 有 AI 就用 AI，否则用轻量引擎
    engine: str = "auto"
    # 设备：auto 会优先 CUDA
    device: str = "auto"
    # 数据类型：fp32 更稳，fp16 更省显存（1050 Ti 上 fp16 计算慢，默认不开）
    dtype: str = "fp32"
    # 推理步数，蒸馏模型默认 8，原版 16
    num_step: int = 0  # 0 = 按模型自动
    # 分块：单块 prompt+生成 的总时长上限（秒），越小越省显存
    max_duration: float = 12.0
    # 显存不足时自动重试的分块上限
    retry_max_durations: list = field(default_factory=lambda: [8.0, 5.0])
    # ZipVoice 仓库与模型
    zipvoice_repo: str = str(THIRD_PARTY_DIR / "ZipVoice")
    zipvoice_model: str = ""  # 留空自动在 models/ 下找
    zipvoice_model_name: str = "zipvoice_distill"
    # 参考音频：推荐长度（秒）
    prompt_min_seconds: float = 2.0
    prompt_max_seconds: float = 8.0
    # 自动转写：whisper（faster-whisper / openai-whisper）、none
    asr: str = "auto"
    asr_model: str = "small"
    # 生成时的采样数上限与随机种子（0 = 每次随机）
    seed: int = 0
    # 服务
    port: int = 9100
    host: str = "127.0.0.1"
    # 允许的朗读文本长度
    max_text_chars: int = 12000

    def path(self, name: str) -> Path:
        return Path(getattr(self, name)).expanduser()

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


_ENV_KEYS = {
    "CREATVIDEO_ENGINE": "engine",
    "CREATVIDEO_DEVICE": "device",
    "CREATVIDEO_DTYPE": "dtype",
    "CREATVIDEO_ZIPVOICE": "zipvoice_repo",
    "CREATVIDEO_MODEL": "zipvoice_model",
    "CREATVIDEO_PORT": "port",
    "CREATVIDEO_HOST": "host",
    "CREATVIDEO_MAX_DURATION": "max_duration",
    "CREATVIDEO_ASR": "asr",
}


def load_config() -> Config:
    values: Dict[str, Any] = {}
    if CONFIG_FILE.exists():
        try:
            values.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    cfg = Config()
    for key, value in values.items():
        if hasattr(cfg, key) and value is not None:
            setattr(cfg, key, value)
    for env, key in _ENV_KEYS.items():
        raw = os.environ.get(env)
        if raw is None or raw == "":
            continue
        current = getattr(cfg, key)
        if isinstance(current, bool):
            setattr(cfg, key, raw.lower() in ("1", "true", "yes", "on"))
        elif isinstance(current, int):
            try:
                setattr(cfg, key, int(raw))
            except ValueError:
                pass
        elif isinstance(current, float):
            try:
                setattr(cfg, key, float(raw))
            except ValueError:
                pass
        else:
            setattr(cfg, key, raw)
    for folder in (DATA_DIR, CLONE_DIR, JOB_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    return cfg


def find_model_dir(cfg: Config) -> Path | None:
    """找到 ZipVoice 模型目录（含 model.pt、model.json、tokens.txt）。"""
    candidates = []
    if cfg.zipvoice_model:
        candidates.append(Path(cfg.zipvoice_model).expanduser())
    candidates.append(MODEL_DIR / cfg.zipvoice_model_name)
    candidates.append(MODEL_DIR / "zipvoice")
    # HuggingFace 缓存里下载过的模型
    hf_home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    snap = Path(hf_home) / "hub"
    if snap.is_dir():
        for repo in snap.glob("models--k2-fsa--ZipVoice*"):
            for name in (cfg.zipvoice_model_name, "zipvoice"):
                candidates.extend(sorted((repo / "snapshots").glob(f"*/{name}"), reverse=True))
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "model.pt").is_file():
            return candidate
    return None


def ffmpeg_path() -> str | None:
    """找一个可用的 ffmpeg（系统安装或 imageio-ffmpeg 自带的）。"""
    found = os.environ.get("CREATVIDEO_FFMPEG")
    if found and Path(found).is_file():
        return found
    from shutil import which

    system = which("ffmpeg")
    if system:
        return system
    try:  # imageio-ffmpeg 是可选依赖，只为读取 mp4/m4a/mp3
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
