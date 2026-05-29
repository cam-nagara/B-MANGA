"""アプリ設定（共有フォルダ・自PC名・Blender実行ファイル等）。"""

from __future__ import annotations

import json
import os
import socket
import uuid
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
    machine_id: str = ""           # マシン固有ID（同名PCでも衝突しない claim 用・自動生成）
    heartbeat_seconds: float = 30.0  # 実行中ジョブの生存印を書き直す間隔
    stale_running_minutes: float = 10.0  # 生存印がこの分数途切れたら孤児(死亡ワーカー)とみなす
    job_timeout_minutes: float = 0.0  # 1ジョブの実行上限(分)。0=無制限

    def resolved_pc(self) -> str:
        return self.pc_name.strip() or socket.gethostname()

    def resolved_claimant(self) -> str:
        """claim の一意識別子。表示名(pc)が同じでもマシンごとに必ず異なる。"""
        return f"{self.resolved_pc()}#{self.machine_id or 'nomid'}"

    def runner_path(self) -> str:
        """同梱 runner.py の絶対パス（このファイルからの相対）。"""
        return str(Path(__file__).resolve().parents[1] / "runner.py")

    def to_dict(self) -> dict:
        return asdict(self)


def load() -> Config:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        known = {f for f in Config.__dataclass_fields__}  # type: ignore[attr-defined]
        cfg = Config(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError):
        cfg = Config()
    if not cfg.machine_id:
        cfg.machine_id = uuid.uuid4().hex[:8]
        try:
            save(cfg)
        except OSError:
            pass
    return cfg


def save(cfg: Config) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
