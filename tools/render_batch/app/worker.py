"""ワーカー: 共有フォルダから仕事を取り、Blender をヘッドレス起動して実行する。

1PC1本ずつ。GUI のバックグラウンドスレッドから回す想定。
進捗は callback（on_event）で通知する。

レンダー実行中も短い間隔でポーリングし、(1) 停止指示で子プロセスを終了、
(2) 実行タイムアウト、(3) 生存印(heartbeat)の更新（孤児検出用）を行う。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import blender_locator
from .config import Config
from .jobstore import JobStore


class Worker:
    def __init__(self, cfg: Config, store: JobStore, on_event=None):
        self.cfg = cfg
        self.store = store
        self.on_event = on_event or (lambda *a, **k: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None

    # ---- スレッド制御 ----
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # 実行中のレンダーを実際に止める（communicate ブロックではなく明示終了）。
        self._terminate_proc()

    def join(self, timeout: float = 10.0) -> None:
        """ワーカースレッドの終了を待つ（停止後の release 書き戻しを取りこぼさない）。"""
        t = self._thread
        if t is not None:
            t.join(timeout)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _terminate_proc(self) -> None:
        """実行中の Blender 子プロセスを終了させる（停止/タイムアウト時）。"""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, kind: str, **data) -> None:
        try:
            self.on_event(kind, **data)
        except Exception:  # noqa: BLE001 - GUI 側の例外でワーカーを止めない
            pass

    # ---- メインループ ----
    def _loop(self) -> None:
        pc = self.cfg.resolved_pc()
        claimant = self.cfg.resolved_claimant()
        stale_seconds = self.cfg.stale_running_minutes * 60.0
        self._emit("worker_started", pc=pc)
        while not self._stop.is_set():
            # 死亡ワーカーが取り残した running ジョブを回収してから探す。
            try:
                self.store.reclaim_stale_running(stale_seconds)
            except Exception:  # noqa: BLE001
                pass
            job = self.store.find_next_for(pc)
            if job is None:
                self._emit("idle")
                if self._stop.wait(self.cfg.poll_seconds):
                    break
                continue
            if not self.store.claim(job.id, pc, claimant):
                # 取得失敗（他PC/他マシンが先取り）。少し待って再探索。
                if self._stop.wait(1.0):
                    break
                continue
            self._run_job(job.id, pc)
        self._emit("worker_stopped", pc=pc)

    def _run_job(self, job_id: str, pc: str) -> None:
        job = self.store.get_job(job_id)
        if job is None:
            return
        self._emit("job_started", job=job)

        work = Path(tempfile.mkdtemp(prefix="bmanga_batch_"))
        result_path = work / "result.json"
        timing_path = work / "timing.json"
        log_path = work / "blender.log"

        blender = blender_locator.find(self.cfg.blender_exe)
        if not blender:
            self._safe_fail(job, "Blender 実行ファイルが見つかりません")
            self._emit("job_failed", job=self.store.get_job(job_id), error="Blender が見つかりません")
            return

        cmd = [
            blender,
            "--background",
            job.blend_path,
            "--python",
            self.cfg.runner_path(),
            "--",
            "--run",
            "--preset",
            job.preset_name,
            "--result",
            str(result_path),
        ]
        env = dict(os.environ)
        env["BMANGA_BATCH_LOG"] = str(timing_path)

        timeout_seconds = self.cfg.job_timeout_minutes * 60.0
        heartbeat_seconds = max(5.0, float(self.cfg.heartbeat_seconds))
        timed_out = False
        returncode: int | None = None
        # 子プロセスの出力はファイルへ流す（PIPE を読み続けないとバッファ詰まりで
        # ハングするため）。ポーリングで停止・タイムアウト・生存印を見る。
        try:
            with open(log_path, "w", encoding="utf-8", errors="replace") as logf:
                self._proc = subprocess.Popen(
                    cmd, env=env, stdout=logf, stderr=subprocess.STDOUT, text=True
                )
                start = time.monotonic()
                last_hb = start
                while True:
                    returncode = self._proc.poll()
                    if returncode is not None:
                        break
                    now = time.monotonic()
                    if self._stop.is_set():
                        self._terminate_proc()
                        returncode = self._proc.poll()
                        break
                    if timeout_seconds > 0 and (now - start) > timeout_seconds:
                        timed_out = True
                        self._terminate_proc()
                        returncode = self._proc.poll()
                        break
                    if (now - last_hb) >= heartbeat_seconds:
                        self.store.heartbeat(job_id)
                        last_hb = now
                    time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            self._safe_fail(job, f"起動失敗: {exc}")
            self._emit("job_failed", job=self.store.get_job(job_id), error=str(exc))
            return
        finally:
            self._proc = None

        # 停止指示で中断された場合は、失敗/完了にせず実行待ちへ戻す（別PC/再開で拾える）。
        if self._stop.is_set() and not timed_out:
            self.store.release(job_id)
            return

        log_tail = self._read_tail(log_path)
        if timed_out:
            self._safe_fail(job, f"タイムアウト（{int(timeout_seconds)}秒）", self._read_json(timing_path))
            self._emit("job_failed", job=self.store.get_job(job_id), error="タイムアウト", log=log_tail)
            return

        timing = self._read_json(timing_path)
        result = self._read_json(result_path)
        ok = bool(result and result.get("ok")) and returncode == 0

        if ok:
            self._safe_complete(job, timing)
            self._emit("job_done", job=self.store.get_job(job_id) or job)
        else:
            err = (result or {}).get("error") or f"異常終了 (code={returncode})"
            self._safe_fail(job, err, timing)
            self._emit("job_failed", job=self.store.get_job(job_id), error=err, log=log_tail)

    def _safe_complete(self, job, timing) -> None:
        try:
            self.store.complete(job, timing)
        except OSError as exc:
            self._emit("error_msg", text=f"完了記録の書き込みに失敗しました: {exc}")

    def _safe_fail(self, job, error, timing=None) -> None:
        try:
            self.store.fail(job, error, timing)
        except OSError as exc:
            self._emit("error_msg", text=f"失敗記録の書き込みに失敗しました: {exc}")

    def _read_json(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _read_tail(self, path: Path, limit: int = 2000) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""


def list_presets(cfg: Config, blend_path: str, timeout: float = 180.0) -> list[str]:
    """指定 .blend のプリセット名一覧を取得（GUI のジョブ追加で使う）。"""
    work = Path(tempfile.mkdtemp(prefix="bmanga_list_"))
    result_path = work / "list.json"
    blender = blender_locator.find(cfg.blender_exe)
    if not blender:
        raise RuntimeError("Blender 実行ファイルが見つかりません")
    cmd = [
        blender,
        "--background",
        blend_path,
        "--python",
        cfg.runner_path(),
        "--",
        "--list-presets",
        "--result",
        str(result_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(data.get("presets", []) or [])
