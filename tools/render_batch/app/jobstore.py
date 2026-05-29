"""共有フォルダ上のジョブ置き場（Dropbox 分担型）。

ディレクトリ構成（共有フォルダ直下）::

    <root>/
      queue/    <id>.json        … 全ジョブ（status は中の status フィールド）
      claim/    <id>__<pc>.json  … 取得宣言（二重実行防止のタイブレーク用）
      history/  <id>.json        … 完了/失敗の記録（done/error）

Dropbox は同時書き込みで「競合コピー」を作り、同期に時間差がある。
そこで実行権の取得は「宣言 → 同期猶予を待つ → 候補を見て決定的に勝者決定」
の方式にする。1PC1本ずつ・重い処理という前提なので、衝突頻度は低い。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import model
from .model import Job


class JobStore:
    def __init__(self, root: str, sync_grace_seconds: float = 3.0):
        self.root = Path(root)
        self.queue_dir = self.root / "queue"
        self.claim_dir = self.root / "claim"
        self.history_dir = self.root / "history"
        self.sync_grace_seconds = float(sync_grace_seconds)

    # ---- 基盤 ----
    def ensure_dirs(self) -> None:
        for d in (self.queue_dir, self.claim_dir, self.history_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))

    def _read_json(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ---- キュー操作 ----
    def add_job(self, job: Job) -> Job:
        self.ensure_dirs()
        if not job.order:
            job.order = self._next_order()
        self._atomic_write(self.queue_dir / f"{job.id}.json", job.to_dict())
        return job

    def _next_order(self) -> int:
        jobs = self.list_jobs()
        return (max((j.order for j in jobs), default=0)) + 10

    def list_jobs(self) -> list[Job]:
        self.ensure_dirs()
        jobs: list[Job] = []
        for path in self.queue_dir.glob("*.json"):
            data = self._read_json(path)
            if data:
                jobs.append(Job.from_dict(data))
        jobs.sort(key=lambda j: (j.order, j.created_at, j.id))
        return jobs

    def get_job(self, job_id: str) -> Job | None:
        data = self._read_json(self.queue_dir / f"{job_id}.json")
        return Job.from_dict(data) if data else None

    def update_job(self, job: Job) -> None:
        self._atomic_write(self.queue_dir / f"{job.id}.json", job.to_dict())

    def remove_job(self, job_id: str) -> None:
        try:
            (self.queue_dir / f"{job_id}.json").unlink()
        except OSError:
            pass
        for path in self.claim_dir.glob(f"{job_id}__*.json"):
            try:
                path.unlink()
            except OSError:
                pass

    def reorder(self, ordered_ids: list[str]) -> None:
        """与えられた順に order を振り直す（10刻み）。"""
        for index, job_id in enumerate(ordered_ids):
            job = self.get_job(job_id)
            if job is not None:
                job.order = (index + 1) * 10
                self.update_job(job)

    # ---- 実行権の取得（claim）----
    def find_next_for(self, pc: str) -> Job | None:
        """このPCが実行すべき次のジョブ（queued, target一致）を返す。"""
        for job in self.list_jobs():
            if job.status != model.STATUS_QUEUED:
                continue
            if job.target_pc and job.target_pc != pc:
                continue
            return job
        return None

    def claim(self, job_id: str, pc: str) -> bool:
        """実行権を取得する。勝者なら True。

        手順:
          1) claim/<id>__<pc>.json を書く（自分の宣言）
          2) 同期猶予を待つ
          3) その job への全宣言を読み、PC名の辞書順で最小を勝者とする
          4) 勝者が自分なら queue の status を running にして True
        """
        self.ensure_dirs()
        job = self.get_job(job_id)
        if job is None or job.status != model.STATUS_QUEUED:
            return False

        my_claim = self.claim_dir / f"{job_id}__{pc}.json"
        self._atomic_write(my_claim, {"job_id": job_id, "pc": pc, "at": model.now_iso()})

        if self.sync_grace_seconds > 0:
            time.sleep(self.sync_grace_seconds)

        winner = self._claim_winner(job_id)
        if winner != pc:
            try:
                my_claim.unlink()
            except OSError:
                pass
            return False

        # 競合チェック: 取得直前に他PCが running 化していないか。
        job = self.get_job(job_id)
        if job is None or job.status != model.STATUS_QUEUED:
            return False
        job.status = model.STATUS_RUNNING
        job.claimed_by = pc
        job.started_at = model.now_iso()
        self.update_job(job)
        return True

    def _claim_winner(self, job_id: str) -> str:
        """その job への宣言のうち、PC名の辞書順で最小のPCを勝者とする。

        辞書順 tie-break は決定的なので、複数PCが同じ結論に至る。
        """
        pcs: list[str] = []
        for path in self.claim_dir.glob(f"{job_id}__*.json"):
            data = self._read_json(path)
            if data and data.get("pc"):
                pcs.append(str(data["pc"]))
        return min(pcs) if pcs else ""

    # ---- 完了・失敗 ----
    def _apply_timing(self, job: Job, timing: dict) -> None:
        job.elapsed_seconds = float(timing.get("elapsed_seconds", 0.0) or 0.0)
        job.exec_count = int(timing.get("exec_count", 0) or 0)
        job.renders = list(timing.get("renders", []) or [])
        job.resolution = list(timing.get("resolution", []) or [])
        if timing.get("started_at"):
            job.started_at = str(timing["started_at"])

    def complete(self, job: Job, timing: dict | None) -> None:
        job.status = model.STATUS_DONE
        job.finished_at = model.now_iso()
        if timing:
            self._apply_timing(job, timing)
        self.update_job(job)
        self._archive_history(job)

    def fail(self, job: Job, error: str, timing: dict | None = None) -> None:
        job.status = model.STATUS_ERROR
        job.finished_at = model.now_iso()
        job.error = str(error or "")
        if timing:
            self._apply_timing(job, timing)
        self.update_job(job)
        self._archive_history(job)

    def cancel(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job is None:
            return
        job.status = model.STATUS_CANCELED
        job.finished_at = model.now_iso()
        self.update_job(job)

    def requeue(self, job_id: str) -> None:
        """失敗/中止ジョブを実行待ちに戻す。"""
        job = self.get_job(job_id)
        if job is None:
            return
        job.status = model.STATUS_QUEUED
        job.claimed_by = ""
        job.started_at = ""
        job.finished_at = ""
        job.error = ""
        self.update_job(job)
        for path in self.claim_dir.glob(f"{job_id}__*.json"):
            try:
                path.unlink()
            except OSError:
                pass

    def _archive_history(self, job: Job) -> None:
        """完了/失敗の記録を history/ に永続化（予測の元データ）。"""
        self._atomic_write(self.history_dir / f"{job.id}.json", job.to_dict())

    def read_history(self) -> list[Job]:
        self.ensure_dirs()
        records: list[Job] = []
        for path in self.history_dir.glob("*.json"):
            data = self._read_json(path)
            if data:
                records.append(Job.from_dict(data))
        records.sort(key=lambda j: (j.finished_at or "", j.id))
        return records
