"""B-Name コマ用 blend のマスクメッシュ + AOV 同期テスト.

実行:
    "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" \
        --background --factory-startup \
        --python test/blender_coma_mask_aov_check.py

確認内容:
- ``utils/coma_mask_object`` モジュールがエラーなく動作する。
- ``ensure_coma_mask_mesh`` 呼び出し後にメッシュオブジェクト・コレクション・
  view layer・AOV が存在する。
- メッシュ頂点数がコマ多角形の頂点数と一致する。
- 再生成 (頂点を変えて 2 回目呼び出し) で頂点が更新される。
"""
from __future__ import annotations

import sys
import importlib.util
import types

import bpy


ROOT = r"D:/Develop/Blender/B-Name/.claude/worktrees/mystifying-jennings-c43858"


def _load_module(qualname: str, path: str):
    spec = importlib.util.spec_from_file_location(qualname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[qualname] = mod
    spec.loader.exec_module(mod)
    return mod


def _bootstrap_b_name_packages() -> None:
    """Build a minimal package skeleton so relative imports resolve."""
    pkg = types.ModuleType("b_name_test")
    pkg.__path__ = [ROOT]
    sys.modules["b_name_test"] = pkg
    for sub in ("utils", "core"):
        m = types.ModuleType(f"b_name_test.{sub}")
        m.__path__ = [f"{ROOT}/{sub}"]
        sys.modules[f"b_name_test.{sub}"] = m
    # log は単独ファイル
    _load_module("b_name_test.utils.log", f"{ROOT}/utils/log.py")
    _load_module("b_name_test.utils.geom", f"{ROOT}/utils/geom.py")


def main() -> int:
    _bootstrap_b_name_packages()
    cmo = _load_module(
        "b_name_test.utils.coma_mask_object", f"{ROOT}/utils/coma_mask_object.py"
    )

    scene = bpy.context.scene

    # 仮の B-Name データ (rect コマ)
    work_rect = types.SimpleNamespace(
        pages=[
            types.SimpleNamespace(
                id="p0001",
                comas=[
                    types.SimpleNamespace(
                        coma_id="c01",
                        shape_type="rect",
                        rect_x_mm=10.0,
                        rect_y_mm=20.0,
                        rect_width_mm=30.0,
                        rect_height_mm=40.0,
                        vertices=[],
                    )
                ],
            )
        ]
    )

    # Test 1: 初回生成
    ok = cmo.ensure_coma_mask_mesh(scene, work_rect, "p0001", "c01")
    assert ok, "ensure_coma_mask_mesh returned False"
    obj = bpy.data.objects.get(cmo.MASK_OBJECT_NAME)
    assert obj is not None, "mask object missing"
    assert obj.type == "MESH"
    assert len(obj.data.vertices) == 4, f"expected 4 verts, got {len(obj.data.vertices)}"
    print(f"[ok] rect mask mesh created with {len(obj.data.vertices)} verts")

    vl = scene.view_layers.get(cmo.MASK_VIEW_LAYER_NAME)
    assert vl is not None, "view layer missing"
    assert any(a.name == cmo.MASK_AOV_NAME for a in vl.aovs), "AOV missing"
    print(f"[ok] view layer {cmo.MASK_VIEW_LAYER_NAME} has AOV {cmo.MASK_AOV_NAME}")

    coll = bpy.data.collections.get(cmo.MASK_COLLECTION_NAME)
    assert coll is not None, "collection missing"
    assert obj.name in {o.name for o in coll.objects}, "obj not linked to collection"
    print(f"[ok] collection {cmo.MASK_COLLECTION_NAME} contains mask object")

    mat = bpy.data.materials.get(cmo.MASK_MATERIAL_NAME)
    assert mat is not None and mat.use_nodes, "material missing/no-nodes"
    aov_nodes = [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeOutputAOV"]
    assert aov_nodes, "AOV output node missing"
    print(f"[ok] material {mat.name} has {len(aov_nodes)} AOV node(s)")

    # Test 2: 多角形コマで再生成 (頂点数が変わる)
    polygon_pts = [(0.0, 0.0), (50.0, 0.0), (50.0, 30.0), (25.0, 50.0), (0.0, 30.0)]
    work_poly = types.SimpleNamespace(
        pages=[
            types.SimpleNamespace(
                id="p0001",
                comas=[
                    types.SimpleNamespace(
                        coma_id="c01",
                        shape_type="polygon",
                        rect_x_mm=0.0, rect_y_mm=0.0, rect_width_mm=0.0, rect_height_mm=0.0,
                        vertices=[
                            types.SimpleNamespace(x_mm=x, y_mm=y) for x, y in polygon_pts
                        ],
                    )
                ],
            )
        ]
    )
    ok = cmo.ensure_coma_mask_mesh(scene, work_poly, "p0001", "c01")
    assert ok, "second ensure returned False"
    obj = bpy.data.objects.get(cmo.MASK_OBJECT_NAME)
    assert obj is not None
    assert len(obj.data.vertices) == 5, f"expected 5 verts, got {len(obj.data.vertices)}"
    print(f"[ok] polygon mask mesh updated to {len(obj.data.vertices)} verts")

    # Test 3: 空ポリゴンならスキップ
    work_empty = types.SimpleNamespace(
        pages=[
            types.SimpleNamespace(
                id="p0001",
                comas=[
                    types.SimpleNamespace(
                        coma_id="c01",
                        shape_type="rect",
                        rect_x_mm=0.0, rect_y_mm=0.0,
                        rect_width_mm=0.0, rect_height_mm=0.0,
                        vertices=[],
                    )
                ],
            )
        ]
    )
    ok = cmo.ensure_coma_mask_mesh(scene, work_empty, "p0001", "c01")
    assert not ok, "empty polygon should return False (no change)"
    obj = bpy.data.objects.get(cmo.MASK_OBJECT_NAME)
    # 直前の polygon が残る (空ポリゴンでは触らない)
    assert obj is not None and len(obj.data.vertices) == 5
    print("[ok] empty polygon is a no-op (previous mesh preserved)")

    # Test 4: page_id / coma_id 不一致 → no-op
    ok = cmo.ensure_coma_mask_mesh(scene, work_rect, "p9999", "c99")
    assert not ok
    print("[ok] non-matching page/coma is a no-op")

    # Test 5: 角処理 (丸角) 反映 — マスクメッシュ頂点が増える
    work_rect_round = types.SimpleNamespace(pages=[types.SimpleNamespace(
        id="p0001", comas=[types.SimpleNamespace(
            coma_id="c01", shape_type="rect",
            rect_x_mm=0.0, rect_y_mm=0.0,
            rect_width_mm=80.0, rect_height_mm=60.0,
            vertices=[],
            border=types.SimpleNamespace(
                corner_type="rounded",
                corner_radius_mm=5.0,
            ),
        )]
    )])
    ok = cmo.ensure_coma_mask_mesh(scene, work_rect_round, "p0001", "c01")
    assert ok
    obj = bpy.data.objects.get(cmo.MASK_OBJECT_NAME)
    rounded_verts = len(obj.data.vertices)
    assert rounded_verts > 4, f"rounded mask should have >4 verts, got {rounded_verts}"
    print(f"[ok] rounded corners reflected: {rounded_verts} verts (>4)")

    # Test 6: 面取り (bevel) もマスクへ反映
    work_rect_bevel = types.SimpleNamespace(pages=[types.SimpleNamespace(
        id="p0001", comas=[types.SimpleNamespace(
            coma_id="c01", shape_type="rect",
            rect_x_mm=0.0, rect_y_mm=0.0,
            rect_width_mm=80.0, rect_height_mm=60.0,
            vertices=[],
            border=types.SimpleNamespace(
                corner_type="bevel",
                corner_radius_mm=5.0,
            ),
        )]
    )])
    ok = cmo.ensure_coma_mask_mesh(scene, work_rect_bevel, "p0001", "c01")
    assert ok
    obj = bpy.data.objects.get(cmo.MASK_OBJECT_NAME)
    bevel_verts = len(obj.data.vertices)
    assert bevel_verts == 8, f"bevel mask should have 8 verts, got {bevel_verts}"
    print(f"[ok] bevel corners reflected: {bevel_verts} verts (== 8)")

    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
