"""データモデル（ジョブ・記録）。標準ライブラリのみ。"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime

# ジョブの状態。
STATUS_QUEUED = "queued"      # 実行待ち
STATUS_RUNNING = "running"    # どこかのPCが実行中
STATUS_DONE = "done"         # 正常完了
STATUS_ERROR = "error"       # 失敗
STATUS_CANCELED = "canceled"  # ユーザーが中止

ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Job:
    """連続実行キューの1件。共有フォルダに1ファイルとして置かれる。"""

    blend_path: str = ""           # 対象 .blend（全PC共通パス）
    preset_name: str = ""          # 実行するプリセット名
    id: str = field(default_factory=new_id)
    order: int = 0                 # 並び順（小さいほど先）
    target_pc: str = ""            # 空=どのPCでも可 / PC名=そのPC限定
    status: str = STATUS_QUEUED
    created_at: str = field(default_factory=now_iso)

    # 実行に関する記録（実行後に埋まる）。
    claimed_by: str = ""           # 実際に実行した（している）PC名
    started_at: str = ""
    finished_at: str = ""
    elapsed_seconds: float = 0.0
    predicted_seconds: float = 0.0  # 予測（GUIが計算して表示用に格納）
    resolution: list = field(default_factory=list)  # 実出力ピクセル [w, h]
    error: str = ""
    exec_count: int = 0            # 実際に走ったレンダー工程数
    # レンダー工程ごとの記録（runner の timing.json から取り込む）。
    renders: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in known}
        return cls(**clean)

    def label(self) -> str:
        """画面表示用の短いラベル（ファイル名 / プリセット名）。"""
        base = os.path.basename(self.blend_path) or self.blend_path
        return f"{base} / {self.preset_name}"
