"""Blender非依存の単一ファイル書込みtransaction。"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .faults import FaultPoint, check_fault


def _new_staging_path(final_path: Path, role: str) -> Path:
    fd, raw = tempfile.mkstemp(
        prefix=f".{final_path.name}.bmanga-{role}-",
        suffix=final_path.suffix or ".tmp",
        dir=str(final_path.parent),
    )
    os.close(fd)
    return Path(raw)


def _unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


@contextmanager
def staged_export_write(
    final_path: str | os.PathLike[str],
    *,
    image_format: str,
) -> Iterator[Path]:
    """同一directoryでstageし、失敗時は置換前の出力を完全復元する。"""

    final = Path(final_path)
    details = {"path": str(final), "image_format": str(image_format)}
    check_fault(FaultPoint.EXPORT_WRITE, **details)
    final.parent.mkdir(parents=True, exist_ok=True)
    staged = _new_staging_path(final, "stage")
    backup: Path | None = None
    committed = False
    try:
        yield staged
        if not staged.is_file() or staged.stat().st_size <= 0:
            raise OSError(f"staged export is empty: {staged}")
        check_fault(FaultPoint.EXPORT_WRITE_AFTER_STAGE, **details)
        if final.exists():
            backup = _new_staging_path(final, "backup")
            _unlink(backup)
            os.replace(final, backup)
        os.replace(staged, final)
        committed = True
        check_fault(FaultPoint.EXPORT_WRITE_AFTER_COMMIT, **details)
    except BaseException:
        _unlink(staged)
        if committed:
            _unlink(final)
        if backup is not None and backup.exists():
            os.replace(backup, final)
        raise
    else:
        _unlink(backup)
