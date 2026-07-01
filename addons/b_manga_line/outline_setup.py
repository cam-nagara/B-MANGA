"""B-MANGA Line — Solidify モディファイアとマテリアルのセットアップ.

背面法（Inverted Hull Method）の実装ロジック。
- Solidify モディファイアで法線反転したシェルを生成
- 専用マテリアルで背面カリング → 輪郭線として描画
- 頂点グループで線幅をパーツごとに制御
"""

from __future__ import annotations

import json

import bpy

from . import plane_filter
from .core import (
    AOV_NAME,
    COLOR_ATTR_NAME,
    DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE,
    MATERIAL_NAME,
    MODIFIER_NAME,
    PROP_LINE_ONLY,
    PROP_LINE_ONLY_MATERIALS,
    PROP_BASE_THICKNESS,
    PROP_REF_DISTANCE,
    VG_INNER_LINE_WIDTH,
    VG_INTERSECTION_LINE_WIDTH,
    VG_LINE_WIDTH,
)
from .scale_utils import modifier_thickness_for_world_width


LINE_ONLY_MATERIAL_NAME = "BML_LineOnly_SurfaceHidden"
LINE_ONLY_WIREFRAME_NAME = "BML_LineOnly_Wire"
PROP_HIDE_THROUGH_TRANSPARENT = "bml_hide_through_transparent"


# ------------------------------------------------------------------
# マテリアル
# ------------------------------------------------------------------

def _is_outline_material(mat: bpy.types.Material) -> bool:
    """BML アウトラインマテリアルかどうか."""
    name = mat.name
    return name == MATERIAL_NAME or name.startswith(MATERIAL_NAME + ".")


def _build_outline_nodes(
    mat: bpy.types.Material,
    color: tuple[float, ...],
    *,
    hide_through_transparent: bool = False,
) -> None:
    """マテリアルノードツリーを構築（背面法 + AOV 出力）.

    背面法（Inverted Hull Method）:
    Solidify の use_flip_normals でシェル法線を反転。
    EEVEE: use_backface_culling でシェル正面側をカリング。
    Cycles: Light Path + Backfacing の 2段 MixShader で制御:
      外側 MixShader — Is Camera Ray で分岐:
        非カメラレイ（影等）→ Transparent（影を落とさない）
        カメラレイ → 内側 MixShader へ
      内側 MixShader — Backfacing で分岐:
        Factor=0（アウトライン面）→ Emission
        Factor=1（カリング対象面）→ Transparent
    """
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (800, 0)

    rgb = nodes.new("ShaderNodeRGB")
    rgb.location = (-400, 100)
    rgb.outputs[0].default_value = (color[0], color[1], color[2], 1.0)
    rgb.label = "BML_Color"

    emission = nodes.new("ShaderNodeEmission")
    emission.location = (200, 100)
    links.new(rgb.outputs[0], emission.inputs["Color"])
    emission.inputs["Strength"].default_value = 1.0

    trans_bf = nodes.new("ShaderNodeBsdfTransparent")
    trans_bf.location = (200, -100)

    geom = nodes.new("ShaderNodeNewGeometry")
    geom.location = (-400, -100)

    lightpath = nodes.new("ShaderNodeLightPath")
    lightpath.location = (-400, 300)

    trans_lp = nodes.new("ShaderNodeBsdfTransparent")
    trans_lp.location = (200, 300)

    mix_bf = nodes.new("ShaderNodeMixShader")
    mix_bf.location = (400, 0)
    links.new(geom.outputs["Backfacing"], mix_bf.inputs[0])
    links.new(emission.outputs["Emission"], mix_bf.inputs[1])
    links.new(trans_bf.outputs["BSDF"], mix_bf.inputs[2])

    camera_shader = mix_bf.outputs["Shader"]
    transparent_depth_mask = None
    if hide_through_transparent:
        depth_cmp = nodes.new("ShaderNodeMath")
        depth_cmp.location = (390, -260)
        depth_cmp.operation = "GREATER_THAN"
        links.new(lightpath.outputs["Transparent Depth"], depth_cmp.inputs[0])
        depth_cmp.inputs[1].default_value = 1.0
        transparent_depth_mask = depth_cmp.outputs[0]

        trans_td = nodes.new("ShaderNodeBsdfTransparent")
        trans_td.location = (400, -430)

        mix_td = nodes.new("ShaderNodeMixShader")
        mix_td.location = (600, -180)
        links.new(transparent_depth_mask, mix_td.inputs[0])
        links.new(mix_bf.outputs["Shader"], mix_td.inputs[1])
        links.new(trans_td.outputs["BSDF"], mix_td.inputs[2])
        camera_shader = mix_td.outputs["Shader"]

    mix_lp = nodes.new("ShaderNodeMixShader")
    mix_lp.location = (600, 0)
    links.new(lightpath.outputs["Is Camera Ray"], mix_lp.inputs[0])
    links.new(trans_lp.outputs["BSDF"], mix_lp.inputs[1])
    links.new(camera_shader, mix_lp.inputs[2])

    links.new(mix_lp.outputs["Shader"], output.inputs["Surface"])

    # --- AOV 出力 ---
    aov = nodes.new("ShaderNodeOutputAOV")
    aov.location = (800, -250)
    aov.aov_name = AOV_NAME

    links.new(rgb.outputs[0], aov.inputs["Color"])

    invert = nodes.new("ShaderNodeMath")
    invert.location = (600, -250)
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    links.new(geom.outputs["Backfacing"], invert.inputs[1])
    aov_value = invert.outputs[0]
    if transparent_depth_mask is not None:
        not_depth = nodes.new("ShaderNodeMath")
        not_depth.location = (760, -430)
        not_depth.operation = "SUBTRACT"
        not_depth.inputs[0].default_value = 1.0
        links.new(transparent_depth_mask, not_depth.inputs[1])

        visible_value = nodes.new("ShaderNodeMath")
        visible_value.location = (940, -350)
        visible_value.operation = "MULTIPLY"
        links.new(invert.outputs[0], visible_value.inputs[0])
        links.new(not_depth.outputs[0], visible_value.inputs[1])
        aov_value = visible_value.outputs[0]

    links.new(aov_value, aov.inputs["Value"])
    mat[PROP_HIDE_THROUGH_TRANSPARENT] = bool(hide_through_transparent)


def _build_line_only_outline_nodes(
    mat: bpy.types.Material,
    color: tuple[float, ...],
) -> None:
    """ライン確認中はライン素材の輪郭方向だけを黒く描く."""
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (700, 0)

    rgb = nodes.new("ShaderNodeRGB")
    rgb.location = (-300, 100)
    rgb.outputs[0].default_value = (color[0], color[1], color[2], 1.0)
    rgb.label = "BML_Color"

    emission = nodes.new("ShaderNodeEmission")
    emission.location = (100, 100)
    emission.inputs["Strength"].default_value = 1.0
    links.new(rgb.outputs[0], emission.inputs["Color"])

    layer = nodes.new("ShaderNodeLayerWeight")
    layer.location = (-300, -60)
    if "Blend" in layer.inputs:
        layer.inputs["Blend"].default_value = 0.25

    silhouette = nodes.new("ShaderNodeMath")
    silhouette.location = (-80, -60)
    silhouette.operation = "LESS_THAN"
    links.new(layer.outputs["Facing"], silhouette.inputs[0])
    silhouette.inputs[1].default_value = 0.08

    surface_transparent = nodes.new("ShaderNodeBsdfTransparent")
    surface_transparent.location = (100, -80)

    surface_mix = nodes.new("ShaderNodeMixShader")
    surface_mix.location = (420, 0)
    links.new(silhouette.outputs[0], surface_mix.inputs[0])
    links.new(surface_transparent.outputs["BSDF"], surface_mix.inputs[1])
    links.new(emission.outputs["Emission"], surface_mix.inputs[2])

    lightpath = nodes.new("ShaderNodeLightPath")
    lightpath.location = (-300, -260)

    transparent = nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (420, -220)

    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (660, 0)
    links.new(lightpath.outputs["Is Camera Ray"], mix.inputs[0])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(surface_mix.outputs["Shader"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])

    aov = nodes.new("ShaderNodeOutputAOV")
    aov.location = (700, -220)
    aov.aov_name = AOV_NAME
    links.new(rgb.outputs[0], aov.inputs["Color"])
    links.new(silhouette.outputs[0], aov.inputs["Value"])


def _has_aov_node(mat: bpy.types.Material) -> bool:
    if not mat.use_nodes:
        return False
    for node in mat.node_tree.nodes:
        if hasattr(node, "aov_name") and node.aov_name == AOV_NAME:
            return True
    return False


def _repair_outline_material(
    mat: bpy.types.Material,
    color: tuple[float, ...],
    *,
    hide_through_transparent: bool,
) -> None:
    current = bool(mat.get(PROP_HIDE_THROUGH_TRANSPARENT, False))
    if not _has_aov_node(mat) or current != hide_through_transparent:
        _build_outline_nodes(
            mat,
            color,
            hide_through_transparent=hide_through_transparent,
        )
    else:
        _update_emission_color(mat, color)
    _configure_material(mat)


def _configure_material(mat: bpy.types.Material) -> None:
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = True
    try:
        mat.shadow_method = "NONE"
    except (AttributeError, TypeError):
        pass


def get_or_create_material(
    obj: bpy.types.Object,
    color: tuple[float, ...] = (0.0, 0.0, 0.0, 1.0),
    *,
    hide_through_transparent: bool = False,
) -> bpy.types.Material:
    """オブジェクト専用のアウトラインマテリアルを取得または作成."""
    for slot in obj.material_slots:
        if slot.material and _is_outline_material(slot.material):
            mat = slot.material
            _repair_outline_material(
                mat,
                color,
                hide_through_transparent=hide_through_transparent,
            )
            return mat

    mat = bpy.data.materials.new(name=MATERIAL_NAME)
    _build_outline_nodes(
        mat,
        color,
        hide_through_transparent=hide_through_transparent,
    )
    _configure_material(mat)
    return mat


def _first_outline_slot(obj: bpy.types.Object) -> int | None:
    for i, slot in enumerate(obj.material_slots):
        if slot.material and _is_outline_material(slot.material):
            return i
    return None


def _ensure_outline_material_slots(
    obj: bpy.types.Object,
    mat: bpy.types.Material,
) -> int:
    """Solidify の素材ずらし先を、元素材数ぶんライン素材で埋める."""
    first = _first_outline_slot(obj)
    if first is None:
        first = len(obj.data.materials)

    source_count = max(1, first)
    needed = first + source_count
    while len(obj.data.materials) < needed:
        obj.data.materials.append(mat)
    for index in range(first, needed):
        obj.data.materials[index] = mat
    return first


def get_outline_material(obj: bpy.types.Object) -> bpy.types.Material | None:
    """オブジェクトのアウトラインマテリアルを取得."""
    settings = getattr(obj, "bmanga_line_settings", None)
    color = tuple(getattr(settings, "outline_color", (0.0, 0.0, 0.0, 1.0)))
    hide_transparent = bool(getattr(settings, "hide_through_transparent", False))
    for slot in obj.material_slots:
        if slot.material and _is_outline_material(slot.material):
            _repair_outline_material(
                slot.material,
                color,
                hide_through_transparent=hide_transparent,
            )
            return slot.material
    return None


def _update_emission_color(mat: bpy.types.Material, color: tuple[float, ...]) -> None:
    if not mat.use_nodes:
        return
    for node in mat.node_tree.nodes:
        if node.type == "RGB" and node.label == "BML_Color":
            node.outputs[0].default_value = (color[0], color[1], color[2], 1.0)
            return
    for node in mat.node_tree.nodes:
        if node.type == "EMISSION" and node.label == "BML_Color":
            node.inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
            return


def update_material_color(obj: bpy.types.Object, color: tuple[float, ...]) -> None:
    """オブジェクトのアウトラインマテリアルの色を更新."""
    settings = getattr(obj, "bmanga_line_settings", None)
    hide_transparent = bool(getattr(settings, "hide_through_transparent", False))
    for slot in obj.material_slots:
        if slot.material and _is_outline_material(slot.material):
            _repair_outline_material(
                slot.material,
                color,
                hide_through_transparent=hide_transparent,
            )
            _update_emission_color(slot.material, color)
            return


def update_transparent_protection(
    obj: bpy.types.Object,
    enabled: bool,
    color: tuple[float, ...],
) -> None:
    """透明面越しに見える裏面ラインの抑制設定を更新."""
    for slot in obj.material_slots:
        if slot.material and _is_outline_material(slot.material):
            _build_outline_nodes(
                slot.material,
                color,
                hide_through_transparent=enabled,
            )
            _configure_material(slot.material)
            return


# ------------------------------------------------------------------
# 頂点グループ / カラーアトリビュート
# ------------------------------------------------------------------

def _ensure_vertex_group(obj: bpy.types.Object) -> bpy.types.VertexGroup:
    vg = obj.vertex_groups.get(VG_LINE_WIDTH)
    if vg is None:
        vg = obj.vertex_groups.new(name=VG_LINE_WIDTH)
    vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    return vg


def _ensure_color_attribute(obj: bpy.types.Object):
    mesh = obj.data
    attr = mesh.color_attributes.get(COLOR_ATTR_NAME)
    if attr is None:
        attr = mesh.color_attributes.new(
            name=COLOR_ATTR_NAME, type="FLOAT_COLOR", domain="POINT"
        )
        for i in range(len(attr.data)):
            attr.data[i].color = (1.0, 1.0, 1.0, 1.0)
    return attr


def _configure_solidify_shape(
    obj: bpy.types.Object,
    mod: bpy.types.Modifier,
    use_rim: bool,
) -> None:
    is_sheet = plane_filter.is_sheet_mesh(obj)
    mod.offset = 1.0
    if hasattr(mod, "use_rim_only"):
        mod.use_rim_only = is_sheet
    mod.use_rim = True if is_sheet else use_rim


def _configure_line_only_solidify_shape(
    obj: bpy.types.Object,
    use_rim: bool | None = None,
) -> None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if obj.type != "MESH" or mod is None:
        return
    if use_rim is None:
        settings = getattr(obj, "bmanga_line_settings", None)
        use_rim = bool(getattr(settings, "use_rim", False))
    is_sheet = plane_filter.is_sheet_mesh(obj)
    mod.offset = 1.0 if is_sheet else -1.0
    if hasattr(mod, "use_rim_only"):
        mod.use_rim_only = is_sheet
    mod.use_rim = True if is_sheet else bool(use_rim)


def _restore_solidify_shape(obj: bpy.types.Object) -> None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if obj.type != "MESH" or mod is None:
        return
    settings = getattr(obj, "bmanga_line_settings", None)
    use_rim = bool(getattr(settings, "use_rim", False))
    _configure_solidify_shape(obj, mod, use_rim)


def update_modifier_rim(obj: bpy.types.Object, use_rim: bool) -> None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if obj.type == "MESH" and mod is not None:
        if bool(obj.get(PROP_LINE_ONLY, False)):
            _configure_line_only_solidify_shape(obj, use_rim)
        else:
            _configure_solidify_shape(obj, mod, use_rim)


# ------------------------------------------------------------------
# 適用 / 削除
# ------------------------------------------------------------------

def apply_outline(
    obj: bpy.types.Object,
    thickness: float = 0.0003,
    color: tuple[float, ...] = (0.0, 0.0, 0.0, 1.0),
    use_vertex_color: bool = False,
    even_thickness: bool = True,
    use_rim: bool = True,
    *,
    use_vertex_group: bool = False,
    hide_through_transparent: bool = False,
    scene=None,
) -> bool:
    """オブジェクトに背面法アウトラインを適用. 成功時 True."""
    if obj.type != "MESH" or obj.data is None:
        return False
    if not obj.data.polygons:
        return False

    # 元メッシュ面用のマテリアルがなければ追加
    # （アウトライン専用マテリアルがスロット0に来ると元面もアウトライン化する）
    if not obj.data.materials:
        surface_mat = bpy.data.materials.new(name=obj.name)
        surface_mat.use_nodes = True
        obj.data.materials.append(surface_mat)

    mat = get_or_create_material(
        obj,
        color,
        hide_through_transparent=hide_through_transparent,
    )

    material_offset = _ensure_outline_material_slots(obj, mat)

    # Solidify モディファイア
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=MODIFIER_NAME, type="SOLIDIFY")
    mod.thickness = modifier_thickness_for_world_width(obj, thickness)
    mod.offset = 1.0
    mod.use_flip_normals = True
    mod.use_even_offset = even_thickness
    _configure_solidify_shape(obj, mod, use_rim)
    mod.material_offset = material_offset
    mod.material_offset_rim = material_offset

    # 頂点グループによる線幅制御
    need_vg = use_vertex_color or use_vertex_group
    if need_vg:
        vg = _ensure_vertex_group(obj)
        if use_vertex_color:
            _ensure_color_attribute(obj)
        mod.vertex_group = vg.name
        mod.thickness_vertex_group = 0.0
    else:
        mod.vertex_group = ""

    # 線幅入力値の基準距離を保存
    if scene is not None and scene.camera is not None:
        obj[PROP_BASE_THICKNESS] = abs(thickness)
        obj[PROP_REF_DISTANCE] = DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE

    if scene is not None:
        ensure_aov_passes(scene)

    if bool(obj.get(PROP_LINE_ONLY, False)):
        _restore_outline_materials(
            obj,
            obj.data,
            hide_through_transparent_override=True,
        )
        _configure_line_only_solidify_shape(obj, use_rim)

    return True


def ensure_aov_pass(view_layer) -> bool:
    """ビューレイヤーに AOV パスを追加. 既存なら何もしない."""
    for aov in view_layer.aovs:
        if aov.name == AOV_NAME:
            return False
    aov = view_layer.aovs.add()
    aov.name = AOV_NAME
    aov.type = "COLOR"
    return True


def ensure_aov_passes(scene=None) -> int:
    """指定シーン、または全シーンの BML_Line AOV を保証する."""
    if scene is not None:
        scenes = [scene]
    else:
        scene_collection = getattr(bpy.data, "scenes", None)
        if scene_collection is None:
            return 0
        scenes = list(scene_collection)
    count = 0
    for scn in scenes:
        if scn is None:
            continue
        for view_layer in scn.view_layers:
            if ensure_aov_pass(view_layer):
                count += 1
    return count


def remove_outline(obj: bpy.types.Object) -> bool:
    """オブジェクトからアウトラインを削除. 削除した場合 True."""
    if obj.type != "MESH":
        return False

    removed = False

    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is not None:
        obj.modifiers.remove(mod)
        removed = True

    set_line_only(obj, False)

    # マテリアルスロットを除去（オブジェクト専用マテリアル）
    for i in range(len(obj.data.materials) - 1, -1, -1):
        mat = obj.data.materials[i]
        if mat and _is_outline_material(mat):
            obj.data.materials.pop(index=i)
            if mat.users == 0:
                bpy.data.materials.remove(mat)
            removed = True

    # 頂点グループ
    for name in (VG_LINE_WIDTH, VG_INNER_LINE_WIDTH, VG_INTERSECTION_LINE_WIDTH):
        vg = obj.vertex_groups.get(name)
        if vg is not None:
            obj.vertex_groups.remove(vg)

    return removed


# ------------------------------------------------------------------
# パラメータ更新
# ------------------------------------------------------------------

def update_modifier_thickness(obj: bpy.types.Object, thickness: float) -> None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is not None:
        mod.thickness = modifier_thickness_for_world_width(obj, thickness)


# ------------------------------------------------------------------
# 頂点カラー → 頂点グループ同期
# ------------------------------------------------------------------

def sync_vertex_colors_to_weights(obj: bpy.types.Object) -> int:
    """頂点カラーの明度を頂点グループウェイトに反映. 処理頂点数を返す."""
    if obj.type != "MESH":
        return 0
    mesh = obj.data
    attr = mesh.color_attributes.get(COLOR_ATTR_NAME)
    vg = obj.vertex_groups.get(VG_LINE_WIDTH)
    if attr is None or vg is None:
        return 0

    count = 0
    n = min(len(attr.data), len(mesh.vertices))
    for i in range(n):
        c = attr.data[i].color
        luminance = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
        vg.add([i], max(0.0, min(1.0, luminance)), "REPLACE")
        count += 1
    return count


def _get_line_only_material() -> bpy.types.Material:
    mat = bpy.data.materials.get(LINE_ONLY_MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(LINE_ONLY_MATERIAL_NAME)
    mat.use_nodes = True
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    try:
        mat.blend_method = "OPAQUE"
    except (AttributeError, TypeError):
        pass
    try:
        mat.surface_render_method = "DITHERED"
    except (AttributeError, TypeError):
        pass
    try:
        mat.show_transparent_back = False
    except (AttributeError, TypeError):
        pass
    try:
        mat.use_transparent_shadow = False
    except (AttributeError, TypeError):
        pass
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    emission.inputs["Strength"].default_value = 1.0
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def _line_only_color(obj: bpy.types.Object) -> tuple[float, ...]:
    settings = getattr(obj, "bmanga_line_settings", None)
    return tuple(getattr(settings, "outline_color", (0.0, 0.0, 0.0, 1.0)))


def _set_outline_materials_for_line_only(
    mesh: bpy.types.Mesh,
    color: tuple[float, ...],
) -> None:
    for mat in mesh.materials:
        if mat is not None and _is_outline_material(mat):
            _build_line_only_outline_nodes(mat, color)
            try:
                mat.use_backface_culling = False
            except (AttributeError, TypeError):
                pass


def _restore_outline_materials(
    obj: bpy.types.Object,
    mesh: bpy.types.Mesh,
    *,
    hide_through_transparent_override: bool | None = None,
) -> None:
    settings = getattr(obj, "bmanga_line_settings", None)
    color = _line_only_color(obj)
    if hide_through_transparent_override is None:
        hide_transparent = bool(getattr(settings, "hide_through_transparent", False))
    else:
        hide_transparent = bool(hide_through_transparent_override)
    for mat in mesh.materials:
        if mat is not None and _is_outline_material(mat):
            _build_outline_nodes(
                mat,
                color,
                hide_through_transparent=hide_transparent,
            )
            _configure_material(mat)


def _ensure_line_only_wire(obj: bpy.types.Object) -> None:
    outline_slot = _first_outline_slot(obj)
    if outline_slot is None:
        return
    wire = obj.modifiers.get(LINE_ONLY_WIREFRAME_NAME)
    if wire is None:
        wire = obj.modifiers.new(name=LINE_ONLY_WIREFRAME_NAME, type="WIREFRAME")
    wire.use_replace = False
    wire.use_even_offset = True
    wire.thickness = 0.025
    wire.material_offset = max(0, outline_slot)


def _remove_line_only_wire(obj: bpy.types.Object) -> None:
    wire = obj.modifiers.get(LINE_ONLY_WIREFRAME_NAME)
    if wire is not None:
        obj.modifiers.remove(wire)


def set_line_only(obj: bpy.types.Object, enabled: bool) -> bool:
    """ライン用以外の素材を一時的に白い光沢素材へ置き換える."""
    if obj.type != "MESH":
        return False
    mesh = obj.data
    if enabled:
        if bool(obj.get(PROP_LINE_ONLY, False)):
            _restore_outline_materials(
                obj,
                mesh,
                hide_through_transparent_override=True,
            )
            _configure_line_only_solidify_shape(obj)
            return True
        stored = []
        hidden = _get_line_only_material()
        _restore_outline_materials(
            obj,
            mesh,
            hide_through_transparent_override=True,
        )
        _configure_line_only_solidify_shape(obj)
        _remove_line_only_wire(obj)
        for index, mat in enumerate(mesh.materials):
            if mat is not None and _is_outline_material(mat):
                continue
            stored.append({"index": index, "material": mat.name if mat else ""})
            mesh.materials[index] = hidden
        mod = obj.modifiers.get(MODIFIER_NAME)
        if mod is not None and hasattr(mod, "material_offset_rim"):
            mod.material_offset_rim = mod.material_offset
        obj[PROP_LINE_ONLY_MATERIALS] = json.dumps(stored, ensure_ascii=False)
        obj[PROP_LINE_ONLY] = True
        return True

    if not bool(obj.get(PROP_LINE_ONLY, False)):
        return True
    raw = obj.get(PROP_LINE_ONLY_MATERIALS, "[]")
    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        stored = []
    for item in stored:
        index = int(item.get("index", -1))
        mat_name = item.get("material") or ""
        if 0 <= index < len(mesh.materials):
            mesh.materials[index] = bpy.data.materials.get(mat_name)
    _restore_outline_materials(obj, mesh)
    _restore_solidify_shape(obj)
    _remove_line_only_wire(obj)
    if PROP_LINE_ONLY_MATERIALS in obj:
        del obj[PROP_LINE_ONLY_MATERIALS]
    if PROP_LINE_ONLY in obj:
        del obj[PROP_LINE_ONLY]
    return True


@bpy.app.handlers.persistent
def _on_load_post(_dummy):
    ensure_aov_passes()


def register() -> None:
    ensure_aov_passes()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister() -> None:
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
