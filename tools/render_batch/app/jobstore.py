"""共有フォルダ上のジョブ置き場（Dropbox 分担型）。

ディレクトリ構成（共有フォルダ直下）::

    <root>/
      queue/    <id>.json        … 全ジョブ（status は中の status フィールド）
      claim/    <id>__<pc>.json  … 取得宣言（二重実行防止のタイブレーク用）
      history/  <id>.json        … 完了/失敗の記録（done/error）

Dropbox は同時書き込みで「競合コピー」を作り、同期に時間差がある。
そこで実行権の取得は「宣言 → 同期猶予を待つ → 候補を見て決定的に勝者決定」
の方式にする。1PC1本ずつ・重い処理という前提なので、衝突頻度は低い。

同一プロセス内では、ワーカースレッドとUI(リクエスト)スレッドが同じ JobStore を
共有して同じ queue ファイルを read-modify-write する。これらが競合して更新を
取りこぼさない（lost update を防ぐ）よう、書き換え系メソッドは内部の RLock で
直列化する。読み取り（list_jobs/get_job/read_history）は _atomic_write による
ファイル単位の原子性で完全なファイルしか見えないため、ロックを取らない。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
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
        # 書き換え系の read-modify-write を直列化する（ワーカー/UIスレッド間）。
        self._lock = threading.RLock()

    # ---- 基盤 ----
    def ensure_dirs(self) -> None:
        for d in (self.queue_dir, self.claim_dir, self.history_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        text = json.dumps(data, ensure_ascii=False, indent=2)
        # Dropbox/OneDrive が一時的にファイルを掴むと os.replace が PermissionError
        # を投げ得る。数回リトライし、最後まで失敗したら孤児 tmp を片付けてから送出する
        # （呼び出し側で握れるように。握り潰すと書き込み消失に気づけない）。
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                tmp.write_text(text, encoding="utf-8")
                os.replace(str(tmp), str(path))
                return
            except OSError as exc:
                last_err = exc
                time.sleep(0.2 * (attempt + 1))
        try:
            tmp.unlink()
        except OSError:
            pass
        if last_err is not None:
            raise last_err

    def _read_json(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ---- キュー操作 ----
    def add_job(self, job: Job) -> Job:
        self.ensure_dirs()
        with self._lock:
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
        with self._lock:
            try:
                (self.queue_dir / f"{job_id}.json").unlink()
            except OSError:
                pass
            self._remove_claims(job_id)

    def reorder(self, ordered_ids: list[str]) -> None:
        """与えられた順に order を振り直す（10刻み）。"""
        with self._lock:
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

    def claim(self, job_id: str, pc: str, claimant: str | None = None) -> bool:
        """実行権を取得する。勝者なら True。

        手順:
          1) claim/<id>__<claimant>.json を書く（自分の宣言）
          2) 同期猶予を待つ
          3) その job への全宣言を読み、claimant の辞書順で最小を勝者とする
          4) 勝者が自分なら queue の status を running にして True

        ``claimant`` は同名PC(pc_name 重複)でもマシンごとに必ず異なる一意IDを渡す
        （省略時は ``pc``。後方互換）。なお Dropbox の同期遅延が宣言の伝播より長い
        場合は排他を保証しきれない best-effort である点は変わらない。

        同期猶予の sleep はロックの外で行う（UIポーリングを止めないため）。
        ローカルの read-modify-write 区間だけをロックする。
        """
        self.ensure_dirs()
        claimant = claimant or pc
        with self._lock:
            job = self.get_job(job_id)
            if job is None or job.status != model.STATUS_QUEUED:
                return False
            safe = claimant.replace("/", "_").replace("\\", "_")
            my_claim = self.claim_dir / f"{job_id}__{safe}.json"
            self._atomic_write(
                my_claim, {"job_id": job_id, "pc": pc, "claimant": claimant, "at": model.now_iso()}
            )

        if self.sync_grace_seconds > 0:
            time.sleep(self.sync_grace_seconds)

        with self._lock:
            if self._claim_winner(job_id) != claimant:
                try:
                    my_claim.unlink()
                except OSError:
                    pass
                return False
            # 競合チェック: 取得直前に他PC/他マシンが running 化していないか。
            job = self.get_job(job_id)
            if job is None or job.status != model.STATUS_QUEUED:
                try:
                    my_claim.unlink()
                except OSError:
                    pass
                return False
            job.status = model.STATUS_RUNNING
            job.claimed_by = pc
            job.started_at = model.now_iso()
            job.heartbeat = job.started_at
            self.update_job(job)
            return True

    def _claim_winner(self, job_id: str) -> str:
        """その job への宣言のうち、claimant の辞書順で最小を勝者とする。

        辞書順 tie-break は決定的なので、同じ宣言集合を見た全PCが同結論に至る。
        旧形式(claimant 無し)の宣言は pc で代替する（後方互換）。
        """
        claimants: list[str] = []
        for path in self.claim_dir.glob(f"{job_id}__*.json"):
            data = self._read_json(path)
            if not data:
                continue
            who = data.get("claimant") or data.get("pc")
            if who:
                claimants.append(str(who))
        return min(claimants) if claimants else ""

    def heartbeat(self, job_id: str) -> None:
        """実行中ジョブの生存印を更新する（孤児検出用）。best-effort。"""
        try:
            with self._lock:
                job = self.get_job(job_id)
                if job is not None and job.status == model.STATUS_RUNNING:
                    job.heartbeat = model.now_iso()
                    self.update_job(job)
        except OSError:
            pass

    def release(self, job_id: str) -> None:
        """実行中ジョブを実行待ちに戻す（停止時など）。claim も消す。"""
        with self._lock:
            job = self.get_job(job_id)
            if job is None or job.status != model.STATUS_RUNNING:
                return
            job.status = model.STATUS_QUEUED
            job.claimed_by = ""
            job.started_at = ""
            job.heartbeat = ""
            self.update_job(job)
            self._remove_claims(job_id)

    def reclaim_stale_running(self, max_silence_seconds: float) -> int:
        """生存印が途切れた running ジョブ（死亡ワーカーの取り残し）を queued に戻す。

        戻した件数を返す。max_silence_seconds <= 0 なら何もしない。生存印(heartbeat)
        基準なので、長時間レンダー中でもワーカーが生きていれば回収されない。
        """
        if max_silence_seconds <= 0:
            return 0
        now = time.time()
        reclaimed = 0
        with self._lock:
            for job in self.list_jobs():
                if job.status != model.STATUS_RUNNING:
                    continue
                epoch = self._iso_epoch(job.heartbeat or job.started_at)
                if epoch <= 0:
                    continue
                if now - epoch > max_silence_seconds:
                    job.status = model.STATUS_QUEUED
                    job.claimed_by = ""
                    job.started_at = ""
                    job.heartbeat = ""
                    self.update_job(job)
                    self._remove_claims(job.id)
                    reclaimed += 1
        return reclaimed

    @staticmethod
    def _iso_epoch(s: str) -> float:
        try:
            return datetime.fromisoformat(str(s)).timestamp()
        except (ValueError, TypeError):
            return 0.0

    def _remove_claims(self, job_id: str) -> None:
        for path in self.claim_dir.glob(f"{job_id}__*.json"):
            try:
                path.unlink()
            except OSError:
                pass

    def purge_finished(self) -> int:
        """完了(done)ジョブの queue ファイルを片付ける（記録は history に残る）。

        失敗/中止は再投入や確認のため残す。戻り値は削除件数。
        """
        removed = 0
        with self._lock:
            for job in self.list_jobs():
                if job.status == model.STATUS_DONE:
                    try:
                        (self.queue_dir / f"{job.id}.json").unlink()
                        removed += 1
                    except OSError:
                        pass
                    self._remove_claims(job.id)
        return removed

    # ---- 完了・失敗 ----
    def _apply_timing(self, job: Job, timing: dict) -> None:
        job.elapsed_seconds = float(timing.get("elapsed_seconds", 0.0) or 0.0)
        job.exec_count = int(timing.get("exec_count", 0) or 0)
        job.renders = list(timing.get("renders", []) or [])
        job.resolution = list(timing.get("resolution", []) or [])
        if timing.get("started_at"):
            job.started_at = str(timing["started_at"])

    def complete(self, job: Job, timing: dict | None) -> None:
        with self._lock:
            job.status = model.STATUS_DONE
            job.finished_at = model.now_iso()
            job.heartbeat = ""
            if timing:
                self._apply_timing(job, timing)
            self.update_job(job)
            self._archive_history(job)
            self._remove_claims(job.id)

    def fail(self, job: Job, error: str, timing: dict | None = None) -> None:
        with self._lock:
            job.status = model.STATUS_ERROR
            job.finished_at = model.now_iso()
            job.heartbeat = ""
            job.error = str(error or "")
            if timing:
                self._apply_timing(job, timing)
            self.update_job(job)
            self._archive_history(job)
            self._remove_claims(job.id)

    def cancel(self, job_id: str) -> None:
        with self._lock:
            job = self.get_job(job_id)
            if job is None:
                return
            job.status = model.STATUS_CANCELED
            job.finished_at = model.now_iso()
            job.heartbeat = ""
            self.update_job(job)
            self._remove_claims(job_id)

    def requeue(self, job_id: str) -> bool:
        """終了したジョブ(失敗/中止/完了)を実行待ちに戻す。実行中(running)は戻さない。

        戻したら True。running を渡された場合は二重実行防止のため何もせず False。
        """
        with self._lock:
            job = self.get_job(job_id)
            if job is None:
                return False
            if job.status not in (model.STATUS_ERROR, model.STATUS_CANCELED, model.STATUS_DONE):
                return False
            job.status = model.STATUS_QUEUED
            job.claimed_by = ""
            job.started_at = ""
            job.heartbeat = ""
            job.finished_at = ""
            job.error = ""
            self.update_job(job)
            self._remove_claims(job_id)
            return True

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
