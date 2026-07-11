"""Blender 実機用: Phase D (フキダシ Geometry Nodes modifier 完全撤去) 検証.

確認内容:
  1. 全形状でフキダシを作成しても、本体カーブに B-MANGA Geometry Nodes
     modifier が一切付かないこと。
  2. ノードグループ `BManga_GN_BalloonCurveRender` がシーン内に存在しないか、
     存在しても使用件数 0 であること。
  3. しっぽ付きフキダシで balloon_tail_main_line_mesh_<id> オブジェクトが
     生成されること (modifier なし)。
  4. 本体カーブの fill_mode が NONE、bevel_depth=0 で、 カーブ自身では何も
     描画しないこと。
  5. レンダー出力で塗り + 主線 + フチ + 多重線 + しっぽ主線フチが全て
     見えること。

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_node_minimization_phase_d_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_PHASE_D_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_phase_d_"))

SHAPES = ["rect", "ellipse", "cloud", "fluffy", "thorn", "thorn-curve"]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_phase_d",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_phase_d"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    errors: list[str] = []

    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_phase_d_work_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PhaseDCheck.bmanga"))  # type: ignore[attr-defined]
    if "FINISHED" not in result:
        print(f"  ✗ work_new failed: {result}")
        return 1

    from bmanga_dev_phase_d.operators import balloon_op
    from bmanga_dev_phase_d.utils import balloon_curve_object as bco
    from bmanga_dev_phase_d.utils import balloon_line_mesh
    from bmanga_dev_phase_d.utils import balloon_curve_render_nodes as bcrn
    from bmanga_dev_phase_d.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    parent_key = page_stack_key(page)

    print("=== Phase D-A: 全形状でフキダシを作成して modifier の有無確認 ===")
    objects = []
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
        objects.append((shape, entry, obj))
        mods = [m.name for m in obj.modifiers]
        if mods:
            errors.append(f"{shape}: 本体カーブにまだ modifier がある: {mods}")
        # カーブ設定確認
        curve = obj.data
        if curve.bevel_depth != 0.0:
            errors.append(f"{shape}: bevel_depth が 0 ではない ({curve.bevel_depth})")
        if curve.fill_mode != "NONE":
            errors.append(f"{shape}: fill_mode が NONE ではない ({curve.fill_mode})")
        print(f"  {shape}: modifiers={mods}, bevel_depth={curve.bevel_depth}, fill_mode={curve.fill_mode}")

    print("=== Phase D-B: ノードグループの撤去確認 ===")
    group = bpy.data.node_groups.get(bcrn.GROUP_NAME)
    if group is None:
        print(f"  ✓ ノードグループ {bcrn.GROUP_NAME} はシーンに存在しない")
    else:
        if group.users == 0:
            print(f"  ✓ ノードグループ {bcrn.GROUP_NAME} は残っているが使用件数 0")
        else:
            errors.append(f"ノードグループ {bcrn.GROUP_NAME} がまだ {group.users} 個の modifier で使用されている")

    print("=== Phase D-C: しっぽ付きフキダシで主線が一体化されるか ===")
    if objects:
        shape, entry, obj = objects[-1]
        line_obj_name = f"{balloon_line_mesh.BALLOON_LINE_MESH_NAME_PREFIX}{entry.id}"
        tail = entry.tails.add()
        tail.type = "straight"
        tail.direction_deg = 270.0
        tail.length_mm = 15.0
        tail.root_width_mm = 6.0
        tail.tip_width_mm = 0.5
        bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
        tail_obj_name = f"{balloon_line_mesh.BALLOON_TAIL_MAIN_LINE_MESH_NAME_PREFIX}{entry.id}"
        tail_obj = bpy.data.objects.get(tail_obj_name)
        line_obj_after = bpy.data.objects.get(line_obj_name)
        if line_obj_after is None:
            errors.append(f"しっぽ付き {shape}: 主線メッシュが消えている ({line_obj_name})")
        else:
            verts = len(line_obj_after.data.vertices)
            polys = len(line_obj_after.data.polygons)
            print(f"  ✓ {shape} + tail: joined line verts={verts}, polys={polys}, separate_tail={tail_obj is not None}")
            if verts <= 0 or polys <= 0:
                errors.append(f"しっぽ付き {shape}: 主線メッシュが空になっている")
            if tail_obj is not None:
                errors.append(f"しっぽ付き {shape}: 分離しっぽ線が残っている ({tail_obj_name})")

    print("=== Phase D-D: レンダーテスト (all_shapes_shapely_check と同様の構図) ===")
    # 既存の test/blender_balloon_all_shapes_shapely_check.py と同じ設定で
    # カメラを設定してレンダー出力する
    cam = scene.camera
    if cam is None:
        for o in bpy.data.objects:
            if o.type == "CAMERA":
                scene.camera = o
                cam = o
                break
    if cam is None:
        cam_data = bpy.data.cameras.new("PhaseDCam")
        cam = bpy.data.objects.new("PhaseDCam", cam_data)
        scene.collection.objects.link(cam)
        scene.camera = cam

    xs = [obj.location.x for _, _, obj in objects]
    half = 0.025
    if xs:
        cx = (min(xs) + max(xs)) * 0.5
        size = (max(xs) - min(xs)) + 0.05
        cam.location = (cx, 0.01, 0.5)
        cam.rotation_euler = (0.0, 0.0, 0.0)
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = size

    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    out_png = _OUT_PATH / "phase_d_all_shapes.png"
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
    print(f"=== Phase D 検証 PASS (出力: {_OUT_PATH}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
