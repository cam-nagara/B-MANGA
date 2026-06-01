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


def _page_preview_objects() -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get("bname_kind", "") or "") == "page_preview"
    ]


def _visible_page_preview_objects() -> list[bpy.types.Object]:
    return [obj for obj in _page_preview_objects() if not bool(getattr(obj, "hide_viewport", False))]


def _managed_object(kind: str, bname_id: str):
    for obj in bpy.data.objects:
        if (
            str(obj.get("bname_kind", "") or "") == kind
            and str(obj.get("bname_id", "") or "") == bname_id
            and bool(obj.get("bname_managed", False))
        ):
            return obj
    return None


def _add_page_only_probe() -> None:
    from bname_dev_page_file_stage.utils import object_naming as on

    obj = bpy.data.objects.new("page_only_balloon_probe", None)
    bpy.context.scene.collection.objects.link(obj)
    obj[on.PROP_KIND] = "balloon"
    obj[on.PROP_ID] = "page_only_balloon_probe"
    obj[on.PROP_PARENT_KEY] = "p0001"
    obj[on.PROP_MANAGED] = True


def _add_other_page_balloon_entry(work) -> None:
    entry = work.pages[1].balloons.add()
    entry.id = "other_page_balloon"
    entry.title = "other_page_balloon"
    entry.parent_key = "p0002"
    entry.x_mm = 20.0
    entry.y_mm = 20.0
    entry.width_mm = 40.0
    entry.height_mm = 30.0


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

        _add_other_page_balloon_entry(bpy.context.scene.bname_work)
        result = bpy.ops.bname.open_page_file(index=0)
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0001" / "page.blend").resolve()
        assert bool(getattr(bpy.context.scene, "bname_overview_mode", True)) is False
        assert str(getattr(bpy.context.scene, "bname_current_page_id", "")) == "p0001"
        assert bpy.data.collections.get("p0002") is None
        assert _managed_object("balloon", "other_page_balloon") is None
        previews = _page_preview_objects()
        assert len(previews) == 4
        assert len(_visible_page_preview_objects()) == 4
        assert (work_dir / "p0002" / "page_preview.png").is_file()
        assert int(getattr(bpy.context.scene, "bname_page_preview_page_radius", -1)) == 3
        assert abs(float(getattr(bpy.context.scene, "bname_page_preview_resolution_percentage", 0.0)) - 25.0) < 0.001

        from bname_dev_page_file_stage.utils import page_preview_object

        work = bpy.context.scene.bname_work
        rects = page_preview_object.preview_rects_mm(bpy.context.scene, work)
        assert "p0002" in rects
        index, x0, y0, x1, y1 = rects["p0002"]
        assert index == 1
        hit = page_preview_object.page_index_at_world_mm(
            bpy.context.scene,
            work,
            (x0 + x1) * 0.5,
            (y0 + y1) * 0.5,
        )
        assert hit == 1
        bpy.context.scene.bname_page_preview_enabled = False
        assert all(obj.hide_viewport for obj in _page_preview_objects())
        bpy.context.scene.bname_page_preview_enabled = True
        assert any(not obj.hide_viewport for obj in _page_preview_objects())
        bpy.context.scene.bname_page_preview_page_radius = 1
        rects = page_preview_object.preview_rects_mm(bpy.context.scene, work)
        assert set(rects) == {"p0001", "p0002"}
        assert len(_visible_page_preview_objects()) == 2
        bpy.context.scene.bname_page_preview_resolution_percentage = 50.0
        from PIL import Image

        preview_size = Image.open(work_dir / "p0002" / "page_preview.png").size
        assert max(preview_size) == 768

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
        assert bpy.data.collections.get("p0002") is None
        assert len(_page_preview_objects()) == 4

        result = bpy.ops.bname.page_select(index=1)
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0002" / "page.blend").resolve()
        assert str(getattr(bpy.context.scene, "bname_current_page_id", "")) == "p0002"
        assert int(getattr(bpy.context.scene.bname_work, "active_page_index", -1)) == 1
        assert bpy.data.collections.get("p0001") is None
        assert _managed_object("balloon", "page_only_balloon_probe") is None
        assert _managed_object("balloon", "other_page_balloon") is not None
        assert len(_page_preview_objects()) == 4

        work = bpy.context.scene.bname_work
        work.active_page_index = 1
        work.pages[1].active_coma_index = 0
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0002" / "c01" / "c01.blend").resolve()

        result = bpy.ops.bname.exit_coma_mode()
        assert result == {"FINISHED"}, result
        assert _mainfile() == (work_dir / "p0002" / "page.blend").resolve()

        print("BNAME_PAGE_FILE_STAGE_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
