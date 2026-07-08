"""B-MANGA Line ポリゴンスープメッシュのライン適用前補正（距離ウェルド＋法線再計算）.

購入ゲームアセット・FBX書き出し資産などは頂点が面ごとに分割されている
（面同士が頂点を共有しない「ポリゴンスープ」）ことが多い。背面法アウトライン
は面の法線方向にしか膨らまないため、非連結の面ではシルエット横方向への
拡張が起きず、細い柱状ジオメトリの線が構造的に欠落し、法線混在面が黒く
塗り潰される。詳細な調査結論は
docs/bml_soup_mesh_line_preprocess_plan_2026-07-09.md を参照。

対策として、ライン適用前に距離ウェルド（remove_doubles）で面同士の頂点を
統合し、法線を再計算する。実装の流儀は subdivision_lod.py の
quadrangulate_mesh_for_auto_subdivision（bmesh処理・共有メッシュコピー・
処理済みマーカー）を踏襲する。
"""

from __future__ import annotations

import bmesh
import bpy


# 一度補正判定（処理・非対象・スキップのいずれか）を行ったオブジェクトへ
# 立てるマーカー。remove_doubles後は境界エッジ比率が下がり自然に「対象外」
# 判定へ変わることが多いが、境界比率チェック自体（全ポリゴン走査）を毎回の
# ライン適用で繰り返さないため、明示的なフラグで再判定をスキップする。
MESH_LINE_REPAIR_PROP = "bml_soup_mesh_line_repaired"
MESH_LINE_REPAIR_WELDED_VERTS_PROP = "bml_soup_mesh_line_repaired_welded_verts"

# 頂点が面ごとに分割された「ポリゴンスープ」資産は、面同士が頂点を共有
# しないため境界エッジ（隣接する面が1枚しかないエッジ）の比率がほぼ1.0に
# なる。通常の連結メッシュはUVシーム・ハードエッジ部分だけが分割される
# ため比率は大幅に低い。0.3はUVシームの多い通常メッシュを誤検出しない
# 安全マージンとして設定（tokyo0004実測: バラバラ面の街灯・消火栓等は
# ほぼ1.0、通常の連結メッシュは大半0.1未満）。
_SOUP_MESH_BOUNDARY_RATIO_THRESHOLD = 0.3

# ウェルドしきい値: ワールド空間で0.1mm相当（Blenderの内部単位はメートル）。
# 部品同士の意図的な隙間（継ぎ目等）までは統合しないよう小さい値に留める。
_WELD_DISTANCE_WORLD_METERS = 0.0001
_MIN_AXIS_SCALE = 1.0e-9


def _object_max_axis_scale(obj: bpy.types.Object) -> float:
    """ワールド距離をローカル距離へ換算するための最大軸スケールを返す.

    scale_utils.object_width_scale は線幅の見た目安定のため中央値を使うが、
    ウェルドは「取りこぼしなく統合できる」ことを優先するため、最も縮小率の
    小さい（＝ワールド距離に対するローカル距離が最大になる）軸を採用する。
    """
    scale = obj.matrix_world.to_scale()
    return max(abs(scale.x), abs(scale.y), abs(scale.z), _MIN_AXIS_SCALE)


def is_soup_mesh(obj: bpy.types.Object) -> bool:
    """境界エッジ比率からバラバラ面（ポリゴンスープ）判定を行う."""
    if obj.type != "MESH" or obj.data is None or not obj.data.polygons:
        return False
    from . import outline_setup

    ratio = outline_setup._mesh_boundary_edge_ratio(obj)
    return ratio > _SOUP_MESH_BOUNDARY_RATIO_THRESHOLD


def repair_soup_mesh_for_lines(obj: bpy.types.Object) -> bool:
    """バラバラ面メッシュをライン適用前に距離ウェルド＋法線再計算で補正する.

    共有メッシュ（users>1）は quadrangulate_mesh_for_auto_subdivision と
    同様に対象オブジェクト専用のコピーを作ってから処理し、他オブジェクト・
    他ファイルへ波及しないようにする。

    一度処理（または非対象と判定・スキップ）したオブジェクトへはマーカーを
    立てて次回以降の再判定・再計算を避ける。

    戻り値: メッシュデータを実際に書き換えた場合 True。
    """
    if obj.type != "MESH" or obj.data is None:
        return False
    mesh = obj.data
    if not mesh.polygons:
        return False
    if obj.get(MESH_LINE_REPAIR_PROP):
        return False
    if not is_soup_mesh(obj):
        return False

    if mesh.has_custom_normals:
        # カスタム分割法線はウェルドで頂点が統合されると失われるおそれが
        # あるため、安全側に倒してスキップする（計画書のリスク節参照）。
        print(
            f"[B-MANGA Liner] {obj.name}: カスタム分割法線があるためメッシュ"
            "補正（ウェルド＋法線再計算）をスキップしました。"
        )
        obj[MESH_LINE_REPAIR_PROP] = True
        return False

    local_distance = _WELD_DISTANCE_WORLD_METERS / _object_max_axis_scale(obj)

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        before_verts = len(bm.verts)

        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=local_distance)
        welded = before_verts - len(bm.verts)
        if welded <= 0:
            # 座標が一致する重複頂点が無い＝ウェルドで直せる構造ではない。
            # 連結情報が変わらないまま法線再計算しても向きは定まらないため
            # 何もせずマーカーだけ立てて次回以降の再走査を避ける。
            obj[MESH_LINE_REPAIR_PROP] = True
            return False

        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

        target_mesh = mesh
        if mesh.users > 1:
            target_mesh = mesh.copy()
            obj.data = target_mesh
        bm.to_mesh(target_mesh)
        target_mesh.update()
    finally:
        bm.free()

    obj[MESH_LINE_REPAIR_PROP] = True
    obj[MESH_LINE_REPAIR_WELDED_VERTS_PROP] = int(welded)
    return True
