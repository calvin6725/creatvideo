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
  echo [失败] 安装没有完成。常见原因：
  echo   - 网络无法访问 download.pytorch.org：可加参数 --cpu 先装 CPU 版
  echo   - 网络无法访问 huggingface.co：可加参数 --mirror 用镜像下载模型
  echo   例如：python tools\setup_ai.py --mirror
  pause
  exit /b 1
)

echo.
echo 安装完成，按任意键关闭。
pause >nul
