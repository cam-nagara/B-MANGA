"""B-MANGA Line — Solidify モディファイアとマテリアルのセットアップ.

背面法（Inverted Hull Method）の実装ロジック。
- Solidify モディファイアで法線反転したシェルを生成
- 専用マテリアルで背面カリング → 輪郭線として描画
- 頂点グループで線幅をパーツごとに制御
"""

from __future__ import annotations

import json

import bpy

from .core import (
    AOV_NAME,
    COLOR_ATTR_NAME,
    MATERIAL_NAME,
    MODIFIER_NAME,
    PROP_LINE_ONLY,
    PROP_LINE_ONLY_MATERIALS,
    PROP_BASE_THICKNESS,
    PROP_REF_DISTANCE,
    VG_LINE_WIDTH,
)


LINE_ONLY_MATERIAL_NAME = "BML_LineOnly_SurfaceHidden"


# ------------------------------------------------------------------
# マテリアル
# ------------------------------------------------------------------

def _is_outline_material(mat: bpy.types.Material) -> bool:
    """BML アウトラインマテリアルかどうか."""
    name = mat.name
    return name == MATERIAL_NAME or name.startswith(MATERIAL_NAME + ".")


def _build_outline_nodes(mat: bpy.types.Material, color: tuple[float, ...]) -> None:
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

    mix_lp = nodes.new("ShaderNodeMixShader")
    mix_lp.location = (600, 0)
    links.new(lightpath.outputs["Is Camera Ray"], mix_lp.inputs[0])
    links.new(trans_lp.outputs["BSDF"], mix_lp.inputs[1])
    links.new(mix_bf.outputs["Shader"], mix_lp.inputs[2])

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
    links.new(invert.outputs[0], aov.inputs["Value"])


def _has_aov_node(mat: bpy.types.Material) -> bool:
    if not mat.use_nodes:
        return False
    for node in mat.node_tree.nodes:
        if hasattr(node, "aov_name") and node.aov_name == AOV_NAME:
            return True
    return False


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
) -> bpy.types.Material:
    """オブジェクト専用のアウトラインマテリアルを取得または作成."""
    for slot in obj.material_slots:
        if slot.material and _is_outline_material(slot.material):
            mat = slot.material
            if not _has_aov_node(mat):
                _build_outline_nodes(mat, color)
            else:
                _update_emission_color(mat, color)
            _configure_material(mat)
            return mat

    mat = bpy.data.materials.new(name=MATERIAL_NAME)
    _build_outline_nodes(mat, color)
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
    for slot in obj.material_slots:
        if slot.material and _is_outline_material(slot.material):
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
    for slot in obj.material_slots:
        if slot.material and _is_outline_material(slot.material):
            _update_emission_color(slot.material, color)
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

    mat = get_or_create_material(obj, color)

    material_offset = _ensure_outline_material_slots(obj, mat)

    # Solidify モディファイア
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(name=MODIFIER_NAME, type="SOLIDIFY")
    mod.thickness = abs(thickness)
    mod.offset = 1.0
    mod.use_flip_normals = True
    mod.use_even_offset = even_thickness
    mod.use_rim = use_rim
    mod.material_offset = material_offset

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

    # カメラ距離補正の基準値を保存
    if scene is not None and scene.camera is not None:
        dist = (scene.camera.matrix_world.translation
                - obj.matrix_world.translation).length
        obj[PROP_BASE_THICKNESS] = abs(thickness)
        obj[PROP_REF_DISTANCE] = max(dist, 0.001)

    if scene is not None:
        ensure_aov_passes(scene)

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
    scenes = [scene] if scene is not None else list(bpy.data.scenes)
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
        mod.thickness = abs(thickness)


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
    mat.diffuse_color = (1.0, 1.0, 1.0, 0.0)
    try:
        mat.blend_method = "BLEND"
    except (AttributeError, TypeError):
        pass
    try:
        mat.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    links.new(transparent.outputs["BSDF"], output.inputs["Surface"])
    return mat


def set_line_only(obj: bpy.types.Object, enabled: bool) -> bool:
    """元の面素材を一時的に透明化し、ラインだけを見える状態にする."""
    if obj.type != "MESH":
        return False
    mesh = obj.data
    if enabled:
        if bool(obj.get(PROP_LINE_ONLY, False)):
            return True
        stored = []
        hidden = _get_line_only_material()
        for index, mat in enumerate(mesh.materials):
            if mat is not None and _is_outline_material(mat):
                continue
            stored.append({"index": index, "material": mat.name if mat else ""})
            mesh.materials[index] = hidden
        obj[PROP_LINE_ONLY_MATERIALS] = json.dumps(stored, ensure_ascii=False)
        obj[PROP_LINE_ONLY] = True
        return bool(stored)

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
