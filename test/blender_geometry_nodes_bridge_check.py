"""Blender 実機用: 効果線・フキダシの Geometry Nodes 同期を確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_gn_bridge",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_gn_bridge"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _modifier_socket_value(modifier, name: str):
    group = modifier.node_group
    assert group is not None, "Geometry Nodes グループがありません"
    for item in group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        if getattr(item, "in_out", "") != "INPUT":
            continue
        if getattr(item, "name", "") != name:
            continue
        identifier = str(getattr(item, "identifier", "") or "")
        assert identifier, f"{name} の入力IDがありません"
        return modifier[identifier]
    raise AssertionError(f"Geometry Nodes 入力がありません: {name}")


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-5) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _assert_modifier_value(modifier, socket_name: str, expected, label: str) -> None:
    actual = _modifier_socket_value(modifier, socket_name)
    if expected is None:
        assert actual is None, f"{label}: expected None, got {actual!r}"
        return
    if isinstance(expected, bool):
        assert bool(actual) == expected, f"{label}: expected {expected}, got {actual!r}"
        return
    if isinstance(expected, int) and not isinstance(expected, bool):
        assert int(actual) == expected, f"{label}: expected {expected}, got {actual!r}"
        return
    if isinstance(expected, float):
        _assert_close(float(actual), expected, label)
        return
    if isinstance(expected, (tuple, list)):
        actual_values = tuple(float(actual[i]) for i in range(len(expected)))
        for index, expected_value in enumerate(expected):
            _assert_close(actual_values[index], float(expected_value), f"{label}[{index}]")
        return
    assert str(actual) == str(expected), f"{label}: expected {expected!r}, got {actual!r}"


def _assert_modifier_values(modifier, values: dict, *, skip: set[str] | None = None, label: str) -> None:
    skipped = set(skip or ())
    for socket_name, expected in values.items():
        if socket_name in skipped:
            continue
        _assert_modifier_value(modifier, socket_name, expected, f"{label} {socket_name}")


def _evaluated_polygon_count(obj) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(getattr(mesh, "polygons", []) or [])
    finally:
        evaluated.to_mesh_clear()


def _assert_generated_group(group, *, kind: str) -> None:
    nodes = {node.bl_idname for node in group.nodes}
    required = {
        "effect_line": {
            "GeometryNodeCurvePrimitiveLine",
            "GeometryNodeCurveToMesh",
            "GeometryNodeMeshLine",
            "GeometryNodeInstanceOnPoints",
        },
        "balloon": {"GeometryNodeMeshCircle", "GeometryNodeCurveToMesh", "GeometryNodeSetMaterial"},
    }[kind]
    assert required.issubset(nodes), f"{kind} の Geometry Nodes が生成ノードを持っていません: {nodes}"
    assert "GeometryNodeObjectInfo" not in nodes, f"{kind} がB-Name生成の参照形状を読んでいます"
    direct_links = [
        link
        for link in group.links
        if link.from_node.bl_idname == "NodeGroupInput"
        and link.to_node.bl_idname == "NodeGroupOutput"
        and link.from_socket.name == "Geometry"
        and link.to_socket.name == "Geometry"
    ]
    assert not direct_links, f"{kind} が入力形状をそのまま出力しています"
    input_geometry_links = [
        link
        for link in group.links
        if link.from_node.bl_idname == "NodeGroupInput" and link.from_socket.name == "Geometry"
    ]
    assert not input_geometry_links, f"{kind} が入力形状に依存しています"


def _assert_all_setting_inputs_linked(group, gn, *, kind: str) -> None:
    input_node = next((node for node in group.nodes if node.bl_idname == "NodeGroupInput"), None)
    assert input_node is not None, f"{kind} の入力ノードがありません"
    output_node = next((node for node in group.nodes if node.bl_idname == "NodeGroupOutput"), None)
    assert output_node is not None, f"{kind} の出力ノードがありません"
    missing: list[str] = []
    missing_audit: list[str] = []
    for spec in gn._GROUP_SOCKETS[kind]:  # noqa: SLF001 - 実機監査で内部契約を固定する
        source = input_node.outputs.get(spec.name)
        if source is None:
            missing.append(spec.name)
            continue
        links = [link for link in group.links if link.from_socket == source]
        if not links:
            missing.append(spec.name)
        if spec.socket_type == "NodeSocketMaterial":
            continue
        audit_name = f"{gn._SETTING_OUTPUT_PREFIX}{spec.name}"  # noqa: SLF001
        target = output_node.inputs.get(audit_name)
        if target is None or not any(link.from_socket == source and link.to_socket == target for link in group.links):
            missing_audit.append(spec.name)
    assert not missing, f"{kind} の未接続入力があります: {missing}"
    assert not missing_audit, f"{kind} の設定接続確認に未接続があります: {missing_audit}"


def _assert_nodes(obj, *, kind: str, group_name: str):
    from bname_dev_gn_bridge.utils import geometry_nodes_bridge as gn

    modifier = obj.modifiers.get(gn.MODIFIER_NAME)
    assert modifier is not None, f"{getattr(obj, 'name', '')} に Geometry Nodes がありません"
    assert getattr(modifier, "type", "") == "NODES"
    assert modifier.node_group is not None
    assert modifier.node_group.name == group_name
    assert str(obj.get(gn.PROP_GN_KIND, "") or "") == kind
    _assert_generated_group(modifier.node_group, kind=kind)
    _assert_all_setting_inputs_linked(modifier.node_group, gn, kind=kind)
    return modifier


def _create_legacy_passthrough_group() -> None:
    group = bpy.data.node_groups.new("BName_GN_Balloon", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    input_node = group.nodes.new("NodeGroupInput")
    output_node = group.nodes.new("NodeGroupOutput")
    group.links.new(input_node.outputs["Geometry"], output_node.inputs["Geometry"])


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_gn_bridge_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _create_legacy_passthrough_group()
        mod = _load_addon()
        _assert_generated_group(bpy.data.node_groups["BName_GN_Balloon"], kind="balloon")
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "GeometryNodes.bname"))
        assert "FINISHED" in result, result

        from bname_dev_gn_bridge.core.work import get_work
        from bname_dev_gn_bridge.core import effect_line as effect_core
        from bname_dev_gn_bridge.operators import balloon_op, effect_line_op
        from bname_dev_gn_bridge.utils import balloon_curve_object
        from bname_dev_gn_bridge.utils import effect_line_object
        from bname_dev_gn_bridge.utils import geometry_nodes_bridge as gn_bridge
        from bname_dev_gn_bridge.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)

        params = context.scene.bname_effect_line_params
        params.effect_type = "focus"
        params.rotation_deg = 17.5
        params.start_shape = "cloud"
        params.start_to_coma_frame = False
        params.start_frame_density_basis = "ellipse"
        params.start_frame_density_rounding_percent = 62.5
        params.start_rounded_corner_enabled = True
        params.start_rounded_corner_radius_mm = 2.2
        params.start_cloud_bump_width_mm = 8.1
        params.start_cloud_bump_width_jitter = 0.12
        params.start_cloud_bump_height_mm = 3.4
        params.start_cloud_bump_height_jitter = 0.23
        params.start_cloud_offset_percent = 41.0
        params.start_cloud_sub_width_ratio = 15.0
        params.start_cloud_sub_width_jitter = 0.07
        params.start_cloud_sub_height_ratio = 22.0
        params.start_cloud_sub_height_jitter = 0.09
        params.end_shape = "thorn-curve"
        params.end_rounded_corner_enabled = True
        params.end_rounded_corner_radius_mm = 4.4
        params.end_cloud_bump_width_mm = 11.2
        params.end_cloud_bump_width_jitter = 0.21
        params.end_cloud_bump_height_mm = 5.6
        params.end_cloud_bump_height_jitter = 0.18
        params.end_cloud_offset_percent = 37.0
        params.end_cloud_sub_width_ratio = 18.0
        params.end_cloud_sub_width_jitter = 0.13
        params.end_cloud_sub_height_ratio = 25.0
        params.end_cloud_sub_height_jitter = 0.16
        params.brush_size_mm = 0.72
        params.brush_jitter_enabled = True
        params.brush_jitter_amount = 0.31
        params.length_jitter_enabled = True
        params.length_jitter_amount = 0.27
        params.end_length_jitter_enabled = True
        params.end_length_jitter_amount = 0.29
        params.spacing_mode = "angle"
        params.spacing_angle_deg = 4.5
        params.spacing_distance_mm = 0.52
        params.spacing_density_compensation = False
        params.spacing_jitter_enabled = True
        params.spacing_jitter_amount = 0.19
        params.opacity = 0.63
        params.max_line_count = 77
        params.bundle_enabled = True
        params.bundle_line_count = 5
        params.bundle_line_count_jitter = 0.11
        params.bundle_jitter_amount = 0.17
        params.bundle_gap_mm = 0.9
        params.bundle_gap_jitter_amount = 0.14
        params.inout_apply = "opacity"
        params.in_percent = 82.0
        params.out_percent = 35.0
        params.in_start_percent = 43.0
        params.out_start_percent = 38.0
        params.in_easing_curve = "0.0000,0.0000;0.2500,0.1000;1.0000,1.0000"
        params.out_easing_curve = "0.0000,0.0000;0.7000,0.9000;1.0000,1.0000"
        params.inout_range_mode = "length"
        params.in_range_percent = 64.0
        params.out_range_percent = 71.0
        params.in_range_mm = 12.5
        params.out_range_mm = 9.75
        params.line_color = (0.18, 0.24, 0.36, 1.0)
        params.fill_color = (0.33, 0.22, 0.11, 1.0)
        params.fill_opacity = 0.58
        params.fill_base_shape = True
        params.speed_angle_deg = 21.0
        params.speed_line_count = 144
        params.white_outline_count = 6
        params.white_outline_spacing_mm = 0.8
        params.white_outline_width_mm = 12.0
        params.white_outline_width_jitter_enabled = True
        params.white_outline_width_min_percent = 44.0
        params.white_outline_length_jitter_enabled = True
        params.white_outline_length_min_percent = 55.0
        params.white_outline_white_ratio_percent = 31.0
        params.white_outline_white_brush_mm = 0.41
        params.white_outline_white_attenuation = 3.0
        params.white_outline_black_brush_mm = 0.63
        params.white_outline_black_attenuation = -2.0
        params.white_outline_angle_deg = 12.0
        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            context,
            (15.0, 20.0, 64.0, 48.0),
            parent_key=page_key,
        )
        assert effect_obj is not None and effect_layer is not None
        effect_line_op._write_effect_strokes(
            context,
            effect_obj,
            effect_layer,
            (15.0, 20.0, 64.0, 48.0),
            seed=123,
        )
        effect_display = effect_line_object.find_effect_display_object(effect_obj)
        assert effect_display is not None, "効果線のGeometry Nodes表示実体がありません"
        assert effect_obj.hide_viewport, "効果線の制御用レイヤーが表示対象のままです"
        effect_modifier = _assert_nodes(
            effect_display,
            kind="effect_line",
            group_name="BName_GN_EffectLine",
        )
        _assert_close(_modifier_socket_value(effect_modifier, "線幅"), 0.72, "効果線 線幅")
        _assert_close(_modifier_socket_value(effect_modifier, "不透明度"), 0.63, "効果線 不透明度")
        assert int(_modifier_socket_value(effect_modifier, "本数")) == 77
        assert int(_modifier_socket_value(effect_modifier, "乱数")) == 123
        _assert_close(_modifier_socket_value(effect_modifier, "位置 X"), 15.0, "効果線 位置 X")
        _assert_close(_modifier_socket_value(effect_modifier, "位置 Y"), 20.0, "効果線 位置 Y")
        _assert_close(_modifier_socket_value(effect_modifier, "幅"), 64.0, "効果線 幅")
        _assert_close(_modifier_socket_value(effect_modifier, "高さ"), 48.0, "効果線 高さ")
        effect_material = effect_display.data.materials[0]
        _assert_close(effect_material.diffuse_color[0], 0.18, "効果線 素材色 R")
        _assert_close(effect_material.diffuse_color[1], 0.24, "効果線 素材色 G")
        _assert_close(effect_material.diffuse_color[2], 0.36, "効果線 素材色 B")
        _assert_close(effect_material.diffuse_color[3], 0.63, "効果線 素材不透明度")
        effect_socket_names = gn_bridge.effect_field_socket_names()
        internal_legacy_fields = {"spacing_density_compensation"}
        missing = [
            field
            for field in effect_core.EFFECT_PARAM_FIELDS
            if field not in internal_legacy_fields
            and (field not in effect_socket_names
            or _modifier_socket_value(effect_modifier, effect_socket_names[field]) is None
            )
        ]
        assert not missing, f"効果線の詳細設定がGeometry Nodes入力へ移植されていません: {missing}"
        assert all(
            getattr(item, "name", "") != "密度補正"
            for item in effect_modifier.node_group.interface.items_tree
            if getattr(item, "item_type", "") == "SOCKET" and getattr(item, "in_out", "") == "INPUT"
        ), "密度補正が独立した設定欄として残っています"
        _assert_modifier_values(
            effect_modifier,
            gn_bridge.effect_values(params, (15.0, 20.0, 64.0, 48.0), 123),
            label="効果線",
        )
        assert _evaluated_polygon_count(effect_display) > 0, "効果線のGeometry Nodes表示結果が空です"
        effect_line_op._select_effect_layer(context, effect_obj, effect_layer)
        params.brush_size_mm = 1.11
        params.opacity = 0.41
        params.effect_type = "speed"
        params.speed_line_count = 33
        params.fill_base_shape = True
        params.line_color = (0.7, 0.11, 0.22, 1.0)
        updated_display = effect_line_object.find_effect_display_object(effect_obj)
        assert updated_display is effect_display, "詳細設定変更で効果線の表示実体が重複しました"
        effect_modifier = _assert_nodes(
            updated_display,
            kind="effect_line",
            group_name="BName_GN_EffectLine",
        )
        _assert_close(_modifier_socket_value(effect_modifier, "線幅"), 1.11, "効果線 線幅 更新")
        _assert_close(_modifier_socket_value(effect_modifier, "不透明度"), 0.41, "効果線 不透明度 更新")
        assert int(_modifier_socket_value(effect_modifier, "種類")) == 4
        assert int(_modifier_socket_value(effect_modifier, "本数")) == 77
        assert int(_modifier_socket_value(effect_modifier, "流線の本数上限")) == 33
        assert bool(_modifier_socket_value(effect_modifier, "終点形状を下地として塗る"))
        effect_material = updated_display.data.materials[0]
        _assert_close(effect_material.diffuse_color[0], 0.7, "効果線 素材色 R 更新")
        _assert_close(effect_material.diffuse_color[1], 0.11, "効果線 素材色 G 更新")
        _assert_close(effect_material.diffuse_color[2], 0.22, "効果線 素材色 B 更新")
        _assert_close(effect_material.diffuse_color[3], 0.41, "効果線 素材不透明度 更新")
        display_count = sum(
            1
            for obj in bpy.data.objects
            if str(obj.get(effect_line_object.PROP_EFFECT_CONTROLLER_ID, "") or "")
            == str(effect_obj.get("bname_id", "") or "")
        )
        assert display_count == 1, f"効果線の表示実体が重複しています: {display_count}"

        balloon = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=30.0,
            y=50.0,
            w=42.0,
            h=23.0,
            parent_kind="page",
            parent_key=page_key,
        )
        balloon.shape = "cloud"
        balloon.custom_preset_name = "custom_name_check"
        balloon.x_mm = 31.25
        balloon.y_mm = 51.5
        balloon.width_mm = 43.0
        balloon.height_mm = 24.5
        balloon.rotation_deg = 8.0
        balloon.center_offset_x_mm = 1.5
        balloon.center_offset_y_mm = -2.25
        balloon.rounded_corner_enabled = True
        balloon.rounded_corner_radius_mm = 3.75
        balloon.line_style = "double"
        balloon.line_width_mm = 0.55
        balloon.line_color = (0.14, 0.21, 0.34, 1.0)
        balloon.fill_color = (0.72, 0.62, 0.52, 1.0)
        balloon.fill_opacity = 0.44
        balloon.fill_material_name = "MaterialCheck"
        balloon.fill_blur_amount = 0.18
        balloon.fill_blur_dither = True
        balloon.fill_gradient_enabled = True
        balloon.fill_gradient_start_color = (0.9, 0.8, 0.7, 1.0)
        balloon.fill_gradient_end_color = (0.2, 0.3, 0.4, 1.0)
        balloon.fill_gradient_angle_deg = 32.0
        balloon.outer_white_margin_enabled = True
        balloon.outer_white_margin_width_mm = 1.25
        balloon.outer_white_margin_color = (0.95, 0.96, 0.97, 1.0)
        balloon.inner_white_margin_enabled = True
        balloon.inner_white_margin_width_mm = 0.75
        balloon.inner_white_margin_color = (0.82, 0.83, 0.84, 1.0)
        balloon.blend_mode = "lighten"
        balloon.flip_h = True
        balloon.flip_v = True
        balloon.opacity = 0.77
        sp = balloon.shape_params
        sp.cloud_bump_width_mm = 9.1
        sp.cloud_bump_width_jitter = 0.15
        sp.cloud_bump_height_mm = 4.2
        sp.cloud_bump_height_jitter = 0.16
        sp.cloud_offset_percent = 47.0
        sp.cloud_sub_width_ratio = 21.0
        sp.cloud_sub_width_jitter = 0.17
        sp.cloud_sub_height_ratio = 24.0
        sp.cloud_sub_height_jitter = 0.18
        sp.cloud_wave_count = 17
        sp.cloud_wave_amplitude_mm = 3.3
        sp.spike_count = 29
        sp.spike_depth_mm = 7.7
        sp.spike_jitter = 0.26
        tail_types = ("straight", "curve", "sticky", "straight", "curve", "sticky", "straight", "curve")
        for index, tail_type in enumerate(tail_types):
            tail = balloon.tails.add()
            tail.type = tail_type
            tail.direction_deg = 35.0 + index * 20.0
            tail.length_mm = 6.0 + index
            tail.root_width_mm = 2.5 + index * 0.25
            tail.tip_width_mm = 0.5 + index * 0.1
            tail.curve_bend = -0.4 + index * 0.1
            tail.custom_points_enabled = index % 2 == 1
            tail.start_x_mm = -3.0 + index
            tail.start_y_mm = 2.0 + index
            tail.end_x_mm = 7.0 + index
            tail.end_y_mm = -5.0 - index
        balloon_obj = balloon_curve_object.ensure_balloon_curve_object(
            scene=context.scene,
            entry=balloon,
            page=page,
        )
        assert balloon_obj is not None
        balloon_modifier = _assert_nodes(
            balloon_obj,
            kind="balloon",
            group_name="BName_GN_Balloon",
        )
        _assert_close(_modifier_socket_value(balloon_modifier, "線幅"), 0.55, "フキダシ 線幅")
        _assert_close(_modifier_socket_value(balloon_modifier, "塗り不透明度"), 0.44, "フキダシ 塗り")
        _assert_close(_modifier_socket_value(balloon_modifier, "幅"), 43.0, "フキダシ 幅")
        _assert_close(_modifier_socket_value(balloon_modifier, "高さ"), 24.5, "フキダシ 高さ")
        assert int(_modifier_socket_value(balloon_modifier, "形状")) == 3
        _assert_modifier_values(
            balloon_modifier,
            gn_bridge.balloon_values(balloon),
            label="フキダシ",
        )
        assert int(_modifier_socket_value(balloon_modifier, "しっぽ数")) == 8
        assert int(_modifier_socket_value(balloon_modifier, "しっぽ 種類")) == 1
        assert int(_modifier_socket_value(balloon_modifier, "しっぽ2 種類")) == 2
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 方向"), 55.0, "フキダシ しっぽ2 方向")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 長さ"), 7.0, "フキダシ しっぽ2 長さ")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 根元幅"), 2.75, "フキダシ しっぽ2 根元幅")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 先端幅"), 0.6, "フキダシ しっぽ2 先端幅")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 曲げ"), -0.3, "フキダシ しっぽ2 曲げ")
        assert bool(_modifier_socket_value(balloon_modifier, "しっぽ2 始点・終点を固定"))
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 始点 X"), -2.0, "フキダシ しっぽ2 始点 X")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 始点 Y"), 3.0, "フキダシ しっぽ2 始点 Y")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 終点 X"), 8.0, "フキダシ しっぽ2 終点 X")
        _assert_close(_modifier_socket_value(balloon_modifier, "しっぽ2 終点 Y"), -6.0, "フキダシ しっぽ2 終点 Y")
        assert len(balloon_obj.data.polygons) == 0, "フキダシ本体にB-Name側の表示メッシュが残っています"
        assert _evaluated_polygon_count(balloon_obj) > 0, "フキダシのGeometry Nodes表示結果が空です"
        source_obj = bpy.data.objects.get(f"{balloon_curve_object.BALLOON_SOURCE_NAME_PREFIX}{balloon.id}")
        assert source_obj is None, "フキダシにB-Name生成の参照形状が残っています"

        balloon.line_width_mm = 0.91
        balloon_modifier = _assert_nodes(balloon_obj, kind="balloon", group_name="BName_GN_Balloon")
        _assert_close(_modifier_socket_value(balloon_modifier, "線幅"), 0.91, "フキダシ 線幅 更新")

        balloon_shape_ids = {
            str(getattr(item, "identifier", "") or "")
            for item in balloon.bl_rna.properties["shape"].enum_items
        }
        assert "uni_flash" not in balloon_shape_ids, "フキダシ形状にウニフラッシュが残っています"

        legacy = balloon_op._create_balloon_entry(
            context,
            page,
            shape="uni_flash",
            x=88.0,
            y=66.0,
            w=58.0,
            h=36.0,
            parent_kind="page",
            parent_key=page_key,
        )
        legacy.line_width_mm = 0.38
        legacy_obj = balloon_curve_object.ensure_balloon_curve_object(
            scene=context.scene,
            entry=legacy,
            page=page,
        )
        assert legacy_obj is not None
        legacy_modifier = _assert_nodes(legacy_obj, kind="balloon", group_name="BName_GN_Balloon")
        _assert_close(_modifier_socket_value(legacy_modifier, "線幅"), 0.38, "旧フキダシ 線幅")
        assert int(_modifier_socket_value(legacy_modifier, "形状")) == 2
        assert legacy_modifier.node_group.name != "BName_GN_UniFlash"

        print("BNAME_GEOMETRY_NODES_BRIDGE_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
