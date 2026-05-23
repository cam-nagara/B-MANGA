"""Blender runtime check: 保存フォルダを開くボタンの対象解決."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_open_current_folder",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_open_current_folder"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    mod = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bname_open_current_folder_"))
    try:
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "FolderCheck.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev_open_current_folder.core.work import get_work
        from bname_dev_open_current_folder.operators import folder_op
        from bname_dev_open_current_folder.utils import paths

        work = get_work(bpy.context)
        assert work is not None
        work_dir = Path(work.work_dir)
        page = work.pages[0]
        coma = page.comas[0]
        page.active_coma_index = 0

        assert folder_op.resolve_folder(bpy.context, "WORK") == work_dir
        assert folder_op.resolve_folder(bpy.context, "AUTO") == paths.coma_dir(
            work_dir,
            page.id,
            coma.coma_id,
        )
        assert folder_op.resolve_folder(bpy.context, "COMA") == paths.coma_dir(
            work_dir,
            page.id,
            coma.coma_id,
        )

        opened: list[Path] = []
        original_open_folder = folder_op._open_folder  # noqa: SLF001
        folder_op._open_folder = lambda path: opened.append(Path(path))  # noqa: SLF001
        try:
            result = bpy.ops.bname.open_current_folder("EXEC_DEFAULT", target="WORK")
        finally:
            folder_op._open_folder = original_open_folder  # noqa: SLF001
        assert result == {"FINISHED"}, result
        assert opened == [work_dir.resolve()]
    finally:
        try:
            mod.unregister()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    print("BNAME_OPEN_CURRENT_FOLDER_CHECK_OK")


main()
