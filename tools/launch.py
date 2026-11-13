#!/usr/bin/env python3
"""启动器：先把服务拉起来，等接口真的能响应后再打开浏览器。

原来的 .bat 只等 2 秒就打开浏览器，而服务冷启动要 3 秒以上（要 import torch、
检测 CUDA），浏览器会先看到“无法访问”。这里改成轮询 /api/status，失败时把日志打出来。

用法：
    python tools/launch.py --port 9100 --page /
    python tools/launch.py --port 9100 --page /video.html
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_our_server(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> bool:
    """端口有人监听不代表就是本项目，用 /api/status 确认一下。"""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/status", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def python_exe() -> str:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.exists() else sys.executable


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as exc:  # 没有默认浏览器等情况
        print(f"打开浏览器失败：{exc}")
    print(f"如果浏览器没有自动弹出，请手动打开：{url}")


def show_log(log_path: Path) -> None:
    print(f"日志文件：{log_path}")
    print("------ 日志内容 ------")
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        content = ""
    print(content or "（日志为空）")
    print("----------------------")


def main() -> int:
    parser = argparse.ArgumentParser(description="声线实验室启动器")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--page", default="/")
    parser.add_argument("--wait", type=float, default=90.0, help="等待服务就绪的最长秒数")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}{args.page}"

    # 已经有本项目的服务在跑：直接打开页面
    if port_open(args.port) and is_our_server(args.port):
        print(f"服务已在运行：{url}")
        open_browser(url)
        return 0

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / "server.log"
    log = open(log_path, "w", encoding="utf-8", buffering=1)

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    print("正在启动服务，请稍候（首次启动需要几秒）...")
    proc = subprocess.Popen(
        [python_exe(), str(ROOT / "voice_server.py"), "--port", str(args.port)],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )

    deadline = time.time() + args.wait
    while time.time() < deadline:
        if is_our_server(args.port):
            print(f"服务已就绪：{url}")
            open_browser(url)
            return 0
        if proc.poll() is not None:
            # 进程已退出：区分“端口被别人占”和“自己启动报错”
            time.sleep(0.5)
            if port_open(args.port):
                print(f"\n启动失败：端口 {args.port} 已被其他程序占用。")
                print("请关掉占用该端口的程序，或换个端口启动：python tools\\launch.py --port 9200")
            else:
                print("\n启动失败：服务进程异常退出。")
                show_log(log_path)
            return 1
        time.sleep(0.4)

    print(f"\n启动超时：{args.wait:.0f} 秒内服务还没就绪。")
    show_log(log_path)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
