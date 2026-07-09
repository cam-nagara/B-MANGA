"""B-MANGA Line: 「反映」ボタンのディスパッチ（作成/軽い更新/何もしない/作り直し/削除）.

ボタン再編（docs/bml_reflect_button_reorg_plan_2026-07-09.md §4/§10）で、線種別の
「作成」「更新」ボタンは reflect_target(target=...) の1個へ統合された。反映は
「更新待ち印」（update_state）と「メッシュ指紋」（mesh_fingerprint）を見て、
対象オブジェクト×線種ごとに次のどれかへ自動振り分けする（reflect.py _classify）:

  1. 未適用+有効                       → 重い経路（新規作成）
  2. 適用済み+無効                     → 重い経路（削除）
  3. 作成待ち(create)印あり            → 重い経路（作り直し）
  4. 指紋不一致（メッシュ編集等）       → 重い経路（作り直し）
  5. 更新待ち(visual)印のみ            → 軽い経路（見た目だけ更新）
  6. 印なし                            → 何もしない

本ファイルは計画書§10の新規テスト7ケースをこの順序で検証する。ケース1〜5は
同一オブジェクト・同一線種（稜谷線=inner。GNモディファイアを持つため
「node_group参照が同じ=作り直されていない」を軽い経路の証拠にできる）を
連続して使い回し、ライフサイクル全体（作成→軽い更新→無変化→メッシュ編集での
作り直し→チェックOFFでの削除）を1本の流れとして検証する。ケース6は
「ラインのみを表示」ON中の reflect_all 付帯処理引き継ぎ、ケース7は交差線の
シーン再検出（refresh_scene_intersections）が軽い経路では走らないことを、
それぞれ独立したセットアップで検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    intersection_lines,
    line_only_display,
    mesh_fingerprint,
    presets,
    update_state,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (bpy.data.meshes, bpy.data.materials, bpy.data.node_groups, bpy.data.images):
        for item in list(datablocks):
            if item.users == 0:
                datablocks.remove(item)


def _make_camera(location=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    bpy.ops.object.camera_add(location=location, rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "PERSP"
    camera.data.lens = 50.0
    scene.camera = camera
    return camera


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(bpy.data.materials.new(name + "_surface"))
    return obj


def _select(active: bpy.types.Object, objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


class _CallCounter:
    """モジュール属性を差し替えて呼び出し回数を数える（reflect.py は関数内で
    `from .module import name` / `module.attr` の形で都度参照するため、モジュール
    属性の差し替えは以後の呼び出しへ確実に反映される）."""

    def __init__(self, module, attr_name: str) -> None:
        self._module = module
        self._attr_name = attr_name
        self._original = getattr(module, attr_name)
        self.calls: list[tuple[tuple, dict]] = []

    def __enter__(self) -> "_CallCounter":
        def _wrapped(*args, **kwargs):
            self.calls.append((args, kwargs))
            return self._original(*args, **kwargs)

        setattr(self._module, self._attr_name, _wrapped)
        return self

    def __exit__(self, *exc_info) -> None:
        setattr(self._module, self._attr_name, self._original)

    @property
    def count(self) -> int:
        return len(self.calls)

    def reset(self) -> None:
        self.calls.clear()


# ---------------------------------------------------------------------
# ケース1〜5: 稜谷線(inner)の反映ライフサイクルを1オブジェクトで通す
# ---------------------------------------------------------------------

def _case_1_to_5_inner_line_lifecycle() -> None:
    _clear_scene()
    _make_camera()
    obj = _make_cube("BML_Reflect_Inner_Lifecycle", (0.0, 0.0, -4.0))
    obj.bmanga_line_settings.inner_line_enabled = True
    _select(obj, [obj])

    with _CallCounter(presets, "apply_line_settings") as apply_counter:
        # ケース1: 未適用+有効 → reflect_target で作成される
        assert obj.modifiers.get(core.GN_MODIFIER_NAME) is None
        assert bpy.ops.bmanga_line.reflect_target(
            "EXEC_DEFAULT", target="inner"
        ) == {"FINISHED"}
        assert apply_counter.count == 1, "ケース1: 重い経路(apply_line_settings)が呼ばれていません"
        mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
        assert mod is not None, "ケース1: 稜谷線モディファイアが作成されていません"
        assert mesh_fingerprint.has_stored(obj, "inner"), "ケース1: 指紋が保存されていません"
        assert "inner" not in update_state.pending_targets(obj), "ケース1: 反映待ちが残っています"
        print("[PASS] case1: 未適用+有効 -> reflect_target で作成される")

        # ケース2: 色だけ変更（visual待ち）→ 軽い経路（GNツリー参照が同一のまま）
        node_group_before = mod.node_group
        node_group_name_before = node_group_before.name if node_group_before else None
        fingerprint_before = obj[mesh_fingerprint._prop_name("inner")]

        apply_counter.reset()
        obj.bmanga_line_settings.inner_line_color = (0.9, 0.1, 0.4, 1.0)
        assert "inner" in update_state.pending_visual_targets(obj), (
            "ケース2: 色変更が更新待ち(visual)印を付けていません"
        )
        assert bpy.ops.bmanga_line.reflect_target(
            "EXEC_DEFAULT", target="inner"
        ) == {"FINISHED"}
        assert apply_counter.count == 0, (
            "ケース2: 色だけの変更なのに重い経路(apply_line_settings)が走りました",
            apply_counter.calls,
        )
        mod_after = obj.modifiers.get(core.GN_MODIFIER_NAME)
        assert mod_after is not None
        node_group_after = mod_after.node_group
        assert node_group_after is not None
        assert node_group_after.name == node_group_name_before, (
            "ケース2: GNツリーが再構築されています(軽い経路のはず)",
            node_group_name_before,
            node_group_after.name,
        )
        assert obj[mesh_fingerprint._prop_name("inner")] == fingerprint_before, (
            "ケース2: 指紋が変化しています(重い経路が走った可能性)"
        )
        assert "inner" not in update_state.pending_visual_targets(obj), (
            "ケース2: 反映後も更新待ち(visual)印が残っています"
        )
        print("[PASS] case2: 色だけ変更(visual待ち) -> 軽い経路(GNツリー再構築なし)")

        # ケース3: 待ち無し → 反映で何も起きない
        names_before = sorted(mod.name for mod in obj.modifiers)
        fingerprint_before = obj[mesh_fingerprint._prop_name("inner")]
        assert not update_state.pending_targets(obj), "ケース3の前提: 反映待ちが残っています"

        apply_counter.reset()
        assert bpy.ops.bmanga_line.reflect_target(
            "EXEC_DEFAULT", target="inner"
        ) == {"FINISHED"}
        assert apply_counter.count == 0, (
            "ケース3: 反映待ちが無いのに重い経路が走りました", apply_counter.calls,
        )
        names_after = sorted(mod.name for mod in obj.modifiers)
        assert names_after == names_before, (
            "ケース3: モディファイア構成が変化しています", names_before, names_after,
        )
        assert obj[mesh_fingerprint._prop_name("inner")] == fingerprint_before, (
            "ケース3: 指紋プロパティが変化しています"
        )
        print("[PASS] case3: 待ち無し -> 反映で何も起きない")

        # ケース4: 待ち無し+メッシュ編集 -> 反映で作り直され指紋が更新される
        # (a) 頂点移動(トポロジは同じだが座標チェックサムが変わる)
        fingerprint_before = obj[mesh_fingerprint._prop_name("inner")]
        obj.data.vertices[0].co.x += 0.3
        obj.data.update()
        assert not update_state.pending_targets(obj), (
            "ケース4a前提: メッシュ編集だけでは反映待ち印は付かないはず"
        )

        apply_counter.reset()
        assert bpy.ops.bmanga_line.reflect_target(
            "EXEC_DEFAULT", target="inner"
        ) == {"FINISHED"}
        assert apply_counter.count == 1, (
            "ケース4a: 頂点移動後の指紋不一致で重い経路が走っていません", apply_counter.calls,
        )
        fingerprint_after_vertex_edit = obj[mesh_fingerprint._prop_name("inner")]
        assert fingerprint_after_vertex_edit != fingerprint_before, (
            "ケース4a: 頂点移動後も指紋が更新されていません"
        )
        print("[PASS] case4a: 待ち無し+頂点移動 -> 反映で作り直され指紋が更新される")

        # (b) 非BMLモディファイア追加(トポロジは不変だが非BML署名が変わる)
        obj.modifiers.new("BML_Reflect_ForeignBevel", "BEVEL")
        assert not update_state.pending_targets(obj), (
            "ケース4b前提: モディファイア追加だけでは反映待ち印は付かないはず"
        )

        apply_counter.reset()
        assert bpy.ops.bmanga_line.reflect_target(
            "EXEC_DEFAULT", target="inner"
        ) == {"FINISHED"}
        assert apply_counter.count == 1, (
            "ケース4b: 非BMLモディファイア追加後の指紋不一致で重い経路が走っていません",
            apply_counter.calls,
        )
        assert obj[mesh_fingerprint._prop_name("inner")] != fingerprint_after_vertex_edit, (
            "ケース4b: 非BMLモディファイア追加後も指紋が更新されていません"
        )
        print("[PASS] case4b: 待ち無し+非BMLモディファイア追加 -> 反映で作り直され指紋が更新される")

        # ケース5: チェックOFF+反映 -> モディファイア削除・指紋プロパティ削除
        obj.bmanga_line_settings.inner_line_enabled = False
        apply_counter.reset()
        assert bpy.ops.bmanga_line.reflect_target(
            "EXEC_DEFAULT", target="inner"
        ) == {"FINISHED"}
        assert apply_counter.count == 1, "ケース5: 削除の重い経路が走っていません"
        assert obj.modifiers.get(core.GN_MODIFIER_NAME) is None, (
            "ケース5: 稜谷線モディファイアが削除されていません"
        )
        assert not mesh_fingerprint.has_stored(obj, "inner"), (
            "ケース5: 指紋プロパティが削除されていません"
        )
        print("[PASS] case5: チェックOFF+反映 -> モディファイア削除・指紋プロパティ削除")


# ---------------------------------------------------------------------
# ケース6: 「ラインのみを表示」ON中に未適用オブジェクトへ reflect_all
# -> 新規オブジェクトの素材の出力が白色へ切り替わる(付帯処理の引き継ぎ確認)
# ---------------------------------------------------------------------

def _case_6_line_only_white_output_on_reflect_all() -> None:
    _clear_scene()
    _make_camera()
    scene = bpy.context.scene
    scene.bmanga_line_line_only_visible = True

    obj = _make_cube("BML_Reflect_LineOnly_New", (2.0, 0.0, -4.0))
    mat = obj.data.materials[0]
    original_output = line_only_display.active_material_output(mat)
    assert original_output is not None, "元のアクティブ出力ノードが見つかりません"
    original_output_name = original_output.name

    _select(obj, [obj])
    assert bpy.ops.bmanga_line.reflect_all("EXEC_DEFAULT") == {"FINISHED"}

    active = line_only_display.active_material_output(mat)
    assert active is not None, "反映後にアクティブ出力が見つかりません"
    assert active.name == line_only_display.LINE_ONLY_OUTPUT_NAME, (
        "ラインのみを表示ON中の reflect_all で新規素材が白化されていません(apply付帯処理の"
        "引き継ぎ漏れ)",
        active.name,
    )

    scene.bmanga_line_line_only_visible = False
    restored = line_only_display.active_material_output(mat)
    assert restored is not None and restored.name == original_output_name, (
        "OFF後に元のアクティブ出力へ復元されていません", restored
    )
    print("[PASS] case6: ラインのみを表示ON中の reflect_all で新規素材が白化される")


# ---------------------------------------------------------------------
# ケース7: 交差線のシーン再検出(refresh_scene_intersections)が
# 軽い経路のみの押下では走らないこと
# ---------------------------------------------------------------------

def _case_7_intersection_refresh_only_on_heavy_path() -> None:
    _clear_scene()
    _make_camera()
    a = _make_cube("BML_Reflect_Isect_A", (10.0, 0.0, -4.0))
    b = _make_cube("BML_Reflect_Isect_B", (10.4, 0.0, -4.0))
    for target in (a, b):
        settings = target.bmanga_line_settings
        settings.intersection_enabled = True
        settings.use_intersection_creation_limit = False

    _select(a, [a, b])
    # セットアップの新規作成自体は重い経路なので、ここではカウントしない
    assert bpy.ops.bmanga_line.reflect_all("EXEC_DEFAULT") == {"FINISHED"}
    assert any(core.iter_intersection_modifiers(o) for o in (a, b)), (
        "セットアップ前提: 交差線ペアが作成されていません"
    )

    with _CallCounter(intersection_lines, "refresh_scene_intersections") as refresh_counter:
        # (a) 色だけ変更(visual待ち) -> reflect_target(target="intersection") は
        # 軽い経路のみで済むはずなので、シーン再検出は0回のまま。
        for target in (a, b):
            target.bmanga_line_settings.intersection_color = (0.2, 0.7, 0.3, 1.0)
        assert "intersection" in update_state.pending_visual_targets(a)
        _select(a, [a, b])
        assert bpy.ops.bmanga_line.reflect_target(
            "EXEC_DEFAULT", target="intersection"
        ) == {"FINISHED"}
        assert refresh_counter.count == 0, (
            "ケース7a: 軽い経路のみのはずが refresh_scene_intersections が呼ばれました",
            refresh_counter.calls,
        )
        print("[PASS] case7a: visual待ちのみの reflect_target(intersection) は0回")

        # (b) 待ちが何も無い状態で reflect_all を押しても、重い経路が
        # どの線種でも走らなければ refresh_scene_intersections は0回のまま。
        assert not update_state.pending_targets(a) and not update_state.pending_targets(b), (
            "ケース7b前提: 反映待ちが残っています",
            update_state.pending_targets(a),
            update_state.pending_targets(b),
        )
        _select(a, [a, b])
        assert bpy.ops.bmanga_line.reflect_all("EXEC_DEFAULT") == {"FINISHED"}
        assert refresh_counter.count == 0, (
            "ケース7b: 重い経路が無いはずの reflect_all で refresh_scene_intersections が"
            "呼ばれました",
            refresh_counter.calls,
        )
        print("[PASS] case7b: 待ち無しの reflect_all は0回")


def _case_8_reflect_all_can_defer_initial_intersections() -> None:
    _clear_scene()
    _make_camera()
    a = _make_cube("BML_Reflect_Defer_Isect_A", (0.0, 0.0, -4.0))
    b = _make_cube("BML_Reflect_Defer_Isect_B", (0.4, 0.0, -4.0))
    for target in (a, b):
        settings = target.bmanga_line_settings
        settings.intersection_enabled = True
        settings.use_intersection_creation_limit = False

    _select(a, [a, b])
    with _CallCounter(intersection_lines, "refresh_scene_intersections") as refresh_counter:
        assert bpy.ops.bmanga_line.reflect_all(
            "EXEC_DEFAULT",
            reflect_scope="SKIP_INTERSECTION",
        ) == {"FINISHED"}
        assert refresh_counter.count == 0, (
            "ケース8: 交差線以外の反映で交差線の自動検出が呼ばれました",
            refresh_counter.calls,
        )

    assert a.modifiers.get(core.MODIFIER_NAME) is not None
    assert b.modifiers.get(core.MODIFIER_NAME) is not None
    assert not any(any(core.iter_intersection_modifiers(o)) for o in (a, b)), (
        "ケース8: 交差線以外の反映で交差線が作成されています"
    )
    print("[PASS] case8: 交差線以外の reflect_all は初回交差線を後回しにする")


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _case_1_to_5_inner_line_lifecycle()
        _case_6_line_only_white_output_on_reflect_all()
        _case_7_intersection_refresh_only_on_heavy_path()
        _case_8_reflect_all_can_defer_initial_intersections()
        print("BMANGA_LINE_REFLECT_DISPATCH_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
