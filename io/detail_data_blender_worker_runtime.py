"""詳細データ移行の子Blender起動と所有トークン境界。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import traceback
from typing import Any, Mapping
import uuid


WORKER_TOKEN_ENV = "BMANGA_DETAIL_MIGRATION_WORKER_TOKEN"
WORKER_CLAIM_ENV = "BMANGA_DETAIL_MIGRATION_WORKER_CLAIM"


def run_worker(
    binary_path: str,
    script_path: Path,
    mode: str,
    page_id: str,
    page_path: Path,
    *,
    request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="bmanga_detail_blender_migration_") as temp:
        root = Path(temp)
        output = root / "result.json"
        request_path = root / "request.json"
        if request is not None:
            _write_json(request_path, dict(request))
        token = uuid.uuid4().hex
        command = worker_command(
            binary_path, script_path, mode, page_id, page_path, output, request_path, token
        )
        environment = os.environ.copy()
        environment[WORKER_TOKEN_ENV] = token
        environment.pop(WORKER_CLAIM_ENV, None)
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
            env=environment,
        )
        if not output.is_file():
            raise RuntimeError(worker_failure_message(result, "結果ファイルがありません"))
        payload = _read_json(output)
        if result.returncode != 0 or not bool(payload.get("ok", False)):
            detail = str(payload.get("error", "変換ワーカーが失敗しました"))
            raise RuntimeError(worker_failure_message(result, detail))
        return payload


def worker_command(
    binary_path: str,
    script_path: Path,
    mode: str,
    page_id: str,
    page_path: Path,
    output: Path,
    request: Path,
    worker_token: str,
) -> list[str]:
    return [
        str(binary_path),
        "--background",
        "--factory-startup",
        "--python",
        str(script_path.resolve()),
        "--",
        "--mode",
        str(mode),
        "--page-id",
        str(page_id),
        "--page-path",
        str(page_path.resolve()),
        "--output",
        str(output),
        "--request",
        str(request),
        "--worker-token",
        str(worker_token),
    ]


def claim_worker_runtime(worker_token: str) -> None:
    """明示トークンを継承した子Blenderだけを移行対象の所有者にする。"""

    inherited = str(os.environ.get(WORKER_TOKEN_ENV, "") or "")
    supplied = str(worker_token or "")
    if not inherited or not supplied or not secrets.compare_digest(inherited, supplied):
        raise RuntimeError("移行ワーカーの所有トークンを確認できません")
    os.environ[WORKER_CLAIM_ENV] = supplied


def worker_failure_message(result, detail: str) -> str:
    stdout = str(getattr(result, "stdout", "") or "")[-3000:]
    stderr = str(getattr(result, "stderr", "") or "")[-3000:]
    return f"{detail}\nBlender stdout:\n{stdout}\nBlender stderr:\n{stderr}"


def worker_main(
    argv: list[str],
    *,
    ensure_runtime,
    inspect_callback,
    convert_callback,
    validate_callback,
) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--page-path", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--request", default="")
    parser.add_argument("--worker-token", required=True)
    values = argv[argv.index("--") + 1:] if "--" in argv else []
    args = parser.parse_args(values)
    output = Path(args.output)
    try:
        claim_worker_runtime(args.worker_token)
        ensure_runtime()
        path = Path(args.page_path).resolve(strict=True)
        if args.mode == "inspect":
            payload = inspect_callback(args.page_id, path)
        elif args.mode == "convert":
            payload = convert_callback(args.page_id, path, Path(args.request))
        elif args.mode == "validate":
            payload = validate_callback(args.page_id, path)
        else:
            raise ValueError(f"unknown mode: {args.mode}")
        _write_json(output, {"ok": True, **payload})
    except BaseException:
        _write_json(output, {"ok": False, "error": traceback.format_exc()})
        raise


def read_json(path: Path) -> dict[str, Any]:
    return _read_json(path)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("移行ワーカー結果のルートがオブジェクトではありません")
    return value


__all__ = [
    "WORKER_CLAIM_ENV",
    "WORKER_TOKEN_ENV",
    "claim_worker_runtime",
    "read_json",
    "run_worker",
    "worker_main",
    "worker_command",
]
