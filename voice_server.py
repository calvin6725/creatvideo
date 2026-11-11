#!/usr/bin/env python3
"""声线实验室 · 本机声音克隆服务。

用法：
    python voice_server.py                # 默认 http://localhost:8000/
    python voice_server.py --port 8000    # 指定端口
    python voice_server.py --engine ai    # 只用 AI 克隆引擎

也可以直接用 voice/ 包里的 service：python -m voice.service
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from voice.service import main
except ImportError as exc:  # Python 环境问题给出可执行的提示
    print("启动失败：", exc)
    print("请先安装依赖：pip install -r requirements.txt")
    raise SystemExit(1)

if __name__ == "__main__":
    main()
