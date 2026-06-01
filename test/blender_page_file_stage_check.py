"""ページ一覧とページ用blendファイルが分離されることを確認."""

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
        "bname_dev_page_file_stage",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_page_file_stage"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _mainfile() -> Path:
    return Path(bpy.data.filepath).resolve()


def _managed_kind_count(kind: str) -> int:
    return sum(
        1
        for obj in bpy.data.objects
        if str(obj.get("bname_kind", "") or "") == kind
        and bool(obj.get("bname_managed", False))
    )


def _add_page_only_probe() -> None:
    from bname_dev_page_file_stage.utils import object_naming as on

    obj = bpy.data.objects.new("page_only_balloon_probe", None)
    bpy.context.scene.collection.objects.link(obj)
    obj[on.PROP_KIND] = "balloon"
    obj[on.PROP_ID] = "page_only_balloon_probe"
    obj[on.PROP_PARENT_KEY] = "p0001"
    obj[on.PROP_MANAGED] = True


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_page_file_stage_"))
    mod = None
    try:
        mod = _load_addon()
        work_dir = temp_root / "PageFileStage.bname"
        result = bpy.ops.bname.work_new(filepath=str(work_dir))
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "work.blend").resolve()

        for _ in range(3):
            result = bpy.ops.bname.page_add()
            assert result == {"FINISHED"}, result

        result = bpy.ops.bname.open_page_file(index=0)
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0001" / "page.blend").resolve()
        assert bool(getattr(bpy.context.scene, "bname_overview_mode", True)) is False
        assert str(getattr(bpy.context.scene, "bname_current_page_id", "")) == "p0001"
        assert bpy.data.collections.get("p0002") is None

        _add_page_only_probe()
        result = bpy.ops.bname.work_save()
        assert result == {"FINISHED"}, result
        assert _managed_kind_count("balloon") == 1

        result = bpy.ops.bname.exit_page_file()
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "work.blend").resolve()
        assert _managed_kind_count("balloon") == 0

        result = bpy.ops.bname.open_page_file(index=0)
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0001" / "page.blend").resolve()
        assert _managed_kind_count("balloon") == 1

        work = bpy.context.scene.bname_work
        work.active_page_index = 0
        work.pages[0].active_coma_index = 0
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0001" / "c01" / "c01.blend").resolve()

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0001" / "page.blend").resolve()

        print("BNAME_PAGE_FILE_STAGE_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
