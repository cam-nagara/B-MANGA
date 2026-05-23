"""Blender実機用: フキダシの多重線設定と自由変形を検証。"""

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
        "bname_dev_balloon_multiline_freeform",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_multiline_freeform"] = mod
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

    def box(self):
        return self

    def row(self, align: bool = False):  # noqa: ARG002
        return self

    def column(self, align: bool = False):  # noqa: ARG002
        return self

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


def _draw_props(layer_detail_op, entry, page) -> list[str]:
    layout = _FakeLayout()
    layer_detail_op._draw_balloon_detail(layout, entry, page)  # noqa: SLF001
    return layout.props


def _modifier_socket_value(modifier, name: str):
    for item in modifier.node_group.interface.items_tree:
        if getattr(item, "item_type", "") == "SOCKET" and getattr(item, "in_out", "") == "INPUT":
            if getattr(item, "name", "") == name:
                return modifier.get(item.identifier)
    raise AssertionError(f"modifier socket not found: {name}")


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-5) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _first_anchor_xy(obj) -> tuple[float, float]:
    spline = obj.data.splines[0]
    point = spline.bezier_points[0]
    return float(point.co.x) * 1000.0, float(point.co.y) * 1000.0


def _spline_point_co(spline, index: int):
    if str(getattr(spline, "type", "") or "") == "BEZIER":
        return spline.bezier_points[index].co
    return spline.points[index].co.to_3d()


def _spline_point_radius(spline, index: int) -> float:
    if str(getattr(spline, "type", "") or "") == "BEZIER":
        return float(spline.bezier_points[index].radius)
    return float(spline.points[index].radius)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_multiline_freeform_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonMultiLine.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev_balloon_multiline_freeform.core.work import get_work
        from bname_dev_balloon_multiline_freeform.io import schema
        from bname_dev_balloon_multiline_freeform.operators import balloon_op, layer_detail_op
        from bname_dev_balloon_multiline_freeform.utils import balloon_curve_object
        from bname_dev_balloon_multiline_freeform.utils import balloon_curve_render_nodes
        from bname_dev_balloon_multiline_freeform.utils import balloon_curve_source_state

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
        solid_props = _draw_props(layer_detail_op, entry, page)
        assert "multi_line_count" not in solid_props, "実線でも多重線の設定が表示されています"

        entry.line_style = "double"
        entry.multi_line_count = 4
        entry.multi_line_width_mm = 0.3
        entry.multi_line_spacing_mm = 0.4
        entry.multi_line_width_scale_percent = 80.0
        entry.multi_line_direction = "both"
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        modifier = obj.modifiers.get(balloon_curve_render_nodes.MODIFIER_NAME)
        assert modifier is not None and modifier.node_group is not None
        assert bool(_modifier_socket_value(modifier, "多重線")), "多重線が表示ノードへ渡っていません"
        _assert_close(_modifier_socket_value(modifier, "多重線本数"), 4.0, "多重線本数")
        _assert_close(_modifier_socket_value(modifier, "多重線幅 (mm)"), 0.3, "多重線幅")
        _assert_close(_modifier_socket_value(modifier, "多重線間隔 (mm)"), 0.4, "多重線間隔")
        _assert_close(_modifier_socket_value(modifier, "多重線幅変化 (%)"), 80.0, "多重線幅変化")
        _assert_close(_modifier_socket_value(modifier, "多重線方向"), 2.0, "多重線方向")
        assert bool(_modifier_socket_value(modifier, "多重線1表示")), "2本目が表示対象になっていません"
        assert bool(_modifier_socket_value(modifier, "多重線2表示")), "3本目が表示対象になっていません"
        assert bool(_modifier_socket_value(modifier, "多重線3表示")), "4本目が表示対象になっていません"
        assert not bool(_modifier_socket_value(modifier, "多重線4表示")), "指定本数を超える線が表示対象になっています"
        ring1_width = float(_modifier_socket_value(modifier, "多重線1外半径 (mm)")) - float(_modifier_socket_value(modifier, "多重線1内半径 (mm)"))
        ring2_width = float(_modifier_socket_value(modifier, "多重線2外半径 (mm)")) - float(_modifier_socket_value(modifier, "多重線2内半径 (mm)"))
        _assert_close(ring2_width, ring1_width * 0.8, "多重線の線幅変化")

        entry.shape = "thorn"
        entry.thorn_multi_line_valley_width_mm = 0.22
        entry.thorn_multi_line_peak_width_mm = 0.46
        entry.thorn_multi_line_length_scale_percent = 75.0
        props = _draw_props(layer_detail_op, entry, page)
        for prop_name in (
            "multi_line_count",
            "multi_line_width_mm",
            "multi_line_spacing_mm",
            "multi_line_width_scale_percent",
            "multi_line_direction",
            "thorn_multi_line_valley_width_mm",
            "thorn_multi_line_peak_width_mm",
            "thorn_multi_line_length_scale_percent",
        ):
            assert prop_name in props, f"多重線の設定が詳細設定に表示されていません: {prop_name}"

        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        modifier = obj.modifiers.get(balloon_curve_render_nodes.MODIFIER_NAME)
        assert modifier is not None and modifier.node_group is not None
        assert bool(_modifier_socket_value(modifier, "多重線")), "多重線が表示ノードへ渡っていません"
        _assert_close(_modifier_socket_value(modifier, "多重線本数"), 4.0, "多重線本数")
        _assert_close(_modifier_socket_value(modifier, "多重線幅 (mm)"), 0.3, "多重線幅")
        _assert_close(_modifier_socket_value(modifier, "多重線間隔 (mm)"), 0.4, "多重線間隔")
        _assert_close(_modifier_socket_value(modifier, "多重線幅変化 (%)"), 80.0, "多重線幅変化")
        _assert_close(_modifier_socket_value(modifier, "多重線方向"), 2.0, "多重線方向")
        _assert_close(_modifier_socket_value(modifier, "谷の線幅 (mm)"), 0.22, "谷の線幅")
        _assert_close(_modifier_socket_value(modifier, "山の線幅 (mm)"), 0.46, "山の線幅")
        _assert_close(_modifier_socket_value(modifier, "多重線長さ変化 (%)"), 75.0, "長さ変化")
        radii = [float(point.radius) for point in obj.data.splines[0].bezier_points]
        assert len(radii) >= 4
        _assert_close(radii[0], 0.22 / 0.3, "トゲ谷の多重線幅")
        _assert_close(radii[1], 0.46 / 0.3, "トゲ山の多重線幅")
        helper_splines = [spline for spline in obj.data.splines if not bool(spline.use_cyclic_u)]
        assert helper_splines, "トゲの多重線用補助線が作成されていません"
        body_points = obj.data.splines[0].bezier_points
        body_len = (body_points[1].co - body_points[0].co).length
        helper_len = (_spline_point_co(helper_splines[0], 1) - _spline_point_co(helper_splines[0], 0)).length
        _assert_close(helper_len, body_len * 0.75, "トゲ多重線の長さ変化", eps=1.0e-4)
        _assert_close(_spline_point_radius(helper_splines[0], 0), 0.22 / 0.3, "トゲ多重線の谷側線幅")
        _assert_close(_spline_point_radius(helper_splines[0], 1), 0.46 / 0.3, "トゲ多重線の山側線幅")

        payload = schema.balloon_entry_to_dict(entry)
        roundtrip = page.balloons.add()
        schema.balloon_entry_from_dict(roundtrip, payload, opacity_percent=True)
        assert roundtrip.line_style == "double"
        assert roundtrip.multi_line_count == 4
        _assert_close(roundtrip.multi_line_spacing_mm, 0.4, "保存読込: 多重線間隔")
        assert roundtrip.multi_line_direction == "both"
        _assert_close(roundtrip.thorn_multi_line_peak_width_mm, 0.46, "保存読込: 山の線幅")

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
        assert edited_after != edited_before, "B-Nameハンドル変形で自由形状カーブが変形していません"
        after_location = tuple(float(v) for v in free_obj.location)
        assert after_location != before_location, "B-Nameハンドル変形で自由形状フキダシの位置が追従していません"
        _assert_close(float(freeform.width_mm), 80.0, "自由変形後の幅")
        _assert_close(float(freeform.height_mm), 50.0, "自由変形後の高さ")
    finally:
        try:
            if mod is not None:
                mod.unregister()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    print("BNAME_BALLOON_MULTILINE_FREEFORM_OK")


main()
