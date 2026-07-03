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
    AOV_INNER_LINES_NAME,
    AOV_INTERSECTION_LINES_NAME,
    AOV_NAMES,
    AOV_OBJECT_MASK_NAME,
    AOV_OUTLINE_RAW_NAME,
    COLOR_ATTR_NAME,
    DEFAULT_LINE_WIDTH_REFERENCE_DISTANCE,
    MATERIAL_NAME,
    MODIFIER_NAME,
    PROP_LINE_ONLY,
    PROP_LINE_ONLY_MATERIALS,
    PROP_BASE_THICKNESS,
    PROP_REF_DISTANCE,
    SHEET_OUTLINE_MODIFIER_NAME,
    VG_INNER_LINE_WIDTH,
    VG_INTERSECTION_LINE_WIDTH,
    VG_LINE_WIDTH,
)
from .scale_utils import modifier_thickness_for_world_width


LINE_ONLY_MATERIAL_NAME = "BML_LineOnly_SurfaceHidden"
LINE_ONLY_WIREFRAME_NAME = "BML_LineOnly_Wire"
PROP_HIDE_THROUGH_TRANSPARENT = "bml_hide_through_transparent"
PROP_LINE_MATERIAL_TARGET = "bml_line_material_target"
PROP_DOUBLE_SIDED = "bml_double_sided_line"
PROP_MATERIAL_BUILD = "bml_line_material_build"
PROP_SURFACE_AOV_MASK = "bml_surface_aov_mask"
# ノード構築ロジックを変えたらこの番号を上げる（保存済みファイルの
# 古い・壊れたノードツリーを次回適用時に確実に再構築するため。
# フラグ比較だけだと「フラグは合っているが中身が壊れている」素材を
# 修復できない — 2026-07-03 交差線不可視の実例）
_LINE_MATERIAL_BUILD_VERSION = 3
SHEET_OUTLINE_TREE_NAME = "BML_SheetOutlineTube"
SHEET_RIM_HIDDEN_MATERIAL_NAME = "BML_SheetRimHidden"
_SHEET_TUBE_THICKNESS_SOCKET = "線の太さ"
_SHEET_TUBE_MATERIAL_SOCKET = "マテリアル"
_LINE_MATERIAL_NAMES = {
    "outline": MATERIAL_NAME,
    "inner": f"{MATERIAL_NAME}_Inner",
    "intersection": f"{MATERIAL_NAME}_Intersection",
}
_LINE_COLOR_PROPS = {
    "outline": "outline_color",
    "inner": "inner_line_color",
    "intersection": "intersection_color",
}
_LINE_TARGET_AOVS = {
    "outline": AOV_OUTLINE_RAW_NAME,
    "inner": AOV_INNER_LINES_NAME,
    "intersection": AOV_INTERSECTION_LINES_NAME,
}
_repair_scene_line_materials_timer_running = False


# ------------------------------------------------------------------
# マテリアル
# ------------------------------------------------------------------

def _is_outline_material(mat: bpy.types.Material) -> bool:
    """BML アウトラインマテリアルかどうか."""
    return _line_material_target(mat) == "outline"


def _material_name_matches(mat: bpy.types.Material, base_name: str) -> bool:
    name = mat.name
    return name == base_name or name.startswith(base_name + ".")


def _line_material_target(mat: bpy.types.Material | None) -> str | None:
    if mat is None:
        return None
    try:
        target = mat.get(PROP_LINE_MATERIAL_TARGET, "")
    except TypeError:
        target = ""
    if target in _LINE_MATERIAL_NAMES:
        return str(target)
    for item, base_name in _LINE_MATERIAL_NAMES.items():
        if _material_name_matches(mat, base_name):
            return item
    return None


def _is_line_material(mat: bpy.types.Material | None) -> bool:
    return _line_material_target(mat) is not None


def _set_line_material_target(mat: bpy.types.Material, target: str) -> None:
    try:
        mat[PROP_LINE_MATERIAL_TARGET] = target
    except TypeError:
        pass


def _line_color(obj: bpy.types.Object, target: str) -> tuple[float, ...]:
    settings = getattr(obj, "bmanga_line_settings", None)
    prop_name = _LINE_COLOR_PROPS.get(target, "outline_color")
    fallback = getattr(settings, "outline_color", (0.0, 0.0, 0.0, 1.0))
    return tuple(getattr(settings, prop_name, fallback))


def _line_hide_transparent(obj: bpy.types.Object) -> bool:
    settings = getattr(obj, "bmanga_line_settings", None)
    return bool(getattr(settings, "hide_through_transparent", False))


def _outline_double_sided(obj: bpy.types.Object) -> bool:
    """シートはリムのみのアウトラインになるため両面表示が必要."""
    return plane_filter.is_sheet_mesh(obj)


def _line_material_double_sided(obj: bpy.types.Object, target: str) -> bool:
    if target in {"inner", "intersection"}:
        return True
    return target == "outline" and _outline_double_sided(obj)


def _is_surface_material(mat: bpy.types.Material | None) -> bool:
    return (
        mat is not None
        and not _is_line_material(mat)
        and _line_material_target(mat) is None
        and not _material_name_matches(mat, SHEET_RIM_HIDDEN_MATERIAL_NAME)
    )


def _add_aov_output(
    nodes,
    links,
    aov_name: str,
    color_socket,
    value_socket=None,
    *,
    location: tuple[float, float] = (800, -250),
) -> bpy.types.Node:
    aov = nodes.new("ShaderNodeOutputAOV")
    aov.location = location
    aov.aov_name = aov_name
    links.new(color_socket, aov.inputs["Color"])
    if value_socket is not None:
        links.new(value_socket, aov.inputs["Value"])
    else:
        aov.inputs["Value"].default_value = 1.0
    return aov


def _ensure_surface_mask_aov(mat: bpy.types.Material | None) -> bool:
    if not _is_surface_material(mat):
        return False
    assert mat is not None
    try:
        mat.use_nodes = True
    except RuntimeError:
        return False
    if mat.node_tree is None:
        return False
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    for node in nodes:
        if getattr(node, "aov_name", "") == AOV_OBJECT_MASK_NAME:
            return False

    rgb = nodes.new("ShaderNodeRGB")
    rgb.location = (-240, -420)
    rgb.label = "BML_ObjectMask_Color"
    rgb.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    _add_aov_output(
        nodes,
        links,
        AOV_OBJECT_MASK_NAME,
        rgb.outputs[0],
        None,
        location=(20, -420),
    )
    try:
        mat[PROP_SURFACE_AOV_MASK] = True
    except TypeError:
        pass
    return True


def _ensure_surface_mask_aovs(obj: bpy.types.Object) -> int:
    if obj.type != "MESH" or obj.data is None:
        return 0
    first_line_slot = first_line_material_slot(obj)
    limit = min(first_line_slot, len(obj.data.materials))
    count = 0
    for index in range(limit):
        try:
            mat = obj.data.materials[index]
        except (IndexError, TypeError):
            continue
        if _ensure_surface_mask_aov(mat):
            count += 1
    return count


def _repair_line_material(
    mat: bpy.types.Material,
    color: tuple[float, ...],
    *,
    target: str,
    hide_through_transparent: bool,
    double_sided: bool = False,
) -> bpy.types.Material:
    _repair_outline_material(
        mat,
        color,
        target=target,
        hide_through_transparent=hide_through_transparent,
        double_sided=double_sided,
    )
    _set_line_material_target(mat, target)
    return mat


def _build_outline_nodes(
    mat: bpy.types.Material,
    color: tuple[float, ...],
    *,
    target: str = "outline",
    hide_through_transparent: bool = False,
    double_sided: bool = False,
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

    double_sided（シート用）:
    リムのみアウトラインには隠すべきシェル面が無いので、
    Backfacing 分岐を作らず両面とも Emission にする（手前側の
    フチが背面判定で消えるのを防ぐ）。
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

    geom = nodes.new("ShaderNodeNewGeometry")
    geom.location = (-400, -100)

    lightpath = nodes.new("ShaderNodeLightPath")
    lightpath.location = (-400, 300)

    trans_lp = nodes.new("ShaderNodeBsdfTransparent")
    trans_lp.location = (200, 300)

    if double_sided:
        camera_shader = emission.outputs["Emission"]
    else:
        trans_bf = nodes.new("ShaderNodeBsdfTransparent")
        trans_bf.location = (200, -100)

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
        links.new(camera_shader, mix_td.inputs[1])
        links.new(trans_td.outputs["BSDF"], mix_td.inputs[2])
        camera_shader = mix_td.outputs["Shader"]

    mix_lp = nodes.new("ShaderNodeMixShader")
    mix_lp.location = (600, 0)
    links.new(lightpath.outputs["Is Camera Ray"], mix_lp.inputs[0])
    links.new(trans_lp.outputs["BSDF"], mix_lp.inputs[1])
    links.new(camera_shader, mix_lp.inputs[2])

    links.new(mix_lp.outputs["Shader"], output.inputs["Surface"])

    invert = nodes.new("ShaderNodeMath")
    invert.location = (600, -250)
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    if double_sided:
        invert.inputs[1].default_value = 0.0
    else:
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

    # 互換用のBML_Lineに加え、コンポジット合成用にライン種別別AOVへ出す。
    _add_aov_output(
        nodes,
        links,
        AOV_NAME,
        rgb.outputs[0],
        aov_value,
        location=(800, -250),
    )
    target_aov = _LINE_TARGET_AOVS.get(target)
    if target_aov is not None:
        _add_aov_output(
            nodes,
            links,
            target_aov,
            rgb.outputs[0],
            aov_value,
            location=(800, -430),
        )
    mat[PROP_HIDE_THROUGH_TRANSPARENT] = bool(hide_through_transparent)
    mat[PROP_DOUBLE_SIDED] = bool(double_sided)
    mat[PROP_MATERIAL_BUILD] = _LINE_MATERIAL_BUILD_VERSION


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


def _has_aov_node(mat: bpy.types.Material, target: str = "outline") -> bool:
    if not mat.use_nodes:
        return False
    required = {AOV_NAME}
    target_aov = _LINE_TARGET_AOVS.get(target)
    if target_aov is not None:
        required.add(target_aov)
    found: set[str] = set()
    for node in mat.node_tree.nodes:
        aov_name = getattr(node, "aov_name", "")
        if aov_name in required:
            found.add(aov_name)
    return required.issubset(found)


def _repair_outline_material(
    mat: bpy.types.Material,
    color: tuple[float, ...],
    *,
    target: str = "outline",
    hide_through_transparent: bool,
    double_sided: bool = False,
) -> None:
    current = bool(mat.get(PROP_HIDE_THROUGH_TRANSPARENT, False))
    current_double_sided = bool(mat.get(PROP_DOUBLE_SIDED, False))
    current_build = int(mat.get(PROP_MATERIAL_BUILD, 0) or 0)
    if (
        not _has_aov_node(mat, target)
        or current != hide_through_transparent
        or current_double_sided != double_sided
        or current_build != _LINE_MATERIAL_BUILD_VERSION
    ):
        _build_outline_nodes(
            mat,
            color,
            target=target,
            hide_through_transparent=hide_through_transparent,
            double_sided=double_sided,
        )
    else:
        _update_emission_color(mat, color)
    _configure_material(mat, double_sided=double_sided)


def _configure_material(mat: bpy.types.Material, *, double_sided: bool = False) -> None:
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = not double_sided
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
    double_sided = _outline_double_sided(obj)
    for slot in obj.material_slots:
        if slot.material and _is_outline_material(slot.material):
            mat = slot.material
            _repair_line_material(
                mat,
                color,
                target="outline",
                hide_through_transparent=hide_through_transparent,
                double_sided=double_sided,
            )
            return mat

    mat = bpy.data.materials.new(name=MATERIAL_NAME)
    _build_outline_nodes(
        mat,
        color,
        target="outline",
        hide_through_transparent=hide_through_transparent,
        double_sided=double_sided,
    )
    _set_line_material_target(mat, "outline")
    _configure_material(mat, double_sided=double_sided)
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
            _repair_line_material(
                slot.material,
                color,
                target="outline",
                hide_through_transparent=hide_transparent,
                double_sided=_outline_double_sided(obj),
            )
            return slot.material
    return None


def get_line_material(obj: bpy.types.Object, target: str) -> bpy.types.Material | None:
    """指定ライン種別のマテリアルを取得または作成."""
    if obj.type != "MESH" or obj.data is None:
        return None
    target = target if target in _LINE_MATERIAL_NAMES else "outline"
    color = _line_color(obj, target)
    hide_transparent = _line_hide_transparent(obj)
    if target == "outline":
        return get_or_create_material(
            obj,
            color,
            hide_through_transparent=hide_transparent,
        )
    # 内部線・交差線チューブは生成されたライン実体そのものなので、背面法の
    # 面隠し（背面透明+カリング）は不要。曲線の向きによってチューブの
    # 法線が内向きになると線が消えるため、両面表示で構築する。
    double_sided = _line_material_double_sided(obj, target)
    for slot in obj.material_slots:
        if slot.material and _line_material_target(slot.material) == target:
            return _repair_line_material(
                slot.material,
                color,
                target=target,
                hide_through_transparent=hide_transparent,
                double_sided=double_sided,
            )
    # ライン素材がスロット0（元面の素材位置）を占有しないよう、
    # 先に表面用の素材を確保する（交差線・内部線をアウトラインより
    # 先に有効化した場合の順序バグ対策）
    ensure_surface_material_slot(obj)
    mat = bpy.data.materials.new(name=_LINE_MATERIAL_NAMES[target])
    _build_outline_nodes(
        mat,
        color,
        target=target,
        hide_through_transparent=hide_transparent,
        double_sided=double_sided,
    )
    _set_line_material_target(mat, target)
    _configure_material(mat, double_sided=double_sided)
    obj.data.materials.append(mat)
    return mat


def _has_surface_material(obj: bpy.types.Object) -> bool:
    return any(_is_surface_material(slot.material) for slot in obj.material_slots)


def ensure_surface_material_slot(obj: bpy.types.Object) -> None:
    """元面用の素材スロットを先頭に確保する.

    ライン素材だけがスロットに並ぶと元面がライン素材扱いになり、
    交差線の生成元が空になる・元面がライン色で塗られる等の不具合に
    つながるため、無ければ作り、並びが壊れていれば先頭へ組み直す。
    """
    if obj.type != "MESH" or obj.data is None:
        return
    if _has_surface_material(obj):
        _ensure_surface_mask_aovs(obj)
        return
    line_mats = [slot.material for slot in obj.material_slots if slot.material]
    obj.data.materials.clear()
    surface_mat = bpy.data.materials.new(name=obj.name)
    surface_mat.use_nodes = True
    obj.data.materials.append(surface_mat)
    for mat in line_mats:
        obj.data.materials.append(mat)
    _ensure_surface_mask_aovs(obj)


def first_line_material_slot(obj: bpy.types.Object) -> int:
    """ライン素材が始まるマテリアルスロット番号を返す."""
    if obj.type != "MESH" or obj.data is None:
        return 999
    for index, slot in enumerate(obj.material_slots):
        if _line_material_target(slot.material) is not None:
            return index
    return 999


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
            _repair_line_material(
                slot.material,
                color,
                target="outline",
                hide_through_transparent=hide_transparent,
                double_sided=_outline_double_sided(obj),
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
        target = _line_material_target(slot.material)
        if target is not None:
            double_sided = _line_material_double_sided(obj, target)
            _build_outline_nodes(
                slot.material,
                color if target == "outline" else _line_color(obj, target),
                target=target,
                hide_through_transparent=enabled,
                double_sided=double_sided,
            )
            _set_line_material_target(slot.material, target)
            _configure_material(slot.material, double_sided=double_sided)


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


def _apply_solidify_algorithm_mode(mod: bpy.types.Modifier, is_sheet: bool) -> None:
    """角の背面法ハルが隙間を作らないよう複数面法線を考慮するComplexモードへ切替.

    「Simple」は各頂点を単純な平均法線方向へ押し出すため、角度差の大きい
    面が交差する鋭い角（立方体の角・円柱の縁など）で背面ハルが本体の輪郭まで
    届かず隙間が見えることがある（CEDEC2024のGGトゥーンライン資料で「線が
    浮いて見える問題」として説明されている現象と同一）。「Complex」は面同士の
    接続を解いて閉じたシェルを作るため、この隙間が実測で大幅に軽減される。
    シートは境界チューブ側で輪郭を作り、Solidifyのリム出力は透過素材で
    非表示にしているだけなので対象外（Simpleのまま維持）。
    """
    if not hasattr(mod, "solidify_mode"):
        return
    mod.solidify_mode = "EXTRUDE" if is_sheet else "NON_MANIFOLD"


def _configure_solidify_shape(
    obj: bpy.types.Object,
    mod: bpy.types.Modifier,
    use_rim: bool,
    offset: float = 1.0,
) -> None:
    is_sheet = plane_filter.is_sheet_mesh(obj)
    mod.offset = offset
    if hasattr(mod, "use_rim_only"):
        mod.use_rim_only = is_sheet
    mod.use_rim = True if is_sheet else use_rim
    _apply_solidify_algorithm_mode(mod, is_sheet)


def _configure_line_only_solidify_shape(
    obj: bpy.types.Object,
    use_rim: bool | None = None,
    offset: float | None = None,
) -> None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if obj.type != "MESH" or mod is None:
        return
    settings = getattr(obj, "bmanga_line_settings", None)
    if use_rim is None:
        use_rim = bool(getattr(settings, "use_rim", False))
    if offset is None:
        offset = float(getattr(settings, "outline_offset", 1.0))
    is_sheet = plane_filter.is_sheet_mesh(obj)
    mod.offset = offset
    if hasattr(mod, "use_rim_only"):
        mod.use_rim_only = is_sheet
    mod.use_rim = True if is_sheet else bool(use_rim)
    _apply_solidify_algorithm_mode(mod, is_sheet)


def _restore_solidify_shape(obj: bpy.types.Object) -> None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if obj.type != "MESH" or mod is None:
        return
    settings = getattr(obj, "bmanga_line_settings", None)
    use_rim = bool(getattr(settings, "use_rim", False))
    offset = float(getattr(settings, "outline_offset", 1.0))
    _configure_solidify_shape(obj, mod, use_rim, offset)


def update_modifier_rim(obj: bpy.types.Object, use_rim: bool) -> None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if obj.type == "MESH" and mod is not None:
        settings = getattr(obj, "bmanga_line_settings", None)
        offset = float(getattr(settings, "outline_offset", 1.0))
        if bool(obj.get(PROP_LINE_ONLY, False)):
            _configure_line_only_solidify_shape(obj, use_rim, offset)
        else:
            _configure_solidify_shape(obj, mod, use_rim, offset)


# ------------------------------------------------------------------
# シート（板ポリ）用アウトライン — 境界辺チューブ
# 背面法のリムは面と平行なカメラで消え、片側にしか伸びないため、
# シートでは輪郭辺に沿って全方向へ均等に太らせたチューブを生成する
# （2026-07-03 ユーザー要望「全方向に拡張した立体をライン用オブジェクトに」）。
# ------------------------------------------------------------------

def _find_socket_identifier(tree: bpy.types.NodeTree, name: str) -> str | None:
    for item in tree.interface.items_tree:
        if (
            item.item_type == "SOCKET"
            and item.in_out == "INPUT"
            and item.name == name
        ):
            return item.identifier
    return None


def _get_or_create_sheet_outline_tree() -> bpy.types.NodeTree:
    tree = bpy.data.node_groups.get(SHEET_OUTLINE_TREE_NAME)
    if tree is not None:
        if _find_socket_identifier(tree, _SHEET_TUBE_THICKNESS_SOCKET) is not None:
            return tree
        bpy.data.node_groups.remove(tree)
    tree = bpy.data.node_groups.new(SHEET_OUTLINE_TREE_NAME, "GeometryNodeTree")
    tree.interface.new_socket(
        name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry",
    )
    tree.interface.new_socket(
        name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry",
    )
    width_sock = tree.interface.new_socket(
        name=_SHEET_TUBE_THICKNESS_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketFloat",
    )
    width_sock.default_value = 0.01
    width_sock.min_value = 0.0
    width_sock.max_value = 10.0
    tree.interface.new_socket(
        name=_SHEET_TUBE_MATERIAL_SOCKET,
        in_out="INPUT",
        socket_type="NodeSocketMaterial",
    )

    nodes = tree.nodes
    links = tree.links
    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-800, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (600, 0)

    neighbors = nodes.new("GeometryNodeInputMeshEdgeNeighbors")
    neighbors.location = (-800, -200)
    boundary = nodes.new("FunctionNodeCompare")
    boundary.location = (-600, -200)
    boundary.data_type = "INT"
    boundary.operation = "EQUAL"
    boundary.inputs["B"].default_value = 1
    links.new(neighbors.outputs["Face Count"], boundary.inputs["A"])

    to_curve = nodes.new("GeometryNodeMeshToCurve")
    to_curve.location = (-400, 0)
    links.new(group_in.outputs["Geometry"], to_curve.inputs["Mesh"])
    links.new(boundary.outputs["Result"], to_curve.inputs["Selection"])

    half_width = nodes.new("ShaderNodeMath")
    half_width.location = (-400, -240)
    half_width.operation = "MULTIPLY"
    half_width.inputs[1].default_value = 0.5
    links.new(group_in.outputs[_SHEET_TUBE_THICKNESS_SOCKET], half_width.inputs[0])

    profile = nodes.new("GeometryNodeCurvePrimitiveCircle")
    profile.location = (-200, -200)
    profile.mode = "RADIUS"
    profile.inputs["Resolution"].default_value = 8
    links.new(half_width.outputs[0], profile.inputs["Radius"])

    tube = nodes.new("GeometryNodeCurveToMesh")
    tube.location = (0, 0)
    if "Fill Caps" in tube.inputs:
        tube.inputs["Fill Caps"].default_value = True
    links.new(to_curve.outputs["Curve"], tube.inputs["Curve"])
    links.new(profile.outputs["Curve"], tube.inputs["Profile Curve"])

    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    smooth.location = (150, 0)
    links.new(tube.outputs["Mesh"], smooth.inputs["Geometry"])

    set_mat = nodes.new("GeometryNodeSetMaterial")
    set_mat.location = (300, 0)
    links.new(smooth.outputs["Geometry"], set_mat.inputs["Geometry"])
    links.new(
        group_in.outputs[_SHEET_TUBE_MATERIAL_SOCKET],
        set_mat.inputs["Material"],
    )

    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (450, 0)
    links.new(group_in.outputs["Geometry"], join.inputs["Geometry"])
    links.new(set_mat.outputs["Geometry"], join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], group_out.inputs["Geometry"])
    return tree


def _get_or_create_hidden_rim_material() -> bpy.types.Material:
    """シートのリム面を見えなくするための完全透明マテリアル."""
    mat = bpy.data.materials.get(SHEET_RIM_HIDDEN_MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(SHEET_RIM_HIDDEN_MATERIAL_NAME)
    needs_build = not mat.use_nodes
    if not needs_build:
        needs_build = not any(
            node.type == "BSDF_TRANSPARENT" for node in mat.node_tree.nodes
        )
    if needs_build:
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (200, 0)
        transparent = nodes.new("ShaderNodeBsdfTransparent")
        transparent.location = (0, 0)
        links.new(transparent.outputs["BSDF"], output.inputs["Surface"])
    try:
        mat.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    try:
        mat.shadow_method = "NONE"
    except (AttributeError, TypeError):
        pass
    return mat


def ensure_sheet_outline(
    obj: bpy.types.Object,
    solidify_mod: bpy.types.Modifier,
    line_mat: bpy.types.Material | None,
) -> None:
    """シートなら境界チューブを作り、リムを非表示化する。非シートなら撤去."""
    is_sheet = plane_filter.is_sheet_mesh(obj)
    mod = obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME)
    if not is_sheet:
        if mod is not None:
            obj.modifiers.remove(mod)
        return
    tree = _get_or_create_sheet_outline_tree()
    if mod is None:
        mod = obj.modifiers.new(name=SHEET_OUTLINE_MODIFIER_NAME, type="NODES")
    mod.node_group = tree
    mod.show_viewport = solidify_mod.show_viewport
    mod.show_render = solidify_mod.show_render
    sid_width = _find_socket_identifier(tree, _SHEET_TUBE_THICKNESS_SOCKET)
    if sid_width is not None:
        mod[sid_width] = abs(float(solidify_mod.thickness))
    if line_mat is not None:
        sid_mat = _find_socket_identifier(tree, _SHEET_TUBE_MATERIAL_SOCKET)
        if sid_mat is not None:
            mod[sid_mat] = line_mat

    # 既存リムはチューブと二重になるため非表示マテリアルへ逃がす。
    # 注: material_offset_rim は元面のスロット番号への加算のため、
    # 元面が複数マテリアルのシートでは末尾クランプに頼る。
    hidden = _get_or_create_hidden_rim_material()
    hidden_index = None
    for index, slot in enumerate(obj.material_slots):
        if slot.material is hidden:
            hidden_index = index
            break
    if hidden_index is None:
        obj.data.materials.append(hidden)
        hidden_index = len(obj.material_slots) - 1
    solidify_mod.material_offset_rim = hidden_index

    from . import modifier_stack
    modifier_stack.reorder_line_modifiers(obj)


def sync_sheet_outline_width(obj: bpy.types.Object) -> None:
    """シートチューブの太さを Solidify の線幅設定へ追従させる."""
    if obj.type != "MESH":
        return
    mod = obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME)
    if mod is None or mod.node_group is None:
        return
    solidify = obj.modifiers.get(MODIFIER_NAME)
    if solidify is None:
        return
    sid = _find_socket_identifier(mod.node_group, _SHEET_TUBE_THICKNESS_SOCKET)
    if sid is None:
        return
    value = abs(float(solidify.thickness))
    if abs(float(mod.get(sid, 0.0)) - value) > 1.0e-12:
        mod[sid] = value


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
    offset: float = 1.0,
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

    # 元メッシュ面用のマテリアルがなければ追加・並びが壊れていれば修復
    # （アウトライン専用マテリアルがスロット0に来ると元面もアウトライン化する）
    ensure_surface_material_slot(obj)

    mat = get_or_create_material(
        obj,
        color,
        hide_through_transparent=hide_through_transparent,
    )

    material_offset = _ensure_outline_material_slots(obj, mat)
    _ensure_surface_mask_aovs(obj)

    # Solidify モディファイア
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=MODIFIER_NAME, type="SOLIDIFY")
    mod.thickness = modifier_thickness_for_world_width(obj, thickness)
    mod.use_flip_normals = True
    mod.use_even_offset = even_thickness
    _configure_solidify_shape(obj, mod, use_rim, offset)
    mod.material_offset = material_offset
    mod.material_offset_rim = material_offset

    # シートは境界チューブでアウトラインを作る（リムは非表示化される）
    ensure_sheet_outline(obj, mod, mat)

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
        _configure_line_only_solidify_shape(obj, use_rim, offset)

    return True


def ensure_aov_pass(view_layer, name: str = AOV_NAME, aov_type: str = "COLOR") -> bool:
    """ビューレイヤーに AOV パスを追加. 既存なら何もしない."""
    for aov in view_layer.aovs:
        if aov.name == name:
            return False
    aov = view_layer.aovs.add()
    aov.name = name
    aov.type = aov_type
    return True


def ensure_aov_passes(scene=None) -> int:
    """指定シーン、または全シーンの B-MANGA Line AOV を保証する."""
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
            for name in AOV_NAMES:
                if ensure_aov_pass(view_layer, name):
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

    # シートの境界チューブも一緒に削除
    tube = obj.modifiers.get(SHEET_OUTLINE_MODIFIER_NAME)
    if tube is not None:
        obj.modifiers.remove(tube)
        removed = True

    set_line_only(obj, False)

    # マテリアルスロットを除去（オブジェクト専用マテリアル + シート用リム非表示）
    for i in range(len(obj.data.materials) - 1, -1, -1):
        mat = obj.data.materials[i]
        if mat and (
            _is_line_material(mat)
            or _material_name_matches(mat, SHEET_RIM_HIDDEN_MATERIAL_NAME)
        ):
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


def update_modifier_offset(obj: bpy.types.Object, offset: float) -> None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is not None:
        mod.offset = offset


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


def _set_outline_materials_for_line_only(
    obj: bpy.types.Object,
    mesh: bpy.types.Mesh,
) -> None:
    for mat in mesh.materials:
        target = _line_material_target(mat)
        if target is not None:
            _build_line_only_outline_nodes(mat, _line_color(obj, target))
            _set_line_material_target(mat, target)
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
    if hide_through_transparent_override is None:
        hide_transparent = bool(getattr(settings, "hide_through_transparent", False))
    else:
        hide_transparent = bool(hide_through_transparent_override)
    for mat in mesh.materials:
        target = _line_material_target(mat)
        if target is not None:
            double_sided = _line_material_double_sided(obj, target)
            _build_outline_nodes(
                mat,
                _line_color(obj, target),
                target=target,
                hide_through_transparent=hide_transparent,
                double_sided=double_sided,
            )
            _set_line_material_target(mat, target)
            _configure_material(mat, double_sided=double_sided)


def _ensure_line_only_wire(obj: bpy.types.Object) -> None:
    outline_slot = _first_outline_slot(obj)
    if outline_slot is None:
        return
    wire = obj.modifiers.get(LINE_ONLY_WIREFRAME_NAME)
    if wire is None:
        wire = obj.modifiers.new(name=LINE_ONLY_WIREFRAME_NAME, type="WIREFRAME")
    wire.use_replace = False
    wire.use_even_offset = True
    wire.thickness = modifier_thickness_for_world_width(obj, 0.025)
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
            if mat is not None and (
                _is_line_material(mat)
                or _material_name_matches(mat, SHEET_RIM_HIDDEN_MATERIAL_NAME)
            ):
                # シート用リム非表示マテリアルは白差し替えの対象外
                # （差し替えるとリムが白いヒレとして見えてしまう）
                continue
            stored.append({"index": index, "material": mat.name if mat else ""})
            mesh.materials[index] = hidden
        mod = obj.modifiers.get(MODIFIER_NAME)
        if mod is not None and hasattr(mod, "material_offset_rim"):
            rim_mat = None
            if 0 <= mod.material_offset_rim < len(obj.material_slots):
                rim_mat = obj.material_slots[mod.material_offset_rim].material
            if rim_mat is None or not _material_name_matches(
                rim_mat, SHEET_RIM_HIDDEN_MATERIAL_NAME
            ):
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
    # シートはリムの非表示化とチューブ設定を復元する
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is not None:
        ensure_sheet_outline(obj, mod, get_outline_material(obj))
    if PROP_LINE_ONLY_MATERIALS in obj:
        del obj[PROP_LINE_ONLY_MATERIALS]
    if PROP_LINE_ONLY in obj:
        del obj[PROP_LINE_ONLY]
    return True


@bpy.app.handlers.persistent
def _on_load_post(_dummy):
    ensure_aov_passes()
    _repair_scene_line_materials_now_or_later()


def _scene_data_available() -> bool:
    try:
        getattr(bpy.data, "scenes")
    except AttributeError:
        return False
    return True


def _run_repair_scene_line_materials_timer():
    global _repair_scene_line_materials_timer_running
    if not _scene_data_available():
        return 0.1
    try:
        ensure_aov_passes()
        repair_scene_line_materials()
    finally:
        _repair_scene_line_materials_timer_running = False
    return None


def _queue_repair_scene_line_materials() -> None:
    global _repair_scene_line_materials_timer_running
    if _repair_scene_line_materials_timer_running:
        return
    timers = getattr(bpy.app, "timers", None)
    if timers is None:
        return
    register_timer = getattr(timers, "register", None)
    if register_timer is None:
        return
    _repair_scene_line_materials_timer_running = True
    register_timer(_run_repair_scene_line_materials_timer, first_interval=0.0)


def _repair_scene_line_materials_now_or_later() -> None:
    if _scene_data_available():
        repair_scene_line_materials()
        return
    _queue_repair_scene_line_materials()


def repair_scene_line_materials(scene: bpy.types.Scene | None = None) -> int:
    """既存ファイル内のライン素材を現行ノード構成へ修復する."""
    if scene is not None:
        scenes = [scene]
    else:
        if not _scene_data_available():
            return 0
        scenes = list(bpy.data.scenes)
    seen: set[int] = set()
    repaired = 0
    for item_scene in scenes:
        if item_scene is None:
            continue
        for obj in item_scene.objects:
            if obj.type != "MESH" or obj.data is None:
                continue
            pointer = obj.as_pointer()
            if pointer in seen:
                continue
            seen.add(pointer)
            if not any(_line_material_target(mat) is not None for mat in obj.data.materials):
                continue
            try:
                _restore_outline_materials(obj, obj.data)
                _ensure_surface_mask_aovs(obj)
            except RuntimeError:
                # リンク元データなど、現在のファイル側から書き換えできない素材は
                # ユーザーのライン適用・オーバーライド作成時に改めて修復される。
                continue
            repaired += 1
    return repaired


def register() -> None:
    ensure_aov_passes()
    _repair_scene_line_materials_now_or_later()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister() -> None:
    global _repair_scene_line_materials_timer_running
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    timers = getattr(bpy.app, "timers", None)
    if timers is not None:
        try:
            if timers.is_registered(_run_repair_scene_line_materials_timer):
                timers.unregister(_run_repair_scene_line_materials_timer)
        except (AttributeError, ValueError):
            pass
    _repair_scene_line_materials_timer_running = False
