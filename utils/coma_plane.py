"""コマ平面 Mesh: コマ毎の背景色 + コママスク を 1 枚で兼ねる Mesh Object.

設計趣旨 (2026-05-02 リアーキテクチャ):
- コマ Collection 直下に Mesh Object を 1 枚だけ置く
- 1 枚でビューポート背景色 (``coma.background_color``) と Boolean 用マスク
  (mask_apply 経由) を兼ねる
- 旧 ``__masks__`` Collection の coma mask Mesh を完全置換 (廃止)
- データソース (``coma.rect_*_mm`` / ``coma.vertices``) の update callback
  から ``update_coma_plane_geometry`` を呼べば、 ドラッグ中に property が
  変わるたびに自動同期される (operator 側に同期コールを散らす必要がない)

ページマスクは ``utils/paper_bg_object.py`` の paper_bg Mesh をそのまま
Boolean reference に使う (専用マスク Mesh は持たない)。

Material は per-coma (``BName_ComaPlane_<page>_<coma>``) に分離。 これにより
コマ毎に異なる背景色を持てる。 Solid モードでも ``mat.diffuse_color`` を
同期するので color_type=MATERIAL で色が反映される。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import log
from . import object_naming as on
from . import outliner_model as om
from .geom import mm_to_m

_logger = log.get_logger(__name__)

COMA_PLANE_NAME_PREFIX = "coma_plane_"
COMA_PLANE_MESH_PREFIX = "coma_plane_mesh_"
COMA_PLANE_MATERIAL_PREFIX = "BName_ComaPlane_"

# coma_mask: raster Mesh 用 Boolean Intersect の参照専用 Object.
# coma_plane (背景色表示用、 OPAQUE) とは別 Object として持つ理由:
# - coma_plane に Solidify を入れると raster と同 Z 配置で OPAQUE 白が
#   raster より手前に出て描画を覆い隠してしまう (実機確認済み)
# - coma_plane 自体に Boolean reference を兼ねさせると、 paint preview の
#   shader bypass 経路で Texture Paint mode 中に範囲外がはみ出る
# coma_mask は Solidify 厚み 10m で全 raster Z 範囲をカバーする巨大 volume と
# し、 hide_viewport で viewport 表示は完全に消す (Boolean modifier の
# evaluation のみに使う)。
COMA_MASK_NAME_PREFIX = "coma_mask_"
COMA_MASK_MESH_PREFIX = "coma_mask_mesh_"
COMA_MASK_SOLIDIFY_NAME = "BName Coma Mask Solidify"
# raster の Object.location.z は assign_per_page_z_ranks で page rank * 0.1
# (= 0.1, 0.2, 0.3, ...) になる。 巨大 Z 範囲をカバーするため Solidify は
# 厚み 10m, offset 0 で volume = [Z-5, Z+5] とする。 raster は数 Z 単位なので
# 確実に内部に入る。
COMA_MASK_SOLIDIFY_THICKNESS = 10.0
COMA_MASK_Z_M = 0.0  # 巨大 Solidify 厚みで全 raster Z をカバーするため 0 で OK

PROP_COMA_PLANE_KIND = "bname_coma_plane_kind"  # "coma_plane"
PROP_COMA_PLANE_OWNER_ID = "bname_coma_plane_owner_id"  # "<page_id>:<coma_id>"
PROP_COMA_MASK_KIND = "bname_coma_mask_kind"  # "coma_mask"
PROP_COMA_MASK_OWNER_ID = "bname_coma_mask_owner_id"

# raster Mesh の Object.location.z (= rank * BNAME_Z_STEP_M, 1 段目で 0.01)
# と完全に同一の Z に置く。 描画順は Blender 標準で OPAQUE (coma_plane) →
# BLENDED (raster) になり、 同 Z でも LESS_EQUAL で raster が pass するので
# z-fighting しない。 paper_bg (Z=0) はその下に独立して敷かれる。
# 2026-05-04: BNAME_Z_STEP_M を 0.1 → 0.01 に縮小したのに合わせて 0.1 → 0.01
# に変更。
COMA_PLANE_Z_M = 0.01


# ---------------- Material ----------------


def _coma_plane_material_name(page_id: str, coma_id: str) -> str:
    return f"{COMA_PLANE_MATERIAL_PREFIX}{page_id}_{coma_id}"


def _apply_color_to_material(mat: bpy.types.Material, color_rgba) -> None:
    """Material の Emission Color と diffuse_color を ``color_rgba`` に揃える.

    color_rgba は ``coma.background_color`` (scene-linear, alpha 含む) のまま。
    ``alpha == 0`` でも opaque 扱いとして RGB だけを反映する (mask 用 Mesh
    としては必ず opaque depth を書く必要があるため)。
    """
    try:
        r = float(color_rgba[0])
        g = float(color_rgba[1])
        b = float(color_rgba[2])
    except Exception:  # noqa: BLE001
        r = g = b = 1.0
    rgba = (r, g, b, 1.0)
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (200, 0)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (-100, 0)
    try:
        emission.inputs["Color"].default_value = rgba
        emission.inputs["Strength"].default_value = 1.0
    except Exception:  # noqa: BLE001
        pass
    nt.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    try:
        mat.diffuse_color = rgba
    except Exception:  # noqa: BLE001
        pass


def _ensure_coma_plane_material(page_id: str, coma_id: str, coma) -> bpy.types.Material:
    name = _coma_plane_material_name(page_id, coma_id)
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    try:
        mat.blend_method = "OPAQUE"
        mat.surface_render_method = "DITHERED"  # opaque-equivalent
    except (AttributeError, TypeError):
        pass
    color = (1.0, 1.0, 1.0, 1.0)
    if coma is not None:
        try:
            color = tuple(float(c) for c in coma.background_color[:4])
        except Exception:  # noqa: BLE001
            pass
    _apply_color_to_material(mat, color)
    return mat


# ---------------- Geometry ----------------


def _build_mesh_geometry(mesh: bpy.types.Mesh, coma) -> None:
    shape_type = str(getattr(coma, "shape_type", "rect") or "rect")
    if shape_type == "rect":
        w = max(0.001, float(getattr(coma, "rect_width_mm", 50.0) or 50.0))
        h = max(0.001, float(getattr(coma, "rect_height_mm", 50.0) or 50.0))
        verts = [
            (0.0, 0.0, 0.0),
            (mm_to_m(w), 0.0, 0.0),
            (mm_to_m(w), mm_to_m(h), 0.0),
            (0.0, mm_to_m(h), 0.0),
        ]
        faces = [(0, 1, 2, 3)]
    else:
        vertices = list(getattr(coma, "vertices", []) or [])
        if len(vertices) < 3:
            # 不正な polygon は座標軸付近の小さな三角形を置いておく (depsgraph
            # 安全策。 後で形状が確定したら再生成される)
            verts = [(0.0, 0.0, 0.0), (0.001, 0.0, 0.0), (0.0, 0.001, 0.0)]
            faces = [(0, 1, 2)]
        else:
            verts = [(mm_to_m(float(v.x_mm)), mm_to_m(float(v.y_mm)), 0.0) for v in vertices]
            faces = [tuple(range(len(verts)))]
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)
    mesh.update()


def _resolve_page_index(work, page) -> int:
    pages = getattr(work, "pages", None) or []
    for i, p in enumerate(pages):
        if p is page:
            return i
    target_id = str(getattr(page, "id", "") or "")
    if not target_id:
        return -1
    for i, p in enumerate(pages):
        if str(getattr(p, "id", "") or "") == target_id:
            return i
    return -1


def _set_obj_location(
    obj: bpy.types.Object,
    scene: bpy.types.Scene,
    work,
    page,
    coma,
) -> None:
    page_index = _resolve_page_index(work, page)
    page_ox_mm = 0.0
    page_oy_mm = 0.0
    if page_index >= 0 and scene is not None:
        try:
            from . import page_grid as _pg

            page_ox_mm, page_oy_mm = _pg.page_total_offset_mm(work, scene, page_index)
        except Exception:  # noqa: BLE001
            _logger.exception("coma_plane: page_total_offset_mm failed")
    shape_type = str(getattr(coma, "shape_type", "rect") or "rect")
    if shape_type == "rect":
        local_x_mm = float(getattr(coma, "rect_x_mm", 0.0) or 0.0)
        local_y_mm = float(getattr(coma, "rect_y_mm", 0.0) or 0.0)
    else:
        local_x_mm = 0.0
        local_y_mm = 0.0
    try:
        obj.location.x = mm_to_m(page_ox_mm + local_x_mm)
        obj.location.y = mm_to_m(page_oy_mm + local_y_mm)
        obj.location.z = COMA_PLANE_Z_M
    except Exception:  # noqa: BLE001
        _logger.exception("coma_plane: location set failed")


# ---------------- Lookups ----------------


def find_coma_plane_object(page_id: str, coma_id: str) -> Optional[bpy.types.Object]:
    if not page_id or not coma_id:
        return None
    return bpy.data.objects.get(f"{COMA_PLANE_NAME_PREFIX}{page_id}_{coma_id}")


def find_coma_mask_object(page_id: str, coma_id: str) -> Optional[bpy.types.Object]:
    if not page_id or not coma_id:
        return None
    return bpy.data.objects.get(f"{COMA_MASK_NAME_PREFIX}{page_id}_{coma_id}")


def _ensure_coma_mask_solidify(obj: bpy.types.Object) -> None:
    """coma_mask Object に Solidify modifier を ensure (厚み 10m / offset 0).

    Boolean Intersect FLOAT solver は volume 同士の交差を計算するため、 平面
    (zero volume) では空 mesh を返す。 coma_mask に厚みを持たせて raster の
    Z 範囲を完全包含する volume を与えることで Boolean が正しく評価される。
    """
    sol = obj.modifiers.get(COMA_MASK_SOLIDIFY_NAME)
    if sol is None:
        try:
            sol = obj.modifiers.new(name=COMA_MASK_SOLIDIFY_NAME, type="SOLIDIFY")
        except Exception:  # noqa: BLE001
            _logger.exception("coma_mask: solidify create failed")
            return
    try:
        sol.thickness = COMA_MASK_SOLIDIFY_THICKNESS
        sol.offset = 0.0
        sol.use_even_offset = False
        sol.use_quality_normals = False
    except Exception:  # noqa: BLE001
        _logger.exception("coma_mask: solidify config failed")


def ensure_coma_mask(
    scene: bpy.types.Scene,
    work,
    page,
    coma,
) -> Optional[bpy.types.Object]:
    """coma_mask Object (Boolean reference 専用) を ensure.

    coma_plane と同じ geometry を別 Object に複製し、 Solidify で厚みを
    与えて hide_viewport で表示を消す。 raster の Boolean Intersect は
    この Object を target にする (mask_apply 経由)。
    """
    if scene is None or work is None or page is None or coma is None:
        return None
    page_id = str(getattr(page, "id", "") or "")
    coma_id = str(getattr(coma, "id", "") or "")
    if not page_id or not coma_id:
        return None
    owner_id = f"{page_id}:{coma_id}"
    mesh_name = f"{COMA_MASK_MESH_PREFIX}{page_id}_{coma_id}"
    obj_name = f"{COMA_MASK_NAME_PREFIX}{page_id}_{coma_id}"

    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _build_mesh_geometry(mesh, coma)

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh

    obj[PROP_COMA_MASK_KIND] = "coma_mask"
    obj[PROP_COMA_MASK_OWNER_ID] = owner_id
    obj[on.PROP_MANAGED] = False  # B-Name Outliner mirror 対象外
    obj.hide_viewport = True
    obj.hide_render = True
    obj.hide_select = True

    _ensure_coma_mask_solidify(obj)
    # location は coma_plane と同じ XY を使うが Z は raster Z 範囲全体を包含
    # するために巨大厚み Solidify と組合せて Z=0 ベースとする。 XY だけ
    # _set_obj_location 経由で計算してから Z を上書き。
    _set_obj_location(obj, scene, work, page, coma)
    try:
        obj.location.z = COMA_MASK_Z_M
    except Exception:  # noqa: BLE001
        pass

    # コマ Collection 直下に置く
    coma_title = str(getattr(coma, "title", "") or coma_id)
    coma_coll = on.find_collection_by_bname_id(owner_id, kind="coma")
    if coma_coll is None:
        coma_coll = om.ensure_coma_collection(scene, page_id, coma_id, coma_title)
    if coma_coll is not None and not any(o is obj for o in coma_coll.objects):
        try:
            coma_coll.objects.link(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("link coma_mask to coma collection failed")
    for c in tuple(obj.users_collection):
        if c is coma_coll:
            continue
        try:
            c.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass
    return obj


def update_coma_mask_geometry(
    scene: Optional[bpy.types.Scene],
    work,
    page,
    coma,
) -> bool:
    """coma_mask Mesh / location を更新 (coma 形状変更時に呼ぶ)."""
    if page is None or coma is None:
        return False
    page_id = str(getattr(page, "id", "") or "")
    coma_id = str(getattr(coma, "id", "") or "")
    if not page_id or not coma_id:
        return False
    mesh_name = f"{COMA_MASK_MESH_PREFIX}{page_id}_{coma_id}"
    obj_name = f"{COMA_MASK_NAME_PREFIX}{page_id}_{coma_id}"
    mesh = bpy.data.meshes.get(mesh_name)
    obj = bpy.data.objects.get(obj_name)
    if mesh is None or obj is None:
        return False
    try:
        _build_mesh_geometry(mesh, coma)
    except Exception:  # noqa: BLE001
        _logger.exception("update_coma_mask_geometry: mesh rebuild failed (%s)", mesh_name)
        return False
    if scene is not None and work is not None:
        _set_obj_location(obj, scene, work, page, coma)
        try:
            obj.location.z = COMA_MASK_Z_M
        except Exception:  # noqa: BLE001
            pass
    return True


def remove_coma_mask(page_id: str, coma_id: str) -> bool:
    """指定 (page_id, coma_id) の coma_mask Object/Mesh を削除."""
    if not page_id or not coma_id:
        return False
    obj_name = f"{COMA_MASK_NAME_PREFIX}{page_id}_{coma_id}"
    mesh_name = f"{COMA_MASK_MESH_PREFIX}{page_id}_{coma_id}"
    removed = False
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        mesh_data = obj.data
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed = True
        except Exception:  # noqa: BLE001
            pass
        if mesh_data is not None and mesh_data.users == 0:
            try:
                bpy.data.meshes.remove(mesh_data)
            except Exception:  # noqa: BLE001
                pass
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is not None and mesh.users == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:  # noqa: BLE001
            pass
    return removed


def _find_page_and_coma(work, target_coma):
    """``target_coma`` PropertyGroup の親ページを ``as_pointer()`` で探す.

    Blender の PropertyGroup は属性アクセス毎に新しい Python ラッパが
    返されるため Python ``is`` 比較は不安定。 ``as_pointer()`` (RNA struct
    の C アドレス) は同一データに対して安定なので、 これで identity 一致を
    判定する。 ページ間で同じ ``coma_id`` (cNN) を持ちうるため id 一致
    fallback は採らない。
    """
    if work is None or target_coma is None:
        return None, None
    try:
        target_ptr = int(target_coma.as_pointer())
    except Exception:  # noqa: BLE001
        return None, None
    for page in getattr(work, "pages", []) or []:
        for c in getattr(page, "comas", []) or []:
            try:
                if int(c.as_pointer()) == target_ptr:
                    return page, c
            except Exception:  # noqa: BLE001
                continue
    return None, None


def _find_page_and_coma_for_vertex(work, target_vertex):
    """``BNameComaVertex`` の所属コマ・ページを ``as_pointer()`` で探す."""
    if work is None or target_vertex is None:
        return None, None
    try:
        target_ptr = int(target_vertex.as_pointer())
    except Exception:  # noqa: BLE001
        return None, None
    for page in getattr(work, "pages", []) or []:
        for coma in getattr(page, "comas", []) or []:
            for v in getattr(coma, "vertices", []) or []:
                try:
                    if int(v.as_pointer()) == target_ptr:
                        return page, coma
                except Exception:  # noqa: BLE001
                    continue
    return None, None


# ---------------- Public API ----------------


def ensure_coma_plane(
    scene: bpy.types.Scene,
    work,
    page,
    coma,
) -> Optional[bpy.types.Object]:
    """1 コマ分の coma_plane Mesh Object を ensure (フル生成 / 復旧用)."""
    if scene is None or work is None or page is None or coma is None:
        return None
    page_id = str(getattr(page, "id", "") or "")
    coma_id = str(getattr(coma, "id", "") or "")
    if not page_id or not coma_id:
        return None
    owner_id = f"{page_id}:{coma_id}"
    mesh_name = f"{COMA_PLANE_MESH_PREFIX}{page_id}_{coma_id}"
    obj_name = f"{COMA_PLANE_NAME_PREFIX}{page_id}_{coma_id}"

    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _build_mesh_geometry(mesh, coma)

    mat = _ensure_coma_plane_material(page_id, coma_id, coma)

    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat

    obj[PROP_COMA_PLANE_KIND] = "coma_plane"
    obj[PROP_COMA_PLANE_OWNER_ID] = owner_id
    obj[on.PROP_MANAGED] = False  # B-Name Outliner mirror 対象外
    obj.hide_render = True  # render は別 path
    obj.hide_select = True  # ユーザー誤操作防止
    try:
        obj.display_type = "TEXTURED"
    except Exception:  # noqa: BLE001
        pass

    _set_obj_location(obj, scene, work, page, coma)

    # コマ Collection 直下に置く
    coma_title = str(getattr(coma, "title", "") or coma_id)
    coma_coll = on.find_collection_by_bname_id(owner_id, kind="coma")
    if coma_coll is None:
        coma_coll = om.ensure_coma_collection(scene, page_id, coma_id, coma_title)
    if coma_coll is not None and not any(o is obj for o in coma_coll.objects):
        try:
            coma_coll.objects.link(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("link coma_plane to coma collection failed")
    # 他 Collection から外す (Outliner ヒエラルキ汚染防止)
    for c in tuple(obj.users_collection):
        if c is coma_coll:
            continue
        try:
            c.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass
    return obj


def update_coma_plane_geometry(
    scene: Optional[bpy.types.Scene],
    work,
    page,
    coma,
) -> bool:
    """既存の coma_plane Mesh の geometry / Object location を更新.

    - mesh / object が存在しない場合は False (未生成のまま return)
    - ``__masks__`` 等の副 collection は触らない (副作用ゼロ)
    - ``scene`` / ``work`` が None なら page offset 再計算をスキップし、
      Mesh local geometry のみ更新する
    - 同コマ配下の raster material の shader マスク bbox も同期更新する
      (Boolean Modifier 廃止に伴う代替経路)
    """
    if page is None or coma is None:
        return False
    page_id = str(getattr(page, "id", "") or "")
    coma_id = str(getattr(coma, "id", "") or "")
    if not page_id or not coma_id:
        return False
    mesh_name = f"{COMA_PLANE_MESH_PREFIX}{page_id}_{coma_id}"
    obj_name = f"{COMA_PLANE_NAME_PREFIX}{page_id}_{coma_id}"
    mesh = bpy.data.meshes.get(mesh_name)
    obj = bpy.data.objects.get(obj_name)
    if mesh is None or obj is None:
        return False
    try:
        _build_mesh_geometry(mesh, coma)
    except Exception:  # noqa: BLE001
        _logger.exception("update_coma_plane_geometry: mesh rebuild failed (%s)", mesh_name)
        return False
    if scene is not None and work is not None:
        _set_obj_location(obj, scene, work, page, coma)
    # coma_mask (Boolean reference 専用) の geometry も同期更新
    update_coma_mask_geometry(scene, work, page, coma)
    return True


def update_coma_plane_color(page, coma) -> bool:
    """coma.background_color 変更を coma_plane Material に反映."""
    if page is None or coma is None:
        return False
    page_id = str(getattr(page, "id", "") or "")
    coma_id = str(getattr(coma, "id", "") or "")
    if not page_id or not coma_id:
        return False
    mat = bpy.data.materials.get(_coma_plane_material_name(page_id, coma_id))
    if mat is None:
        return False
    color = (1.0, 1.0, 1.0, 1.0)
    try:
        color = tuple(float(c) for c in coma.background_color[:4])
    except Exception:  # noqa: BLE001
        pass
    _apply_color_to_material(mat, color)
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return True


def update_coma_plane_locations(scene: bpy.types.Scene, work) -> int:
    """全 coma_plane の Object location を page_grid offset に基づき再計算.

    ``apply_page_collection_transforms`` の後で呼ぶ用途。 mesh / material は
    触らない。
    """
    if scene is None or work is None:
        return 0
    n = 0
    for page in getattr(work, "pages", []) or []:
        for coma in getattr(page, "comas", []) or []:
            if not getattr(coma, "id", ""):
                continue
            obj_name = f"{COMA_PLANE_NAME_PREFIX}{page.id}_{coma.id}"
            obj = bpy.data.objects.get(obj_name)
            if obj is None:
                continue
            _set_obj_location(obj, scene, work, page, coma)
            n += 1
    return n


def regenerate_all_coma_planes(scene: bpy.types.Scene, work) -> int:
    """全コマの coma_plane / coma_mask を ensure し、 orphan を掃除.

    coma 形状 / 親階層が大きく変わったあと (work_open / load_post / knife_cut /
    repair) に呼ぶ。 戻り値は ensure 件数。
    """
    if scene is None or work is None:
        return 0
    n = 0
    valid_owner_ids: set[str] = set()
    for page in getattr(work, "pages", []) or []:
        for coma in getattr(page, "comas", []) or []:
            if not getattr(coma, "id", "") or not getattr(page, "id", ""):
                continue
            owner_id = f"{page.id}:{coma.id}"
            if ensure_coma_plane(scene, work, page, coma) is not None:
                n += 1
                valid_owner_ids.add(owner_id)
            # coma_mask (Boolean 用) も並行 ensure
            ensure_coma_mask(scene, work, page, coma)
    # orphan 掃除 (coma_plane)
    for obj in list(bpy.data.objects):
        if obj.get(PROP_COMA_PLANE_KIND) != "coma_plane":
            continue
        owner = str(obj.get(PROP_COMA_PLANE_OWNER_ID, "") or "")
        if owner not in valid_owner_ids:
            mesh_data = obj.data
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:  # noqa: BLE001
                pass
            if mesh_data is not None and mesh_data.users == 0:
                try:
                    bpy.data.meshes.remove(mesh_data)
                except Exception:  # noqa: BLE001
                    pass
    # orphan 掃除 (coma_mask)
    for obj in list(bpy.data.objects):
        if obj.get(PROP_COMA_MASK_KIND) != "coma_mask":
            continue
        owner = str(obj.get(PROP_COMA_MASK_OWNER_ID, "") or "")
        if owner not in valid_owner_ids:
            mesh_data = obj.data
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:  # noqa: BLE001
                pass
            if mesh_data is not None and mesh_data.users == 0:
                try:
                    bpy.data.meshes.remove(mesh_data)
                except Exception:  # noqa: BLE001
                    pass
    # orphan Material 掃除 (名前 prefix で識別)
    for mat in list(bpy.data.materials):
        if not mat.name.startswith(COMA_PLANE_MATERIAL_PREFIX):
            continue
        if mat.users == 0:
            try:
                bpy.data.materials.remove(mat)
            except Exception:  # noqa: BLE001
                pass
    return n


def remove_coma_plane(page_id: str, coma_id: str) -> bool:
    """指定 (page_id, coma_id) の coma_plane Object/Mesh/Material を削除.

    並行して coma_mask Object/Mesh も削除する。
    """
    # coma_mask も同時に削除
    remove_coma_mask(page_id, coma_id)
    try:
        from . import coma_border_object as _cbo

        _cbo.remove_coma_border(page_id, coma_id)
    except Exception:  # noqa: BLE001
        pass
    if not page_id or not coma_id:
        return False
    obj_name = f"{COMA_PLANE_NAME_PREFIX}{page_id}_{coma_id}"
    mesh_name = f"{COMA_PLANE_MESH_PREFIX}{page_id}_{coma_id}"
    mat_name = _coma_plane_material_name(page_id, coma_id)
    removed = False
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        mesh_data = obj.data
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed = True
        except Exception:  # noqa: BLE001
            pass
        if mesh_data is not None and mesh_data.users == 0:
            try:
                bpy.data.meshes.remove(mesh_data)
            except Exception:  # noqa: BLE001
                pass
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is not None and mesh.users == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:  # noqa: BLE001
            pass
    mat = bpy.data.materials.get(mat_name)
    if mat is not None and mat.users == 0:
        try:
            bpy.data.materials.remove(mat)
        except Exception:  # noqa: BLE001
            pass
    return removed


def find_owning_page_and_coma(work, target_coma):
    """update callback 用: ``target_coma`` の親ページを線形探索で返す."""
    return _find_page_and_coma(work, target_coma)


# ---------------- Update callback hooks (called from core/coma.py) ----------------


def on_coma_geometry_changed(coma) -> None:
    """``coma.rect_*_mm`` / ``coma.vertices`` 変更時に呼ぶ.

    work / page を逆引きして ``update_coma_plane_geometry`` を呼ぶ。
    coma_plane Mesh が未生成 (初期状態) のときはスキップ。
    """
    if coma is None:
        return
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if work is None or not getattr(work, "loaded", False):
        return
    page, _ = _find_page_and_coma(work, coma)
    if page is None:
        return
    update_coma_plane_geometry(scene, work, page, coma)
    try:
        from . import coma_border_object as _cbo

        _cbo.update_coma_border_geometry(scene, work, page, coma)
    except Exception:  # noqa: BLE001
        _logger.exception("coma border geometry update failed")


def on_coma_background_color_changed(coma) -> None:
    """``coma.background_color`` 変更時に呼ぶ."""
    if coma is None:
        return
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if work is None or not getattr(work, "loaded", False):
        return
    page, _ = _find_page_and_coma(work, coma)
    if page is None:
        return
    update_coma_plane_color(page, coma)


def on_vertex_changed(vertex) -> None:
    """``BNameComaVertex.x_mm`` / ``y_mm`` 変更時に呼ぶ.

    vertex を持つ coma を ``as_pointer()`` で逆引きして geometry 更新。
    """
    if vertex is None:
        return
    scene = bpy.context.scene if bpy.context is not None else None
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if work is None or not getattr(work, "loaded", False):
        return
    page, coma = _find_page_and_coma_for_vertex(work, vertex)
    if page is None or coma is None:
        return
    update_coma_plane_geometry(scene, work, page, coma)
    try:
        from . import coma_border_object as _cbo

        _cbo.update_coma_border_geometry(scene, work, page, coma)
    except Exception:  # noqa: BLE001
        _logger.exception("coma border vertex update failed")


def register() -> None:
    pass


def unregister() -> None:
    pass
