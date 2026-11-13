"""声线实验室 / 视频解说剪辑 的本机服务。

只用标准库：
- 静态文件：index.html、video.html 等
- 克隆音色库：/api/clones
- 导入音频 → 自动切出最佳参考片段 → 自动转写 → 建音色
- 克隆朗读：/api/tts（同步）与 /api/tts/jobs（长文本异步）

启动：python voice_server.py --port 9100
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from . import asr
from .audio_io import read_audio_bytes, resample, rms, to_mono, wav_bytes
from .config import APP_DIR, DATA_DIR, JOB_DIR, load_config
from .engines import EngineError, EngineManager
from .processor import analyze_clone
from .segment import analyze_reference, best_reference_segment
from .store import CloneStore

log = logging.getLogger("voice.service")

REFERENCE_RATE = 24000
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CONFIG = load_config()
STORE = CloneStore()
ENGINE = EngineManager(CONFIG)
JOBS: Dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# 业务逻辑
# --------------------------------------------------------------------------- #
def _upload_token() -> str:
    return uuid.uuid4().hex[:16]


def _save_upload(token: str, raw: bytes) -> Path:
    path = UPLOAD_DIR / f"{token}.bin"
    path.write_bytes(raw)
    return path


def _load_upload(token: str) -> bytes:
    if not token or not re.fullmatch(r"[0-9a-f]{8,64}", token):
        raise HttpError(400, "上传标识无效，请重新导入音频")
    path = UPLOAD_DIR / f"{token}.bin"
    if not path.is_file():
        raise HttpError(404, "上传的音频已过期，请重新导入")
    return path.read_bytes()


def handle_analyze(raw: bytes, filename: str, want_text: bool = True) -> dict:
    """分析导入的音频：解码、VAD 切段、打分、自动转写最佳片段。"""
    audio, rate = read_audio_bytes(raw, filename)
    mono = to_mono(audio)
    if mono.size < rate * 0.5:
        raise HttpError(400, "音频太短了，请至少导入 1 秒的清晰人声")
    if rms(mono) < 1e-4:
        raise HttpError(400, "音频几乎没有声音，请换一段清晰的录音")
    info = analyze_reference(mono, rate, CONFIG.prompt_min_seconds, CONFIG.prompt_max_seconds)
    best = info.get("best")
    if not best:
        raise HttpError(400, "没能在音频里找到合适的人声片段，请换一段单人说话的录音")
    begin = int(best["start"] * rate)
    end = int(best["stop"] * rate)
    segment = mono[begin:end]
    features = analyze_clone(segment, rate)
    text = ""
    text_error = ""
    if want_text:
        try:
            text = asr.cleanup_reference_text(asr.transcribe(segment, rate, "zh", CONFIG.asr_model))
        except Exception as exc:
            text_error = str(exc)
    token = _upload_token()
    _save_upload(token, raw)
    name = Path(filename or "录音").stem[:30]
    return {
        "token": token,
        "filename": filename,
        "duration": info["duration"],
        "rate": rate,
        "segments": info["segments"][:40],
        "best": best,
        "features": features,
        "text": text,
        "textError": text_error,
        "suggestedName": f"{name} · 克隆" if name else "我的克隆音色",
        "voiceActivity": info.get("voiced_ratio"),
    }


def handle_create_clone(payload: dict) -> dict:
    token = payload.get("token") or ""
    name = (payload.get("name") or "").strip() or "我的克隆音色"
    text = (payload.get("text") or "").strip()
    start = payload.get("start")
    stop = payload.get("stop")
    auto_text = bool(payload.get("autoText", False))
    engine_note = payload.get("engine") or ""
    raw = _load_upload(token)
    audio, rate = read_audio_bytes(raw, payload.get("filename") or "")
    mono = to_mono(audio)
    if start is None or stop is None:
        segment, info = best_reference_segment(mono, rate, CONFIG.prompt_min_seconds, CONFIG.prompt_max_seconds)
        best = info.get("best") or {}
        start, stop = best.get("start", 0.0), best.get("stop", len(segment) / max(1, rate))
    else:
        start, stop = float(start), float(stop)
        segment = mono[int(start * rate) : int(stop * rate)]
    if segment.size < rate * 1.0:
        raise HttpError(400, "参考片段太短，请至少选择 1 秒（推荐 3~8 秒）")
    # 统一存成 24kHz 单声道：和克隆模型采样率一致，参考音频更小、推理更快
    if rate != REFERENCE_RATE:
        segment = resample(segment, rate, REFERENCE_RATE)
        rate = REFERENCE_RATE
    features = analyze_clone(segment, rate)
    if not text and auto_text:
        try:
            text = asr.cleanup_reference_text(asr.transcribe(segment, rate, "zh", CONFIG.asr_model))
        except Exception:
            text = ""
    if not text:
        raise HttpError(
            400,
            "请填写参考音频的文字内容（也可以点“自动识别文字”）。"
            "克隆模型需要知道参考音频说了什么，否则音色会跑偏。",
        )
    record = STORE.create(
        name=name,
        reference=segment,
        rate=rate,
        text=text,
        features=features,
        source=payload.get("source") or "导入音频",
        extra={"engine": engine_note},
    )
    return record


def synthesize(text: str, clone_id: Optional[str], settings: dict, engine: Optional[str], progress=None) -> tuple[bytes, dict]:
    text = (text or "").strip()
    if not text:
        raise HttpError(400, "朗读文字不能为空")
    if len(text) > CONFIG.max_text_chars:
        raise HttpError(400, f"文字太长（上限 {CONFIG.max_text_chars} 字），请分批生成")
    # 语速统一在这里夹一次，旧页面或脚本传越界值也不会把时长搞崩
    from .processor import SPEED_MAX, SPEED_MIN

    try:
        speed_percent = float(settings.get("speed") or 0.0)
    except (TypeError, ValueError):
        speed_percent = 0.0
    settings = {**settings, "speed": float(
        min((SPEED_MAX - 1) * 100, max((SPEED_MIN - 1) * 100, speed_percent))
    )}
    clone = None
    if clone_id:
        clone = STORE.get(clone_id, load_audio=True)
        if clone is None:
            raise HttpError(404, "音色不存在或已删除")
        if clone.get("audio") is None or not clone.get("text"):
            raise HttpError(400, "这个音色的参考音频或参考文字缺失，请重新建立")
    audio, rate, meta = ENGINE.synthesize(text, clone, settings or {}, progress=progress, engine=engine)
    if audio is None or len(audio) < rate * 0.05:
        raise EngineError("合成结果为空：请检查参考音频是否清晰、参考文字是否与音频一致")
    return wav_bytes(audio, rate), meta


# --------------------------------------------------------------------------- #
# 一键安装 AI 引擎（页面上的“一键安装”按钮）
# --------------------------------------------------------------------------- #
INSTALL_STATE = {"running": False, "done": False, "error": "", "log": []}
INSTALL_LOCK = threading.Lock()


def _setup_module():
    import importlib.util

    path = APP_DIR / "tools" / "setup_ai.py"
    spec = importlib.util.spec_from_file_location("creatvideo_setup_ai", path)
    if spec is None or spec.loader is None:
        raise HttpError(500, "找不到安装脚本 tools/setup_ai.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def start_install(mirror: bool = False, cpu: bool = False) -> dict:
    with INSTALL_LOCK:
        if INSTALL_STATE["running"]:
            return dict(INSTALL_STATE)
        INSTALL_STATE.update(running=True, done=False, error="", log=[])
    threading.Thread(target=_run_install, args=(mirror, cpu), daemon=True).start()
    return dict(INSTALL_STATE)


def _run_install(mirror: bool, cpu: bool) -> None:
    import contextlib
    import io

    class _Tee(io.TextIOBase):
        def write(self, text: str) -> int:  # noqa: D401
            for line in text.splitlines():
                if line.strip():
                    with INSTALL_LOCK:
                        INSTALL_STATE["log"] = (INSTALL_STATE["log"] + [line.strip()])[-120:]
            return len(text)

    try:
        module = _setup_module()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(_Tee()), contextlib.redirect_stderr(_Tee()):
            module.install_all(mirror=mirror, cpu=cpu)
            print(buffer.getvalue())
        ENGINE.ai._error = None  # 安装完成后重新检查
        with INSTALL_LOCK:
            INSTALL_STATE.update(running=False, done=True, error="")
    except SystemExit as exc:
        with INSTALL_LOCK:
            INSTALL_STATE.update(running=False, done=False, error=f"安装中断：{exc}")
    except Exception as exc:  # pragma: no cover - 依赖外部网络
        log.error("安装失败：%s\n%s", exc, traceback.format_exc())
        with INSTALL_LOCK:
            INSTALL_STATE.update(running=False, done=False, error=str(exc))


# --------------------------------------------------------------------------- #
# 异步任务
# --------------------------------------------------------------------------- #
def start_job(text: str, clone_id: Optional[str], settings: dict, engine: Optional[str]) -> dict:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "running",
            "progress": 0.02,
            "detail": "排队中",
            "text": text[:80],
            "cloneId": clone_id,
            "created": time.time(),
        }
    threading.Thread(target=_run_job, args=(job_id, text, clone_id, settings, engine), daemon=True).start()
    return JOBS[job_id]


def _run_job(job_id: str, text: str, clone_id: Optional[str], settings: dict, engine: Optional[str]) -> None:
    def progress(value: float, detail: str = "") -> None:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["progress"] = round(min(0.99, max(job.get("progress", 0), value)), 3)
                if detail:
                    job["detail"] = detail

    try:
        payload, meta = synthesize(text, clone_id, settings, engine, progress)
        path = JOB_DIR / f"{job_id}.wav"
        path.write_bytes(payload)
        with JOBS_LOCK:
            job = JOBS.get(job_id) or {}
            job.update(status="done", progress=1.0, detail="完成", url=f"/api/tts/jobs/{job_id}/audio", engine=meta)
            JOBS[job_id] = job
    except Exception as exc:
        log.warning("任务失败 %s: %s", job_id, exc)
        with JOBS_LOCK:
            job = JOBS.get(job_id) or {}
            job.update(status="error", detail=str(exc), progress=1.0)
            JOBS[job_id] = job


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "VoiceLab/2.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- 响应工具
    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _is_loopback(self) -> bool:
        address = self.client_address[0] if self.client_address else ""
        return address in ("127.0.0.1", "::1") or address.startswith("127.")

    def _read_body(self, limit: int = 200 * 1024 * 1024) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise HttpError(413, "文件太大（上限 200 MB）")
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict:
        raw = self._read_body(4 * 1024 * 1024)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise HttpError(400, f"请求格式错误：{exc}") from exc

    def log_message(self, fmt: str, *args) -> None:  # 安静一点的日志
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------- 路由
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/status":
                self._json(200, self._status())
                return
            if path == "/api/clones":
                self._json(200, {"clones": STORE.list()})
                return
            match = re.fullmatch(r"/api/clones/([0-9a-zA-Z]+)", path)
            if match:
                clone = STORE.get(match.group(1))
                if clone is None:
                    raise HttpError(404, "音色不存在")
                self._json(200, clone)
                return
            match = re.fullmatch(r"/api/clones/([0-9a-zA-Z]+)/reference", path)
            if match:
                clone = STORE.get(match.group(1), load_audio=True)
                if clone is None or clone.get("audio") is None:
                    raise HttpError(404, "参考音频不存在")
                data = wav_bytes(clone["audio"], int(clone.get("rate") or 24000))
                self._send(200, data, "audio/wav")
                return
            match = re.fullmatch(r"/api/tts/jobs/([0-9a-f]+)(/audio)?", path)
            if match:
                job_id, want_audio = match.group(1), bool(match.group(2))
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                if job is None:
                    raise HttpError(404, "任务不存在或服务已重启")
                if want_audio:
                    wav_path = JOB_DIR / f"{job_id}.wav"
                    if not wav_path.is_file():
                        raise HttpError(404, "音频还没生成完")
                    self._send(200, wav_path.read_bytes(), "audio/wav")
                else:
                    self._json(200, job)
                return
            if path == "/api/voices":
                self._json(200, {"available": bool(ENGINE.fallback.voices()), "voices": ENGINE.fallback.voices()})
                return
            if path == "/api/stt":
                self._json(200, {"available": asr.available()[0], "detail": asr.available()[1]})
                return
            if path.startswith("/api/"):
                raise HttpError(404, "接口不存在")
            self._static(path)
        except HttpError as exc:
            self._error(exc.status, str(exc))
        except Exception as exc:  # pragma: no cover - 兜底
            log.error("GET %s 失败：%s\n%s", path, exc, traceback.format_exc())
            self._error(500, str(exc))

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        match = re.fullmatch(r"/api/clones/([0-9a-zA-Z]+)", path)
        try:
            if not match:
                raise HttpError(404, "接口不存在")
            ok = STORE.delete(match.group(1))
            if not ok:
                raise HttpError(404, "音色不存在")
            self._json(200, {"ok": True})
        except HttpError as exc:
            self._error(exc.status, str(exc))

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/clone/analyze":
                raw = self._read_body()
                filename = urllib.parse.unquote(self.headers.get("X-Filename") or "audio")
                want_text = (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("text", ["1"])[0]) != "0"
                self._json(200, handle_analyze(raw, filename, want_text))
                return
            if path == "/api/clone/transcribe":
                payload = self._read_json()
                raw = _load_upload(payload.get("token") or "")
                audio, rate = read_audio_bytes(raw, payload.get("filename") or "")
                mono = to_mono(audio)
                start = float(payload.get("start") or 0.0)
                stop = float(payload.get("stop") or mono.size / max(1, rate))
                segment = mono[int(start * rate) : int(stop * rate)]
                text = asr.cleanup_reference_text(asr.transcribe(segment, rate, "zh", CONFIG.asr_model))
                self._json(200, {"text": text})
                return
            if path == "/api/clones":
                payload = self._read_json()
                self._json(200, handle_create_clone(payload))
                return
            match = re.fullmatch(r"/api/clones/([0-9a-zA-Z]+)", path)
            if match:
                payload = self._read_json()
                changes = {k: payload.get(k) for k in ("name", "text") if payload.get(k) is not None}
                self._json(200, STORE.update(match.group(1), changes))
                return
            if path in ("/api/tts", "/api/speak"):
                payload = self._read_json()
                text = payload.get("text") or ""
                engine = payload.get("engine")
                if not payload.get("cloneId") and payload.get("voice") and engine != "ai":
                    engine = "fallback"
                settings = payload.get("settings") or {}
                if payload.get("voice"):
                    settings.setdefault("sapiVoice", payload.get("voice"))
                audio, meta = synthesize(text, payload.get("cloneId"), settings, engine)
                self._send(200, audio, "audio/wav")
                return
            if path == "/api/tts/jobs":
                payload = self._read_json()
                job = start_job(
                    payload.get("text") or "",
                    payload.get("cloneId"),
                    payload.get("settings") or {},
                    payload.get("engine"),
                )
                self._json(200, job)
                return
            if path == "/api/install/status":
                with INSTALL_LOCK:
                    self._json(200, dict(INSTALL_STATE))
                return
            if path == "/api/release":
                self._json(200, {"ok": True, **ENGINE.ai.release()})
                return
            if path == "/api/install":
                if not self._is_loopback():
                    raise HttpError(403, "只允许在本机触发安装")
                payload = self._read_json()
                self._json(200, start_install(bool(payload.get("mirror")), bool(payload.get("cpu"))))
                return
            if path == "/api/warmup":
                try:
                    ENGINE.ai.load()
                    ENGINE.ai.warmup()
                    self._json(200, {"ok": True, "status": ENGINE.ai.probe().detail})
                except Exception as exc:
                    self._json(200, {"ok": False, "error": str(exc)})
                return
            raise HttpError(404, "接口不存在")
        except HttpError as exc:
            self._error(exc.status, str(exc))
        except EngineError as exc:
            self._error(500, str(exc))
        except Exception as exc:  # pragma: no cover
            log.error("POST %s 失败：%s\n%s", path, exc, traceback.format_exc())
            self._error(500, str(exc))

    # ------------------------------------------------------------- 静态文件
    BLOCKED_PREFIXES = ("data", "models", "third_party", "voice", "tools", "tests", ".venv", ".git")

    def _static(self, path: str) -> None:
        relative = urllib.parse.unquote(path.lstrip("/")) or "index.html"
        first = relative.split("/")[0]
        if first in self.BLOCKED_PREFIXES or first.startswith(".") or ".." in relative:
            raise HttpError(404, "文件不存在")
        target = (APP_DIR / relative).resolve()
        if APP_DIR.resolve() not in target.parents and target != APP_DIR.resolve():
            raise HttpError(403, "禁止访问")
        if not target.is_file():
            raise HttpError(404, "文件不存在")
        suffix = target.suffix.lower()
        content_type = STATIC_TYPES.get(suffix) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        cache = "no-cache" if suffix in (".html", ".js", ".css") else "public, max-age=86400"
        self._send(200, target.read_bytes(), content_type, cache)

    def _status(self) -> dict:
        status = ENGINE.status()
        status.update(
            workdir=str(APP_DIR),
            clones=len(STORE.list()),
            transcription={"available": asr.available()[0], "detail": asr.available()[1]},
            prompts={"min": CONFIG.prompt_min_seconds, "max": CONFIG.prompt_max_seconds},
            install={k: v for k, v in INSTALL_STATE.items() if k != "log"},
        )
        return status


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(host: str = "127.0.0.1", port: int = 9100) -> None:
    server = Server((host, port), Handler)
    print(f"声线实验室已启动：http://localhost:{port}/")
    print(f"  引擎：{ENGINE.status()['detail']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="声线实验室本机服务")
    parser.add_argument("--port", type=int, default=CONFIG.port)
    parser.add_argument("--host", default=CONFIG.host)
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
