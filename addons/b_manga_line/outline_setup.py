"""B-MANGA Line — Solidify モディファイアとマテリアルのセットアップ.

背面法（Inverted Hull Method）の実装ロジック。
- Solidify モディファイアで法線反転したシェルを生成
- 専用マテリアルで背面カリング → 輪郭線として描画
- 頂点グループで線幅をパーツごとに制御
"""

from __future__ import annotations

import bpy

from .core import (
    AOV_NAME,
    COLOR_ATTR_NAME,
    MATERIAL_NAME,
    MODIFIER_NAME,
    PROP_BASE_THICKNESS,
    PROP_REF_DISTANCE,
    VG_LINE_WIDTH,
)


# ------------------------------------------------------------------
# マテリアル
# ------------------------------------------------------------------

def _build_outline_nodes(mat: bpy.types.Material, color: tuple[float, ...]) -> None:
    """マテリアルノードツリーを構築（EEVEE + Cycles 両対応 + AOV 出力）."""
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)

    # RGB ノードで色を一元管理（Emission と AOV で共有）
    rgb = nodes.new("ShaderNodeRGB")
    rgb.location = (-400, 100)
    rgb.outputs[0].default_value = (color[0], color[1], color[2], 1.0)
    rgb.label = "BML_Color"

    emission = nodes.new("ShaderNodeEmission")
    emission.location = (0, 100)
    links.new(rgb.outputs[0], emission.inputs["Color"])
    emission.inputs["Strength"].default_value = 1.0

    transparent = nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (0, -100)

    geom = nodes.new("ShaderNodeNewGeometry")
    geom.location = (-200, 0)

    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (200, 0)

    # Backfacing=0 (front face of flipped shell = outline visible) → Emission
    # Backfacing=1 (back face of flipped shell = hidden) → Transparent
    links.new(geom.outputs["Backfacing"], mix.inputs["Fac"])
    links.new(emission.outputs["Emission"], mix.inputs[1])
    links.new(transparent.outputs["BSDF"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])

    # --- AOV 出力（コンポジットで線画を分離可能） ---
    aov = nodes.new("ShaderNodeOutputAOV")
    aov.location = (400, -250)
    aov.aov_name = AOV_NAME

    # Color = ラインの色
    links.new(rgb.outputs[0], aov.inputs["Color"])

    # Value = 可視マスク (1 = 線が見える面, 0 = 裏面で非表示)
    invert = nodes.new("ShaderNodeMath")
    invert.location = (200, -250)
    invert.operation = "SUBTRACT"
    invert.inputs[0].default_value = 1.0
    links.new(geom.outputs["Backfacing"], invert.inputs[1])
    links.new(invert.outputs[0], aov.inputs["Value"])


def _has_aov_node(mat: bpy.types.Material) -> bool:
    if not mat.use_nodes:
        return False
    for node in mat.node_tree.nodes:
        if hasattr(node, "aov_name") and node.aov_name == AOV_NAME:
            return True
    return False


def get_or_create_material(
    color: tuple[float, ...] = (0.0, 0.0, 0.0, 1.0),
) -> bpy.types.Material:
    """アウトライン用共有マテリアルを取得または作成."""
    mat = bpy.data.materials.get(MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(name=MATERIAL_NAME)
        _build_outline_nodes(mat, color)
    elif not _has_aov_node(mat):
        _build_outline_nodes(mat, color)
    else:
        _update_emission_color(mat, color)

    # EEVEE での背面カリング（Cycles はシェーダーで処理）
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = True

    # 影を落とさない
    try:
        mat.shadow_method = "NONE"
    except (AttributeError, TypeError):
        pass

    return mat


def _update_emission_color(mat: bpy.types.Material, color: tuple[float, ...]) -> None:
    if not mat.use_nodes:
        return
    # 新形式: RGB ノードで色を管理
    for node in mat.node_tree.nodes:
        if node.type == "RGB" and node.label == "BML_Color":
            node.outputs[0].default_value = (color[0], color[1], color[2], 1.0)
            return
    # 旧形式フォールバック: Emission ノードに直接設定
    for node in mat.node_tree.nodes:
        if node.type == "EMISSION" and node.label == "BML_Color":
            node.inputs["Color"].default_value = (color[0], color[1], color[2], 1.0)
            return


def update_material_color(color: tuple[float, ...]) -> None:
    """共有マテリアルの色を更新."""
    mat = bpy.data.materials.get(MATERIAL_NAME)
    if mat is not None:
        _update_emission_color(mat, color)


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


# ------------------------------------------------------------------
# 適用 / 削除
# ------------------------------------------------------------------

def apply_outline(
    obj: bpy.types.Object,
    thickness: float = 0.002,
    color: tuple[float, ...] = (0.0, 0.0, 0.0, 1.0),
    use_vertex_color: bool = False,
    even_thickness: bool = True,
    *,
    use_vertex_group: bool = False,
    scene=None,
) -> bool:
    """オブジェクトに背面法アウトラインを適用. 成功時 True.

    use_vertex_group: AO やエッジ角度など、頂点カラー以外でも頂点グループが必要な場合 True.
    scene: カメラ距離補正の基準値保存に使用.
    """
    if obj.type != "MESH" or obj.data is None:
        return False
    if not obj.data.polygons:
        return False

    mat = get_or_create_material(color)

    # マテリアルスロット — 既にあれば再利用
    num_mats_before = len(obj.data.materials)
    existing_slot = None
    for i, slot in enumerate(obj.material_slots):
        if slot.material == mat:
            existing_slot = i
            break
    if existing_slot is None:
        obj.data.materials.append(mat)

    # material_offset: 全フェイスが outline マテリアルを使うように
    material_offset = num_mats_before if existing_slot is None else existing_slot

    # Solidify モディファイア — 既存を更新 or 新規作成
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=MODIFIER_NAME, type="SOLIDIFY")
    mod.thickness = -abs(thickness)
    mod.offset = -1.0
    mod.use_flip_normals = True
    mod.use_even_offset = even_thickness
    mod.material_offset = material_offset

    # 頂点グループによる線幅制御
    need_vg = use_vertex_color or use_vertex_group
    if need_vg:
        vg = _ensure_vertex_group(obj)
        if use_vertex_color:
            _ensure_color_attribute(obj)
        mod.vertex_group = vg.name
        mod.thickness_vertex_group = 1.0
    else:
        mod.vertex_group = ""

    # カメラ距離補正の基準値を保存
    if scene is not None and scene.camera is not None:
        dist = (scene.camera.matrix_world.translation
                - obj.matrix_world.translation).length
        obj[PROP_BASE_THICKNESS] = abs(thickness)
        obj[PROP_REF_DISTANCE] = max(dist, 0.001)

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


def remove_outline(obj: bpy.types.Object) -> bool:
    """オブジェクトからアウトラインを削除. 削除した場合 True."""
    if obj.type != "MESH":
        return False

    removed = False

    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is not None:
        obj.modifiers.remove(mod)
        removed = True

    # マテリアルスロットを除去
    mat = bpy.data.materials.get(MATERIAL_NAME)
    if mat is not None:
        for i in range(len(obj.data.materials) - 1, -1, -1):
            if obj.data.materials[i] == mat:
                obj.data.materials.pop(index=i)
                removed = True
                break

    # 頂点グループ
    vg = obj.vertex_groups.get(VG_LINE_WIDTH)
    if vg is not None:
        obj.vertex_groups.remove(vg)

    return removed


# ------------------------------------------------------------------
# パラメータ更新
# ------------------------------------------------------------------

def update_modifier_thickness(obj: bpy.types.Object, thickness: float) -> None:
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is not None:
        mod.thickness = -abs(thickness)


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
