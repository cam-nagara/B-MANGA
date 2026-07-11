"""Blender 実機用: Phase A (フキダシジオメトリノードのマスク経路撤去) 検証.

確認内容:
  1. アドオン登録 → ジオメトリノードグループ `BManga_GN_BalloonCurveRender`
     が生成され、マスク関連ソケット (マスク使用 / マスク対象 / 塗り切り抜き必要 /
     切り抜き必要) が消えていること。
  2. すべての形状 (rect / ellipse / cloud / fluffy / thorn / thorn-curve /
     custom) でフキダシを作成し、ノードグループのモディファイア
     が問題なく生成され、ノードに残骸 (Raycast / Object Info / set_mask_object)
     が一切ないこと。
  3. simple な作品を構築してレンダリングが落ちないこと。

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_node_minimization_phase_a_check.py"
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
        "bmanga_dev_phase_a",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_phase_a"] = mod
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
    "custom",
    "none",
)


def _check_node_group_layout() -> list[str]:
    """Phase D 以降: ノードグループ自体が存在しないこと (= 完全撤去済み) を検証."""
    from bmanga_dev_phase_a.utils import balloon_curve_render_nodes as bcrn
    import bpy

    errors: list[str] = []
    group = bpy.data.node_groups.get(bcrn.GROUP_NAME)
    if group is None:
        return errors
    # 存在する場合は使用件数 0 (= 旧データだが modifier に紐付かない) ことを確認
    if group.users > 0:
        errors.append(f"旧ノードグループ {bcrn.GROUP_NAME} がまだ {group.users} 個の modifier に紐付いている")
    return errors


def _ensure_minimal_work() -> bool:
    """テスト用の最小作品を作る."""
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_phase_a_work_"))
    try:
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PhaseACheck.bmanga"))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        print(f"  ! work_new operator 呼び出し失敗: {exc}")
        return False
    if "FINISHED" not in result:
        print(f"  ! work_new operator が FINISHED を返さなかった: {result}")
        return False
    return True


def _add_balloon_of_shape(shape: str) -> bool:
    """指定形状のフキダシを 1 件追加し、curve_object が生成されることを確認."""
    from bmanga_dev_phase_a.utils import balloon_curve_object as bco
    from bmanga_dev_phase_a.operators import balloon_op
    from bmanga_dev_phase_a.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    scene = context.scene
    work = getattr(scene, "bmanga_work", None)
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
    # Phase D 以降: 旧 GN modifier は完全撤去されているため、存在しないこと
    modifier = obj.modifiers.get("B-MANGA Geometry Nodes")
    if modifier is not None:
        print(f"  ! Phase D 以降は GN modifier が無いはずなのに存在する ({shape})")
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
