"""コマ用 blend のマスクメッシュ + AOV 同期.

コマ用 blend では、 ページ一覧側で決まったコマの形 (多角形) を、 シーン内の
1 枚のメッシュとして持つ。 このメッシュをカメラ前面に置き、 専用の view layer
``コマ枠`` で AOV ``コマ枠拡張`` (1.0 = コマ内、 0.0 = コマ外) を書き出す。
コンポジタやレンダラはこの AOV をマスクとして読む。

旧 c00.blend では同じ名前の view layer / AOV / メッシュ / マテリアルが既に
あり、 マテリアルは ``コマ枠`` という名前で AOV を書き出すよう作られているため、
そのまま再利用する。 メッシュの頂点だけを毎回ロード時に書き換える。

ない場合は最小構成 (透過 BSDF + 値 1.0 を AOV へ流すマテリアル) を新規作成する。
"""

from __future__ import annotations

from typing import Iterable

import bpy

from . import log
from .geom import mm_to_m

_logger = log.get_logger(__name__)


MASK_OBJECT_NAME = "コマ枠"
MASK_MESH_NAME = "コマ枠"
MASK_MATERIAL_NAME = "コマ枠"
MASK_COLLECTION_NAME = "コマ枠"
MASK_VIEW_LAYER_NAME = "コマ枠"
MASK_AOV_NAME = "コマ枠拡張"


def _resolve_coma(work, page_id: str, coma_id: str):
    """page_id / coma_id から PropertyGroup を取得."""
    if work is None or not page_id or not coma_id:
        return None
    for page in getattr(work, "pages", []) or []:
        if str(getattr(page, "id", "") or "") != page_id:
            continue
        for panel in getattr(page, "comas", []) or []:
            if str(getattr(panel, "coma_id", "") or "") == coma_id:
                return panel
    return None


def _coma_polygon_mm(panel) -> list[tuple[float, float]]:
    """コマ多角形を mm 座標で返す (rect / polygon + 角処理 反映)."""
    if panel is None:
        return []
    base = _coma_base_polygon_mm(panel)
    if not base:
        return []
    return _apply_corner_treatment(panel, base)


def _coma_base_polygon_mm(panel) -> list[tuple[float, float]]:
    shape = str(getattr(panel, "shape_type", "rect") or "rect")
    if shape == "rect":
        x = float(getattr(panel, "rect_x_mm", 0.0) or 0.0)
        y = float(getattr(panel, "rect_y_mm", 0.0) or 0.0)
        w = float(getattr(panel, "rect_width_mm", 0.0) or 0.0)
        h = float(getattr(panel, "rect_height_mm", 0.0) or 0.0)
        if w <= 0.0 or h <= 0.0:
            return []
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    verts = getattr(panel, "vertices", None)
    if verts is None or len(verts) < 3:
        return []
    return [(float(v.x_mm), float(v.y_mm)) for v in verts]


def _apply_corner_treatment(panel, base_pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """コマ枠線の角処理 (丸角 / 面取り) をマスク用にも反映する.

    コマ平面メッシュ (``utils/coma_plane.py``) と同じ処理を経由させて、
    マスク AOV と画面表示の形が一致するようにする。
    """
    border = getattr(panel, "border", None)
    if border is None:
        return base_pts
    corner_type = str(getattr(border, "corner_type", "square") or "square")
    radius_mm = float(getattr(border, "corner_radius_mm", 0.0) or 0.0)
    if corner_type == "square" or radius_mm <= 0.0 or len(base_pts) < 3:
        return base_pts
    try:
        from . import border_geom

        styled = border_geom.styled_closed_path_mm(base_pts, corner_type, radius_mm)
        if len(styled) >= 3:
            return styled
    except Exception:  # noqa: BLE001
        _logger.exception("coma mask corner treatment failed")
    return base_pts


def _centered_polygon_meters(points_mm: Iterable[tuple[float, float]]):
    """重心を原点に合わせ、 mm → m 変換して返す."""
    pts = list(points_mm)
    if not pts:
        return []
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return [(mm_to_m(x - cx), mm_to_m(y - cy), 0.0) for x, y in pts]


def _ensure_collection(scene, name: str):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if scene is not None and coll.name not in {c.name for c in scene.collection.children_recursive}:
        try:
            scene.collection.children.link(coll)
        except Exception:  # noqa: BLE001
            pass
    return coll


def _ensure_mask_material():
    """既存マテリアルを優先利用。 無ければ最小 AOV マテリアルを新規作成."""
    mat = bpy.data.materials.get(MASK_MATERIAL_NAME)
    if mat is not None and mat.use_nodes and _material_has_aov_output(mat):
        return mat
    if mat is None:
        mat = bpy.data.materials.new(MASK_MATERIAL_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out_mat = nt.nodes.new("ShaderNodeOutputMaterial")
    out_mat.location = (200, 0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-200, 0)
    nt.links.new(transparent.outputs[0], out_mat.inputs["Surface"])
    value_one = nt.nodes.new("ShaderNodeValue")
    value_one.location = (-200, -150)
    value_one.outputs[0].default_value = 1.0
    out_aov = nt.nodes.new("ShaderNodeOutputAOV")
    out_aov.location = (200, -150)
    out_aov.name = MASK_AOV_NAME
    try:
        out_aov.aov_name = MASK_AOV_NAME  # Blender 5.x property name
    except Exception:  # noqa: BLE001
        pass
    nt.links.new(value_one.outputs[0], out_aov.inputs[0])
    return mat


def _material_has_aov_output(mat) -> bool:
    if not mat or not mat.use_nodes or mat.node_tree is None:
        return False
    for node in mat.node_tree.nodes:
        if node.bl_idname == "ShaderNodeOutputAOV":
            return True
    return False


def _ensure_aov_on_view_layer(scene) -> None:
    if scene is None:
        return
    vl = scene.view_layers.get(MASK_VIEW_LAYER_NAME)
    if vl is None:
        vl = scene.view_layers.new(MASK_VIEW_LAYER_NAME)
    has_aov = any(a.name == MASK_AOV_NAME for a in vl.aovs)
    if not has_aov:
        aov = vl.aovs.add()
        aov.name = MASK_AOV_NAME
        aov.type = "COLOR"


def _ensure_mask_object(scene):
    """マスク用 Object を取得 (無ければ作成)."""
    obj = bpy.data.objects.get(MASK_OBJECT_NAME)
    if obj is None or obj.type != "MESH":
        mesh = bpy.data.meshes.new(MASK_MESH_NAME)
        obj = bpy.data.objects.new(MASK_OBJECT_NAME, mesh)
    elif obj.data is None or obj.data.name != MASK_MESH_NAME:
        # 別名のメッシュデータが付いていても、 同じオブジェクト名なら継続利用
        pass
    coll = _ensure_collection(scene, MASK_COLLECTION_NAME)
    if obj.name not in {o.name for o in coll.objects}:
        try:
            coll.objects.link(obj)
        except RuntimeError:
            # 既に他コレクションに居る場合、 link 失敗しても致命的ではない
            pass
    # マテリアル割当
    mat = _ensure_mask_material()
    if not obj.material_slots:
        obj.data.materials.append(mat)
    else:
        if obj.material_slots[0].material is not mat:
            obj.material_slots[0].material = mat
    return obj


def _update_mesh_geometry(obj, polygon_mm: list[tuple[float, float]]) -> None:
    verts = _centered_polygon_meters(polygon_mm)
    mesh = obj.data
    if mesh is None:
        return
    mesh.clear_geometry()
    if not verts:
        mesh.update()
        return
    faces = [tuple(range(len(verts)))]
    mesh.from_pydata(verts, [], faces)
    mesh.update()


def ensure_coma_mask_mesh(scene, work, page_id: str, coma_id: str) -> bool:
    """現在コマの多角形からマスクメッシュ・AOV をシーンへ反映する.

    呼び出しタイミング: コマ用 blend の ``load_post`` / ``save_pre``。
    戻り値: 何らかの更新を行ったら True。
    """
    if scene is None:
        return False
    panel = _resolve_coma(work, page_id, coma_id)
    polygon = _coma_polygon_mm(panel)
    if not polygon:
        return False
    try:
        _ensure_aov_on_view_layer(scene)
        obj = _ensure_mask_object(scene)
        _update_mesh_geometry(obj, polygon)
        # マスクは原点に固定 (カメラがコマ中央を向く設計)
        try:
            obj.location = (0.0, 0.0, 0.0)
            obj.scale = (1.0, 1.0, 1.0)
            obj.rotation_euler = (0.0, 0.0, 0.0)
        except Exception:  # noqa: BLE001
            pass
        # コマ枠ビューレイヤーは AOV 出力専用なので、 ビューポートでは非表示。
        try:
            obj.hide_viewport = True
            obj.hide_render = False
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("ensure_coma_mask_mesh failed")
        return False


def remove_coma_mask_mesh(scene) -> None:
    """マスクメッシュを片付ける (テスト用)."""
    obj = bpy.data.objects.get(MASK_OBJECT_NAME)
    if obj is None:
        return
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        _logger.exception("remove_coma_mask_mesh failed")
