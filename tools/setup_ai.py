#!/usr/bin/env python3
"""一键安装 AI 声音克隆引擎（ZipVoice + PyTorch），专为 4GB 显存显卡（GTX 1050 Ti）优化。

做的事情：
1. 在项目里建 .venv 虚拟环境（不污染系统 Python）
2. 安装 PyTorch（默认 CUDA 12.1 版本；装不上或没有 NVIDIA 显卡时退回 CPU 版）
3. 安装 voice/requirements 里的推理依赖
4. 下载 ZipVoice-Distill 模型（蒸馏版，8 步推理，显存占用最低）+ 声码器
5. 检查依赖、打印显卡与显存信息

用法：
    python tools/setup_ai.py                 # 完整安装
    python tools/setup_ai.py --skip-torch    # 只装依赖和模型（PyTorch 已装好）
    python tools/setup_ai.py --cpu           # 强制安装 CPU 版 PyTorch
    python tools/setup_ai.py --mirror        # 用 hf-mirror.com 下载模型（国内网络）

Windows 用户直接双击“安装AI克隆引擎.bat”即可。
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
MODELS = ROOT / "models"
MODEL_NAME = "zipvoice_distill"
HF_REPO = "k2-fsa/ZipVoice"
VOCOS_REPO = "charactr/vocos-mel-24khz"
ZIPVOICE_REPO = "https://github.com/k2-fsa/ZipVoice.git"
THIRD_PARTY = ROOT / "third_party"


def log(message: str) -> None:
    print(f"\n=== {message}")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd))
    return subprocess.run(cmd, **kwargs)


def venv_python() -> Path:
    if platform.system() == "Windows":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def ensure_venv(force_recreate: bool = False) -> Path:
    python = venv_python()
    if force_recreate and VENV.is_dir():
        shutil.rmtree(VENV, ignore_errors=True)
    if not python.exists():
        log("创建虚拟环境 .venv")
        run([sys.executable, "-m", "venv", str(VENV)], check=True)
        run([str(python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], check=True)
    return python


def install_base(python: Path) -> None:
    log("安装基础依赖（numpy / ffmpeg / 语音识别）")
    run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=False)


def install_torch(python: Path, force_cpu: bool) -> bool:
    def has_cuda() -> bool:
        probe = run([str(python), "-c", "import torch;print(torch.cuda.is_available())"], capture_output=True, text=True)
        return probe.returncode == 0 and probe.stdout.strip() == "True"

    if not force_cpu:
        log("安装 PyTorch（CUDA 12.1，适配 GTX 1050 Ti 等 Pascal 显卡）")
        run([
            str(python), "-m", "pip", "install", "torch", "torchaudio",
            "--index-url", "https://download.pytorch.org/whl/cu121",
        ], check=False)
        if has_cuda():
            print("✓ 已安装支持 CUDA 的 PyTorch")
            return True
        print("! CUDA 版没装上或检测不到显卡，改用 CPU 版 PyTorch（合成会慢一些）")

    log("安装 CPU 版 PyTorch")
    run([
        str(python), "-m", "pip", "install", "torch", "torchaudio",
        "--index-url", "https://download.pytorch.org/whl/cpu",
    ], check=False)
    return has_cuda()


INSTALL_HINT = """\
请先安装 AI 克隆引擎（只做一次）：

  1) 双击项目里的“安装AI克隆引擎.bat”
  2) 或在命令行执行：{python} tools/setup_ai.py

  国内网络建议加 --mirror（用 hf-mirror.com 下模型）
  没有 NVIDIA 显卡可加 --cpu（CPU 推理很慢，但能跑）
"""


def install_all(python: Path | None = None, mirror: bool = False, cpu: bool = False) -> int:
    """给服务端调用的一键安装（返回 0 表示成功）。"""
    if python is None:
        python = ensure_venv()
    install_base(Path(python))
    has_cuda = install_torch(Path(python), cpu)
    install_ai_deps(Path(python))
    try:
        ensure_zipvoice_repo()
    except SystemExit as exc:  # git 不可用等
        print(exc)
    download_models(mirror)
    write_config("cuda" if has_cuda else "cpu")
    return 0


def install_ai_deps(python: Path) -> None:
    log("安装 ZipVoice 推理依赖")
    run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements-ai.txt")], check=False)
    # piper_phonemize 需要从 k2-fsa 的轮子源安装
    run([
        str(python), "-m", "pip", "install", "piper_phonemize",
        "-f", "https://k2-fsa.github.io/icefall/piper_phonemize.html",
    ], check=False)


def ensure_zipvoice_repo() -> Path:
    target = THIRD_PARTY / "ZipVoice"
    if (target / "zipvoice").is_dir():
        print(f"✓ ZipVoice 代码已存在：{target}")
        return target
    THIRD_PARTY.mkdir(parents=True, exist_ok=True)
    log("下载 ZipVoice 代码（只用于模型结构，不训练、不占显存）")
    result = run(["git", "clone", "--depth", "1", ZIPVOICE_REPO, str(target)], check=False)
    if result.returncode != 0 or not (target / "zipvoice").is_dir():
        raise SystemExit(
            "下载 ZipVoice 失败。请手动执行：\n"
            f"  git clone --depth 1 {ZIPVOICE_REPO} {target}"
        )
    return target


def download_models(use_mirror: bool) -> None:
    if use_mirror or os.environ.get("CREATVIDEO_HF_MIRROR"):
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        print(f"使用镜像：{os.environ['HF_ENDPOINT']}")
    from huggingface_hub import hf_hub_download

    target = MODELS / MODEL_NAME
    target.mkdir(parents=True, exist_ok=True)
    log(f"下载 ZipVoice-Distill 模型到 {target}（约 500 MB，只需一次）")
    for name in ("model.pt", "model.json", "tokens.txt"):
        path = hf_hub_download(HF_REPO, filename=f"{MODEL_NAME}/{name}")
        shutil.copy2(path, target / name)
        print(f"  ✓ {name}")
    vocos = MODELS / MODEL_NAME / "vocos"
    vocos.mkdir(exist_ok=True)
    log("下载声码器 vocos-mel-24khz（约 50 MB）")
    for name, local in (("config.yaml", "config.yaml"), ("pytorch_model.bin", "pytorch_model.bin")):
        path = hf_hub_download(VOCOS_REPO, filename=name)
        shutil.copy2(path, vocos / local)
        print(f"  ✓ vocos/{local}")


def model_ready() -> bool:
    target = MODELS / MODEL_NAME
    return all((target / name).is_file() for name in ("model.pt", "model.json", "tokens.txt"))


def verify(python: Path) -> None:
    log("自检")
    code = r"""
import json, sys
print("python", sys.version.split()[0])
try:
    import torch
    print("torch", torch.__version__, "cuda", torch.cuda.is_available())
    if torch.cuda.is_available():
        p = torch.cuda.get_device_properties(0)
        print("gpu", p.name, round(p.total_memory / 1024**3, 2), "GB")
except Exception as exc:
    print("torch 导入失败：", exc)
for module in ("vocos", "lhotse", "jieba", "pypinyin", "piper_phonemize", "safetensors", "huggingface_hub"):
    try:
        __import__(module)
        print("ok", module)
    except Exception as exc:
        print("缺少", module, "->", exc)
"""
    run([str(python), "-c", code], check=False)


def write_config(device: str) -> None:
    config_path = ROOT / "voice" / "config.json"
    import json

    data = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.setdefault("engine", "auto")
    data["device"] = device
    data.setdefault("dtype", "fp32")
    data.setdefault("max_duration", 12.0)
    data.setdefault("asr", "auto")
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 写入配置 {config_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="安装本机 AI 声音克隆引擎")
    parser.add_argument("--skip-torch", action="store_true", help="跳过 PyTorch 安装")
    parser.add_argument("--skip-model", action="store_true", help="跳过模型下载")
    parser.add_argument("--cpu", action="store_true", help="强制使用 CPU 版 PyTorch")
    parser.add_argument("--mirror", action="store_true", help="用 hf-mirror.com 下载模型")
    parser.add_argument("--recreate", action="store_true", help="删除并重建 .venv")
    args = parser.parse_args()

    python = ensure_venv(args.recreate)
    install_base(python)
    has_cuda = False
    if not args.skip_torch:
        has_cuda = install_torch(python, args.cpu)
    else:
        probe = run([str(python), "-c", "import torch;print(torch.cuda.is_available())"], capture_output=True, text=True)
        has_cuda = probe.returncode == 0 and probe.stdout.strip() == "True"
    install_ai_deps(python)
    ensure_zipvoice_repo()
    if not args.skip_model:
        download_models(args.mirror)
    write_config("cuda" if has_cuda else "cpu")
    verify(python)

    print("\n" + "=" * 60)
    print("安装完成！")
    print("  1. 双击“启动声线实验室.bat”")
    print("  2. 导入一段 3–10 秒的清晰人声（或整段视频），点“建立克隆音色”")
    print("  3. 输入文字 → 生成朗读")
    if has_cuda:
        print("  当前使用 GPU 推理（4GB 显存已按蒸馏 8 步 + 12 秒分块配置）")
    else:
        print("  当前是 CPU 推理：能跑，但一句话可能要等十几秒；有条件请检查显卡驱动")
    print("=" * 60)


if __name__ == "__main__":
    main()
