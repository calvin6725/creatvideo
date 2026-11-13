#!/usr/bin/env python3
"""声线实验室 · 本机声音克隆服务。

用法：
    python voice_server.py                       # 默认 http://localhost:9100/
    python voice_server.py --port 9100           # 指定端口
    python voice_server.py --host 0.0.0.0        # 允许局域网访问（默认只监听本机）
    python voice_server.py --engine ai           # 强制使用 AI 克隆引擎

Windows 上请优先用“启动声线实验室.bat”，它会使用 .venv 里装好 AI 依赖的 Python。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED = ("numpy",)


def _missing() -> list[str]:
    from importlib.util import find_spec

    return [name for name in REQUIRED if find_spec(name) is None]


def main() -> None:
    missing = _missing()
    if missing:
        print("缺少依赖：" + "、".join(missing))
        print("请先安装：")
        print("  pip install -r requirements.txt")
        print("如果是 AI 克隆引擎，直接双击“安装AI克隆引擎.bat”即可。")
        raise SystemExit(1)
    try:
        from voice.service import main as serve
    except ImportError as exc:  # 项目文件缺失等
        print("启动失败：", exc)
        print("请确认 voice/ 目录完整，并执行 pip install -r requirements.txt")
        raise SystemExit(1)
    serve()


if __name__ == "__main__":
    main()
