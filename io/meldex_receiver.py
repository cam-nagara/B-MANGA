"""Authenticated, opt-in local HTTP receiver for Meldex scenarios."""

from __future__ import annotations

import hmac
import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import bpy

from ..core.work import get_work
from ..utils import log
from . import meldex_scenario_import
from .meldex_contract import ContractError, SUPPORTED_CONTRACT_VERSIONS, validate_payload

_logger = log.get_logger(__name__)
TOKEN_HEADER = "X-B-MANGA-Token"
DEFAULT_PORT = 47817
MAX_BODY_BYTES = 2 * 1024 * 1024
QUEUE_CAPACITY = 8
DISABLE_RECEIVER_ENV = "BMANGA_DISABLE_MELDEX_RECEIVER"

_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_in_queue: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_CAPACITY)
_timer_registered = False


class _ReceiverServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, token: str):
        self.token = token
        super().__init__(address, _MeldexHandler)


class _MeldexHandler(BaseHTTPRequestHandler):
    server: _ReceiverServer

    def log_message(self, format, *args):  # noqa: A003
        _logger.debug("meldex http: " + format, *args)

    def _discard_rejected_body(self) -> None:
        """Drain a bounded body so Windows does not replace the HTTP rejection with RST."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            return
        previous_timeout = self.connection.gettimeout()
        try:
            self.connection.settimeout(0.25)
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 64 * 1024))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self.connection.settimeout(previous_timeout)

    def _reject_request(self, status: int, code: str) -> bool:
        self._discard_rejected_body()
        self._json_response(status, code)
        return False

    def _json_response(self, status: int, code: str, payload: dict | None = None) -> None:
        response = payload if payload is not None else {"status": code}
        body = json.dumps(response, separators=(",", ":")).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _request_allowed(self) -> bool:
        if self.client_address[0] != "127.0.0.1":
            return self._reject_request(403, "forbidden")
        if "Origin" in self.headers:
            return self._reject_request(403, "origin-rejected")
        supplied = str(self.headers.get(TOKEN_HEADER, "") or "")
        if not supplied or not hmac.compare_digest(supplied, self.server.token):
            return self._reject_request(401, "unauthorized")
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._request_allowed():
            return
        if self.path != "/api/capabilities":
            self._json_response(404, "not-found")
            return
        self._json_response(200, "ok", {
            "contracts": {
                "meldex-bmanga-scenario": {
                    "versions": list(SUPPORTED_CONTRACT_VERSIONS),
                    "features": {
                        "presentationText": True,
                        "presentationRuby": True,
                        "rubySpanOrigins": True,
                        "rubySegments": True,
                        "rowPresentationOverride": True,
                    },
                },
            },
        })

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/scenario":
            self._json_response(404, "not-found")
            return
        if not self._request_allowed():
            return
        media_type = str(self.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self._json_response(415, "json-required")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json_response(413 if length > MAX_BODY_BYTES else 400, "invalid-size")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            validate_payload(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ContractError):
            self._json_response(400, "invalid-contract")
            return
        try:
            _in_queue.put_nowait(payload)
        except queue.Full:
            self._json_response(503, "queue-full")
            return
        self._json_response(202, "accepted")


def start(port: int = DEFAULT_PORT, token: str = "") -> bool:
    global _server, _server_thread, _timer_registered
    if _server is not None:
        return True
    if os.environ.get(DISABLE_RECEIVER_ENV, "").strip() == "1":
        _logger.info("Meldex receiver disabled by process environment")
        return True
    if not token:
        _logger.error("Meldex receiver token is missing")
        return False
    try:
        _server = _ReceiverServer(("127.0.0.1", int(port)), token)
    except OSError as exc:
        _logger.error("Meldex receiver port unavailable: %d (%s)", port, exc)
        return False
    _server_thread = threading.Thread(target=_server.serve_forever, name="BManga-MeldexReceiver", daemon=True)
    _server_thread.start()
    if not _timer_registered:
        try:
            from ..utils import lifecycle_scheduler

            lifecycle_scheduler.schedule(
                "meldex.poll_queue",
                _poll_queue,
                first_interval=0.1,
                persistent=True,
                restart_on_invalidate=True,
                on_cancel=_mark_timer_stopped,
            )
            _timer_registered = True
        except Exception:  # noqa: BLE001
            _logger.exception("Meldex receiver queue timer failed")
            stop()
            return False
    _logger.info("Meldex receiver started on the configured local port")
    return True


def stop() -> None:
    global _server, _server_thread, _timer_registered
    server, thread = _server, _server_thread
    _server = None
    _server_thread = None
    if server is not None:
        server.shutdown()
        server.server_close()
    if thread is not None:
        thread.join(timeout=3.0)
    if _timer_registered:
        try:
            from ..utils import lifecycle_scheduler

            lifecycle_scheduler.cancel("meldex.poll_queue")
        except Exception:  # noqa: BLE001
            pass
        _timer_registered = False
    _clear_queue()
    _logger.info("Meldex receiver stopped")


def restart_from_preferences(context=None) -> bool:
    from ..preferences import get_preferences

    stop()
    prefs = get_preferences(context)
    if prefs is None or not bool(prefs.meldex_enabled):
        return True
    return start(int(prefs.meldex_port), str(prefs.meldex_token or ""))


def is_running() -> bool:
    return _server is not None and _server_thread is not None and _server_thread.is_alive()


def _mark_timer_stopped() -> None:
    global _timer_registered
    _timer_registered = False


def _clear_queue() -> None:
    while True:
        try:
            _in_queue.get_nowait()
        except queue.Empty:
            return


def _poll_queue() -> float:
    try:
        for _index in range(QUEUE_CAPACITY):
            try:
                payload = _in_queue.get_nowait()
            except queue.Empty:
                break
            work = get_work()
            if work is None or not work.loaded or not work.work_dir:
                _logger.warning("Meldex scenario ignored because no work is open")
                continue
            try:
                result = meldex_scenario_import.import_payload(bpy.context, work, payload)
                _logger.info(
                    "Meldex scenario imported: pagesAdded=%d created=%d updated=%d",
                    result["pagesAdded"], result["created"], result["updated"],
                )
            except Exception:  # noqa: BLE001
                _logger.exception("Meldex scenario import failed")
    except Exception:  # noqa: BLE001
        _logger.exception("Meldex queue polling failed")
    return 0.5


def register() -> None:
    restart_from_preferences()


def unregister() -> None:
    stop()
