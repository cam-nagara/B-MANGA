"""Blender 実機用: Phase C (フキダシ塗り面を Python earcut で焼き込み) 検証.

確認内容:
  1. 全形状で `balloon_fill_mesh_<id>` オブジェクトが生成されること。
  2. 塗り面メッシュに modifier が一切付いていないこと (Python 焼き込みのみ)。
  3. 塗り面の頂点に bmanga_fill_blur_alpha 属性が POINT domain Float で存在すること。
  4. ジオメトリノードグループから GeometryNodeFillCurve が body 経路では削除されていること
     (main_line_fill 用の FillCurve は残る)。
  5. しっぽ付きフキダシで body + tail の union 塗り面が生成されること。
  6. レンダー出力で塗り面が正しく見えること。

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_node_minimization_phase_c_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_PHASE_C_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_phase_c_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_phase_c",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_phase_c"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


SHAPES = ["rect", "ellipse", "octagon", "cloud", "fluffy", "thorn", "thorn-curve"]


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    errors: list[str] = []

    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_phase_c_work_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PhaseCCheck.bmanga"))  # type: ignore[attr-defined]
    if "FINISHED" not in result:
        print(f"  ✗ work_new failed: {result}")
        return 1

    from bmanga_dev_phase_c.operators import balloon_op
    from bmanga_dev_phase_c.utils import balloon_curve_object as bco
    from bmanga_dev_phase_c.utils import balloon_fill_mesh
    from bmanga_dev_phase_c.utils import balloon_curve_render_nodes as bcrn
    from bmanga_dev_phase_c.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    parent_key = page_stack_key(page)

    print("=== Phase C-A: Phase D 以降は GN グループ自体が存在しない ===")
    group = bpy.data.node_groups.get(bcrn.GROUP_NAME)
    if group is None:
        print(f"  ✓ ノードグループ {bcrn.GROUP_NAME} は完全撤去済み (Phase D)")
    elif group.users == 0:
        print(f"  ✓ ノードグループ {bcrn.GROUP_NAME} は使用件数 0")
    else:
        errors.append(f"ノードグループ {bcrn.GROUP_NAME} がまだ使用中: users={group.users}")

    print("=== Phase C-B: 全形状で fill mesh が生成されるか ===")
    for idx, shape in enumerate(SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape,
            x=10.0 + idx * 50.0, y=10.0, w=40.0, h=40.0,
            parent_kind="page",
            parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = 1.0
        entry.line_color = (0.0, 0.0, 0.0, 1.0)
        entry.fill_color = (1.0, 1.0, 0.7, 1.0)
        entry.fill_opacity = 100.0
        obj = bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
        if obj is None:
            errors.append(f"{shape}: curve object 生成失敗")
            continue
        fill_obj_name = f"{balloon_fill_mesh.BALLOON_FILL_MESH_NAME_PREFIX}{entry.id}"
        fill_obj = bpy.data.objects.get(fill_obj_name)
        if fill_obj is None:
            errors.append(f"{shape}: fill mesh オブジェクト {fill_obj_name} が生成されていない")
            print(f"  ✗ {shape}: fill mesh 未生成")
            continue
        verts = len(fill_obj.data.vertices)
        polys = len(fill_obj.data.polygons)
        mods = [m.name for m in fill_obj.modifiers]
        attr = fill_obj.data.attributes.get(bcrn.FILL_BLUR_ALPHA_ATTRIBUTE)
        attr_ok = (attr is not None and attr.domain == "POINT" and attr.data_type == "FLOAT")
        material = fill_obj.data.materials[0] if fill_obj.data.materials else None
        print(f"  {shape}: verts={verts}, polys={polys}, modifiers={mods}, "
              f"blur_alpha_attr={'OK' if attr_ok else 'NG'}, material={material.name if material else None}")
        if mods:
            errors.append(f"{shape}: fill mesh に modifier が付いている: {mods}")
        if verts < 3:
            errors.append(f"{shape}: fill mesh の頂点数が少なすぎる ({verts})")
        if not attr_ok:
            errors.append(f"{shape}: bmanga_fill_blur_alpha 属性が無いか型が違う")
        if material is None:
            errors.append(f"{shape}: fill mesh にマテリアル未割当")

    print("=== Phase C-C: しっぽ付きフキダシの union 塗り面 ===")
    # 最後の thorn-curve に straight tail を 1 本追加
    last_entry = page.balloons[-1]
    tail = last_entry.tails.add()
    tail.type = "straight"
    tail.direction_deg = 270.0
    tail.length_mm = 15.0
    tail.root_width_mm = 6.0
    tail.tip_width_mm = 0.5
    obj_with_tail = bco.ensure_balloon_curve_object(scene=scene, entry=last_entry, page=page)
    fill_with_tail = bpy.data.objects.get(f"{balloon_fill_mesh.BALLOON_FILL_MESH_NAME_PREFIX}{last_entry.id}")
    if fill_with_tail is None:
        errors.append("しっぽ付きフキダシの fill mesh が消えた")
    else:
        # 頂点数がしっぽなしより増えていることを期待 (union で面積が広がるため)
        bbox_min = (
            min(v.co.x for v in fill_with_tail.data.vertices),
            min(v.co.y for v in fill_with_tail.data.vertices),
        )
        bbox_max = (
            max(v.co.x for v in fill_with_tail.data.vertices),
            max(v.co.y for v in fill_with_tail.data.vertices),
        )
        print(f"  しっぽ付き fill: verts={len(fill_with_tail.data.vertices)}, "
              f"bbox=[{bbox_min[0]:.4f},{bbox_min[1]:.4f}]-[{bbox_max[0]:.4f},{bbox_max[1]:.4f}]")
        # しっぽが下方向に伸びているため、bbox_min[1] (Y最小) が小さくなるはず
        # (フキダシ中心が y=0 付近、しっぽ 15mm 下方向 → 約 -0.015m 以下)
        if bbox_min[1] >= -0.005:
            errors.append(f"しっぽが fill mesh に含まれていない可能性: bbox_min_y={bbox_min[1]:.4f}")
        else:
            print(f"  ✓ しっぽが union 塗り面に含まれている (y_min={bbox_min[1]:.4f})")

    print("=== Phase C-D: レンダーテスト ===")
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    out_png = _OUT_PATH / "phase_c_all_shapes.png"
    cam = scene.camera
    if cam is None:
        # シーンにカメラがあれば使う
        for o in bpy.data.objects:
            if o.type == "CAMERA":
                scene.camera = o
                cam = o
                break
    # フキダシをカバーする範囲にカメラ調整
    xs = []; ys = []
    for obj in bpy.data.objects:
        if obj.get("bmanga_balloon_fill_mesh_kind") == "balloon_fill_mesh":
            xs.append(obj.location.x); ys.append(obj.location.y)
            for v in obj.data.vertices:
                xs.append(obj.location.x + v.co.x); ys.append(obj.location.y + v.co.y)
    if xs and cam:
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        size = max(max(xs) - min(xs), max(ys) - min(ys)) + 0.02
        cam.location = (cx, cy, 0.5)
        cam.rotation_euler = (0.0, 0.0, 0.0)
        if cam.data.type == "ORTHO":
            cam.data.ortho_scale = size
        else:
            cam.data.type = "ORTHO"
            cam.data.ortho_scale = size

    scene.render.resolution_x = 1024
    scene.render.resolution_y = 256
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(out_png)
    scene.render.film_transparent = False
    try:
        bpy.ops.render.render(write_still=True)
        print(f"  ✓ レンダー完了: {out_png}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"レンダー失敗: {exc}")

    print()
    if errors:
        print(f"=== 失敗: {len(errors)} 件 ===")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"=== Phase C 検証 PASS (出力: {_OUT_PATH}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
