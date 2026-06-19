"""Blender 実機用: Phase D しっぽ付きフキダシのレンダー目視確認.

大きめのしっぽを 4 方向 (上下左右) に付けたフキダシを 1 ページに並べて
レンダーする。AI 目視で:
  - しっぽ部分が body と同じ色で塗りつぶされている (union 塗り面)
  - しっぽの周りに黒い主線フチがある (tail_main_line_mesh)
  - body と tail の繋ぎ目で線が綺麗につながる
を確認する。

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_node_minimization_phase_d_tail_visual_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_PHASE_D_TAIL_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_phase_d_tail_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_phase_d_tail",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_phase_d_tail"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()

    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_phase_d_tail_work_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PhaseDTail.bmanga"))  # type: ignore[attr-defined]
    if "FINISHED" not in result:
        print(f"  ✗ work_new failed: {result}")
        return 1

    from bmanga_dev_phase_d_tail.operators import balloon_op
    from bmanga_dev_phase_d_tail.utils import balloon_curve_object as bco
    from bmanga_dev_phase_d_tail.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    parent_key = page_stack_key(page)

    # 4 方向のしっぽを持つ ellipse フキダシを 1 つ
    entry = balloon_op._create_balloon_entry(
        context, page,
        shape="ellipse",
        x=100.0, y=100.0, w=50.0, h=50.0,
        parent_kind="page",
        parent_key=parent_key,
    )
    entry.line_style = "solid"
    entry.line_width_mm = 1.0
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    entry.fill_color = (1.0, 0.9, 0.5, 1.0)
    entry.fill_opacity = 100.0

    # 4 方向 (上下左右) にしっぽを追加
    for direction_deg, label in [(0.0, "right"), (90.0, "up"), (180.0, "left"), (270.0, "down")]:
        tail = entry.tails.add()
        tail.type = "straight"
        tail.direction_deg = direction_deg
        tail.length_mm = 20.0
        tail.root_width_mm = 10.0
        tail.tip_width_mm = 0.0

    obj = bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    assert obj is not None

    # 生成された Mesh オブジェクトを列挙
    print("=== 生成された Mesh ===")
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        kind = (
            o.get("bmanga_balloon_line_mesh_kind")
            or o.get("bmanga_balloon_fill_mesh_kind")
            or ""
        )
        if not kind:
            continue
        verts = len(o.data.vertices) if o.data else 0
        polys = len(o.data.polygons) if o.data else 0
        print(f"  {o.name}: kind={kind}, verts={verts}, polys={polys}, "
              f"hide_v={o.hide_viewport}, hide_r={o.hide_render}")

    # 既存カメラを使用 (作品作成時に自動で配置されたカメラを使う)
    cam = scene.camera
    if cam is None:
        for o in bpy.data.objects:
            if o.type == "CAMERA":
                scene.camera = o
                cam = o
                break
    if cam is None:
        cam_data = bpy.data.cameras.new("PhaseDTailCam")
        cam = bpy.data.objects.new("PhaseDTailCam", cam_data)
        scene.collection.objects.link(cam)
        scene.camera = cam

    # フキダシ全体 (body + tails) が見えるようカメラ調整
    target_xy = (obj.location.x, obj.location.y)
    cam.location = (target_xy[0], target_xy[1], 0.5)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam.data.type = "ORTHO"
    # body 50mm + tail 20mm × 2 = 90mm に余白
    cam.data.ortho_scale = 0.12  # 120mm

    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    out_png = _OUT_PATH / "phase_d_tail_4dir.png"
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(out_png)
    scene.render.film_transparent = False
    bpy.ops.render.render(write_still=True)
    print(f"=== レンダー完了: {out_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
