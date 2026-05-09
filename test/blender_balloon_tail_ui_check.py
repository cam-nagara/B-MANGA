"""Blender実機用: フキダシしっぽ編集UIと追加/削除の確認."""

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
        "bname_dev_tail_ui",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_tail_ui"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _FakeOp:
    pass


class _FakeLayout:
    def __init__(self) -> None:
        self.ops: list[str] = []
        self.props: list[str] = []
        self.labels: list[str] = []
        self.enabled = True

    def box(self):
        return self

    def row(self, align: bool = False):  # noqa: ARG002
        return self

    def column(self, align: bool = False):  # noqa: ARG002
        return self

    def label(self, text: str = "", icon: str = ""):  # noqa: ARG002
        self.labels.append(text)

    def prop(self, data, prop_name: str, **_kwargs):
        if not hasattr(data, prop_name):
            raise AssertionError(f"missing prop: {prop_name}")
        self.props.append(prop_name)

    def operator(self, op_id: str, **_kwargs):
        self.ops.append(op_id)
        return _FakeOp()


def _add_balloon(page):
    entry = page.balloons.add()
    entry.id = "tail_ui_balloon"
    entry.shape = "ellipse"
    entry.x_mm = 30.0
    entry.y_mm = 40.0
    entry.width_mm = 50.0
    entry.height_mm = 25.0
    entry.parent_kind = "page"
    entry.parent_key = page.id
    page.active_balloon_index = len(page.balloons) - 1
    return entry


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_tail_ui_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "TailUI.bname"))
        assert "FINISHED" in result, result

        from bname_dev_tail_ui.operators import layer_detail_op
        from bname_dev_tail_ui.utils import balloon_curve_object
        from bname_dev_tail_ui.utils.geom import Rect
        from bname_dev_tail_ui.io import export_balloon

        context = bpy.context
        work = context.scene.bname_work
        page = work.pages[0]
        entry = _add_balloon(page)

        result = bpy.ops.bname.balloon_tail_add_target(
            "EXEC_DEFAULT",
            page_id=page.id,
            balloon_id=entry.id,
        )
        assert "FINISHED" in result, result
        assert len(entry.tails) == 1
        tail = entry.tails[0]
        tail.type = "straight"
        tail.direction_deg = 315.0
        tail.length_mm = 12.0
        tail.root_width_mm = 5.0
        tail.tip_width_mm = 3.0
        assert len(balloon_curve_object._tail_polygon_for_entry(entry, tail)) == 4
        rect = Rect(entry.x_mm, entry.y_mm, entry.width_mm, entry.height_mm)
        assert len(export_balloon._balloon_tail_polygon(rect, tail)) == 4

        tail.type = "curve"
        tail.curve_bend = 0.4
        assert len(balloon_curve_object._tail_polygon_for_entry(entry, tail)) == 6
        assert len(export_balloon._balloon_tail_polygon(rect, tail)) == 6

        obj = balloon_curve_object.ensure_balloon_curve_object(
            scene=context.scene,
            entry=entry,
            page=page,
        )
        assert obj is not None and obj.type == "MESH"

        layout = _FakeLayout()
        layer_detail_op._draw_balloon_detail(layout, entry, page)
        assert "bname.balloon_tail_add_target" in layout.ops
        assert "bname.balloon_tail_remove" in layout.ops
        for prop_name in {
            "type",
            "direction_deg",
            "length_mm",
            "root_width_mm",
            "tip_width_mm",
            "curve_bend",
        }:
            assert prop_name in layout.props, prop_name

        result = bpy.ops.bname.balloon_tail_remove(
            "EXEC_DEFAULT",
            page_id=page.id,
            balloon_id=entry.id,
            tail_index=0,
        )
        assert "FINISHED" in result, result
        assert len(entry.tails) == 0

        print("BNAME_BALLOON_TAIL_UI_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
