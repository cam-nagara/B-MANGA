"""Blender check: boundary-tube + Solidify material slot order regression.

背景: 境界辺を持つ開いたメッシュ（板ポリではない、街灯ポールのような
「立体だが開いている」形状）に境界チューブ(BML_SheetOutline)と
Solidify(BML_Outline)を併用すると、シートアウトラインGNツリーの
Join Geometryが素材スロット表を並べ替えてしまい、柱状オブジェクトの
シェル面が非表示素材(BML_SheetRimHidden)に化けて消える不具合があった。
また material_offset_rim を絶対スロット番号として設定していたため、
複数素材オブジェクトではリム面がInner/Intersection等の可視ライン素材に
化ける不具合もあった。

詳細: docs/tokyo0004_boundary_tube_material_order_plan_2026-07-09.md

このテストは実際に tokyo0004 で欠落が確認された「境界辺を持つ開いた
単一素材シリンダー」「境界辺を持つ開いた2素材メッシュ」の2種の
フィクスチャをリポジトリ内で生成し、以下を確認する:

単一素材（tokyo0004の主要欠落クラス・完全保証）:
  (a) 評価後メッシュの素材スロット順 == オブジェクトのスロット順
  (b) 元面由来のシェル面の素材が BML_Outline であること
  (c) リム面・チューブ袖面の素材が BML_SheetRimHidden であること

2素材（既知の制限あり・保証範囲のみアサート）:
  (a') 評価後メッシュの素材表の先頭 n 本が元素材の並びを保つこと
       （Joinの接続順が退行するとチューブ素材が先頭に来るため検出できる）
  (b') 元素材0番の面のシェルが BML_Outline で描かれること
  (c') リム面・チューブ袖面が BML_SheetRimHidden で隠れ、可視ライン素材
       （Inner/Intersection等）へ化けないこと（マゼンタ縁の再発防止）

【既知の制限（2026-07-09 実機で確認）】
複数素材のオブジェクトでは、Blenderのマルチ入力 Join Geometry が
「同一マテリアルポインタの重複スロットを統合して素材表を再構築する」
ため、アウトライン素材をn個並べるパディング帯がGN通過時に潰れる
（例: [s,sb,O,O,H,H] → [s,sb,O,H,H,H]）。その結果、2番目以降の
元素材の面のシェルは非表示素材へ落ち、その部分のアウトラインは
描かれない（修正前の「可視素材へ化けるマゼンタ縁」は解消済み）。
単一素材では重複が発生しないため完全に正しく動作する。
検証プローブ: _verify/2026-07-09_boundary_tube_material_order/
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, outline_setup, plane_filter, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, 0.0, 6.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    return camera


def _select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _open_cylinder_geometry(
    segments: int, radius: float, depth: float,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int, int]]]:
    """境界辺(上下2ループ)を持つ、キャップの無い開いたチューブ形状."""
    top_z = depth / 2.0
    bottom_z = -depth / 2.0
    verts: list[tuple[float, float, float]] = []
    for i in range(segments):
        angle = 2.0 * math.pi * i / segments
        verts.append((radius * math.cos(angle), radius * math.sin(angle), bottom_z))
    for i in range(segments):
        angle = 2.0 * math.pi * i / segments
        verts.append((radius * math.cos(angle), radius * math.sin(angle), top_z))
    faces: list[tuple[int, int, int, int]] = []
    for i in range(segments):
        i_next = (i + 1) % segments
        faces.append((i, i_next, i_next + segments, i + segments))
    return verts, faces


def _add_open_single_material_cylinder(
    name: str, *, segments: int = 12, radius: float = 0.3, depth: float = 1.5,
) -> bpy.types.Object:
    """街灯ポール相当: 境界辺を持つ開いた単一素材の柱状メッシュ."""
    verts, faces = _open_cylinder_geometry(segments, radius, depth)
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    mat = bpy.data.materials.new(f"{name}_surface")
    mat.diffuse_color = (0.8, 0.8, 0.8, 1.0)
    obj.data.materials.append(mat)
    return obj


def _add_open_two_material_cylinder(
    name: str, *, segments: int = 12, radius: float = 0.3, depth: float = 1.5,
) -> bpy.types.Object:
    """バナー板相当: 境界辺を持つ開いた2素材の柱状メッシュ."""
    obj = _add_open_single_material_cylinder(
        name, segments=segments, radius=radius, depth=depth,
    )
    mat_b = bpy.data.materials.new(f"{name}_surface_b")
    mat_b.diffuse_color = (0.2, 0.3, 0.9, 1.0)
    obj.data.materials.append(mat_b)
    mesh = obj.data
    half = len(mesh.polygons) // 2
    assert half > 0, "2素材フィクスチャの面数が足りません"
    for index, poly in enumerate(mesh.polygons):
        poly.material_index = 0 if index < half else 1
    mesh.update()
    return obj


def _apply_outline_only(obj: bpy.types.Object) -> None:
    settings = obj.bmanga_line_settings
    settings.outline_enabled = True
    settings.outline_thickness_mm = 0.5
    settings.inner_line_enabled = False
    settings.intersection_enabled = False
    settings.selection_line_enabled = False
    _select(obj)
    assert presets.apply_line_settings(obj, bpy.context), (
        f"{obj.name}: ラインの適用に失敗しました"
    )


def _evaluated_mesh_materials_and_indices(
    obj: bpy.types.Object,
) -> tuple[list[str], list[int]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    try:
        names = [mat.name if mat else "" for mat in mesh.materials]
        indices = [poly.material_index for poly in mesh.polygons]
        return names, indices
    finally:
        ev.to_mesh_clear()


def _base_name(name: str) -> str:
    """Blenderの重複サフィックス（.001等）を除いた素材名を返す.

    テストを繰り返すと同名素材が BML_Outline.001 のように連番化するため、
    素材の種別判定は基本名で行う。
    """
    head, sep, tail = name.rpartition(".")
    if sep and tail.isdigit():
        return head
    return name


def _is_outline_name(name: str) -> bool:
    return _base_name(name) == outline_setup.MATERIAL_NAME


def _is_hidden_name(name: str) -> bool:
    return _base_name(name) == outline_setup.SHEET_RIM_HIDDEN_MATERIAL_NAME


def _assert_slot_order_preserved(obj: bpy.types.Object) -> None:
    """(a) 評価後メッシュの素材スロット順 == オブジェクトのスロット順."""
    obj_names = [slot.material.name if slot.material else "" for slot in obj.material_slots]
    eval_names, _indices = _evaluated_mesh_materials_and_indices(obj)
    assert eval_names == obj_names, (
        f"{obj.name}: 評価後メッシュの素材スロット順がオブジェクトと一致しません "
        f"(obj={obj_names}, eval={eval_names})"
    )


def _assert_shell_and_rim_materials(obj: bpy.types.Object) -> None:
    """(b) 元面由来のシェル面がBML_Outline、(c) リム/袖面がBML_SheetRimHiddenであること."""
    eval_names, indices = _evaluated_mesh_materials_and_indices(obj)
    outline_count = sum(
        1 for idx in indices
        if 0 <= idx < len(eval_names) and _is_outline_name(eval_names[idx])
    )
    hidden_count = sum(
        1 for idx in indices
        if 0 <= idx < len(eval_names) and _is_hidden_name(eval_names[idx])
    )
    original_face_count = len(obj.data.polygons)
    assert outline_count >= original_face_count, (
        f"{obj.name}: シェル面(BML_Outline)の枚数が不足しています "
        f"(outline_count={outline_count}, original_face_count={original_face_count}). "
        "境界チューブ併用時にシェル面が非表示素材へ化ける不具合の再発の疑い。"
    )
    assert hidden_count > 0, (
        f"{obj.name}: リム面/チューブ袖面(BML_SheetRimHidden)が見つかりません"
    )
    # 非表示帯に属する面はすべて同一の非表示マテリアル名であること
    # （material_offset_rim が絶対番号扱いだと、複数素材オブジェクトの
    # リムが可視ライン素材(Inner等)へ化けるため、ここで検出できる）。
    stray_indices = {
        idx for idx in indices
        if idx >= 0
        and idx < len(eval_names)
        and not _is_outline_name(eval_names[idx])
        and not _is_hidden_name(eval_names[idx])
        and not eval_names[idx].endswith("_surface")
        and not eval_names[idx].endswith("_surface_b")
    }
    assert not stray_indices, (
        f"{obj.name}: 想定外の素材に化けている面があります: "
        f"{[eval_names[i] for i in stray_indices]}"
    )


def _test_single_material_open_cylinder() -> None:
    _clear_scene()
    _make_camera()
    obj = _add_open_single_material_cylinder("BML_bt_single_mat_cyl")
    assert not plane_filter.is_sheet_mesh(obj), "円柱が板ポリ判定されています"
    _apply_outline_only(obj)

    mod = obj.modifiers.get(core.MODIFIER_NAME)
    assert mod is not None, "通常アウトライン(Solidify)が作成されていません"
    tube = obj.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
    assert tube is not None, "境界チューブが作成されていません"

    _assert_slot_order_preserved(obj)
    _assert_shell_and_rim_materials(obj)


def _test_two_material_open_cylinder() -> None:
    _clear_scene()
    _make_camera()
    obj = _add_open_two_material_cylinder("BML_bt_two_mat_cyl")
    assert not plane_filter.is_sheet_mesh(obj), "円柱が板ポリ判定されています"
    assert len(obj.material_slots) == 2, "2素材フィクスチャの初期スロット数が違います"
    original_names = [
        slot.material.name if slot.material else "" for slot in obj.material_slots
    ]
    _apply_outline_only(obj)

    mod = obj.modifiers.get(core.MODIFIER_NAME)
    assert mod is not None, "通常アウトライン(Solidify)が作成されていません"
    tube = obj.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
    assert tube is not None, "境界チューブが作成されていません"

    # 素材スロットが n(元素材) + n(アウトライン) + n(非表示) の3n本になっていること
    material_offset = outline_setup._first_outline_slot(obj)
    assert material_offset == 2, (material_offset, [s.material.name for s in obj.material_slots])
    assert len(obj.material_slots) >= 6, [s.material.name for s in obj.material_slots]
    assert mod.material_offset == 2
    assert mod.material_offset_rim == 4, (
        "material_offset_rim が加算オフセット(2n)になっていません"
    )

    # (a') 評価後の素材表の先頭が元素材の並びを保つこと。
    # マルチ入力Joinの素材統合（既知の制限・モジュールdocstring参照）により
    # 完全一致は保証されないが、接続順が退行するとチューブ素材(BML_Outline)が
    # 先頭へ来るため、先頭 n 本の一致で退行を検出できる。
    eval_names, indices = _evaluated_mesh_materials_and_indices(obj)
    assert eval_names[: len(original_names)] == original_names, (
        f"{obj.name}: 評価後メッシュの素材表の先頭が元素材の並びを保っていません "
        f"(original={original_names}, eval={eval_names}). "
        "Join Geometryの接続順（後接続が先に評価される）の退行の疑い。"
    )

    # (b') 元素材0番の面のシェルがBML_Outlineで描かれること。
    # チューブ面数を境界チューブ単独評価（Solidify無効）で数え、
    # フルスタックとの差分が「元面由来のシェル面」であることを使う。
    def _outline_face_count() -> int:
        names, idxs = _evaluated_mesh_materials_and_indices(obj)
        return sum(
            1 for idx in idxs
            if 0 <= idx < len(names) and _is_outline_name(names[idx])
        )

    full_outline = _outline_face_count()
    mod.show_viewport = False
    tube_only_outline = _outline_face_count()
    mod.show_viewport = True
    mat0_faces = sum(1 for poly in obj.data.polygons if poly.material_index == 0)
    assert mat0_faces > 0
    shell_faces = full_outline - tube_only_outline
    assert shell_faces >= mat0_faces, (
        f"{obj.name}: 元素材0番のシェル面がアウトライン素材で描かれていません "
        f"(shell_faces={shell_faces}, mat0_faces={mat0_faces})"
    )

    # (c') リム面・袖面が非表示素材で隠れ、可視ライン素材へ化けないこと
    hidden_count = sum(
        1 for idx in indices
        if 0 <= idx < len(eval_names) and _is_hidden_name(eval_names[idx])
    )
    assert hidden_count > 0, (
        f"{obj.name}: リム面/チューブ袖面(BML_SheetRimHidden)が見つかりません"
    )
    stray = {
        eval_names[idx] for idx in indices
        if 0 <= idx < len(eval_names)
        and not _is_outline_name(eval_names[idx])
        and not _is_hidden_name(eval_names[idx])
        and eval_names[idx] not in original_names
    }
    assert not stray, (
        f"{obj.name}: リム面が可視ライン素材へ化けています（マゼンタ縁の再発）: {stray}"
    )


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    _test_single_material_open_cylinder()
    _test_two_material_open_cylinder()
    print("[PASS] B-MANGA Line boundary tube + Solidify material slot order")


if __name__ == "__main__":
    main()
