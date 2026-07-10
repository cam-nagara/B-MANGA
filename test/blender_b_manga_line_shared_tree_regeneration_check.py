"""Blender check: shared GN tree regeneration must not orphan other objects' modifiers.

背景: 稜谷線・選択線・交差線のGNツリーは、単一のデータブロック名（例
"BML_InnerLines_Cached"）を全オブジェクトのモディファイアが共有する。
ツリー世代ラベル不一致を検出した際、旧来の実装は
``bpy.data.node_groups.remove(旧ツリー)`` を実行してから新ツリーを構築して
いた。Blenderの既定挙動（do_unlink=True）により、この remove() は旧ツリーを
参照していた**全オブジェクト**のモディファイアの ``node_group`` を強制的に
None化する。一方、ライン更新系のオペレーター/関数は選択中オブジェクトしか
処理しないため、選択していなかった他オブジェクトの線が理由表示なく消える
（2026-07-09 徹底チェックで実機確認・AGENT_INBOX.md参照）。

修正: ``modifier_stack.replace_shared_node_tree`` が、旧ツリー削除前に
参照する全モディファイアを収集し、新ツリー構築後に一括で張り替えてから
旧ツリーを削除するようにした（inner_line_cache.py / inner_lines.py /
intersection_cache.py / intersection_shell.py で使用）。

このテストは以下の再現手順を実機で検証する:
  1. オブジェクトA・Bへ同じ線種を適用し、同一の共有GNツリーを参照させる。
  2. ツリー内の世代判定ラベルを旧世代相当へ書き換え、次回の取得で
     無効判定（_tree_valid 相当）になるようにする。
  3. Aだけを選択して再適用（更新相当）する。
  4. Bのモディファイアの node_group が None化されていないこと、かつ
     Aと同じ新ツリーを参照していることを確認する。

稜谷線（inner_line_cache.py の既定適用経路）と交差線（intersection_cache.py
の既定適用経路）の2ケースを検証する。
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    inner_line_cache,
    intersection_cache,
    outline_setup,
    presets,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
        bpy.data.collections,
    ):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def _make_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, -8.0, 4.0), rotation=(1.1, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 8.0
    bpy.context.scene.camera = camera
    return camera


def _select(*objs: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objs[-1]


def _make_plain_cube(
    name: str, location: tuple[float, float, float], *, size: float = 1.0,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    mat = bpy.data.materials.new(f"{name}_surface")
    mat.diffuse_color = (0.7, 0.7, 0.8, 1.0)
    obj.data.materials.append(mat)
    return obj


def _disable_all_limits(settings) -> None:
    settings.use_outline_creation_limit = False
    settings.use_intersection_creation_limit = False
    settings.use_outline_distance_limit = False
    settings.use_intersection_distance_limit = False
    settings.use_camera_culling = False
    settings.use_camera_compensation = True


def _relabel_node(tree: bpy.types.NodeTree, old_label: str, new_label: str) -> None:
    """ツリー世代判定ラベルを書き換え、次回取得時に旧世代相当と判定させる."""
    node = next(
        (n for n in tree.nodes if getattr(n, "label", "") == old_label),
        None,
    )
    assert node is not None, f"ラベル {old_label!r} を持つノードが見つかりません"
    node.label = new_label


def _count_trees_named(prefix: str) -> int:
    return sum(1 for tree in bpy.data.node_groups if tree.name.startswith(prefix))


# ------------------------------------------------------------------
# 1. 稜谷線（inner_line_cache.py の既定適用経路）
# ------------------------------------------------------------------

def _test_inner_line_shared_tree_regeneration() -> None:
    _clear_scene()
    _make_camera()

    obj_a = _make_plain_cube("BML_str_inner_a", (0.0, 0.0, 0.0))
    obj_b = _make_plain_cube("BML_str_inner_b", (3.0, 0.0, 0.0))
    for obj in (obj_a, obj_b):
        settings = obj.bmanga_line_settings
        settings.outline_enabled = False
        settings.inner_line_enabled = True
        settings.selection_line_enabled = False
        settings.intersection_enabled = False
        _disable_all_limits(settings)
        _select(obj)
        assert presets.apply_line_settings(obj, bpy.context), (
            f"{obj.name}: 稜谷線の適用に失敗しました"
        )

    mod_a = obj_a.modifiers.get(core.GN_MODIFIER_NAME)
    mod_b = obj_b.modifiers.get(core.GN_MODIFIER_NAME)
    assert mod_a is not None and mod_b is not None, "稜谷線モディファイアが作成されていません"
    assert mod_a.node_group is not None and mod_b.node_group is not None
    assert mod_a.node_group == mod_b.node_group, (
        "A・Bが同じ共有GNツリーを参照していません(テスト前提が崩れています)"
    )
    shared_tree = mod_a.node_group
    assert shared_tree.name == inner_line_cache.CACHE_TREE_NAME

    # ツリーの世代判定ラベルを旧世代相当へ改変(実機で確認された「保存済み.blend
    # の旧ツリー」の状態を模する)。
    _relabel_node(
        shared_tree,
        inner_line_cache._SUBDIVIDE_LABEL,
        "BML_InnerCachedSubdivide",  # 平滑化導入前の旧ラベル
    )
    assert not inner_line_cache._tree_valid(shared_tree), (
        "テスト前提が崩れています: ラベル改変後も_tree_validがTrueのままです"
    )

    # Aだけを選択して更新する(Bは選択しない)。
    _select(obj_a)
    assert presets.apply_line_settings(obj_a, bpy.context), (
        "A単独選択での稜谷線再適用に失敗しました"
    )

    # 修正前: mod_b.node_group が None化される(Bの稜谷線が無音で消える)。
    # 修正後: mod_b.node_group は新ツリーを参照したまま維持される。
    mod_b_after = obj_b.modifiers.get(core.GN_MODIFIER_NAME)
    assert mod_b_after is not None, "Bの稜谷線モディファイア自体が失われています"
    assert mod_b_after.node_group is not None, (
        "退行: Bの稜谷線モディファイアのnode_groupがNone化されました"
        "(共有GNツリー再構築で選択外オブジェクトの参照が失われるバグ)"
    )
    assert inner_line_cache._tree_valid(mod_b_after.node_group), (
        "Bの稜谷線モディファイアが新世代ツリーを参照していません"
    )

    mod_a_after = obj_a.modifiers.get(core.GN_MODIFIER_NAME)
    assert mod_a_after is not None and mod_a_after.node_group is not None
    assert mod_a_after.node_group == mod_b_after.node_group, (
        "再構築後もA・Bが同じ共有ツリーを参照している必要があります"
    )
    assert _count_trees_named(inner_line_cache.CACHE_TREE_NAME) == 1, (
        "旧ツリーが削除されずに孤児として残っています"
    )


# ------------------------------------------------------------------
# 2. 板ポリアウトライン
# ------------------------------------------------------------------

def _test_sheet_outline_shared_tree_regeneration() -> None:
    _clear_scene()
    _make_camera()
    planes: list[bpy.types.Object] = []
    for index, x in enumerate((-1.5, 1.5)):
        bpy.ops.mesh.primitive_plane_add(size=1.5, location=(x, 0.0, 0.0))
        obj = bpy.context.object
        obj.name = f"BML_str_sheet_{index}"
        settings = obj.bmanga_line_settings
        settings.outline_enabled = True
        settings.inner_line_enabled = False
        settings.intersection_enabled = False
        _disable_all_limits(settings)
        _select(obj)
        assert presets.apply_line_settings(obj, bpy.context)
        planes.append(obj)

    mod_a = planes[0].modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
    mod_b = planes[1].modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
    assert mod_a is not None and mod_b is not None
    assert mod_a.node_group is not None and mod_a.node_group == mod_b.node_group
    shared_tree = mod_a.node_group
    _relabel_node(
        shared_tree,
        outline_setup._SHEET_TUBE_SUBDIVIDE_LABEL,
        "BML_SheetOutlinePathWidthV20Midpoints",
    )
    assert not outline_setup._sheet_outline_tree_is_current(shared_tree)

    _select(planes[0])
    assert presets.apply_line_settings(planes[0], bpy.context)
    mod_a_after = planes[0].modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
    mod_b_after = planes[1].modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
    assert mod_a_after is not None and mod_b_after is not None
    assert mod_a_after.node_group is not None
    assert mod_b_after.node_group is not None, (
        "退行: 選択外の板ポリアウトライン構成が失われました"
    )
    assert mod_a_after.node_group == mod_b_after.node_group
    assert outline_setup._sheet_outline_tree_is_current(mod_b_after.node_group)
    assert _count_trees_named(outline_setup.SHEET_OUTLINE_TREE_NAME) == 1


# ------------------------------------------------------------------
# 3. 交差線（intersection_cache.py の既定適用経路）
# ------------------------------------------------------------------

def _make_intersection_pair(
    prefix: str, base_location: tuple[float, float, float],
) -> tuple[bpy.types.Object, bpy.types.Object]:
    x, y, z = base_location
    source = _make_plain_cube(f"{prefix}_source", (x, y, z))
    target = _make_plain_cube(f"{prefix}_target", (x + 0.35, y, z))
    for obj in (source, target):
        settings = obj.bmanga_line_settings
        settings.outline_enabled = True
        settings.inner_line_enabled = False
        settings.selection_line_enabled = False
        settings.intersection_enabled = True
        settings.outline_thickness_mm = 0.7
        settings.intersection_thickness_mm = 0.7
        _disable_all_limits(settings)
    return source, target


def _test_intersection_shared_tree_regeneration() -> None:
    _clear_scene()
    _make_camera()

    # 2組のペアをカメラ内で十分離し、ペア所有権が組をまたがないようにする。
    source_a, target_a = _make_intersection_pair("BML_str_isect_a", (0.0, 0.0, 0.0))
    source_b, target_b = _make_intersection_pair("BML_str_isect_b", (4.0, 0.0, 0.0))
    all_objs = (source_a, target_a, source_b, target_b)
    for obj in all_objs:
        assert presets.apply_line_settings(obj, bpy.context, refresh_scene=False), obj.name
    presets._refresh_after_line_settings(bpy.context)
    bpy.context.view_layer.update()

    mod_a = source_a.modifiers.get(core.INTERSECTION_MODIFIER_NAME)
    mod_b = source_b.modifiers.get(core.INTERSECTION_MODIFIER_NAME)
    assert mod_a is not None and mod_b is not None, (
        "交差線の表示モディファイアが作成されていません(テスト前提が崩れています)"
    )
    assert mod_a.node_group is not None and mod_b.node_group is not None
    assert mod_a.node_group == mod_b.node_group, (
        "組A・組Bが同じ共有GNツリーを参照していません(テスト前提が崩れています)"
    )
    shared_tree = mod_a.node_group
    assert shared_tree.name == intersection_cache.CACHE_TREE_NAME

    _relabel_node(
        shared_tree,
        intersection_cache._SUBDIVIDE_LABEL,
        "BML_IntersectionCachedSubdivide",  # V1相当の旧ラベル
    )
    assert not intersection_cache._tree_valid(shared_tree), (
        "テスト前提が崩れています: ラベル改変後も_tree_validがTrueのままです"
    )

    # 組Aだけを選択して更新する(組Bは選択しない)。
    _select(source_a, target_a)
    assert presets.apply_line_settings(
        source_a, bpy.context, refresh_scene=False,
    ), "組A単独選択での交差線再適用に失敗しました"
    presets._refresh_after_line_settings(bpy.context, sources=[source_a])
    bpy.context.view_layer.update()

    mod_b_after = source_b.modifiers.get(core.INTERSECTION_MODIFIER_NAME)
    assert mod_b_after is not None, "組Bの交差線モディファイア自体が失われています"
    assert mod_b_after.node_group is not None, (
        "退行: 組Bの交差線モディファイアのnode_groupがNone化されました"
        "(共有GNツリー再構築で選択外オブジェクトの参照が失われるバグ)"
    )
    assert intersection_cache._tree_valid(mod_b_after.node_group), (
        "組Bの交差線モディファイアが新世代ツリーを参照していません"
    )

    mod_a_after = source_a.modifiers.get(core.INTERSECTION_MODIFIER_NAME)
    assert mod_a_after is not None and mod_a_after.node_group is not None
    assert mod_a_after.node_group == mod_b_after.node_group, (
        "再構築後も組A・組Bが同じ共有ツリーを参照している必要があります"
    )
    assert _count_trees_named(intersection_cache.CACHE_TREE_NAME) == 1, (
        "旧ツリーが削除されずに孤児として残っています"
    )


def main() -> None:
    b_manga_line.register()
    try:
        _clear_scene()
        _test_inner_line_shared_tree_regeneration()
        print(
            "[PASS] shared inner-line GN tree regeneration preserves unselected modifiers"
        )
        _test_sheet_outline_shared_tree_regeneration()
        print(
            "[PASS] shared sheet-outline GN tree regeneration preserves unselected modifiers"
        )
        _test_intersection_shared_tree_regeneration()
        print(
            "[PASS] shared intersection GN tree regeneration preserves unselected modifiers"
        )
        print("ALL TESTS PASSED")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    exit_code = 0
    try:
        main()
    except BaseException:
        traceback.print_exc()
        exit_code = 1
    # ユーザープレファレンス経由で読み込まれる常駐系アドオン(HTTPサーバー・
    # ロガー等のスレッド)がBlenderの通常終了処理を妨げてプロセスが残るため、
    # テスト結果の確定後は出力をフラッシュして即時終了する。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
