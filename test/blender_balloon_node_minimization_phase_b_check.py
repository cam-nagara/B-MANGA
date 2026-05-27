"""Blender 実機用: Phase B (フキダシ custom 形状を Shapely バンドメッシュへ移行) 検証.

確認内容:
  1. カスタム形状 (星型 polygon) をプリセットとして用意し、
     shape=custom + custom_preset_name で balloon を作成する。
  2. 主線・外側フチ・内側フチ・多重線がすべて Shapely バンドメッシュ
     (balloon_line_mesh / balloon_outer_edge_mesh / balloon_inner_edge_mesh /
     balloon_multi_line_mesh) として生成されることを確認する。
  3. ノード側の outer_edge / inner_edge / multi_line / line_legacy 経路は
     override で停止されていることを確認する (modifier の対応ソケットが False)。
  4. レンダリングが落ちないことを確認する。

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-Name/test/blender_balloon_node_minimization_phase_b_check.py"
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_PHASE_B_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_phase_b_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_phase_b",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_phase_b"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _make_star_vertices(num_points: int = 5, outer_r: float = 1.0, inner_r: float = 0.5) -> list[tuple[float, float]]:
    """中心 (0,0), 外接半径 outer_r / 内接半径 inner_r の星型を返す."""
    out: list[tuple[float, float]] = []
    for i in range(num_points * 2):
        angle = (math.pi * 2.0) * (i / (num_points * 2)) - math.pi * 0.5
        r = outer_r if i % 2 == 0 else inner_r
        out.append((math.cos(angle) * r, math.sin(angle) * r))
    return out


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    errors: list[str] = []

    temp_root = Path(tempfile.mkdtemp(prefix="bname_phase_b_work_"))
    work_path = temp_root / "PhaseBCheck.bname"
    result = bpy.ops.bname.work_new(filepath=str(work_path))  # type: ignore[attr-defined]
    if "FINISHED" not in result:
        print(f"  ✗ work_new failed: {result}")
        return 1

    from bname_dev_phase_b.io import balloon_presets
    from bname_dev_phase_b.operators import balloon_op
    from bname_dev_phase_b.utils import balloon_curve_object as bco
    from bname_dev_phase_b.utils import balloon_line_mesh
    from bname_dev_phase_b.utils.layer_hierarchy import page_stack_key

    # 1. カスタムプリセット (星型) を作品ローカルに保存
    star_vertices = _make_star_vertices(5, 1.0, 0.5)
    preset_path = balloon_presets.save_local_preset(
        work_dir=temp_root,
        name="phase_b_star",
        description="Phase B 検証用カスタム星型",
        vertices_mm=star_vertices,
        absolute_coords=False,
    )
    print(f"=== カスタムプリセット保存: {preset_path}")

    # 2. shape=custom のフキダシを追加
    context = bpy.context
    scene = context.scene
    work = scene.bname_work
    page = work.pages[0]
    parent_key = page_stack_key(page)

    entry = balloon_op._create_balloon_entry(
        context, page,
        shape="custom",
        x=20.0, y=20.0, w=60.0, h=60.0,
        parent_kind="page",
        parent_key=parent_key,
    )
    entry.custom_preset_name = "phase_b_star"
    entry.line_style = "solid"
    entry.line_width_mm = 1.5
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = 1.0
    entry.outer_white_margin_color = (1.0, 0.0, 0.0, 1.0)
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = 1.0
    entry.inner_white_margin_color = (0.0, 0.0, 1.0, 1.0)
    entry.line_style = "double"
    entry.multi_line_count = 3
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 0.4
    entry.multi_line_spacing_mm = 0.7

    # 3. curve object を生成
    obj = bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    if obj is None:
        print("  ✗ ensure_balloon_curve_object が None を返した")
        return 1
    if obj.type != "CURVE":
        print(f"  ✗ obj.type != CURVE: {obj.type}")
        return 1

    # 4. is_shapely_line_shape / is_shapely_multi_line_shape / is_mesh_band_shape
    #    が全て True であることを確認
    print("=== Shapely 認識テスト ===")
    if not balloon_line_mesh.is_shapely_line_shape(entry):
        errors.append("is_shapely_line_shape(custom) が False を返した")
    if not balloon_line_mesh.is_shapely_multi_line_shape(entry):
        errors.append("is_shapely_multi_line_shape(custom) が False を返した")
    if not balloon_line_mesh.is_mesh_band_shape(entry):
        errors.append("is_mesh_band_shape(custom) が False を返した")
    if not errors:
        print("  ✓ 3 つの is_shapely_*_shape() が True (custom も Shapely 対象)")
    else:
        for e in errors:
            print(f"  ✗ {e}")

    # 5. Shapely バンドメッシュオブジェクトが作られているか確認
    print("=== バンドメッシュ生成確認 ===")
    balloon_id = entry.id
    expected_mesh_names = {
        "line": f"balloon_line_mesh_{balloon_id}",
        "outer_edge": f"balloon_outer_edge_mesh_{balloon_id}",
        "inner_edge": f"balloon_inner_edge_mesh_{balloon_id}",
        "multi_line": f"balloon_multi_line_mesh_{balloon_id}",
    }
    for kind, name in expected_mesh_names.items():
        mesh_obj = bpy.data.objects.get(name)
        if mesh_obj is None:
            errors.append(f"{kind} メッシュオブジェクト {name} が生成されていない")
            print(f"  ✗ {kind}: 未生成")
            continue
        verts = len(mesh_obj.data.vertices)
        polys = len(mesh_obj.data.polygons)
        mods = [m.name for m in mesh_obj.modifiers]
        print(f"  ✓ {kind}: verts={verts}, polys={polys}, modifiers={mods}")
        if mods:
            errors.append(f"{kind} メッシュに modifier が付いている: {mods}")
        if verts < 3:
            errors.append(f"{kind} メッシュの頂点数が少なすぎる ({verts})")

    # 6. ノード側 modifier の outer/inner/multi が override で停止状態か確認
    print("=== ノードモディファイア override 検証 ===")
    modifier = obj.modifiers.get("B-Name Geometry Nodes")
    if modifier is None:
        errors.append("ノードモディファイアが存在しない")
    else:
        socket_values = {}
        for item in modifier.node_group.interface.items_tree:
            if getattr(item, "item_type", "") != "SOCKET":
                continue
            if getattr(item, "in_out", "") != "INPUT":
                continue
            name = str(getattr(item, "name", "") or "")
            ident = getattr(item, "identifier", "")
            if name in ("外側フチ", "内側フチ", "多重線", "線を面で生成"):
                socket_values[name] = modifier.get(ident)
        print(f"  socket values: {socket_values}")
        # filled_line_enabled は True (= 中心線方式停止)、外側フチ・内側フチ・多重線は False (= Shapely 側で描画)
        if socket_values.get("外側フチ") is not False:
            errors.append(f"外側フチ socket が False ではない: {socket_values.get('外側フチ')}")
        if socket_values.get("内側フチ") is not False:
            errors.append(f"内側フチ socket が False ではない: {socket_values.get('内側フチ')}")
        if socket_values.get("多重線") is not False:
            errors.append(f"多重線 socket が False ではない: {socket_values.get('多重線')}")
        if socket_values.get("線を面で生成") is not True:
            errors.append(f"線を面で生成 socket が True ではない: {socket_values.get('線を面で生成')}")
        if not any(e for e in errors if "socket" in e):
            print("  ✓ ノード側の outer/inner/multi/line_legacy 経路はすべて停止状態")

    # 7. レンダリングが落ちないことを確認
    print("=== レンダーテスト ===")
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    out_png = _OUT_PATH / "phase_b_custom_star.png"
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(out_png)
    scene.render.film_transparent = False
    try:
        bpy.ops.render.render(write_still=True)
        print(f"  ✓ レンダー完了: {out_png}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"レンダー失敗: {exc}")
        print(f"  ✗ レンダー失敗: {exc}")

    print()
    if errors:
        print(f"=== 失敗: {len(errors)} 件 ===")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"=== Phase B 検証 PASS (出力: {_OUT_PATH}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
