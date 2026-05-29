"""ワーカー: 共有フォルダから仕事を取り、Blender をヘッドレス起動して実行する。

1PC1本ずつ。GUI のバックグラウンドスレッドから回す想定。
進捗は callback（on_event）で通知する。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
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

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _emit(self, kind: str, **data) -> None:
        try:
            self.on_event(kind, **data)
        except Exception:  # noqa: BLE001 - GUI 側の例外でワーカーを止めない
            pass

    # ---- メインループ ----
    def _loop(self) -> None:
        pc = self.cfg.resolved_pc()
        self._emit("worker_started", pc=pc)
        while not self._stop.is_set():
            job = self.store.find_next_for(pc)
            if job is None:
                self._emit("idle")
                if self._stop.wait(self.cfg.poll_seconds):
                    break
                continue
            if not self.store.claim(job.id, pc):
                # 取得失敗（他PCが先取り）。少し待って再探索。
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

        work = Path(tempfile.mkdtemp(prefix="bname_batch_"))
        result_path = work / "result.json"
        timing_path = work / "timing.json"

        blender = blender_locator.find(self.cfg.blender_exe)
        if not blender:
            self.store.fail(job, "Blender 実行ファイルが見つかりません")
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
        env["BNAME_BATCH_LOG"] = str(timing_path)

        try:
            self._proc = subprocess.Popen(
                cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            stdout, _ = self._proc.communicate()
            returncode = self._proc.returncode
        except Exception as exc:  # noqa: BLE001
            self.store.fail(job, f"起動失敗: {exc}")
            self._emit("job_failed", job=self.store.get_job(job_id), error=str(exc))
            return
        finally:
            self._proc = None

        timing = self._read_json(timing_path)
        result = self._read_json(result_path)
        ok = bool(result and result.get("ok")) and returncode == 0

        if ok:
            self.store.complete(job, timing)
            self._emit("job_done", job=self.store.get_job(job_id))
        else:
            err = (result or {}).get("error") or f"異常終了 (code={returncode})"
            self.store.fail(job, err, timing)
            self._emit("job_failed", job=self.store.get_job(job_id), error=err, log=(stdout or "")[-2000:])

    def _read_json(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def list_presets(cfg: Config, blend_path: str, timeout: float = 180.0) -> list[str]:
    """指定 .blend のプリセット名一覧を取得（GUI のジョブ追加で使う）。"""
    work = Path(tempfile.mkdtemp(prefix="bname_list_"))
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
