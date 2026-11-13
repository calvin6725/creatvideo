@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  声线实验室 - 安装 AI 声音克隆引擎
echo  适用：NVIDIA 显卡（4GB 显存即可，如 GTX 1050 Ti）
echo  第一次安装需要下载约 1GB 文件，请保持网络畅通。
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 Python。
  echo 请先安装 Python 3.10 或 3.11：https://www.python.org/downloads/
  echo 安装时务必勾选 "Add python.exe to PATH"。
  pause
  exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,9) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [错误] Python 版本太低，请安装 3.10 或 3.11（PyTorch 与 ZipVoice 的要求）。
  pause
  exit /b 1
)

if not exist "third_party\ZipVoice" (
  where git >nul 2>nul
  if errorlevel 1 (
    echo [提示] 没有检测到 git，将跳过 ZipVoice 代码下载。
    echo        如果后续提示缺少 zipvoice 模块，请安装 Git 后重新运行本脚本。
  )
)

echo 开始安装，过程中会多次显示 pip 输出，请耐心等待...
echo.
python tools\setup_ai.py %*
if errorlevel 1 (
  echo.
  echo [失败] 安装没有完成。常见原因与办法：
  echo.
  echo [1] PyTorch 下载失败 - 连不上官方源
  echo    先执行下面这行命令，从阿里云镜像装：
  echo      .venv\Scripts\python.exe -m pip install torch torchaudio -f https://mirrors.aliyun.com/pytorch-wheels/cu121
  echo    装好后继续：
  echo      .venv\Scripts\python.exe tools\setup_ai.py --skip-torch --mirror
  echo.
  echo [2] 模型下载失败 - 连不上 huggingface
  echo      .venv\Scripts\python.exe tools\setup_ai.py --skip-torch --mirror
  echo.
  echo [3] 没有 NVIDIA 显卡，或想先用 CPU
  echo      .venv\Scripts\python.exe tools\setup_ai.py --cpu --mirror
  echo.
  echo 安装日志已保存到 install-log.txt（如果存在）
  pause
  exit /b 1
)

echo.
echo 安装完成，按任意键关闭。
pause >nul
