"""Windows上でのjournal／sidecar用durable file primitive。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class DurableStorageError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json_mapping(path: Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DurableStorageError(f"JSONを読めません: {path}") from exc
    if not isinstance(value, dict):
        raise DurableStorageError(f"JSONルートがobjectではありません: {path}")
    return value


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(json_bytes(data))
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def json_bytes(data: Mapping[str, Any]) -> bytes:
    text = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    return text.encode("utf-8")


def replace_with_retry(source: Path, destination: Path) -> None:
    delay = 0.05
    for attempt in range(7):
        try:
            os.replace(source, destination)
            return
        except OSError:
            if attempt == 6:
                raise
            time.sleep(delay)
            delay *= 2


def fsync_file(path: Path) -> None:
    with Path(path).open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())


def new_transaction_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = (
    "DurableStorageError",
    "atomic_write_json",
    "fsync_file",
    "json_bytes",
    "new_transaction_id",
    "read_json_mapping",
    "replace_with_retry",
    "sha256",
    "utc_now",
)
