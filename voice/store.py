"""克隆音色库。

每个音色保存在 data/clones/<id>/ 下：
    clone.json   名称、参考文字、特征、创建/更新时间、来源
    reference.wav 参考音频（已裁剪为最佳片段、24kHz 单声道）

参考音频只留在本机，不会上传。删除音色即删除整个目录。
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import List, Optional

import numpy as np

from .audio_io import read_audio, write_wav
from .config import CLONE_DIR

__all__ = ["CloneStore", "CloneRecord"]

META = "clone.json"
AUDIO = "reference.wav"


class CloneRecord(dict):
    @property
    def id(self) -> str:
        return self["id"]


class CloneStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or CLONE_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ CRUD
    def list(self) -> List[dict]:
        items = []
        for folder in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            meta = folder / META
            if not folder.is_dir() or not meta.is_file():
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except Exception:
                continue
            data.pop("audio", None)
            items.append(data)
        return items

    def get(self, clone_id: str, load_audio: bool = False) -> Optional[dict]:
        folder = self._safe(clone_id)
        if folder is None or not (folder / META).is_file():
            return None
        data = json.loads((folder / META).read_text(encoding="utf-8"))
        if load_audio:
            wav = folder / AUDIO
            if not wav.is_file():
                return None
            audio, rate = read_audio(wav)
            data["audio"] = audio
            data["rate"] = rate
        return data

    def create(
        self,
        name: str,
        reference: np.ndarray,
        rate: int,
        text: str,
        features: dict,
        source: str = "导入音频",
        extra: dict | None = None,
    ) -> dict:
        clone_id = uuid.uuid4().hex[:12]
        folder = self.root / clone_id
        folder.mkdir(parents=True, exist_ok=True)
        write_wav(folder / AUDIO, reference, rate)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        data = {
            "id": clone_id,
            "name": (name or "未命名音色").strip()[:40],
            "text": (text or "").strip(),
            "features": features,
            "source": source,
            "engine": (extra or {}).get("engine", ""),
            "created": now,
            "updated": now,
            "seconds": round(len(reference) / max(1, rate), 2),
            "rate": rate,
        }
        (folder / META).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def update(self, clone_id: str, changes: dict, reference: np.ndarray | None = None, rate: int | None = None) -> dict:
        folder = self._safe(clone_id)
        if folder is None or not (folder / META).is_file():
            raise KeyError("音色不存在")
        data = json.loads((folder / META).read_text(encoding="utf-8"))
        for key, value in changes.items():
            if key in ("id", "created"):
                continue
            if value is not None:
                data[key] = value
        if reference is not None and rate:
            write_wav(folder / AUDIO, reference, rate)
            data["seconds"] = round(len(reference) / max(1, rate), 2)
            data["rate"] = rate
        data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (folder / META).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def delete(self, clone_id: str) -> bool:
        folder = self._safe(clone_id)
        if folder is None or not folder.is_dir():
            return False
        shutil.rmtree(folder, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ 工具
    def _safe(self, clone_id: str) -> Optional[Path]:
        if not clone_id or any(ch in clone_id for ch in "/\\.."):
            return None
        folder = (self.root / clone_id).resolve()
        if self.root.resolve() not in folder.parents:
            return None
        return folder
