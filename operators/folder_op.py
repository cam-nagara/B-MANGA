"""B-Name の保存先フォルダを開くオペレーター."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from ..core.mode import MODE_COMA, MODE_PAGE, get_mode
from ..core.work import get_active_page, get_work
from ..utils import log, paths

_logger = log.get_logger(__name__)


def _current_coma_blend_folder() -> Path | None:
    filepath = str(getattr(bpy.data, "filepath", "") or "")
    if not filepath:
        return None
    try:
        blend_path = Path(filepath).resolve()
    except OSError:
        return None
    parts = blend_path.parts
    if len(parts) < 3:
        return None
    page_id, coma_id, fname = parts[-3], parts[-2], parts[-1]
    if (
        paths.is_valid_page_id(page_id)
        and paths.is_valid_coma_id(coma_id)
        and fname == f"{coma_id}.blend"
    ):
        return blend_path.parent
    return None


def resolve_folder(context, target: str = "AUTO") -> Path | None:
    """現在の状況から開くべき保存先フォルダを返す."""
    if target == "COMA":
        current = _current_coma_blend_folder()
        if current is not None:
            return current
    work = get_work(context)
    work_dir_raw = str(getattr(work, "work_dir", "") or "") if work is not None else ""
    work_dir = Path(work_dir_raw) if work_dir_raw else None
    if target == "WORK":
        return work_dir if work_dir and work_dir.exists() else None
    current_mode = get_mode(context)
    if current_mode == MODE_COMA:
        current = _current_coma_blend_folder()
        if current is not None:
            return current
    if work is None or not getattr(work, "loaded", False) or work_dir is None:
        return None
    page = get_active_page(context)
    if target in {"AUTO", "COMA"} and page is not None:
        idx = int(getattr(page, "active_coma_index", -1))
        if 0 <= idx < len(getattr(page, "comas", [])):
            coma = page.comas[idx]
            coma_id = str(getattr(coma, "coma_id", "") or getattr(coma, "id", "") or "")
            if paths.is_valid_coma_id(coma_id):
                return paths.coma_dir(work_dir, page.id, coma_id)
    if page is not None and paths.is_valid_page_id(str(getattr(page, "id", "") or "")):
        return paths.page_dir(work_dir, page.id)
    return work_dir


def _open_folder(path: Path) -> None:
    path = Path(path).resolve()
    if platform.system() == "Windows":
        os.startfile(str(path))  # noqa: S606
        return
    if platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603,S607
        return
    subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607


class BNAME_OT_open_current_folder(Operator):
    bl_idname = "bname.open_current_folder"
    bl_label = "保存フォルダを開く"
    bl_options = {"REGISTER"}

    target: EnumProperty(  # type: ignore[valid-type]
        name="対象",
        items=(
            ("AUTO", "現在の対象", ""),
            ("WORK", "作品", ""),
            ("COMA", "コマ", ""),
        ),
        default="AUTO",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return resolve_folder(context, "AUTO") is not None

    def execute(self, context):
        folder = resolve_folder(context, str(getattr(self, "target", "AUTO") or "AUTO"))
        if folder is None:
            self.report({"WARNING"}, "開くフォルダが見つかりません")
            return {"CANCELLED"}
        try:
            folder.mkdir(parents=True, exist_ok=True)
            _open_folder(folder)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("open current folder failed: %s", folder)
            self.report({"ERROR"}, f"フォルダを開けませんでした: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, "保存フォルダを開きました")
        return {"FINISHED"}


_CLASSES = (BNAME_OT_open_current_folder,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
