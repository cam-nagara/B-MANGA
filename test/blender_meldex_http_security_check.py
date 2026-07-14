"""Blender 5.1: Meldex receiver security boundary."""

from __future__ import annotations

import http.client
import importlib.util
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_meldex_http"


def _load_addon():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _payload() -> bytes:
    return json.dumps({"contract": "meldex-bmanga-scenario", "version": 1, "source": {"documentId": "d"}, "pages": []}).encode()


def _post(port: int, token: str, *, origin=False, content_type="application/json", body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    headers = {"Content-Type": content_type, "X-B-MANGA-Token": token}
    if origin:
        headers["Origin"] = "http://127.0.0.1"
    connection.request("POST", "/api/scenario", body=body if body is not None else _payload(), headers=headers)
    response = connection.getresponse()
    response.read()
    connection.close()
    return response.status


def _post_declared_length(port: int, token: str, length: int) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    connection.putrequest("POST", "/api/scenario")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("X-B-MANGA-Token", token)
    connection.putheader("Content-Length", str(length))
    connection.endheaders()
    response = connection.getresponse()
    response.read()
    connection.close()
    return response.status


def _get_capabilities(port: int, token: str, *, origin=False):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    headers = {"X-B-MANGA-Token": token}
    if origin:
        headers["Origin"] = "http://127.0.0.1"
    connection.request("GET", "/api/capabilities", headers=headers)
    response = connection.getresponse()
    body = json.loads(response.read().decode("ascii"))
    connection.close()
    return response.status, body


def main() -> None:
    addon = _load_addon()
    receiver = sys.modules[f"{MODULE_NAME}.io.meldex_receiver"]
    try:
        assert not receiver.is_running(), "受信は既定オフ"
        prefs = SimpleNamespace(meldex_token="secret", meldex_enabled=False, log_level="INFO")
        addon.preferences.get_preferences = lambda _context=None: prefs
        addon.preferences.request_user_preferences_save = lambda: None
        settings_bundle = sys.modules[f"{MODULE_NAME}.io.settings_bundle"]
        exported = settings_bundle._preferences_to_dict(bpy.context)
        assert "meldex_token" not in exported and "meldex_enabled" not in exported
        old_token, old_enabled = prefs.meldex_token, prefs.meldex_enabled
        settings_bundle._apply_preferences_from_dict(bpy.context, {"meldex_token": "leak", "meldex_enabled": True})
        assert prefs.meldex_token == old_token and prefs.meldex_enabled == old_enabled
        port, token = _free_port(), "a" * 64
        assert receiver.start(port, token)
        assert _get_capabilities(port, "wrong")[0] == 401
        assert _get_capabilities(port, token, origin=True)[0] == 403
        capability_status, capabilities = _get_capabilities(port, token)
        assert capability_status == 200
        advertised = capabilities["contracts"]["meldex-bmanga-scenario"]
        assert advertised["versions"] == [1, 2]
        assert advertised["features"]["presentationRuby"] is True
        assert _post(port, "wrong") == 401
        assert _post(port, token, origin=True) == 403
        assert _post(port, token, content_type="text/plain") == 415
        assert _post_declared_length(port, token, receiver.MAX_BODY_BYTES + 1) == 413
        assert _post(port, token, body=b"{}") == 400
        assert _post(port, token) == 202
        statuses = [_post(port, token) for _index in range(receiver.QUEUE_CAPACITY)]
        assert 503 in statuses, "満杯の受信キューを拒否しませんでした"
        receiver.stop()
        try:
            _post(port, token)
        except OSError:
            pass
        else:
            raise AssertionError("停止後も接続できました")
        blocked = socket.socket()
        blocked.bind(("127.0.0.1", 0))
        blocked.listen(1)
        try:
            assert not receiver.start(blocked.getsockname()[1], token), "別ポートへ勝手に切り替えました"
        finally:
            blocked.close()
        print("BMANGA_MELDEX_HTTP_SECURITY_OK")
    finally:
        receiver.stop()
        addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
