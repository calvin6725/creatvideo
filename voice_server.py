import base64
import argparse
import json
import os
import subprocess
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class VoiceHandler(SimpleHTTPRequestHandler):
    helper_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_helper.ps1")
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/voices":
            result = subprocess.run(["pwsh.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.helper_path, "-Mode", "voices"], capture_output=True, timeout=20)
            lines = result.stdout.decode("utf-8", "ignore").splitlines()
            voices = []
            for line in lines:
                name, _, language = line.strip().partition("|")
                if name:
                    voices.append({"name": name, "language": language or "Windows local"})
            self.send_json(200, {"available": bool(voices), "voices": voices})
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/tts":
            self.send_error(404)
            return
        wav_path = None
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            text = str(payload.get("text", "")).strip()
            voice = str(payload.get("voice", "Microsoft Huihui Desktop"))
            if not text or len(text) > 12000:
                raise ValueError("文本为空或超过 12000 字")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                wav_path = tmp.name
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            encoded_voice = base64.b64encode(voice.encode("utf-8")).decode("ascii")
            result = subprocess.run(["pwsh.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.helper_path, "-Mode", "speak", "-TextBase64", encoded, "-VoiceBase64", encoded_voice, "-OutputPath", wav_path], capture_output=True, timeout=90)
            if result.returncode != 0 or not os.path.exists(wav_path):
                raise RuntimeError(result.stderr.decode("utf-8", "ignore") or "系统语音合成失败")
            with open(wav_path, "rb") as wav:
                data = wav.read()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})
        finally:
            if wav_path:
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), VoiceHandler).serve_forever()
