"""フキダシ塗り面メッシュを Shapely + earcut で生成する.

フキダシの本体と全しっぽの輪郭を Shapely Polygon の和集合として
1 つの閉じた形状へ統合し、mapbox_earcut で三角分割して Mesh を作る。

これまで本体カーブに付いた Geometry Nodes の Fill Curve ノードが担っていた
塗り面の生成を、Python 側 (本体カーブと無関係の別オブジェクト) で焼き込む
ことで、ジオメトリノードの評価コストを丸ごと無くす。

塗り輪郭ぼかしのアルファ属性 (`bname_fill_blur_alpha`) は、各メッシュ頂点
からフキダシ輪郭までの距離フィールドとして頂点属性に書き込み、マテリアル
側 (`_mat_fill_blur_alpha_socket`) でアルファに乗算される。
"""

from __future__ import annotations

from typing import Optional, Sequence

import bpy

from . import balloon_line_mesh
from . import balloon_tail_geom
from . import log
from . import object_naming as on
from . import python_deps
from .balloon_render_contract import FILL_Z_M
from .balloon_curve_render_nodes import FILL_BLUR_ALPHA_ATTRIBUTE
from .geom import Rect, mm_to_m

_logger = log.get_logger(__name__)

BALLOON_FILL_MESH_NAME_PREFIX = "balloon_fill_mesh_"
PROP_BALLOON_FILL_MESH_KIND = "bname_balloon_fill_mesh_kind"
PROP_BALLOON_FILL_MESH_OWNER_ID = "bname_balloon_fill_mesh_owner_id"
_KIND_FILL = "balloon_fill_mesh"

# 塗り輪郭ぼかしの幅は、線幅と blur_amount (0..1) から決まる。
# ノード側 `_fill_blur_width_socket` と同じ式: blur_mm = max(0.15, line_width_mm * (0.65 + 3.35 * blur))。
_FILL_BLUR_BASE = 0.65
_FILL_BLUR_SCALE = 3.35
_FILL_BLUR_MIN_MM = 0.15


def _line_width_mm(entry) -> float:
    style = str(getattr(entry, "line_style", "") or "")
    if style == "none":
        return 0.0
    return max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))


def _entry_local_offset_mm(entry) -> tuple[float, float]:
    """body bezier と同じ rect→balloon-local 平行移動量 (mm)."""
    return (
        float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0)
        - max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)) * 0.5,
        float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0)
        - max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)) * 0.5,
    )


def _fill_mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_FILL_MESH_NAME_PREFIX}{balloon_id}"


def _fill_mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_FILL_MESH_NAME_PREFIX}{balloon_id}"


def remove_balloon_fill_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    obj_name = _fill_mesh_object_name(balloon_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
    mesh_name = _fill_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is not None and mesh.users == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:  # noqa: BLE001
            pass


def cleanup_orphan_fill_meshes(valid_balloon_ids: set[str]) -> int:
    """有効な balloon_id のセットに含まれない fill mesh オブジェクトを削除し件数を返す."""
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.get(PROP_BALLOON_FILL_MESH_KIND) != _KIND_FILL:
            continue
        owner_id = str(obj.get(PROP_BALLOON_FILL_MESH_OWNER_ID, "") or "")
        if owner_id and owner_id in valid_balloon_ids:
            continue
        try:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            removed += 1
        except Exception:  # noqa: BLE001
            pass
    return removed


def _resolve_body_spline(body_object: bpy.types.Object | None):
    """フキダシ本体カーブの中で、最初の閉じた Bezier spline (= 本体) を返す."""
    if body_object is None or getattr(body_object, "type", "") != "CURVE":
        return None
    curve = getattr(body_object, "data", None)
    if curve is None:
        return None
    for spline in list(getattr(curve, "splines", []) or []):
        if str(getattr(spline, "type", "") or "") != "BEZIER":
            continue
        if not bool(getattr(spline, "use_cyclic_u", False)):
            continue
        return spline
    return None


def _sample_body_polygon_local_m(body_spline) -> list[tuple[float, float]]:
    """本体 bezier をサンプリングし、balloon-local (m) の輪郭点列を返す."""
    raw = balloon_line_mesh._sample_body_bezier(body_spline, balloon_line_mesh.SAMPLES_PER_SEGMENT)
    return [(float(x), float(y)) for (x, y, _r) in raw]


def _tail_polygon_local_m(entry, tail) -> list[tuple[float, float]]:
    """しっぽの mm 座標 (rect ローカル) を balloon-local m 座標へ変換した点列."""
    rect = Rect(
        0.0,
        0.0,
        max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    pts_mm = balloon_tail_geom.polygon_for_tail(rect, tail)
    ox, oy = _entry_local_offset_mm(entry)
    return [(mm_to_m(x + ox), mm_to_m(y + oy)) for x, y in pts_mm]


def _build_union_polygon(body_pts: Sequence[tuple[float, float]], tails_pts: Sequence[Sequence[tuple[float, float]]]):
    """本体 + 全しっぽの和集合 Shapely Polygon (または MultiPolygon) を返す。失敗時 None。

    しっぽが無い (= body のみ) ときは body polygon をそのまま返す。 余計な buffer 操作で
    sharp corner に微小頂点が増え、 earcut が大きな triangle を生成してしまう不具合を
    回避するため。

    しっぽがある場合だけ、 各 polygon を微小に mitre buffer して確実に重ねた上で union
    し、 同量だけ shrink して戻す方式で 1 つの polygon に統合する (round join だと sharp
    corner に大量の頂点を追加してしまうため、 join_style=2 を使う)。
    """
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    def _validate(pts):
        p = Polygon(pts)
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty or p.area <= 0:
            return None
        return p

    body_poly = _validate(body_pts) if len(body_pts) >= 3 else None
    tail_polys = []
    for tail_pts in tails_pts:
        if len(tail_pts) < 3:
            continue
        try:
            tp = _validate(tail_pts)
        except Exception:  # noqa: BLE001
            tp = None
        if tp is not None:
            tail_polys.append(tp)

    if body_poly is None and not tail_polys:
        return None
    if not tail_polys:
        # body のみ: そのまま返す (buffer 不要)
        return body_poly
    if body_poly is None:
        # body 無し (=異常系) : tail のみで union
        polys_to_union = tail_polys
        return unary_union(polys_to_union)

    # body + tails: 微小 mitre buffer で重ね、 union 後に shrink して戻す
    # mitre join (join_style=2) は sharp corner に頂点を追加しないため、 earcut の
    # 安定性を維持できる。 mitre_limit は十分大きく取って fallback round を防ぐ。
    overlap_m = 1.0e-6
    try:
        polys_buffered = [body_poly.buffer(overlap_m, join_style=2, mitre_limit=50.0)]
        for tp in tail_polys:
            polys_buffered.append(tp.buffer(overlap_m, join_style=2, mitre_limit=50.0))
        merged = unary_union(polys_buffered)
        if merged.is_empty:
            return None
        # buffer で広げた分を戻す (同じく mitre)
        merged = merged.buffer(-overlap_m, join_style=2, mitre_limit=50.0)
        if merged.is_empty:
            return None
        if merged.geom_type == "MultiPolygon":
            # それでも MultiPolygon (= しっぽが body から離れて完全に disjoint な場合) は
            # 最大面積の polygon を採用する (実用上は body 本体)。
            polygons = list(merged.geoms)
            polygons.sort(key=lambda p: p.area, reverse=True)
            return polygons[0]
        return merged
    except Exception:  # noqa: BLE001
        return body_poly  # fall back to body only


def _polygon_to_outer_holes(poly) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]:
    """Shapely Polygon を (outer_ring, holes) の対に展開する."""
    outer = [(float(x), float(y)) for x, y in poly.exterior.coords]
    holes = []
    for inner in poly.interiors:
        holes.append([(float(x), float(y)) for x, y in inner.coords])
    return outer, holes


def _compute_fill_blur_alpha(
    poly,
    verts_2d: Sequence[tuple[float, float]],
    *,
    blur_amount: float,
    line_width_mm: float,
) -> list[float]:
    """各頂点の塗り輪郭ぼかしアルファ値を計算する.

    ノード側 `_store_fill_blur_alpha` と同じ式:
      blur_width_m = max(0.15, line_width_mm * (0.65 + 3.35 * blur_amount)) * 0.001
      alpha = clamp(distance_to_boundary_m / blur_width_m, 0, 1)
    blur_amount <= 0 の場合は全頂点 1.0 を返す (= 完全不透明)。
    """
    blur = max(0.0, min(1.0, float(blur_amount or 0.0)))
    if blur <= 1.0e-4 or poly is None:
        return [1.0] * len(verts_2d)
    try:
        from shapely.geometry import Point  # type: ignore
    except Exception:  # noqa: BLE001
        return [1.0] * len(verts_2d)
    width_mm = max(_FILL_BLUR_MIN_MM, float(line_width_mm or 0.0) * (_FILL_BLUR_BASE + _FILL_BLUR_SCALE * blur))
    width_m = width_mm * 0.001
    if width_m <= 1.0e-9:
        return [1.0] * len(verts_2d)
    boundary = poly.boundary
    out: list[float] = []
    for x, y in verts_2d:
        try:
            d = boundary.distance(Point(float(x), float(y)))
        except Exception:  # noqa: BLE001
            d = 0.0
        alpha = max(0.0, min(1.0, d / width_m))
        out.append(alpha)
    return out


def _write_fill_blur_alpha_attribute(mesh: bpy.types.Mesh, alpha: Sequence[float]) -> None:
    """頂点属性 bname_fill_blur_alpha を POINT domain Float として書き込む."""
    name = FILL_BLUR_ALPHA_ATTRIBUTE
    # 既存属性を消して作り直す (型と長さを確実に揃える)
    try:
        existing = mesh.attributes.get(name)
        if existing is not None:
            mesh.attributes.remove(existing)
    except Exception:  # noqa: BLE001
        pass
    try:
        attr = mesh.attributes.new(name=name, type="FLOAT", domain="POINT")
    except Exception:  # noqa: BLE001
        return
    n = min(len(attr.data), len(alpha))
    for i in range(n):
        try:
            attr.data[i].value = float(alpha[i])
        except Exception:  # noqa: BLE001
            pass


def _shrink_polygon_rings(poly, *, blur_width_m: float) -> list:
    """blur 効果のために、 外周から内側に向けて段階的に縮小したリング群を返す.

    earcut は boundary 頂点のみしか出力しないため、 内部の距離フィールド
    (= boundary からの距離) が得られない。 各リングを「境界線」として与えると、
    earcut の出力に内側リング頂点が含まれ、 そこに正しい alpha 値を計算できる。

    返り値: [poly_0, poly_1, ...] (poly_0 = 元の poly。 リング数は blur_width に応じる)
    """
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
    except Exception:  # noqa: BLE001
        return [poly]
    if blur_width_m <= 1.0e-9:
        return [poly]
    # 4 段階に shrink: blur_width の 25%, 50%, 75%, 100% (内側 100% = 完全不透明)
    fractions = [0.25, 0.5, 0.75, 1.0]
    rings = [poly]
    for frac in fractions:
        try:
            shrunk = poly.buffer(-blur_width_m * frac)
            if shrunk.is_empty:
                break
            if shrunk.geom_type == "MultiPolygon":
                geoms = sorted(shrunk.geoms, key=lambda p: p.area, reverse=True)
                shrunk = geoms[0] if geoms else None
            if shrunk is None or shrunk.is_empty or shrunk.area <= 0:
                break
            rings.append(shrunk)
        except Exception:  # noqa: BLE001
            break
    return rings


def _fan_triangulate(
    outer_ring: Sequence[tuple[float, float]],
    holes: Sequence[Sequence[tuple[float, float]]] = (),
) -> tuple[list[tuple[float, float]], list[tuple[int, int, int]]]:
    """中心点ファン分割: ポリゴンの重心を内部頂点として追加し、
    外周の各 edge について (中心, v[i], v[i+1]) の三角形を生成する.

    ear clipping (earcut/bmesh) はトゲ本体のような subtle bumps のある
    near-circular polygon で 1 つの巨大な三角形を生成してしまう病的ケースが
    あるが、 ファン分割はその問題を回避する。 holes は無視する (フキダシ塗りは
    holes 不要のため)。

    純凸でない polygon でも、 重心が内部にあれば全 fan triangle が内側にある
    ことを保証 — ただし極端な凹形状では fan triangle がはみ出す可能性あり。
    トゲ/雲のような subtle 凸+凹形状では問題なし。
    """
    outer = [(float(x), float(y)) for x, y in outer_ring]
    if len(outer) >= 2 and outer[0] == outer[-1]:
        outer = outer[:-1]
    n = len(outer)
    if n < 3:
        return [], []
    cx = sum(p[0] for p in outer) / n
    cy = sum(p[1] for p in outer) / n
    pts = list(outer)
    pts.append((cx, cy))
    center_idx = n
    tris = []
    for i in range(n):
        j = (i + 1) % n
        tris.append((center_idx, i, j))
    return pts, tris


def _bmesh_triangulate(
    outer_ring: Sequence[tuple[float, float]],
    holes: Sequence[Sequence[tuple[float, float]]],
) -> tuple[list[tuple[float, float]], list[tuple[int, int, int]]]:
    """ホールあり多角形を bmesh で三角分割する (ホールが必要な場合の retry path)."""
    import bmesh  # type: ignore

    bm = bmesh.new()
    try:
        outer = [(float(x), float(y)) for x, y in outer_ring]
        if len(outer) >= 2 and outer[0] == outer[-1]:
            outer = outer[:-1]
        if len(outer) < 3:
            return [], []
        outer_verts = [bm.verts.new((p[0], p[1], 0.0)) for p in outer]
        outer_edges = [bm.edges.new((outer_verts[i], outer_verts[(i + 1) % len(outer_verts)]))
                       for i in range(len(outer_verts))]
        hole_verts_list = []
        hole_edges_list = []
        for hole in holes:
            hpts = [(float(x), float(y)) for x, y in hole]
            if len(hpts) >= 2 and hpts[0] == hpts[-1]:
                hpts = hpts[:-1]
            if len(hpts) < 3:
                continue
            hverts = [bm.verts.new((p[0], p[1], 0.0)) for p in hpts]
            hedges = [bm.edges.new((hverts[i], hverts[(i + 1) % len(hverts)]))
                      for i in range(len(hverts))]
            hole_verts_list.append(hverts)
            hole_edges_list.append(hedges)

        bmesh.ops.contextual_create(bm, geom=outer_edges)
        if hole_edges_list:
            for hedges in hole_edges_list:
                bmesh.ops.contextual_create(bm, geom=hedges)
            faces_to_remove = []
            for face in bm.faces:
                fv = set(face.verts)
                for hverts in hole_verts_list:
                    if fv == set(hverts):
                        faces_to_remove.append(face)
                        break
            if faces_to_remove:
                bmesh.ops.delete(bm, geom=faces_to_remove, context="FACES_ONLY")
        bmesh.ops.triangulate(bm, faces=list(bm.faces), ngon_method="BEAUTY", quad_method="BEAUTY")
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        pts = [(float(v.co.x), float(v.co.y)) for v in bm.verts]
        vert_idx = {v: i for i, v in enumerate(bm.verts)}
        tris = []
        for f in bm.faces:
            if len(f.verts) != 3:
                continue
            tris.append((vert_idx[f.verts[0]], vert_idx[f.verts[1]], vert_idx[f.verts[2]]))
        return pts, tris
    finally:
        bm.free()


def _build_fill_mesh(
    mesh: bpy.types.Mesh,
    outer_ring: Sequence[tuple[float, float]],
    holes: Sequence[Sequence[tuple[float, float]]],
    z_m: float,
    *,
    blur_alpha: Sequence[float] | None,
) -> None:
    """単一 polygon (hole 込み) を三角分割して mesh に流し込む.

    earcut/bmesh の ear-clipping は near-circular な polygon (トゲ本体など) で
    1 つの巨大な三角形を生成してしまう病的ケースがあるため、 ファン分割
    (中心点ベース) を優先する。 ホール (= 多重線で削った穴) がある場合のみ
    bmesh を使う。

    blur_alpha が None でない場合、各頂点に bname_fill_blur_alpha 属性を書き込む。
    """
    if holes:
        pts, faces = _bmesh_triangulate(outer_ring, holes)
    else:
        pts, faces = _fan_triangulate(outer_ring, holes)
    mesh.clear_geometry()
    if not faces or len(pts) < 3:
        mesh.update()
        return
    verts = [(float(x), float(y), float(z_m)) for x, y in pts]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    if blur_alpha is None:
        return
    _write_fill_blur_alpha_attribute(mesh, blur_alpha)


def _build_fill_mesh_with_blur_rings(
    mesh: bpy.types.Mesh,
    union_poly,
    z_m: float,
    *,
    blur_width_m: float,
) -> tuple[list[tuple[float, float]], list[float]]:
    """blur 用の同心リング群を含む塗り面メッシュを構築する.

    各リングを別個に earcut で三角分割し、 全リングの頂点とフェースを連結する。
    内側のリングほど boundary から遠いので、 alpha 値が高くなる。

    返り値: (verts_2d, blur_alpha_per_vertex)
    """
    rings = _shrink_polygon_rings(union_poly, blur_width_m=blur_width_m)
    all_verts: list[tuple[float, float]] = []
    all_faces: list[tuple[int, int, int]] = []
    all_alpha: list[float] = []
    # 外側リングのみで earcut し、 内側リングを hole として与えると、 リング間の
    # 帯のみ三角分割される。 さらに内側リングを単独で三角分割すると、 内部 fill が
    # 得られる。 リング 0 = 元の外周。 alpha は: 外側=0, 各リング上=対応 alpha, 最内=1。
    n_rings = len(rings)
    for i, ring_poly in enumerate(rings):
        outer, holes = _polygon_to_outer_holes(ring_poly)
        # このリングを単独で earcut すると、 全頂点はリング境界上にある。
        pts_i, faces_i = balloon_line_mesh._triangulate_polygon(outer, holes)
        if not faces_i or len(pts_i) < 3:
            continue
        # alpha = i / (n_rings - 1) (外側 = 0, 最内 = 1)
        # ただし n_rings=1 なら 1.0 固定 (blur 不可)
        if n_rings <= 1:
            ring_alpha = 1.0
        else:
            ring_alpha = float(i) / float(n_rings - 1)
        offset = len(all_verts)
        all_verts.extend(pts_i)
        all_alpha.extend([ring_alpha] * len(pts_i))
        for a, b, c in faces_i:
            all_faces.append((a + offset, b + offset, c + offset))
    if not all_verts:
        return [], []
    mesh.clear_geometry()
    verts_3d = [(float(x), float(y), float(z_m)) for x, y in all_verts]
    mesh.from_pydata(verts_3d, [], all_faces)
    mesh.update()
    _write_fill_blur_alpha_attribute(mesh, all_alpha)
    return all_verts, all_alpha


def _attach_fill_mesh_object(
    *,
    obj_name: str,
    mesh: bpy.types.Mesh,
    material: bpy.types.Material,
    body_object: bpy.types.Object,
    scene,
    balloon_id: str,
    visible: bool,
) -> bpy.types.Object:
    """塗り面メッシュをフキダシ本体に親付けする (band mesh と同じパターン)."""
    obj = bpy.data.objects.get(obj_name)
    if obj is not None and getattr(obj, "type", "") != "MESH":
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh

    if material is not None:
        if not mesh.materials:
            mesh.materials.append(material)
        elif mesh.materials[0] is not material:
            mesh.materials[0] = material

    obj[PROP_BALLOON_FILL_MESH_KIND] = _KIND_FILL
    obj[PROP_BALLOON_FILL_MESH_OWNER_ID] = balloon_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True

    target_collections = list(getattr(body_object, "users_collection", []) or [])
    if not target_collections:
        target_collections = [scene.collection] if scene is not None else []
    current_collections = set(getattr(obj, "users_collection", []) or [])
    for coll in target_collections:
        if coll not in current_collections:
            try:
                coll.objects.link(obj)
            except Exception:  # noqa: BLE001
                pass
    for coll in list(current_collections):
        if coll not in target_collections:
            try:
                coll.objects.unlink(obj)
            except Exception:  # noqa: BLE001
                pass

    if obj.parent is not body_object:
        obj.parent = body_object
        obj.matrix_parent_inverse.identity()
    obj.location = (0.0, 0.0, 0.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.scale = (1.0, 1.0, 1.0)

    obj.hide_viewport = not visible
    obj.hide_render = not visible
    return obj


def ensure_balloon_fill_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    fill_material: bpy.types.Material,
) -> Optional[bpy.types.Object]:
    """フキダシ塗り面のメッシュオブジェクトを生成・更新する.

    対象形状でない (= 本体カーブが無い "本体なし") 場合や、本体 spline が見つからない
    場合は既存のメッシュを撤去する。
    """
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None

    body_spline = _resolve_body_spline(body_object)
    if body_spline is None:
        remove_balloon_fill_mesh(balloon_id)
        return None

    body_pts = _sample_body_polygon_local_m(body_spline)
    if len(body_pts) < 3:
        remove_balloon_fill_mesh(balloon_id)
        return None
    tails_pts = [
        _tail_polygon_local_m(entry, tail)
        for tail in (getattr(entry, "tails", []) or [])
    ]
    union_poly = _build_union_polygon(body_pts, tails_pts)
    if union_poly is None or union_poly.is_empty:
        remove_balloon_fill_mesh(balloon_id)
        return None

    outer_ring, holes = _polygon_to_outer_holes(union_poly)
    if len(outer_ring) < 3:
        remove_balloon_fill_mesh(balloon_id)
        return None

    mesh_name = _fill_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)

    blur_amount = float(getattr(entry, "fill_blur_amount", 0.0) or 0.0)
    blur_amount = max(0.0, min(1.0, blur_amount))
    if blur_amount > 1.0e-4:
        # blur 有効: 同心リングを生成して内部頂点に距離フィールドを焼き込む
        width_mm = max(
            _FILL_BLUR_MIN_MM,
            _line_width_mm(entry) * (_FILL_BLUR_BASE + _FILL_BLUR_SCALE * blur_amount),
        )
        width_m = width_mm * 0.001
        _build_fill_mesh_with_blur_rings(mesh, union_poly, FILL_Z_M, blur_width_m=width_m)
    else:
        # blur 無効: シンプルな earcut のみ + 全頂点 alpha=1.0
        _build_fill_mesh(mesh, outer_ring, holes, FILL_Z_M, blur_alpha=None)

    return _attach_fill_mesh_object(
        obj_name=_fill_mesh_object_name(balloon_id),
        mesh=mesh,
        material=fill_material,
        body_object=body_object,
        scene=scene,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
    )
