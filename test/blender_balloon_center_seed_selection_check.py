"""Blender実機用: フキダシの中心原点・線なし・シード・選択を検証。"""

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
        "bmanga_dev_balloon_center_seed_selection",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_balloon_center_seed_selection"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _FakeOp:
    pass


class _FakeLayout:
    def __init__(self) -> None:
        self.props: list[str] = []
        self.labels: list[str] = []
        self.enabled = True

    def box(self):
        return self

    def row(self, align: bool = False):  # noqa: ARG002
        return self

    def column(self, align: bool = False):  # noqa: ARG002
        return self

    def grid_flow(self, **_kwargs):
        return self

    def label(self, text: str = "", icon: str = ""):  # noqa: ARG002
        self.labels.append(str(text or ""))

    def prop(self, data, prop_name: str, **_kwargs):
        if not hasattr(data, prop_name):
            raise AssertionError(f"missing prop: {prop_name}")
        self.props.append(str(prop_name))

    def prop_search(self, data, prop_name: str, _search_data, _search_prop: str, **_kwargs):
        self.prop(data, prop_name)

    def operator(self, _op_id: str, **_kwargs):
        return _FakeOp()


def _modifier_socket_value(modifier, name: str):
    for item in modifier.node_group.interface.items_tree:
        if getattr(item, "item_type", "") == "SOCKET" and getattr(item, "in_out", "") == "INPUT":
            if getattr(item, "name", "") == name:
                return modifier.get(item.identifier)
    raise AssertionError(f"modifier socket not found: {name}")


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-6) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _body_anchor_xy(obj) -> list[tuple[float, float]]:
    spline = obj.data.splines[0]
    return [(float(p.co.x) * 1000.0, float(p.co.y) * 1000.0) for p in spline.bezier_points]


def _draw_props(layer_detail_op, entry, page) -> list[str]:
    layout = _FakeLayout()
    layer_detail_op._draw_balloon_detail(layout, entry, page)  # noqa: SLF001
    return layout.props


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_balloon_center_seed_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BalloonCenterSeed.bmanga"))
        assert result == {"FINISHED"}, result

        from bmanga_dev_balloon_center_seed_selection.core.work import get_work
        from bmanga_dev_balloon_center_seed_selection.operators import balloon_op, layer_detail_op, object_tool_selection
        from bmanga_dev_balloon_center_seed_selection.utils import balloon_curve_object, balloon_line_mesh, object_selection, page_grid
        from bmanga_dev_balloon_center_seed_selection.utils.layer_hierarchy import OUTSIDE_STACK_KEY

        context = bpy.context
        work = get_work(context)
        assert work is not None
        page = work.pages[0]

        rect = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            page,
            shape="rect",
            x=20.0,
            y=30.0,
            w=40.0,
            h=20.0,
        )
        rect.rounded_corner_enabled = True
        rect.rounded_corner_radius_mm = 5.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=rect, page=page)
        assert obj is not None and obj.type == "CURVE"
        assert int(obj.data.resolution_u) == 64, f"フキダシのプレビュー解像度U初期値が64ではありません: {obj.data.resolution_u}"
        assert int(obj.data.render_resolution_u) == 64, f"フキダシのレンダーU初期値が64ではありません: {obj.data.render_resolution_u}"
        obj.data.resolution_u = 12
        obj.data.render_resolution_u = 24
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=rect, page=page)
        assert int(obj.data.resolution_u) == 12, "ユーザー変更後のプレビュー解像度UをB-MANGAが上書きしています"
        assert int(obj.data.render_resolution_u) == 24, "ユーザー変更後のレンダーUをB-MANGAが上書きしています"
        page_ox, page_oy = page_grid.page_total_offset_mm(work, context.scene, 0)
        _assert_close(obj.location.x, (page_ox + 40.0) * 0.001, "中心原点 X")
        _assert_close(obj.location.y, (page_oy + 40.0) * 0.001, "中心原点 Y")
        anchors = _body_anchor_xy(obj)
        assert len(anchors) == 8, f"角丸矩形が8点ベジェではありません: {len(anchors)}"
        xs = [x for x, _y in anchors]
        ys = [y for _x, y in anchors]
        assert min(xs) < -19.9 and max(xs) > 19.9, f"フキダシ曲線が中心原点基準ではありません: {anchors}"
        assert min(ys) < -9.9 and max(ys) > 9.9, f"フキダシ曲線が中心原点基準ではありません: {anchors}"

        rect_props = _draw_props(layer_detail_op, rect, page)
        assert "corner_type" in rect_props, "矩形で角の種類が表示されていません"
        assert "blend_mode" not in rect_props, "フキダシの合成モードが詳細設定に残っています"

        round100 = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            page,
            shape="rect",
            x=115.0,
            y=30.0,
            w=90.0,
            h=10.0,
        )
        round100.corner_type = "rounded"
        round100.rounded_corner_enabled = True
        round100.rounded_corner_radius_unit = "percent"
        round100.rounded_corner_radius_percent = 100.0
        round100_obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=round100, page=page)
        assert round100_obj is not None and round100_obj.type == "CURVE"
        round100_anchors = _body_anchor_xy(round100_obj)
        assert len(round100_anchors) == 4, f"丸角100%の長細い矩形に直線部が残っています: {round100_anchors}"

        rect.line_style = "none"
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=rect, page=page)
        line_obj = bpy.data.objects.get(balloon_line_mesh._line_mesh_object_name(rect.id))  # noqa: SLF001
        assert line_obj is None, "線なしでもフキダシの主線が残っています"

        ellipse = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            page,
            shape="ellipse",
            x=75.0,
            y=30.0,
            w=30.0,
            h=18.0,
        )
        ellipse_props = _draw_props(layer_detail_op, ellipse, page)
        assert "corner_type" not in ellipse_props, "矩形以外で角の種類が表示されています"

        cloud = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            page,
            shape="cloud",
            x=20.0,
            y=70.0,
            w=60.0,
            h=34.0,
        )
        assert float(cloud.shape_params.cloud_sub_height_ratio) == 50.0, "小山高の初期値が50%ではありません"
        cloud.shape_params.cloud_bump_width_jitter = 0.65
        cloud.shape_params.cloud_bump_height_jitter = 0.65
        cloud.shape_params.cloud_sub_width_ratio = 45.0
        cloud.shape_params.cloud_sub_width_jitter = 0.5
        cloud.shape_params.cloud_sub_height_ratio = 35.0
        cloud.shape_params.cloud_sub_height_jitter = 0.5
        cloud.shape_params.shape_seed = 0
        cloud_obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=cloud, page=page)
        seed0 = _body_anchor_xy(cloud_obj)
        cloud.shape_params.shape_seed = 73
        balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=cloud, page=page)
        seed73 = _body_anchor_xy(cloud_obj)
        assert seed0 != seed73, "シードを変えても形状パラメータの乱れが変わりません"
        cloud_props = _draw_props(layer_detail_op, cloud, page)
        assert "shape_seed" in cloud_props, "形状パラメータにシードが表示されていません"
        assert "cloud_sub_width_jitter" in cloud_props, "小山幅の乱れが表示されていません"
        assert "cloud_sub_height_jitter" in cloud_props, "小山高の乱れが表示されていません"
        cloud.shape_params.dynamic_shape_base_kind = "rect"
        cloud.shape_params.dynamic_base_rounded_corner_enabled = True
        cloud.shape_params.dynamic_base_rounded_corner_radius_unit = "percent"
        cloud.shape_params.dynamic_base_rounded_corner_radius_percent = 45.0
        cloud_props = _draw_props(layer_detail_op, cloud, page)
        assert "dynamic_base_rounded_corner_enabled" in cloud_props, "矩形ベースの丸角が表示されていません"
        assert "dynamic_base_rounded_corner_radius_percent" in cloud_props, "矩形ベースの角半径が表示されていません"

        from bmanga_dev_balloon_center_seed_selection.io import schema

        cloud.line_style = "shape"
        cloud.line_shape_seed = 991
        payload = schema.balloon_entry_to_dict(cloud)
        assert payload["lineShapeSeed"] == 73, "図形線のシードが形状パラメータのシードに統一されていません"
        payload["lineShapeSeed"] = 91
        payload["shapeParams"].pop("shapeSeed", None)
        migrated = page.balloons.add()
        schema.balloon_entry_from_dict(migrated, payload)
        assert int(migrated.shape_params.shape_seed) == 91, "旧シードが新しいシードへ移行されていません"
        assert int(migrated.line_shape_seed) == 91, "互換用シードが新しいシードと同期していません"

        outside1 = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            None,
            shape="ellipse",
            x=360.0,
            y=70.0,
            w=40.0,
            h=22.0,
        )
        outside2 = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            None,
            shape="ellipse",
            x=430.0,
            y=70.0,
            w=40.0,
            h=22.0,
        )
        outside_obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=outside1, page=None)
        assert outside_obj is not None
        _assert_close(outside_obj.location.x, 0.380, "ページ外フキダシ中心原点 X")
        hit = object_tool_selection.hit_shared_balloon_at_world(context, 380.0, 81.0)
        assert hit is not None and hit["key"] == object_selection.balloon_key(None, outside1), "ページ外フキダシをクリック判定できません"
        assert balloon_op._select_balloon_index(context, work, None, 0, mode="single")  # noqa: SLF001
        keys = object_selection.get_keys(context)
        expected_key = object_selection.balloon_key(None, outside1)
        assert keys == [expected_key], f"別のフキダシまで選択されています: {keys}"
        assert bool(getattr(outside1, "selected", False))
        assert not bool(getattr(outside2, "selected", False))
        assert object_tool_selection.selection_bounds_for_key(context, expected_key) is not None
        assert object_selection.parse_key(expected_key)[1] == OUTSIDE_STACK_KEY
    finally:
        try:
            if mod is not None:
                mod.unregister()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    print("BMANGA_BALLOON_CENTER_SEED_SELECTION_OK")


main()
