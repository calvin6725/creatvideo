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
ZIPVOICE_ZIP = "https://codeload.github.com/k2-fsa/ZipVoice/zip/refs/heads/master"
MODELSCOPE_URL = "https://modelscope.cn/models/k2-fsa/ZipVoice"
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
        print("  提示：如果下载中断，可先执行")
        print("  pip install torch torchaudio -i https://pypi.tuna.tsinghua.edu.cn/simple")
        print("  再运行：python tools/setup_ai.py --skip-torch")

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
    install_ai_deps(Path(python), mirror)
    try:
        ensure_zipvoice_repo()
        patch_zipvoice()
    except SystemExit as exc:  # git 不可用等
        print(exc)
    download_models(mirror)
    write_config("cuda" if has_cuda else "cpu")
    return 0


# 推理必需的包（装不上就是装不上，要提示用户）
AI_CORE = [
    "vocos", "safetensors", "huggingface_hub", "lhotse", "jieba",
    "pypinyin", "cn2an", "inflect", "urllib3", "tensorboard",
]
# 可选增强：语音识别（不装可以手填参考文字）
AI_OPTIONAL = [("faster-whisper", "参考音频自动识别文字")]
PIPER_WHEEL = (
    "https://github.com/csukuangfj/piper-phonemize/releases/download/v1.5.9/"
    "piper_phonemize-1.5.9-{tag}-{platform}.whl"
)


MIRROR_INDEXES = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
]


def _pip(python: Path, args: list[str], extra_index: list[str] | None = None, attempts: int = 2) -> int:
    """pip 安装，失败自动重试，最后再试国内镜像（官方源偶发 TLS 断流时很有效）。"""
    base = [str(python), "-m", "pip", "install", *args,
            "--disable-pip-version-check", "--retries", "5", "--timeout", "60"]
    if extra_index:
        base += ["-f", *extra_index]
    for attempt in range(attempts):
        if run(base, check=False).returncode == 0:
            return 0
        print(f"! 第 {attempt + 1} 次安装失败，重试中…")
    for index in MIRROR_INDEXES:
        print(f"! 改用镜像源重试：{index}")
        if run([*base, "-i", index], check=False).returncode == 0:
            return 0
    return 1


def install_piper(python: Path) -> None:
    """安装音素化库 piper_phonemize（ZipVoice 的分词器依赖它，属于必需项）。

    优先：k2-fsa 的 wheel 源（PyPI 上的 1.1.0 对中文 eSpeak 语音不可用）；
    失败则退回 PyPI 版本，由引擎侧决定能否使用。
    """
    probe = run([str(python), "-c", "import piper_phonemize"], capture_output=True, text=True)
    if probe.returncode == 0:
        print("✓ piper_phonemize 已安装")
        return
    log("安装音素化库 piper_phonemize（中文拼音/音素需要）")
    code = _pip(python, ["piper_phonemize"], ["https://k2-fsa.github.io/icefall/piper_phonemize.html"])
    if code != 0:
        print("! k2-fsa 轮子源不可用，尝试直连 GitHub Release / PyPI")
        import platform
        import sys as _sys

        tag = f"cp{_sys.version_info.major}{_sys.version_info.minor}"
        if platform.system() == "Windows":
            plat = "win_amd64"
        elif platform.system() == "Darwin":
            plat = "macosx_11_0_arm64" if platform.machine() in ("arm64", "aarch64") else "macosx_10_15_x86_64"
        else:
            plat = "manylinux2014_x86_64.manylinux_2_17_x86_64"
        wheel = PIPER_WHEEL.format(tag=tag, platform=plat)
        code = _pip(python, [wheel])
        if code != 0:
            print("! piper_phonemize 安装失败：AI 克隆可能无法合成（需要它做音素化）")
            print("  可手动下载安装：")
            print(f"  {wheel}")


def install_ai_deps(python: Path, mirror: bool = False) -> None:
    log("安装 ZipVoice 推理依赖")
    code = _pip(python, AI_CORE)
    if code != 0:
        print("! 核心依赖安装失败，请检查网络后重新运行本脚本")
    install_piper(python)
    for package, purpose in AI_OPTIONAL:
        probe = run([str(python), "-c", f"import {package.replace('-', '_')}"], capture_output=True, text=True)
        if probe.returncode == 0:
            print(f"✓ {package} 已就绪（{purpose}）")
        else:
            print(f"! 跳过 {package}（{purpose}），不影响克隆合成")


def ensure_zipvoice_repo() -> Path:
    target = THIRD_PARTY / "ZipVoice"
    if (target / "zipvoice").is_dir():
        print(f"✓ ZipVoice 代码已存在：{target}")
        return target
    THIRD_PARTY.mkdir(parents=True, exist_ok=True)
    log("下载 ZipVoice 代码（只用于模型结构，不训练、不占显存）")
    result = run(["git", "clone", "--depth", "1", ZIPVOICE_REPO, str(target)], check=False)
    if result.returncode != 0 or not (target / "zipvoice").is_dir():
        # 没装 git 的电脑：直接下 GitHub 的 zip 包
        print("! git 方式失败，改用压缩包下载")
        if not _download_zip(ZIPVOICE_ZIP, target):
            raise SystemExit(
                "下载 ZipVoice 失败。可以手动下载后解压：\n"
                f"  {ZIPVOICE_ZIP}\n"
                f"  解压出的 zipvoice 目录放到 {target}"
            )
    return target


def _download_zip(url: str, target: Path) -> bool:
    """下载 GitHub 压缩包并解出 zipvoice 目录（不需要 git）。"""
    import io
    import urllib.request
    import zipfile

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "creatvideo-setup"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            root = archive.namelist()[0].split("/")[0] + "/"
            tmp = target.parent / (target.name + "_extract")
            shutil.rmtree(tmp, ignore_errors=True)
            archive.extractall(tmp)
        src = tmp / root
        target.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            dest = target / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"✓ 代码已解压到 {target}")
        return (target / "zipvoice").is_dir()
    except Exception as exc:
        print(f"! 压缩包下载失败：{exc}")
        return False


HF_ENDPOINTS = ["https://huggingface.co", "https://hf-mirror.com"]


def _fetch_hf_file(repo: str, filename: str, target: Path, endpoints: list[str]) -> None:
    """从 HuggingFace（或镜像）下载单个文件，多个入口依次尝试。"""
    from huggingface_hub import hf_hub_download

    errors = []
    for endpoint in endpoints:
        os.environ["HF_ENDPOINT"] = endpoint
        try:
            path = hf_hub_download(repo, filename=filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            return
        except Exception as exc:  # 换下一个入口
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("；".join(errors))


def download_models(use_mirror: bool) -> None:
    endpoints = HF_ENDPOINTS[::-1] if (use_mirror or os.environ.get("CREATVIDEO_HF_MIRROR")) else HF_ENDPOINTS
    print(f"下载入口顺序：{' → '.join(endpoints)}")
    target = MODELS / MODEL_NAME
    target.mkdir(parents=True, exist_ok=True)
    log(f"下载 ZipVoice-Distill 模型到 {target}（约 500 MB，只需一次）")
    try:
        for name in ("model.pt", "model.json", "tokens.txt"):
            _fetch_hf_file(HF_REPO, f"{MODEL_NAME}/{name}", target / name, endpoints)
            print(f"  ✓ {name}")
    except Exception as exc:
        raise SystemExit(
            "模型下载失败：\n"
            f"  {exc}\n\n"
            "可以这样补救（任选其一）：\n"
            "  1) 加镜像参数重跑：python tools/setup_ai.py --skip-torch --mirror\n"
            "     （镜像也会失败时，用浏览器打开 ModelScope 下载）\n"
            f"     {MODELSCOPE_URL}\n"
            "  2) 手动下载下面 3 个文件，放到 " + str(target) + "\n"
            f"     https://huggingface.co/{HF_REPO}/tree/main/{MODEL_NAME}\n"
            "     （model.pt、model.json、tokens.txt）\n"
            "  3) 用浏览器打开项目页，点“一键安装”并勾选“国内网络”"
        )
    vocos = MODELS / MODEL_NAME / "vocos"
    vocos.mkdir(exist_ok=True)
    log("下载声码器 vocos-mel-24khz（约 50 MB）")
    try:
        for name in ("config.yaml", "pytorch_model.bin"):
            _fetch_hf_file(VOCOS_REPO, name, vocos / name, endpoints)
            print(f"  ✓ vocos/{name}")
    except Exception as exc:
        print(f"! 声码器下载失败：{exc}")
        print("  引擎会自动改用 vocos 官方缓存（首次合成时联网下载一次）")


def model_ready() -> bool:
    target = MODELS / MODEL_NAME
    if not all((target / name).is_file() for name in ("model.pt", "model.json", "tokens.txt")):
        return False
    # 文件可能只下了一半（模型约 500MB）
    return (target / "model.pt").stat().st_size > 100 * 1024 * 1024


def _zipvoice_patched() -> bool:
    """检查 third_party/ZipVoice 是否已经打过 fp16 dtype 补丁。"""
    file = THIRD_PARTY / "ZipVoice" / "zipvoice" / "models" / "modules" / "scaling.py"
    if not file.is_file():
        return False
    return "creatvideo fp16 dtype fix" in file.read_text(encoding="utf-8", errors="ignore")


def patch_zipvoice() -> None:
    """给 ZipVoice 的 SwooshL/R 补 fp16 dtype（否则显存不足降级 fp16 会直接崩）。

    ZipVoice 把 fp16 输入转成 fp32 计算后忘了转回来，后面 linear 就会报
    「Float 与 Half 混用」。这里把三处函数的返回值 dtype 还原，逻辑等价。
    """
    file = THIRD_PARTY / "ZipVoice" / "zipvoice" / "models" / "modules" / "scaling.py"
    if not file.is_file():
        print("! 没找到 ZipVoice 代码，跳过 fp16 补丁")
        return
    text = file.read_text(encoding="utf-8")
    if "creatvideo fp16 dtype fix" in text:
        print("✓ fp16 补丁已存在")
        return
    original = text
    # SwooshLFunction / SwooshRFunction：返回前还原 dtype
    text = text.replace(
        "                if x.dtype == torch.float16 or torch.is_autocast_enabled():\n"
        "                    y = y.to(torch.float16)\n"
        "                return y",
        "                return y.to(torch.float16)  # creatvideo fp16 dtype fix",
    )
    text = text.replace(
        "if not requires_grad:\n                    return y\n",
        "if not requires_grad:\n                    return y.to(x.dtype)  # creatvideo fp16 dtype fix\n",
    )
    # SwooshLForward / SwooshRForward：函数式版本同样漏转
    for shift, const in (("4.0", "0.035"), ("1.0", "0.313261687")):
        old = (
            f"        x_offset = x - {shift}\n"
            "        log_sum = (1.0 + x_offset.exp()).log().to(x.dtype)\n"
            "        log_sum = torch.where(log_sum == float(\"inf\"), x_offset, log_sum)\n"
            f"        return log_sum - 0.08 * x - {const}"
        )
        new = (
            f"        x_offset = x - {shift}\n"
            "        log_sum = (1.0 + x_offset.exp()).log().to(x.dtype)\n"
            "        log_sum = torch.where(log_sum == float(\"inf\"), x_offset, log_sum)\n"
            f"        return (log_sum - 0.08 * x - {const}).to(x.dtype)  # creatvideo fp16 dtype fix"
        )
        text = text.replace(old, new)
    if text == original:
        print("! 补丁没有匹配到代码（ZipVoice 版本可能变了）；运行时会用运行时补丁兜底")
        return
    file.write_text(text, encoding="utf-8")
    print("✓ 已给 ZipVoice 打上 fp16 dtype 补丁")


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


def fix_bat_files() -> None:
    """把项目里的 .bat 统一成「UTF-8 BOM + CRLF」。

    中文 Windows 的 cmd 只认 CRLF；LF 行尾 + 长提示行会被截断执行，
    出现 “'xx' is not recognized as an internal or external command” 这类噪音。
    """
    root = ROOT
    changed = 0
    for path in sorted(root.glob("*.bat")):
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        normalised = b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8")
        if normalised != raw:
            path.write_bytes(normalised)
            changed += 1
    if changed:
        print(f"✓ 已修正 {changed} 个 .bat 的行尾/BOM（避免中文提示被 cmd 截断）")


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
    patch_zipvoice()
    if not args.skip_model:
        download_models(args.mirror)
    write_config("cuda" if has_cuda else "cpu")
    fix_bat_files()
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
