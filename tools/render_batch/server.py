"""連続実行アプリのローカルWebサーバ（標準ライブラリのみ）。

Blender 同梱 Python でも動く（http.server / json / subprocess / threading のみ使用）。
127.0.0.1 だけにバインドし、ブラウザ(Edge/Chrome の --app 窓)をUIにする。
中核ロジック（jobstore / worker / predictor / model / config）は app/ を流用する。

単一インスタンス: 固定ポート(既定 8765)。既に自分のサーバが居れば、起動側は
そこへブラウザを向けるだけにする（main 参照）。レンダリング中はブラウザ窓を
閉じてもサーバは生き続ける。遊休（窓を閉じてワーカー停止中）なら自動終了する。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app import config as config_mod
from app import model, worker
from app.jobstore import JobStore
from app.model import Job
from app.predictor import Predictor, project_finish_times

PORT = 8765
WEB_DIR = Path(__file__).resolve().parent / "web"
IDLE_EXIT_SECONDS = 30.0  # 窓を閉じてポーリングが止まり、かつ非実行ならこの秒数で自動終了

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _basename(path: str) -> str:
    s = str(path or "").replace("\\", "/")
    return s.rsplit("/", 1)[-1] or s


class AppState:
    """サーバ全体の状態（設定・ストア・ワーカー）。スレッド安全に操作する。"""

    def __init__(self) -> None:
        self.cfg = config_mod.load()
        self.store = self._make_store()
        self.worker: worker.Worker | None = None
        self.lock = threading.RLock()
        self.activity = {"kind": "stopped", "text": "停止中", "error": ""}
        self.shutdown_event = threading.Event()
        self.last_poll = time.monotonic()

    def _make_store(self) -> JobStore | None:
        return JobStore(self.cfg.shared_root, self.cfg.sync_grace_seconds) if self.cfg.shared_root else None

    # ---- ワーカーイベント（ポーリングで拾えない「現在の活動」を保持）----
    def _on_event(self, kind: str, **data) -> None:
        with self.lock:
            if kind == "worker_started":
                self.activity = {"kind": "running", "text": "実行中（待機）", "error": ""}
            elif kind == "idle":
                self.activity = {"kind": "idle", "text": "待機中（仕事なし）", "error": ""}
            elif kind == "job_started":
                job = data.get("job")
                self.activity = {"kind": "job", "text": f"実行中: {job.label() if job else ''}", "error": ""}
            elif kind == "job_done":
                self.activity = {"kind": "running", "text": "完了。次を待機", "error": ""}
            elif kind == "job_failed":
                self.activity = {"kind": "error", "text": "失敗", "error": str(data.get("error", ""))}
            elif kind == "worker_stopped":
                self.activity = {"kind": "stopped", "text": "停止中", "error": ""}
            elif kind == "error_msg":
                self.activity = {**self.activity, "error": str(data.get("text", ""))}

    def touch_poll(self) -> None:
        self.last_poll = time.monotonic()

    def worker_running(self) -> bool:
        return bool(self.worker and self.worker.is_running())

    # ---- 状態 ----
    def state(self) -> dict:
        with self.lock:
            cfg = self.cfg
            jobs_payload: list[dict] = []
            history_payload: list[dict] = []
            if self.store is not None:
                jobs = self.store.list_jobs()
                predictor = Predictor(self.store.read_history())
                active = [j for j in jobs if j.status in model.ACTIVE_STATUSES]
                eta = project_finish_times(active, predictor, now_epoch=time.time())
                for idx, j in enumerate(jobs, 1):
                    psecs, why = predictor.predict(j)
                    jobs_payload.append({
                        "id": j.id,
                        "order": idx,
                        "file": _basename(j.blend_path),
                        "blend_path": j.blend_path,
                        "preset": j.preset_name,
                        "target_pc": j.target_pc,
                        "status": j.status,
                        "predict_seconds": psecs,
                        "predict_why": why,
                        "eta": eta.get(j.id, 0),
                        "elapsed": j.elapsed_seconds,
                        "claimed_by": j.claimed_by,
                    })
                for j in reversed(self.store.read_history()):
                    history_payload.append({
                        "finished_at": j.finished_at,
                        "file": _basename(j.blend_path),
                        "preset": j.preset_name,
                        "pc": j.claimed_by,
                        "elapsed": j.elapsed_seconds,
                        "resolution": list(j.resolution or []),
                        "status": j.status,
                    })
            return {
                "configured": self.store is not None,
                "worker": {"running": self.worker_running(), "pc": cfg.resolved_pc(), **self.activity},
                "config": {
                    "shared_root": cfg.shared_root,
                    "pc_name": cfg.pc_name,
                    "blender_exe": cfg.blender_exe,
                    "sync_grace_seconds": cfg.sync_grace_seconds,
                    "poll_seconds": cfg.poll_seconds,
                    "job_timeout_minutes": cfg.job_timeout_minutes,
                    "stale_running_minutes": cfg.stale_running_minutes,
                },
                "jobs": jobs_payload,
                "history": history_payload,
            }

    # ---- 操作 ----
    def list_presets(self, blend_path: str) -> list[str]:
        if not blend_path:
            raise RuntimeError("ファイルが指定されていません")
        return worker.list_presets(self.cfg, blend_path)

    def pick_blend(self) -> str:
        """ネイティブのファイル選択ダイアログ(.blend)。Windows は PowerShell を使う。"""
        if sys.platform != "win32":
            return ""
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
            "$f = New-Object System.Windows.Forms.OpenFileDialog; "
            "$f.Filter = 'Blend (*.blend)|*.blend|All files (*.*)|*.*'; "
            "$f.Title = 'レンダリングする .blend を選択'; "
            "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
            "{ [Console]::Out.Write($f.FileName) }"
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", ps],
                capture_output=True, text=True, timeout=300,
            )
            return out.stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    def add_jobs(self, blend_path: str, presets: list[str]) -> None:
        with self.lock:
            if self.store is None:
                raise RuntimeError("先に設定で共有フォルダを指定してください")
            if not blend_path:
                raise RuntimeError("ファイルが指定されていません")
            for name in presets:
                if name:
                    self.store.add_job(Job(blend_path=blend_path, preset_name=str(name)))

    def remove_job(self, job_id: str) -> None:
        with self.lock:
            if self.store is not None and job_id:
                self.store.remove_job(job_id)

    def reorder(self, ids: list[str]) -> None:
        with self.lock:
            if self.store is not None and ids:
                self.store.reorder(ids)

    def requeue(self, job_id: str) -> bool:
        with self.lock:
            return bool(self.store is not None and job_id and self.store.requeue(job_id))

    def purge_done(self) -> int:
        with self.lock:
            return self.store.purge_finished() if self.store is not None else 0

    def start_worker(self) -> None:
        with self.lock:
            if self.store is None:
                raise RuntimeError("先に設定で共有フォルダを指定してください")
            if self.worker_running():
                return
            self.worker = worker.Worker(self.cfg, self.store, on_event=self._on_event)
            self.worker.start()

    def stop_worker(self) -> None:
        with self.lock:
            if self.worker is not None:
                self.worker.stop()

    def save_config(self, data: dict) -> None:
        with self.lock:
            new_root = str(data.get("shared_root", "") or "").strip()
            if self.worker_running() and new_root != self.cfg.shared_root:
                raise RuntimeError("実行中は共有フォルダを変更できません。先に停止してください。")
            cfg = self.cfg
            cfg.shared_root = new_root
            cfg.pc_name = str(data.get("pc_name", "") or "").strip()
            blender = str(data.get("blender_exe", "") or "").strip()
            if blender:
                cfg.blender_exe = blender
            for key in ("sync_grace_seconds", "poll_seconds", "job_timeout_minutes", "stale_running_minutes"):
                if key in data and data[key] not in ("", None):
                    try:
                        setattr(cfg, key, float(data[key]))
                    except (TypeError, ValueError):
                        raise RuntimeError(f"{key} は数値で入力してください")
            config_mod.save(cfg)
            self.store = self._make_store()

    def request_shutdown(self) -> None:
        with self.lock:
            if self.worker is not None:
                self.worker.stop()
        self.shutdown_event.set()

    # ---- 遊休自動終了 ----
    def idle_watch(self) -> None:
        while not self.shutdown_event.wait(5.0):
            if self.worker_running():
                continue
            if (time.monotonic() - self.last_poll) > IDLE_EXIT_SECONDS:
                self.shutdown_event.set()
                return


class Handler(BaseHTTPRequestHandler):
    app: AppState = None  # main() で差し込む

    def log_message(self, *args) -> None:  # noqa: D401 - サーバログを抑制
        return

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _safe_web(self, name: str) -> Path | None:
        target = (WEB_DIR / name).resolve()
        if target == WEB_DIR.resolve() or WEB_DIR.resolve() in target.parents:
            return target
        return None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._file(WEB_DIR / "index.html")
            return
        if path == "/api/state":
            self.app.touch_poll()
            self._json(self.app.state())
            return
        if path in ("/app.js", "/style.css"):
            self._file(WEB_DIR / path.lstrip("/"))
            return
        if path.startswith("/static/"):
            target = self._safe_web(path[len("/static/"):])
            if target is not None:
                self._file(target)
                return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001
            data = {}
        try:
            result = self._dispatch(path, data if isinstance(data, dict) else {})
        except Exception as exc:  # noqa: BLE001 - APIエラーは200+{ok:false}で返す
            self._json({"ok": False, "error": str(exc)})
            return
        self._json({"ok": True, **(result or {})})

    def _dispatch(self, path: str, data: dict) -> dict:
        app = self.app
        if path == "/api/presets":
            return {"presets": app.list_presets(str(data.get("blend_path", "")))}
        if path == "/api/pick_blend":
            return {"path": app.pick_blend()}
        if path == "/api/job/add":
            app.add_jobs(str(data.get("blend_path", "")), list(data.get("presets", []) or []))
            return {}
        if path == "/api/job/remove":
            app.remove_job(str(data.get("id", "")))
            return {}
        if path == "/api/job/reorder":
            app.reorder([str(x) for x in (data.get("ids", []) or [])])
            return {}
        if path == "/api/job/requeue":
            return {"requeued": app.requeue(str(data.get("id", "")))}
        if path == "/api/job/purge_done":
            return {"removed": app.purge_done()}
        if path == "/api/worker/start":
            app.start_worker()
            return {}
        if path == "/api/worker/stop":
            app.stop_worker()
            return {}
        if path == "/api/config/save":
            app.save_config(data)
            return {}
        if path == "/api/shutdown":
            app.request_shutdown()
            return {}
        raise RuntimeError(f"unknown endpoint: {path}")


def _server_alive(port: int) -> bool:
    """既に自分のサーバが動いているか（/api/state が応答するか）。"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=0.8) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def _find_browser() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for exe in candidates:
        if os.path.isfile(exe):
            return exe
    return None


def open_app_window(url: str) -> None:
    """Edge/Chrome の --app（枠だけのアプリ窓）で開く。無ければ既定ブラウザ。"""
    exe = _find_browser()
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}", "--window-size=1180,780"])
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def main(open_window: bool = True) -> None:
    url = f"http://127.0.0.1:{PORT}/"
    if _server_alive(PORT):
        # 既に動いているので窓だけ開いて終わる（単一インスタンス）。
        if open_window:
            open_app_window(url)
        return

    app = AppState()
    Handler.app = app
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # ポート使用中（競合）。ブラウザだけ開いて諦める。
        if open_window:
            open_app_window(url)
        return

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=app.idle_watch, daemon=True).start()
    if open_window:
        open_app_window(url)
    try:
        app.shutdown_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
