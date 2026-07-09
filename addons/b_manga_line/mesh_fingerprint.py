"""B-MANGA Line メッシュ編集検出用の指紋（フィンガープリント）ユーティリティ.

「反映」ボタン（bmanga_line.reflect_target / reflect_all）を押したときに、対象メッシュが
直前の重い経路実行（作成・再作成）以降に編集されたかどうかを判定するための軽量な
チェックサムを、線種ごとにオブジェクトのカスタムプロパティへ保存する。

イベントハンドラは追加しない（誤検知・自己発火リスクを避け、ボタン押下時にのみ判定する）。

計画書: docs/bml_reflect_button_reorg_plan_2026-07-09.md §6
"""

from __future__ import annotations

import array
import zlib

import bpy

_PROP_PREFIX = "bml_reflected_fp_"
_MATRIX_ROUND_DIGITS = 4
_INTERSECTION_SCHEMA = "intersection-post-refresh-v2"

# 指紋を保持する線種（バンプ線は常に軽い経路のため対象外 — 計画書§4/§6）。
FINGERPRINT_TARGETS = ("outline", "inner", "intersection", "selection")


def _prop_name(target: str) -> str:
    return f"{_PROP_PREFIX}{target}"


def _is_bml_modifier(mod: bpy.types.Modifier) -> bool:
    """BMLが自動管理するモディファイアか判定する（指紋の対象から除外する）."""
    from . import core, subdivision_lod

    if mod.name in (
        core.MODIFIER_NAME,
        core.SHEET_OUTLINE_MODIFIER_NAME,
        core.OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
        core.GN_MODIFIER_NAME,
        core.SELECTION_LINE_MODIFIER_NAME,
    ):
        return True
    if core.is_intersection_modifier_name(mod.name):
        return True
    return bool(subdivision_lod.is_auto_subsurf_modifier(mod))


def _vertex_checksum(mesh: bpy.types.Mesh) -> int:
    """頂点座標配列のadler32チェックサム（数百万頂点でも十数ms程度）."""
    count = len(mesh.vertices)
    if not count:
        return 0
    coords = array.array("f", [0.0]) * (count * 3)
    mesh.vertices.foreach_get("co", coords)
    return zlib.adler32(coords.tobytes())


def _edge_checksum(mesh: bpy.types.Mesh) -> int:
    """辺の頂点インデックス配列のadler32チェックサム（トポロジ変更検出用）."""
    count = len(mesh.edges)
    if not count:
        return 0
    indices = array.array("i", [0]) * (count * 2)
    mesh.edges.foreach_get("vertices", indices)
    return zlib.adler32(indices.tobytes())


def _non_bml_modifier_signature(obj: bpy.types.Object) -> tuple:
    """非BML管理モディファイアの署名（名前・タイプ・show_render のタプル列）."""
    return tuple(
        (mod.name, mod.type, bool(mod.show_render))
        for mod in obj.modifiers
        if not _is_bml_modifier(mod)
    )


def _rounded_world_matrix(obj: bpy.types.Object) -> tuple:
    """交差線用: matrix_world を丸めて指紋へ含める（他オブジェクトとの位置関係に依存するため）."""
    return tuple(
        round(value, _MATRIX_ROUND_DIGITS)
        for row in obj.matrix_world
        for value in row
    )


def _intersection_creation_in_range(obj: bpy.types.Object, scene) -> bool:
    """交差線の「作成範囲」（カメラに写り指定距離以内）判定."""
    from . import camera_comp

    return bool(
        camera_comp.intersection_line_creation_in_range(
            obj,
            scene,
            getattr(obj, "bmanga_line_settings", None),
        )
    )


def _intersection_has_outline_source(obj: bpy.types.Object) -> bool:
    """交差線検出の前提となるライン形状が作成済みか返す."""
    from . import core

    try:
        return (
            obj.modifiers.get(core.MODIFIER_NAME) is not None
            or obj.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME) is not None
        )
    except ReferenceError:
        return False


def compute(obj: bpy.types.Object, target: str, *, scene=None) -> str:
    """対象オブジェクト×線種の指紋文字列を計算する."""
    mesh = obj.data
    if obj.type != "MESH" or mesh is None:
        return ""

    parts = [
        str(len(mesh.vertices)),
        str(len(mesh.edges)),
        str(len(mesh.polygons)),
        str(_vertex_checksum(mesh)),
        str(_edge_checksum(mesh)),
        repr(_non_bml_modifier_signature(obj)),
    ]
    if target == "intersection":
        # v2 は交差線のシーン検出完了後にだけ保存する世代。旧版は検出前に
        # 保存していたため、実体が0件でも「変更なし」になった記録を無効化する。
        parts.append(_INTERSECTION_SCHEMA)
        parts.append(repr(_rounded_world_matrix(obj)))
        # アウトラインを後から反映した場合も交差線を再検出する。BML管理
        # モディファイアは通常の指紋から除外されるため、この前提だけ明示する。
        parts.append(
            f"outline-source:{int(_intersection_has_outline_source(obj))}"
        )
        if scene is not None:
            # 作成範囲フラグ。「作成する距離」制限で範囲外のまま反映（重い経路→
            # 作成スキップ→指紋保存）した後にカメラが近づいて範囲内へ入っても、
            # 自オブジェクトのメッシュ・matrix_world は不変のため指紋が一致し続け、
            # 「変更なし」扱いで交差線が永久に作られない。カメラ移動による
            # 範囲内外の反転を指紋の不一致として検出できるよう、判定の真偽値を
            # 末尾要素に含める（カメラが動いても反転しなければ従来どおり変更なし）。
            parts.append(f"range:{int(_intersection_creation_in_range(obj, scene))}")
    return "|".join(parts)


def store(obj: bpy.types.Object, target: str, *, scene=None) -> None:
    """§4の重い経路が成功した直後に、その線種の指紋を保存する."""
    if obj is None or obj.type != "MESH" or obj.data is None:
        return
    obj[_prop_name(target)] = compute(obj, target, scene=scene)


def has_stored(obj: bpy.types.Object, target: str) -> bool:
    """指紋が一度でも保存されているか（内容が現状と一致するかは問わない）.

    交差線は片側のオブジェクトだけがモディファイアを持つ非対称な構造のため、
    「モディファイア有無」だけでは判定できない「既に反映済みか」を
    reflect.py が判定する際に使う。
    """
    if obj is None:
        return False
    return _prop_name(target) in obj


def matches(obj: bpy.types.Object, target: str, *, scene=None) -> bool:
    """保存済み指紋と現在のメッシュ状態が一致するか判定する.

    未保存（初回反映前・後方互換の移行前）は「変更あり」= False を返す。
    scene は交差線の作成範囲フラグ判定に使う（store 時と同じ値を渡すこと）。
    """
    if obj is None or obj.type != "MESH" or obj.data is None:
        return False
    prop_name = _prop_name(target)
    if prop_name not in obj:
        return False
    return str(obj[prop_name]) == compute(obj, target, scene=scene)


def clear(obj: bpy.types.Object, target: str | None = None) -> None:
    """指紋プロパティを削除する（線種削除時・remove_all時に使用）."""
    if obj is None:
        return
    targets = (target,) if target is not None else FINGERPRINT_TARGETS
    for item in targets:
        prop_name = _prop_name(item)
        if prop_name in obj:
            del obj[prop_name]
