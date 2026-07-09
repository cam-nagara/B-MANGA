"""B-MANGA Line: graph UI backing data and linked-object batch operations."""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, edge_width_curve, panels, presets, update_state  # noqa: E402


class _DummyLayout:
    def __init__(self) -> None:
        self.props: list[str] = []
        self.labels: list[str] = []
        self.curves = 0
        self.enabled = True
        self.alignment = "LEFT"
        self.scale_y = 1.0

    def box(self):
        return self

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def separator(self) -> None:
        return

    def label(self, text: str = "", **_kwargs) -> None:
        self.labels.append(str(text))

    def prop(self, _data, prop_name: str, **_kwargs) -> None:
        self.props.append(str(prop_name))

    def template_curve_mapping(self, *_args, **_kwargs) -> None:
        self.curves += 1
        return

    def operator(self, *_args, **_kwargs):
        return self

    def template_list(self, *_args, **_kwargs) -> None:
        return


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, -6.0, 0.0), rotation=(math.radians(90), 0.0, 0.0))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    return camera


def _make_cube(name: str, location=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _test_edge_width_curve_sync() -> None:
    _clear_scene()
    obj = _make_cube("BML_curve_source")
    settings = obj.bmanga_line_settings
    cases = {
        "outline": (
            "edge_width_curve_25",
            "edge_width_curve_50",
            "edge_width_curve_75",
        ),
        "inner": (
            "inner_edge_width_curve_25",
            "inner_edge_width_curve_50",
            "inner_edge_width_curve_75",
        ),
        "intersection": (
            "intersection_edge_width_curve_25",
            "intersection_edge_width_curve_50",
            "intersection_edge_width_curve_75",
        ),
        "selection": (
            "selection_edge_width_curve_25",
            "selection_edge_width_curve_50",
            "selection_edge_width_curve_75",
        ),
    }
    for target, props in cases.items():
        node = edge_width_curve.ensure_node(settings, target)
        assert node is not None

        edge_width_curve._apply_points_to_node(
            node,
            (
                (0.0, 0.0),
                (0.25, 0.80),
                (0.50, 0.20),
                (0.75, 0.60),
                (1.0, 1.0),
            ),
        )
        assert edge_width_curve.sync_node_to_settings(settings, target)
        assert abs(getattr(settings, props[0]) - 0.80) < 1.0e-4
        assert abs(getattr(settings, props[1]) - 0.20) < 1.0e-4
        assert abs(getattr(settings, props[2]) - 0.60) < 1.0e-4


def _test_detail_curve_commits_only_on_ok() -> None:
    _clear_scene()
    obj = _make_cube("BML_curve_manual_commit")
    _select(obj)
    settings = obj.bmanga_line_settings
    node = edge_width_curve.reset_node_from_settings(settings, "outline")
    assert node is not None
    before = (
        settings.edge_width_curve_25,
        settings.edge_width_curve_50,
        settings.edge_width_curve_75,
    )
    edge_width_curve._apply_points_to_node(
        node,
        ((0.0, 0.0), (0.25, 0.9), (0.5, 0.1), (0.75, 0.7), (1.0, 1.0)),
    )
    assert (
        settings.edge_width_curve_25,
        settings.edge_width_curve_50,
        settings.edge_width_curve_75,
    ) == before, "グラフ編集中に設定値へリアルタイム反映されています"
    assert not update_state.pending_targets(obj)

    operator_type = panels.BMANGA_LINE_OT_detail_settings
    assert operator_type.execute(None, bpy.context) == {"FINISHED"}
    assert abs(settings.edge_width_curve_25 - 0.9) < 1.0e-4
    assert abs(settings.edge_width_curve_50 - 0.1) < 1.0e-4
    assert abs(settings.edge_width_curve_75 - 0.7) < 1.0e-4
    assert "outline" in update_state.pending_targets(obj)

    update_state.clear_pending(obj)
    committed = (
        settings.edge_width_curve_25,
        settings.edge_width_curve_50,
        settings.edge_width_curve_75,
    )
    node = edge_width_curve.reset_node_from_settings(settings, "outline")
    edge_width_curve._apply_points_to_node(
        node,
        ((0.0, 0.0), (0.25, 0.2), (0.5, 0.8), (0.75, 0.3), (1.0, 1.0)),
    )
    operator_type.cancel(None, bpy.context)
    assert (
        settings.edge_width_curve_25,
        settings.edge_width_curve_50,
        settings.edge_width_curve_75,
    ) == committed
    points = edge_width_curve._read_node_points(edge_width_curve.get_node("outline"))
    assert abs(edge_width_curve._evaluate(points, 0.25) - committed[0]) < 1.0e-4
    assert abs(edge_width_curve._evaluate(points, 0.50) - committed[1]) < 1.0e-4
    assert abs(edge_width_curve._evaluate(points, 0.75) - committed[2]) < 1.0e-4


def _remove_curve_ui_material() -> None:
    mat = bpy.data.materials.get(edge_width_curve.MATERIAL_NAME)
    if mat is not None:
        bpy.data.materials.remove(mat)


def _test_panel_draw_has_no_live_edge_width_curve_sync() -> None:
    _clear_scene()
    _remove_curve_ui_material()
    obj = _make_cube("BML_curve_panel_draw")
    settings = obj.bmanga_line_settings

    assert edge_width_curve.get_node("outline") is None
    assert bpy.data.materials.get(edge_width_curve.MATERIAL_NAME) is None

    original_ensure = edge_width_curve.ensure_node
    original_sync = edge_width_curve.sync_node_to_settings

    def _forbidden_ensure(*_args, **_kwargs):
        raise AssertionError("パネル描画中に線幅グラフ用素材を作成しています")

    def _forbidden_sync(*_args, **_kwargs):
        raise AssertionError("パネル描画中に線幅グラフ設定を同期しています")

    edge_width_curve.ensure_node = _forbidden_ensure
    edge_width_curve.sync_node_to_settings = _forbidden_sync
    try:
        panels._draw_midpoint_width_controls(  # noqa: SLF001
            _DummyLayout(),
            settings,
            "outline",
            "線幅の詳細",
            "edge_smooth_factor",
            "edge_midpoint_jitter_percent",
            "edge_midpoint_angle",
        )
    finally:
        edge_width_curve.ensure_node = original_ensure
        edge_width_curve.sync_node_to_settings = original_sync

    assert bpy.data.materials.get(edge_width_curve.MATERIAL_NAME) is None

    edge_width_curve.reset_node_from_settings(settings, "outline")
    assert edge_width_curve.get_node("outline") is not None

    for target in ("inner", "intersection", "selection"):
        edge_width_curve.reset_node_from_settings(settings, target)
    layout = _DummyLayout()
    panels._draw_line_detail_grid(layout, settings)  # noqa: SLF001
    assert layout.curves == 4, f"中間頂点への変化グラフが4列ぶん表示されていません: {layout.curves}"
    assert "中間頂点への変化グラフ" in layout.labels
    hidden_curve_sliders = {
        "edge_width_curve_25",
        "edge_width_curve_50",
        "edge_width_curve_75",
        "inner_edge_width_curve_25",
        "inner_edge_width_curve_50",
        "inner_edge_width_curve_75",
        "intersection_edge_width_curve_25",
        "intersection_edge_width_curve_50",
        "intersection_edge_width_curve_75",
        "selection_edge_width_curve_25",
        "selection_edge_width_curve_50",
        "selection_edge_width_curve_75",
    }
    assert hidden_curve_sliders.isdisjoint(layout.props), (
        "25/50/75%の数値スライダーが詳細設定に残っています"
    )
    sidebar_line_props = {
        "auto_subdivision_for_midpoint",
        "outline_enabled",
        "outline_thickness_mm",
        "outline_color",
        "use_outline_creation_limit",
        "outline_creation_max_distance",
        "inner_line_enabled",
        "inner_line_thickness_mm",
        "inner_line_color",
        "use_inner_line_creation_limit",
        "inner_line_creation_max_distance",
        "intersection_enabled",
        "intersection_thickness_mm",
        "intersection_color",
        "use_intersection_creation_limit",
        "intersection_creation_max_distance",
        "selection_line_enabled",
        "selection_line_thickness_mm",
        "selection_line_color",
        "use_selection_line_creation_limit",
        "selection_line_creation_max_distance",
    }
    missing = sidebar_line_props.difference(layout.props)
    assert not missing, f"ライン設定の項目が詳細設定にありません: {sorted(missing)}"


def _save_link_source(path: Path) -> None:
    _clear_scene()
    _make_camera()
    linked_source = _make_cube("BML_linked_line_source")
    _select(linked_source)
    settings = linked_source.bmanga_line_settings
    settings.outline_thickness_mm = 0.2
    settings.use_uniform_line_width = True
    assert presets.apply_line_settings(linked_source, bpy.context)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))


def _link_source(path: Path) -> bpy.types.Object:
    with bpy.data.libraries.load(str(path), link=True) as (data_from, data_to):
        assert "BML_linked_line_source" in data_from.objects
        data_to.objects = ["BML_linked_line_source"]
    linked = data_to.objects[0]
    bpy.context.scene.collection.objects.link(linked)
    return linked


def _test_linked_batch_apply() -> None:
    source_path = Path(tempfile.gettempdir()) / "bml_linked_batch_source.blend"
    _save_link_source(source_path)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _make_camera()
    linked = _link_source(source_path)

    local_source = _make_cube("BML_local_batch_source", (2.0, 0.0, 0.0))
    _select(local_source)
    settings = local_source.bmanga_line_settings
    settings.outline_thickness_mm = 0.7
    settings.inner_line_enabled = True
    settings.inner_line_thickness_mm = 0.3
    settings.use_uniform_line_width = True
    settings.use_outline_distance_limit = True
    settings.outline_max_distance = 3.0
    assert presets.apply_line_settings(local_source, bpy.context)

    assert bpy.ops.bmanga_line.refresh_linked() == {"FINISHED"}
    assert bpy.ops.bmanga_line.apply_active_to_linked() == {"FINISHED"}
    linked_settings = linked.bmanga_line_settings
    assert abs(linked_settings.outline_thickness_mm - 0.7) < 1.0e-4
    assert linked_settings.inner_line_enabled
    assert abs(linked_settings.inner_line_thickness_mm - 0.3) < 1.0e-4
    assert linked_settings.use_uniform_line_width
    assert linked_settings.use_outline_distance_limit
    assert abs(linked_settings.outline_max_distance - 3.0) < 1.0e-4
    assert core.has_line(linked)

    try:
        source_path.unlink()
    except OSError:
        pass


def main() -> None:
    b_manga_line.register()
    _test_panel_draw_has_no_live_edge_width_curve_sync()
    _test_edge_width_curve_sync()
    _test_detail_curve_commits_only_on_ok()
    _test_linked_batch_apply()
    print("[PASS] B-MANGA Line curve graph and linked batch operations work")


if __name__ == "__main__":
    main()
