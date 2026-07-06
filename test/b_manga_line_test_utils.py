"""Shared helpers for B-MANGA Line Blender tests."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def temporary_line_preset_store():
    """Route line preset writes to a disposable directory for this test."""
    old_value = os.environ.get("BMANGA_LINE_PRESET_STORE_DIR")
    with tempfile.TemporaryDirectory(prefix="bmanga_line_presets_") as temp_dir:
        os.environ["BMANGA_LINE_PRESET_STORE_DIR"] = temp_dir
        try:
            yield Path(temp_dir) / "b_manga_line_presets.json"
        finally:
            if old_value is None:
                os.environ.pop("BMANGA_LINE_PRESET_STORE_DIR", None)
            else:
                os.environ["BMANGA_LINE_PRESET_STORE_DIR"] = old_value
