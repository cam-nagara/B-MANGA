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
        "bmanga_dev_tail_ui",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_tail_ui"] = mod
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
    def split(self, factor: float = 0.5, align: bool = False):  # noqa: ARG002
        return self


    def label(self, text: str = "", icon: str = ""):  # noqa: ARG002
        self.labels.append(text)

    def prop(self, data, prop_name: str, **_kwargs):
        if not hasattr(data, prop_name):
            raise AssertionError(f"missing prop: {prop_name}")
        self.props.append(prop_name)

    def prop_search(self, data, prop_name: str, _search_data, _search_prop: str, **_kwargs):
        self.prop(data, prop_name)

    def operator(self, op_id: str, **_kwargs):
        self.ops.append(op_id)
        return _FakeOp()

    def operator_menu_enum(self, op_id: str, _prop: str, **_kwargs):
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
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_balloon_tail_ui_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "TailUI.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_tail_ui.operators import layer_detail_op
        from bmanga_dev_tail_ui.utils import (
            balloon_curve_object,
            balloon_render_contract,
            balloon_shapes,
            balloon_tail_geom,
        )
        from bmanga_dev_tail_ui.utils.geom import Rect
        from bmanga_dev_tail_ui.io import export_balloon, schema

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        entry = _add_balloon(page)

        result = bpy.ops.bmanga.balloon_tail_add_target(
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

        shape_rect = Rect(0.0, 0.0, 40.0, 20.0)
        low = balloon_shapes.outline_for_shape("cloud", shape_rect, cloud_bump_width_mm=8.0, cloud_bump_height_mm=3.0)
        high = balloon_shapes.outline_for_shape("cloud", shape_rect, cloud_bump_width_mm=8.0, cloud_bump_height_mm=8.0)
        cx = shape_rect.x + shape_rect.width * 0.5
        cy = shape_rect.y + shape_rect.height * 0.5
        low_min = min(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in low)
        high_min = min(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in high)
        low_max = max(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in low)
        high_max = max(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in high)
        assert len(low) == len(high), "山の高さ変更で山の見かけ幅が変わっています"
        assert high_min >= low_min - 0.5, "山の高さ変更で谷が中心へ伸びています"
        assert high_max > low_max + 1.0, "山の高さ変更で山が外側へ高くなっていません"
        sub_low = balloon_shapes.outline_for_shape(
            "cloud",
            shape_rect,
            cloud_bump_width_mm=8.0,
            cloud_bump_height_mm=4.0,
            cloud_sub_width_ratio=35.0,
            cloud_sub_height_ratio=20.0,
        )
        sub_high = balloon_shapes.outline_for_shape(
            "cloud",
            shape_rect,
            cloud_bump_width_mm=8.0,
            cloud_bump_height_mm=4.0,
            cloud_sub_width_ratio=35.0,
            cloud_sub_height_ratio=80.0,
        )
        assert len(sub_low) == len(sub_high), "小山高の変更で山の見かけ幅が変わっています"
        thorn_low = balloon_shapes.outline_for_shape("thorn-curve", shape_rect, cloud_bump_width_mm=8.0, cloud_bump_height_mm=3.0)
        thorn_high = balloon_shapes.outline_for_shape("thorn-curve", shape_rect, cloud_bump_width_mm=8.0, cloud_bump_height_mm=8.0)
        thorn_low_min = min(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in thorn_low)
        thorn_high_min = min(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in thorn_high)
        thorn_low_max = max(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in thorn_low)
        thorn_high_max = max(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in thorn_high)
        assert len(thorn_low) == len(thorn_high), "トゲの高さ変更で山の見かけ幅が変わっています"
        assert thorn_high_min >= thorn_low_min - 0.5, "トゲの高さ変更で谷が中心へ伸びています"
        assert thorn_high_max > thorn_low_max + 1.0, "トゲの高さ変更で山が外側へ高くなっていません"

        tail.type = "curve"
        tail.curve_bend = 0.4
        # v0.6.277: 曲げしっぽは 3 点の折れ線ではなく、なめらかな 2 次曲線で
        # サンプリングされる (カクカク解消)
        assert len(balloon_curve_object._tail_polygon_for_entry(entry, tail)) >= 20
        assert len(export_balloon._balloon_tail_polygon(rect, tail)) >= 20
        balloon_tail_geom.write_polyline_points(tail, [(25.0, 12.0), (34.0, 4.0), (46.0, -8.0)])
        tail.points[1].corner_type = "curve"
        assert len(tail.points) == 3
        assert len(balloon_curve_object._tail_polygon_for_entry(entry, tail)) > 6
        inserted = balloon_tail_geom.add_polyline_point(tail, (40.0, -2.0), insert_index=2)
        assert inserted == 2 and len(tail.points) == 4
        assert balloon_tail_geom.set_point(tail, 2, (41.0, -3.0))
        data = schema.balloon_entry_to_dict(entry)
        restored = page.balloons.add()
        schema.balloon_entry_from_dict(restored, data)
        assert len(restored.tails) == 1 and len(restored.tails[0].points) == 4
        assert restored.tails[0].points[1].corner_type == "curve"
        result = bpy.ops.bmanga.balloon_tail_point_toggle_corner(
            "EXEC_DEFAULT",
            page_id=page.id,
            balloon_id=entry.id,
            tail_index=0,
            point_index=1,
        )
        assert "FINISHED" in result and entry.tails[0].points[1].corner_type == "line"
        result = bpy.ops.bmanga.balloon_tail_point_delete(
            "EXEC_DEFAULT",
            page_id=page.id,
            balloon_id=entry.id,
            tail_index=0,
            point_index=2,
        )
        assert "FINISHED" in result and len(entry.tails[0].points) == 3

        entry.fill_opacity = 42.0
        entry.fill_gradient_enabled = True
        entry.fill_gradient_start_color = (1.0, 0.0, 0.0, 1.0)
        entry.fill_gradient_end_color = (0.0, 0.0, 1.0, 1.0)
        entry.fill_blur_amount = 1.0
        entry.fill_blur_dither = True
        entry.outer_white_margin_enabled = True
        entry.outer_white_margin_width_mm = 1.5
        entry.inner_white_margin_enabled = True
        entry.inner_white_margin_width_mm = 0.8
        layer = export_balloon.render_balloon_layer(entry, canvas_height_px=1200, dpi=600)
        assert layer is not None and layer.image.size[0] > 0
        alpha_values = set(layer.image.getchannel("A").getdata())
        expected_fill_alpha = int(round(255 * (entry.fill_opacity / 100.0)))
        assert alpha_values.issubset({0, expected_fill_alpha, 255}), "ディザ化した塗り輪郭のalphaが2値化されていません"

        source_mat = bpy.data.materials.new("TailUI_SourceMaterial")
        source_mat.use_nodes = True
        source_nodes = [node.name for node in source_mat.node_tree.nodes]
        entry.fill_material_name = source_mat.name
        entry.fill_gradient_enabled = False
        obj = balloon_curve_object.ensure_balloon_curve_object(
            scene=context.scene,
            entry=entry,
            page=page,
        )
        assert obj is not None and obj.type == "CURVE"
        fill_obj = bpy.data.objects.get(f"balloon_fill_{entry.id}")
        assert fill_obj is None, "フキダシの塗りが別オブジェクトとして残っています"
        fill_slot = balloon_render_contract.MATERIAL_SLOT_FILL
        assert len(obj.data.materials) > fill_slot
        used_mat = obj.data.materials[fill_slot]
        assert used_mat is not source_mat
        assert used_mat.get("bmanga_balloon_fill_source_material") == source_mat.name
        assert [node.name for node in source_mat.node_tree.nodes] == source_nodes

        layout = _FakeLayout()
        layer_detail_op._draw_balloon_detail(layout, entry, page)
        # v0.6.275: しっぽ設定は専用ダイアログへ分離され、開くボタンだけが置かれる
        assert "bmanga.balloon_tail_detail_open" in layout.ops
        for prop_name in {
            "fill_opacity",
            "fill_material_name",
            "fill_blur_amount",
            "fill_blur_dither",
            "fill_gradient_enabled",
            "outer_white_margin_enabled",
            "inner_white_margin_enabled",
        }:
            assert prop_name in layout.props, prop_name

        # しっぽの詳細設定ダイアログ側の UI (追加・削除・プリセット・新設定)
        from bmanga_dev_tail_ui.operators import balloon_tail_detail_op

        tail_layout = _FakeLayout()
        balloon_tail_detail_op._draw_tail_box(
            tail_layout, context, page, entry, entry.tails[0], 0
        )
        assert "bmanga.balloon_tail_remove" in tail_layout.ops
        assert "bmanga.balloon_tail_preset_apply" in tail_layout.ops
        assert "bmanga.balloon_tail_preset_save" in tail_layout.ops
        for prop_name in {"line_type", "root_width_mm", "tip_width_mm", "sharp_corners"}:
            assert prop_name in tail_layout.props, prop_name

        result = bpy.ops.bmanga.balloon_tail_remove(
            "EXEC_DEFAULT",
            page_id=page.id,
            balloon_id=entry.id,
            tail_index=0,
        )
        assert "FINISHED" in result, result
        assert len(entry.tails) == 0

        print("BMANGA_BALLOON_TAIL_UI_OK")
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
