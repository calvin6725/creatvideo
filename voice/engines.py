"""AI 声音克隆引擎。

主引擎：ZipVoice（小米 k2-fsa 开源，123M 参数，流匹配零样本克隆）
- 只要 3~10 秒参考音频 + 对应文字，就能用参考人的音色朗读任意文字；
- 模型很小，4GB 显存（GTX 1050 Ti）可以完整跑在 GPU 上；
- 单句（参考 + 生成）控制在 ~12 秒以内，显存不足会自动拆得更小重试。

备选引擎：Windows SAPI + 后处理（没装 PyTorch 时仍可用）。

引擎都是「懒加载」：只有第一次真正合成时才把模型读进显存，
平时打开页面、导入音频不会占用显卡。
"""

from __future__ import annotations

import gc
import json
import logging
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from .audio_io import read_audio_bytes, resample, to_mono
from .config import Config, find_model_dir
from .segment import split_text_for_tts

log = logging.getLogger("voice.engine")

ProgressFn = Optional[Callable[[float, str], None]]

SAMPLE_RATE = 24000  # ZipVoice 输出 24kHz
TARGET_RMS = 0.1
FEAT_SCALE = 0.1


@dataclass
class EngineStatus:
    kind: str
    available: bool
    detail: str
    device: str = "cpu"
    dtype: str = "fp32"
    model: str = ""


class EngineError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 音频小工具（不依赖 torch）
# --------------------------------------------------------------------------- #
def cross_fade_concat(chunks: List[np.ndarray], rate: int, fade: float = 0.08) -> np.ndarray:
    """把多句合成结果拼接，接缝处做 80 毫秒等功率交叉淡化，避免爆音。"""
    chunks = [np.asarray(c, dtype=np.float32) for c in chunks if c is not None and len(c) > 16]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    if len(chunks) == 1:
        return _limit(chunks[0])
    fade_n = max(16, int(rate * fade))
    total = sum(len(c) for c in chunks) - fade_n * (len(chunks) - 1)
    out = np.zeros(max(total, 0), dtype=np.float32)
    cursor = 0
    for index, chunk in enumerate(chunks):
        if index > 0:
            cursor -= fade_n
            ramp = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
            out[cursor : cursor + fade_n] *= ramp[::-1]
            head = chunk[:fade_n] * ramp
            out[cursor : cursor + fade_n] += head[: max(0, min(fade_n, len(out) - cursor))]
            chunk = chunk[fade_n:]
            cursor += fade_n
        end = min(len(out), cursor + len(chunk))
        if end > cursor:
            out[cursor:end] = chunk[: end - cursor]
        cursor = end
    return _limit(out)


def _limit(audio: np.ndarray, ceiling: float = 0.99) -> np.ndarray:
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > ceiling > 0:
        audio = audio * (ceiling / peak)
    return np.asarray(audio, dtype=np.float32)


def rms_norm(audio: np.ndarray, target: float = TARGET_RMS) -> tuple[np.ndarray, float]:
    """与 ZipVoice 官方实现一致：只放大不衰减，返回原始音量用于还原。"""
    current = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))) if audio.size else 0.0
    if current > 0 and current < target:
        audio = (audio * (target / current)).astype(np.float32)
    return audio, current


def remove_silence(audio: np.ndarray, rate: int, threshold_db: float = -40.0, trail_ms: int = 200) -> np.ndarray:
    """去掉参考音频首尾的静音（多余静音会让克隆模型「学不到话」）。"""
    if audio.size == 0:
        return audio
    window = max(1, int(rate * 0.02))
    frames = audio.size // window
    if frames < 3:
        return audio
    energy = np.sqrt(np.mean(audio[: frames * window].reshape(frames, window) ** 2, axis=1))
    reference = float(np.percentile(energy, 95)) or 1e-6
    active = np.flatnonzero(energy > reference * (10 ** (threshold_db / 20.0)) * 2.0)
    if active.size == 0:
        return audio
    start = max(0, active[0] * window - int(rate * 0.05))
    stop = min(audio.size, (active[-1] + 1) * window + int(rate * trail_ms / 1000))
    return audio[start:stop]


def split_for_budget(sentence: str, prompt_seconds: float, limit: float) -> List[str]:
    """按「参考 + 生成 ≤ 预算」把一句话再拆小（4GB 显存关键）。"""
    budget = max(2.0, limit - prompt_seconds)
    max_chars = max(8, int(budget * 4.6))  # 中文约每秒 4.6 字（含停顿）
    if len(sentence) <= max_chars:
        return [sentence]
    pieces: List[str] = []
    for piece in split_text_for_tts(sentence, max_chars=max_chars):
        pieces.append(piece)
    return pieces or [sentence]


# --------------------------------------------------------------------------- #
# AI 引擎
# --------------------------------------------------------------------------- #
class ZipVoiceEngine:
    kind = "ai"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.model = None
        self.vocoder = None
        self.tokenizer = None
        self.fbank = None
        self.device = "cpu"
        self.dtype = "fp32"
        self.num_step = 16
        self.guidance_scale = 1.0
        self.warmed = False
        self.model_dir: Optional[Path] = None
        self._helpers = None
        self._error: Optional[str] = None
        self._torch = None

    # ---------------------------------------------------------------- 状态
    def probe(self) -> EngineStatus:
        if self.model is not None:
            return EngineStatus(
                "ai", True,
                f"AI 克隆引擎已就绪（{self.model_dir.name if self.model_dir else 'ZipVoice'} · {self.num_step} 步）",
                self.device, self.dtype, str(self.model_dir or ""),
            )
        try:
            import torch  # noqa: F401
        except Exception:
            return EngineStatus("ai", False, "未安装 PyTorch，运行“安装AI克隆引擎.bat”即可启用")
        model_dir = find_model_dir(self.cfg)
        if model_dir is None:
            return EngineStatus("ai", False, "未找到 ZipVoice 模型文件（models/zipvoice_distill）")
        device = self._pick_device()
        return EngineStatus(
            "ai", True,
            f"AI 克隆引擎可用（{model_dir.name} · {device}），首次合成时载入模型",
            device, self._dtype_name(), str(model_dir),
        )

    def _dtype_name(self) -> str:
        return "fp16" if str(self.cfg.dtype).lower() in ("fp16", "half") else "fp32"

    def _pick_device(self) -> str:
        if self.cfg.device in ("cpu", "cuda"):
            return self.cfg.device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def device_report(self) -> dict:
        info = {"device": self.device, "cuda": False, "gpu": "", "vram_gb": 0.0, "torch": "", "dtype": self._dtype_name()}
        try:
            import torch

            info["torch"] = torch.__version__
            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                info.update(cuda=True, gpu=props.name, vram_gb=round(props.total_memory / 1024 ** 3, 2))
                info["device"] = "cuda"
        except Exception:
            pass
        return info

    # ---------------------------------------------------------------- 载入
    def _ensure_helpers(self):
        if self._helpers is not None:
            return self._helpers
        repo = Path(self.cfg.zipvoice_repo).expanduser()
        if not (repo / "zipvoice").is_dir():
            raise EngineError(
                f"没有找到 ZipVoice 代码目录：{repo}。请运行“安装AI克隆引擎.bat”，"
                "或手动执行 git clone https://github.com/k2-fsa/ZipVoice.git third_party/ZipVoice"
            )
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from zipvoice.models.zipvoice import ZipVoice  # type: ignore
        from zipvoice.models.zipvoice_distill import ZipVoiceDistill  # type: ignore
        from zipvoice.tokenizer.tokenizer import EmiliaTokenizer  # type: ignore
        from zipvoice.utils.checkpoint import load_checkpoint  # type: ignore
        from zipvoice.utils.feature import VocosFbank  # type: ignore

        self._helpers = {
            "VocosFbank": VocosFbank,
            "EmiliaTokenizer": EmiliaTokenizer,
            "ZipVoice": ZipVoice,
            "ZipVoiceDistill": ZipVoiceDistill,
            "load_checkpoint": load_checkpoint,
        }
        return self._helpers

    def load(self, progress: ProgressFn = None) -> None:
        if self.model is not None:
            return
        if self._error:
            raise EngineError(self._error)
        with self.lock:
            if self.model is not None:
                return
            try:
                self._load_locked(progress)
            except Exception as exc:
                self._error = str(exc)
                raise

    def _load_locked(self, progress: ProgressFn = None) -> None:
        import torch

        self._torch = torch
        helpers = self._ensure_helpers()
        model_dir = find_model_dir(self.cfg)
        if model_dir is None:
            raise EngineError("未找到 ZipVoice 模型。请运行“安装AI克隆引擎.bat”下载模型。")
        self.model_dir = model_dir
        self.device = self._pick_device()
        use_fp16 = self._dtype_name() == "fp16" and self.device == "cuda"
        self.dtype = "fp16" if use_fp16 else "fp32"
        torch.set_num_threads(min(6, os.cpu_count() or 2))
        if self.device == "cuda":
            torch.backends.cudnn.benchmark = True

        if progress:
            progress(0.02, "读取模型配置")
        config = json.loads((model_dir / "model.json").read_text(encoding="utf-8"))
        tokenizer = helpers["EmiliaTokenizer"](token_file=str(model_dir / "tokens.txt"))
        tokenizer_config = {"vocab_size": tokenizer.vocab_size, "pad_id": tokenizer.pad_id}
        is_distill = "distill" in model_dir.name
        cls = helpers["ZipVoiceDistill"] if is_distill else helpers["ZipVoice"]
        model = cls(**config["model"], **tokenizer_config)
        checkpoint = model_dir / "model.pt"
        if progress:
            progress(0.1, "载入克隆模型")
        if checkpoint.name.endswith(".safetensors"):
            import safetensors.torch

            safetensors.torch.load_model(model, str(checkpoint))
        else:
            helpers["load_checkpoint"](filename=str(checkpoint), model=model, strict=True)
        model = model.to(self.device)
        if use_fp16:
            model = model.half()
        model.eval()

        if progress:
            progress(0.45, "载入声码器")
        from vocos import Vocos  # type: ignore

        vocos_dir = model_dir / "vocos"
        if (vocos_dir / "config.yaml").is_file():
            vocoder = Vocos.from_hparams(str(vocos_dir / "config.yaml"))
            state = torch.load(str(vocos_dir / "pytorch_model.bin"), weights_only=True, map_location="cpu")
            vocoder.load_state_dict(state)
        else:
            vocoder = Vocos.from_pretrained("charactr/vocos-mel-24khz")
        # 声码器保持 fp32：体积很小，却能让 fp16 模型的特征直接解码，避免精度混用报错
        vocoder = vocoder.to(self.device).eval()

        self.num_step = int(self.cfg.num_step or (8 if is_distill else 16))
        self.guidance_scale = 3.0 if is_distill else 1.0
        self.tokenizer = tokenizer
        self.fbank = helpers["VocosFbank"]()
        self.model = model
        self.vocoder = vocoder
        log.info("ZipVoice 已载入：%s (%s, %s, %d 步)", model_dir, self.device, self.dtype, self.num_step)
        if progress:
            progress(0.7, "载入完成")

    def _empty_cache(self) -> None:
        if self._torch is not None and self.device == "cuda":
            try:
                self._torch.cuda.empty_cache()
            except Exception:
                pass
        gc.collect()

    # ---------------------------------------------------------------- 合成
    def prepare_prompt(self, audio: np.ndarray, rate: int) -> tuple[np.ndarray, float]:
        audio = resample(to_mono(audio), rate, SAMPLE_RATE)
        audio = remove_silence(audio, SAMPLE_RATE)
        audio, original_rms = rms_norm(audio.astype(np.float32), TARGET_RMS)
        return audio, original_rms

    def synthesize_chunk(self, prompt_audio: np.ndarray, prompt_text: str, text: str) -> np.ndarray:
        """单句合成。模型没载入时直接失败（由上层 load 负责）。"""
        import torch

        if self.model is None:
            raise EngineError("模型尚未载入")
        feat_dtype = torch.float16 if self.dtype == "fp16" else torch.float32
        with torch.inference_mode():
            features = self.fbank.extract(np.asarray(prompt_audio, dtype=np.float32), sampling_rate=SAMPLE_RATE)
            features = torch.as_tensor(np.asarray(features)).to(self.device, dtype=feat_dtype)
            features = features.unsqueeze(0) * FEAT_SCALE
            features_lens = torch.tensor([features.size(1)], device=self.device)
            tokens = self.tokenizer.texts_to_token_ids([_punct(text)])
            prompt_tokens = self.tokenizer.texts_to_token_ids([_punct(prompt_text)])
            pred, pred_lens, _, _ = self.model.sample(
                tokens=tokens,
                prompt_tokens=prompt_tokens,
                prompt_features=features,
                prompt_features_lens=features_lens,
                speed=1.0,
                t_shift=0.5,
                duration="predict",
                num_step=self.num_step,
                guidance_scale=self.guidance_scale,
            )
            pred = pred.permute(0, 2, 1) / FEAT_SCALE
            pred = pred[:, :, : int(pred_lens[0])]
            wav = self.vocoder.decode(pred.float()).squeeze(1).clamp(-1, 1)
        return wav[0].float().cpu().numpy().astype(np.float32)

    def synthesize(
        self,
        text: str,
        prompt_audio: np.ndarray,
        prompt_rate: int,
        prompt_text: str,
        progress: ProgressFn = None,
    ) -> tuple[np.ndarray, int]:
        self.load(progress)
        prompt, prompt_rms = self.prepare_prompt(prompt_audio, prompt_rate)
        prompt_seconds = len(prompt) / SAMPLE_RATE
        sentences = split_text_for_tts(text)
        if not sentences:
            raise EngineError("没有可朗读的文字")
        if progress:
            progress(0.8, f"开始合成（{len(sentences)} 句）")
        limits = [float(self.cfg.max_duration)] + [float(v) for v in self.cfg.retry_max_durations]
        chunks: List[np.ndarray] = []
        for index, sentence in enumerate(sentences):
            chunk = self._generate_with_retry(prompt, prompt_text, sentence, prompt_seconds, limits)
            if prompt_rms and prompt_rms < TARGET_RMS:  # 参考音本身很小，保持同样的音量关系
                chunk = chunk * (prompt_rms / TARGET_RMS)
            chunks.append(chunk)
            if progress:
                progress(0.8 + 0.19 * (index + 1) / len(sentences), f"已合成 {index + 1}/{len(sentences)} 句")
        audio = cross_fade_concat(chunks, SAMPLE_RATE, 0.08)
        self._empty_cache()
        return audio, SAMPLE_RATE

    def _generate_with_retry(self, prompt, prompt_text, sentence, prompt_seconds, limits) -> np.ndarray:
        errors: List[str] = []
        budgets = list(limits) + [max(2.0, limits[-1] - 1.0)]
        for index, limit in enumerate(budgets):
            try:
                pieces = split_for_budget(sentence, prompt_seconds, limit)
                return cross_fade_concat(
                    [self.synthesize_chunk(prompt, prompt_text, piece) for piece in pieces], SAMPLE_RATE, 0.05
                )
            except Exception as exc:  # 显存不足 → 缩小分块 / 降精度重试
                message = str(exc)
                errors.append(message)
                if "out of memory" not in message.lower() and "cuda" not in message.lower():
                    raise EngineError(message) from exc
                log.warning("显存不足，改用更小的分块重试：%s", message[:160])
                self._empty_cache()
                if index >= len(limits) - 1:
                    self._switch_to_fp16()
        raise EngineError("显存不足，合成失败：" + (errors[-1] if errors else "未知错误"))

    def _switch_to_fp16(self) -> None:
        """显存实在不够时，把模型换成半精度（1050 Ti 上会慢一点，但能用）。"""
        if self.dtype == "fp16" or self.device != "cuda" or self.model is None:
            return
        try:
            log.warning("显存不足：切换为 fp16 重试")
            self.model = self.model.half()
            self.dtype = "fp16"
            self.warmed = False
            self._empty_cache()
        except Exception as exc:
            log.warning("切换 fp16 失败：%s", exc)

    def warmup(self, progress: ProgressFn = None) -> None:
        if self.warmed or self.model is None:
            return
        try:
            if progress:
                progress(0.75, "预热模型")
            tone = harmonic_probe(SAMPLE_RATE, 1.2)
            self.synthesize_chunk(tone, "你好。", "你好。")
            self.warmed = True
        except Exception as exc:  # 预热失败不影响使用
            log.warning("预热失败：%s", exc)
        finally:
            self._empty_cache()


def harmonic_probe(rate: int, seconds: float = 1.2) -> np.ndarray:
    """一段合成的「人声样」信号，只用于预热分配显存。"""
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    signal = sum(0.4 / h * np.sin(2 * np.pi * 165 * h * t) for h in range(1, 12))
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * t - np.pi / 2)
    return _limit((signal * envelope).astype(np.float32), 0.5)


def _punct(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "。"
    return text if text[-1] in "。！？；…!?;." else text + "。"


# --------------------------------------------------------------------------- #
# 备选引擎（Windows SAPI + 后处理）
# --------------------------------------------------------------------------- #
class SapiEngine:
    """没有 PyTorch 时的备选：Windows 自带朗读 + 本项目的音色后处理。

    它不能克隆音色，但参数（音高 / 共振峰 / 频响）会真实作用到结果上。
    """

    kind = "fallback"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()

    def probe(self) -> EngineStatus:
        if sys.platform != "win32":
            return EngineStatus("fallback", False, "当前系统没有 Windows 本机朗读，只能使用 AI 引擎")
        return EngineStatus("fallback", True, "Windows 本机朗读 + 后处理（无法克隆音色）")

    def voices(self) -> list[dict]:
        if sys.platform != "win32":
            return []
        try:
            return _sapi_voices()
        except Exception:
            return []

    def synthesize(self, text: str, voice: str, progress: ProgressFn = None) -> tuple[np.ndarray, int]:
        if sys.platform != "win32":
            raise EngineError("当前系统不支持 Windows 本机朗读")
        raw = _sapi_speech(text, voice or "")
        audio, rate = read_audio_bytes(raw, "sapi.wav")
        return to_mono(audio), rate


_SAPI_SCRIPT = r"""
param([string]$TextBase64, [string]$Voice)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  if ($Voice) { try { $synth.SelectVoice($Voice) } catch {} }
  $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($TextBase64))
  $path = Join-Path ([IO.Path]::GetTempPath()) ('voice-' + [Guid]::NewGuid().ToString('N') + '.wav')
  $synth.SetOutputToWaveFile($path)
  $synth.Speak($text)
  [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))
} finally {
  try { $synth.Dispose() } catch {}
  if ($path -and (Test-Path -LiteralPath $path)) { Remove-Item -LiteralPath $path -Force }
}
"""


def _powershell() -> str:
    from shutil import which

    for name in ("pwsh", "powershell"):
        path = which(name)
        if path:
            return path
    return "powershell"


def _run_powershell(script: str, args: list[str], timeout: int = 180) -> str:
    import subprocess
    import tempfile

    path = Path(tempfile.gettempdir()) / f"creatvideo_{abs(hash(script)) % 100000}.ps1"
    if not path.exists():
        path.write_text(script, encoding="utf-8-sig")
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path), *args],
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise EngineError(result.stderr.decode("utf-8", "ignore")[:400] or "Windows 本机朗读失败")
    return result.stdout.decode("utf-8", "ignore").strip()


def _sapi_speech(text: str, voice: str) -> bytes:
    import base64

    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    output = _run_powershell(_SAPI_SCRIPT, ["-TextBase64", encoded, "-Voice", voice])
    if not output:
        raise EngineError("Windows 本机朗读没有返回音频")
    return base64.b64decode(output)


def _sapi_voices() -> list[dict]:
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture.Name };"
        "$s.Dispose()"
    )
    output = _run_powershell(script, [], timeout=60)
    voices = []
    for line in output.splitlines():
        name, _, language = line.strip().partition("|")
        if name:
            voices.append({"name": name, "language": language or "Windows"})
    return voices


# --------------------------------------------------------------------------- #
# 引擎管理
# --------------------------------------------------------------------------- #
class EngineManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ai = ZipVoiceEngine(cfg)
        self.fallback = SapiEngine(cfg)

    def status(self) -> dict:
        ai = self.ai.probe()
        fb = self.fallback.probe()
        preferred = str(self.cfg.engine).lower()
        if preferred == "fallback":
            active = fb
        elif preferred == "ai":
            active = ai if ai.available else fb
        else:
            active = ai if ai.available else fb
        report = self.ai.device_report()
        voices = self.fallback.voices() if (fb.available and active.kind == "fallback") else []
        if active.kind == "fallback" and not fb.available:
            detail = ai.detail or "没有可用的语音引擎"
        else:
            detail = active.detail
        return {
            "engine": active.kind if active.available else "none",
            "available": bool(active.available),
            "detail": detail,
            "device": report,
            "ai": {"available": ai.available, "detail": ai.detail, "model": ai.model or ""},
            "fallback": {"available": fb.available, "detail": fb.detail, "voices": voices},
            "settings": {
                "num_step": self.ai.num_step,
                "max_duration": self.cfg.max_duration,
                "dtype": self.ai._dtype_name(),
                "prompt_min": self.cfg.prompt_min_seconds,
                "prompt_max": self.cfg.prompt_max_seconds,
            },
        }

    def active(self):
        preferred = str(self.cfg.engine).lower()
        if preferred == "fallback":
            return self.fallback
        if self.ai.probe().available:
            return self.ai
        if self.fallback.probe().available:
            return self.fallback
        raise EngineError("没有可用的语音引擎：请运行“安装AI克隆引擎.bat”安装 AI 克隆引擎")

    def synthesize(
        self,
        text: str,
        clone: dict | None,
        settings: dict,
        progress: ProgressFn = None,
        engine: str | None = None,
    ) -> tuple[np.ndarray, int, dict]:
        """合成 + 后处理。clone 为 None 时只能走备选引擎。"""
        from .processor import finish_audio, prepare_clone_profile

        if engine == "fallback" or (clone is None and self.cfg.engine == "fallback"):
            fb = self.fallback
            if not fb.probe().available:
                raise EngineError(self.ai.probe().detail or "没有可用的语音引擎")
            voices = fb.voices()
            voice = settings.get("sapiVoice") or (voices[0]["name"] if voices else "")
            audio, rate = fb.synthesize(text, voice, progress)
            profile = prepare_clone_profile(clone, audio, rate, settings)
            audio, rate = finish_audio(audio, rate, profile, settings, use_formant_shift=False)
            return audio, rate, {"engine": "fallback", "detail": "Windows 本机朗读 + 后处理"}

        ai = self.ai
        if not ai.probe().available:
            if self.fallback.probe().available:
                return self.synthesize(text, clone, settings, progress, engine="fallback")
            raise EngineError("AI 克隆引擎不可用：" + ai.probe().detail)
        if clone is None:
            raise EngineError("请先导入一段参考音频并建立克隆音色")
        prompt_audio = clone.get("audio")
        prompt_rate = int(clone.get("rate") or SAMPLE_RATE)
        prompt_text = (clone.get("text") or "").strip()
        if prompt_audio is None or not prompt_text:
            raise EngineError("克隆音色缺少参考音频或参考文字，请重新导入音频建立音色")
        audio, rate = ai.synthesize(text, prompt_audio, prompt_rate, prompt_text, progress)
        profile = prepare_clone_profile(clone, audio, rate, settings)
        audio, rate = finish_audio(audio, rate, profile, settings, use_formant_shift=True)
        return audio, rate, {
            "engine": "ai",
            "detail": f"ZipVoice 克隆 · {ai.dtype} · {ai.device}",
        }
