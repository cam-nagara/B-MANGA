"""Blender実機用: フキダシの多重線設定と自由変形を検証。"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "test"))

from detail_dialog_public_test_support import draw_actual_detail  # noqa: E402


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_balloon_multiline_freeform",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_balloon_multiline_freeform"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _FakeOp:
    pass


class _FakeLayout:
    def __init__(self) -> None:
        self.props: list[str] = []
        self.enabled = True
        self.active = True

    def box(self):
        return self

    def row(self, align: bool = False):  # noqa: ARG002
        return self

    def column(self, align: bool = False):  # noqa: ARG002
        return self

    def grid_flow(self, **_kwargs):
        return self

    def split(self, **_kwargs):
        return self

    def separator(self):
        return None

    def label(self, text: str = "", icon: str = ""):  # noqa: ARG002
        return None

    def prop(self, data, prop_name: str, **_kwargs):
        if not hasattr(data, prop_name):
            raise AssertionError(f"missing prop: {prop_name}")
        self.props.append(str(prop_name))

    def prop_search(self, data, prop_name: str, _search_data, _search_prop: str, **_kwargs):
        self.prop(data, prop_name)

    def operator(self, _op_id: str, **_kwargs):
        return _FakeOp()

    def operator_menu_enum(self, _op_id: str, _prop: str, **_kwargs):
        return _FakeOp()


def _draw_props(context, entry) -> list[str]:
    layout = _FakeLayout()
    draw_actual_detail(
        "bmanga_dev_balloon_multiline_freeform",
        layout,
        context,
        entry,
        "balloon",
    )
    return layout.props


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-5) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _first_anchor_xy(obj) -> tuple[float, float]:
    spline = obj.data.splines[0]
    point = spline.bezier_points[0]
    return float(point.co.x) * 1000.0, float(point.co.y) * 1000.0


def _spline_bounds_xy(spline) -> tuple[float, float]:
    if str(getattr(spline, "type", "") or "") == "BEZIER":
        coords = [point.co for point in spline.bezier_points]
    else:
        coords = [point.co.to_3d() for point in spline.points]
    assert coords
    return max(co.x for co in coords) - min(co.x for co in coords), max(co.y for co in coords) - min(co.y for co in coords)


def _band_mesh(prefix: str, entry):
    obj = bpy.data.objects.get(f"{prefix}{entry.id}")
    assert obj is not None and obj.type == "MESH", f"描画用メッシュがありません: {prefix}"
    assert len(obj.data.vertices) > 0 and len(obj.data.polygons) > 0, f"描画用メッシュが空です: {prefix}"
    return obj


def _mesh_bounds_xy(obj) -> tuple[float, float]:
    coords = [vertex.co for vertex in obj.data.vertices]
    assert coords
    return max(co.x for co in coords) - min(co.x for co in coords), max(co.y for co in coords) - min(co.y for co in coords)


def _mesh_geometry_summary(obj) -> tuple[int, int, float, float, float, float]:
    coords = [vertex.co for vertex in obj.data.vertices]
    assert coords
    return (
        len(coords),
        len(obj.data.polygons),
        round(min(co.x for co in coords), 7),
        round(max(co.x for co in coords), 7),
        round(min(co.y for co in coords), 7),
        round(max(co.y for co in coords), 7),
    )


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_balloon_multiline_freeform_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BalloonMultiLine.bmanga"))
        assert result == {"FINISHED"}, result

        from bmanga_dev_balloon_multiline_freeform.core.work import get_work
        from bmanga_dev_balloon_multiline_freeform.io import schema
        from bmanga_dev_balloon_multiline_freeform.operators import balloon_op
        from bmanga_dev_balloon_multiline_freeform.utils import balloon_curve_object
        from bmanga_dev_balloon_multiline_freeform.utils import balloon_curve_render_nodes
        from bmanga_dev_balloon_multiline_freeform.utils import balloon_curve_source_state

        context = bpy.context
        work = get_work(context)
        assert work is not None
        page = work.pages[0]

        entry = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            page,
            shape="ellipse",
            x=20.0,
            y=30.0,
            w=50.0,
            h=28.0,
        )
        assert entry is not None
        solid_props = _draw_props(context, entry)
        assert "multi_line_count" not in solid_props, "実線でも多重線の設定が表示されています"

        entry.line_style = "double"
        entry.multi_line_count = 4
        entry.multi_line_width_mm = 0.3
        entry.multi_line_spacing_mm = 0.4
        entry.multi_line_width_scale_percent = 80.0
        entry.multi_line_direction = "both"
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        assert obj.modifiers.get(balloon_curve_render_nodes.MODIFIER_NAME) is None, "廃止済みの表示補助が復活しています"
        initial_multi = _band_mesh("balloon_multi_line_mesh_", entry)
        initial_multi_summary = _mesh_geometry_summary(initial_multi)
        assert int(entry.multi_line_count) == 4
        _assert_close(entry.multi_line_width_mm, 0.3, "多重線幅")
        _assert_close(entry.multi_line_spacing_mm, 0.4, "多重線間隔")
        _assert_close(entry.multi_line_width_scale_percent, 80.0, "多重線幅変化")
        assert entry.multi_line_direction == "both"

        entry.shape = "thorn"
        entry.thorn_multi_line_valley_width_pct = 40.0
        entry.thorn_multi_line_peak_width_pct = 90.0
        entry.thorn_multi_line_length_scale_near_percent = 100.0
        entry.thorn_multi_line_length_scale_far_percent = 75.0
        props = _draw_props(context, entry)
        for prop_name in (
            "multi_line_count",
            "multi_line_width_mm",
            "multi_line_spacing_mm",
            "multi_line_width_scale_percent",
            "multi_line_direction",
            "thorn_multi_line_valley_width_pct",
            "thorn_multi_line_peak_width_pct",
            "thorn_multi_line_length_scale_near_percent",
            "thorn_multi_line_length_scale_far_percent",
            "thorn_multi_line_cross_enabled",
        ):
            assert prop_name in props, f"多重線の設定が詳細設定に表示されていません: {prop_name}"

        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        assert obj.modifiers.get(balloon_curve_render_nodes.MODIFIER_NAME) is None, "廃止済みの表示補助が復活しています"
        thorn_multi = _band_mesh("balloon_multi_line_mesh_", entry)
        thorn_summary = _mesh_geometry_summary(thorn_multi)
        assert thorn_summary != initial_multi_summary, "形状と多重線設定を変えても描画実体が更新されていません"
        _assert_close(entry.thorn_multi_line_valley_width_pct, 40.0, "谷の線幅")
        _assert_close(entry.thorn_multi_line_peak_width_pct, 90.0, "山の線幅")
        _assert_close(entry.thorn_multi_line_length_scale_near_percent, 100.0, "主線寄りの長さ変化")
        _assert_close(entry.thorn_multi_line_length_scale_far_percent, 75.0, "遠い側の長さ変化")
        assert not bool(entry.thorn_multi_line_cross_enabled), "交差設定の初期値がオンになっています"
        body_radii = [float(point.radius) for point in obj.data.splines[0].bezier_points]
        assert len(body_radii) >= 4
        assert all(abs(radius - 1.0) <= 1.0e-6 for radius in body_radii), "トゲ本体の主線幅が多重線設定で変わっています"
        entry.thorn_multi_line_cross_enabled = True
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        cross_multi = _band_mesh("balloon_multi_line_mesh_", entry)
        assert _mesh_geometry_summary(cross_multi) != thorn_summary, "交差設定を変えても多重線実体が更新されていません"
        payload_cross = schema.balloon_entry_to_dict(entry)
        cross_roundtrip = page.balloons.add()
        schema.balloon_entry_from_dict(cross_roundtrip, payload_cross, opacity_percent=True)
        assert bool(cross_roundtrip.thorn_multi_line_cross_enabled), "保存読込: 交差設定"
        entry.thorn_multi_line_cross_enabled = False
        entry.outer_white_margin_enabled = True
        entry.outer_white_margin_width_mm = 1.2
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        bpy.context.view_layer.update()
        _band_mesh("balloon_multi_line_mesh_", entry)
        _band_mesh("balloon_outer_edge_mesh_", entry)

        payload = schema.balloon_entry_to_dict(entry)
        roundtrip = page.balloons.add()
        schema.balloon_entry_from_dict(roundtrip, payload, opacity_percent=True)
        assert roundtrip.line_style == "double"
        assert roundtrip.multi_line_count == 4
        _assert_close(roundtrip.multi_line_spacing_mm, 0.4, "保存読込: 多重線間隔")
        assert roundtrip.multi_line_direction == "both"
        _assert_close(roundtrip.thorn_multi_line_valley_width_pct, 40.0, "保存読込: 谷の線幅")
        _assert_close(roundtrip.thorn_multi_line_peak_width_pct, 90.0, "保存読込: 山の線幅")
        _assert_close(roundtrip.thorn_multi_line_length_scale_far_percent, 75.0, "保存読込: 遠い側の長さ変化")

        edge_entry = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            page,
            shape="rect",
            x=35.0,
            y=82.0,
            w=34.0,
            h=24.0,
        )
        edge_entry.line_width_mm = 0.6
        edge_entry.outer_white_margin_enabled = True
        edge_entry.outer_white_margin_width_mm = 2.0
        edge_entry.inner_white_margin_enabled = True
        edge_entry.inner_white_margin_width_mm = 1.5
        edge_obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=edge_entry, page=page)
        body_w, body_h = _spline_bounds_xy(edge_obj.data.splines[0])
        outer_w, outer_h = _mesh_bounds_xy(_band_mesh("balloon_outer_edge_mesh_", edge_entry))
        inner_w, inner_h = _mesh_bounds_xy(_band_mesh("balloon_inner_edge_mesh_", edge_entry))
        assert outer_w > body_w and outer_h > body_h, "外側フチが外側の輪郭になっていません"
        assert inner_w <= body_w + 1.0e-7 and inner_h <= body_h + 1.0e-7, "内側フチが本体の外へ出ています"

        freeform = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            page,
            shape="rect",
            x=90.0,
            y=30.0,
            w=40.0,
            h=20.0,
        )
        free_obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=freeform, page=page)
        before_location = tuple(float(v) for v in free_obj.location)
        free_obj.data.splines[0].bezier_points[0].co.x += 0.006
        assert balloon_curve_source_state.detect_state(free_obj) == balloon_curve_source_state.STATE_MANUAL
        edited_before = _first_anchor_xy(free_obj)
        balloon_op._set_balloon_rect(page, freeform, 80.0, 20.0, 80.0, 50.0)  # noqa: SLF001
        edited_after = _first_anchor_xy(free_obj)
        assert balloon_curve_source_state.detect_state(free_obj) == balloon_curve_source_state.STATE_FREEFORM
        assert edited_after != edited_before, "B-MANGAハンドル変形で自由形状カーブが変形していません"
        after_location = tuple(float(v) for v in free_obj.location)
        assert after_location != before_location, "B-MANGAハンドル変形で自由形状フキダシの位置が追従していません"
        _assert_close(float(freeform.width_mm), 80.0, "自由変形後の幅")
        _assert_close(float(freeform.height_mm), 50.0, "自由変形後の高さ")
    finally:
        try:
            if mod is not None:
                mod.unregister()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    print("BMANGA_BALLOON_MULTILINE_FREEFORM_OK")


main()
