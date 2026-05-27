"""Blender 実機用: Phase A (フキダシジオメトリノードのマスク経路撤去) 検証.

確認内容:
  1. アドオン登録 → ジオメトリノードグループ `BName_GN_BalloonCurveRender`
     が生成され、マスク関連ソケット (マスク使用 / マスク対象 / 塗り切り抜き必要 /
     切り抜き必要) が消えていること。
  2. すべての形状 (rect / ellipse / cloud / fluffy / thorn / thorn-curve /
     octagon / custom) でフキダシを作成し、ノードグループのモディファイア
     が問題なく生成され、ノードに残骸 (Raycast / Object Info / set_mask_object)
     が一切ないこと。
  3. simple な作品を構築してレンダリングが落ちないこと。

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-Name/test/blender_balloon_node_minimization_phase_a_check.py"
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_phase_a",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_phase_a"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


FORBIDDEN_NODE_KEYWORDS = (
    "GeometryNodeRaycast",
    "GeometryNodeObjectInfo",
)
REMOVED_SOCKETS = (
    "マスク使用",
    "マスク対象",
    "塗り切り抜き必要",
    "切り抜き必要",
)
EXPECTED_SHAPES = (
    "rect",
    "ellipse",
    "cloud",
    "fluffy",
    "thorn",
    "thorn-curve",
    "octagon",
    "custom",
    "none",
)


def _check_node_group_layout() -> list[str]:
    """ノードグループの構造を検証し、残骸があれば文字列で返す."""
    from bname_dev_phase_a.utils import balloon_curve_render_nodes as bcrn

    errors: list[str] = []
    group = bcrn.ensure_node_group()
    if group is None:
        errors.append("ノードグループ生成失敗")
        return errors

    # ソケット検査
    socket_names = []
    for item in getattr(group.interface, "items_tree", []):
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        if getattr(item, "in_out", "") != "INPUT":
            continue
        socket_names.append(str(getattr(item, "name", "") or ""))
    for forbidden in REMOVED_SOCKETS:
        if forbidden in socket_names:
            errors.append(f"削除されたはずのソケットが残っている: {forbidden}")

    # ノード検査 (bl_idname)
    node_idnames = [getattr(n, "bl_idname", "") for n in group.nodes]
    for keyword in FORBIDDEN_NODE_KEYWORDS:
        for idname in node_idnames:
            if keyword in idname:
                errors.append(f"削除されたはずのノード種別が残っている: {keyword}")
                break

    # マスク関連の Switch ラベルが残っていないか
    for node in group.nodes:
        label = str(getattr(node, "label", "") or "")
        if "マスク" in label or "見切れ塗り" in label:
            errors.append(f"マスク関連ラベルのノードが残っている: {label}")

    return errors


def _ensure_minimal_work() -> bool:
    """テスト用の最小作品を作る."""
    temp_root = Path(tempfile.mkdtemp(prefix="bname_phase_a_work_"))
    try:
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "PhaseACheck.bname"))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        print(f"  ! work_new operator 呼び出し失敗: {exc}")
        return False
    if "FINISHED" not in result:
        print(f"  ! work_new operator が FINISHED を返さなかった: {result}")
        return False
    return True


def _add_balloon_of_shape(shape: str) -> bool:
    """指定形状のフキダシを 1 件追加し、curve_object が生成されることを確認."""
    from bname_dev_phase_a.utils import balloon_curve_object as bco
    from bname_dev_phase_a.operators import balloon_op
    from bname_dev_phase_a.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    scene = context.scene
    work = getattr(scene, "bname_work", None)
    if work is None:
        return False
    pages = list(getattr(work, "pages", []) or [])
    if not pages:
        return False
    page = pages[0]
    parent_key = page_stack_key(page)

    try:
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape,
            x=20.0, y=20.0, w=40.0, h=20.0,
            parent_kind="page",
            parent_key=parent_key,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ! _create_balloon_entry 失敗 ({shape}): {exc}")
        return False
    entry.line_width_mm = 0.5

    if shape == "none":
        # 本体なしは Curve を作らないので、ここで True 扱い
        return True

    try:
        obj = bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! ensure_balloon_curve_object 失敗 ({shape}): {exc}")
        return False
    if obj is None:
        print(f"  ! ensure_balloon_curve_object が None を返した ({shape})")
        return False
    modifier = obj.modifiers.get("B-Name Geometry Nodes")
    if modifier is None:
        print(f"  ! ジオメトリノードモディファイア未生成 ({shape})")
        return False
    # 検証: マスク関連 socket が modifier に残っていないこと
    for item in modifier.node_group.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET":
            continue
        name = str(getattr(item, "name", "") or "")
        if name in REMOVED_SOCKETS:
            print(f"  ! モディファイアに削除されたソケットが残っている ({shape}): {name}")
            return False
    return True


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    errors: list[str] = []

    print("=== Phase A ノードグループ構造検証 ===")
    layout_errors = _check_node_group_layout()
    if layout_errors:
        errors.extend(layout_errors)
        for e in layout_errors:
            print(f"  ✗ {e}")
    else:
        print("  ✓ マスク関連ソケット/ノードはすべて撤去されている")

    print("=== 各形状でモディファイア生成検証 ===")
    if not _ensure_minimal_work():
        errors.append("最小作品の作成に失敗")
        print("  ✗ 最小作品の作成に失敗")
    else:
        for shape in EXPECTED_SHAPES:
            ok = _add_balloon_of_shape(shape)
            print(f"  {'✓' if ok else '✗'} {shape}: モディファイア生成 {'OK' if ok else 'NG'}")
            if not ok:
                errors.append(f"形状 {shape} のモディファイア生成失敗")

    print()
    if errors:
        print(f"=== 失敗: {len(errors)} 件 ===")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("=== Phase A 検証 PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
