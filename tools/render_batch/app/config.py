"""アプリ設定（共有フォルダ・自PC名・Blender実行ファイル等）。"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, asdict
from pathlib import Path

# 設定ファイルは各PCのローカルに置く（共有フォルダではなくPC個別の設定）。
CONFIG_PATH = Path(os.path.expanduser("~")) / ".bname_render_batch.json"

DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"


@dataclass
class Config:
    shared_root: str = ""          # 共有フォルダ（Dropbox、全PC同一パス）
    pc_name: str = ""             # 自PC名（空ならホスト名）
    blender_exe: str = DEFAULT_BLENDER
    sync_grace_seconds: float = 3.0  # 取得宣言の同期猶予（Dropbox向け）
    poll_seconds: float = 5.0       # キュー監視間隔

    def resolved_pc(self) -> str:
        return self.pc_name.strip() or socket.gethostname()

    def runner_path(self) -> str:
        """同梱 runner.py の絶対パス（このファイルからの相対）。"""
        return str(Path(__file__).resolve().parents[1] / "runner.py")

    def to_dict(self) -> dict:
        return asdict(self)


def load() -> Config:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        known = {f for f in Config.__dataclass_fields__}  # type: ignore[attr-defined]
        return Config(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError):
        return Config()


def save(cfg: Config) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
