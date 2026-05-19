"""Blender実機用: 出力範囲fixtureを別プロセスで再オープン確認する."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_package(package_name: str, package_root: Path):
    spec = importlib.util.spec_from_file_location(
        package_name,
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _border(scene) -> tuple[float, float, float, float]:
    return (
        round(float(scene.render.border_min_x), 6),
        round(float(scene.render.border_max_x), 6),
        round(float(scene.render.border_min_y), 6),
        round(float(scene.render.border_max_y), 6),
    )


def main() -> None:
    blend_path = Path(os.environ["BNAME_RANGE_REOPEN_BLEND"])
    json_path = Path(os.environ["BNAME_RANGE_REOPEN_JSON"])
    bpy.ops.wm.open_mainfile(filepath=str(blend_path))
    bname = _load_package("bname_dev_range_reopen", ROOT)
    render = _load_package("bname_render_range_reopen", ROOT / "addons" / "b_name_render")
    try:
        scene = bpy.context.scene
        from bname_dev_range_reopen.utils import coma_camera

        coma_camera.resync_coma_camera_output_layout(bpy.context)
        payload = {
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "border": _border(scene),
            "source": scene.get("bname_coma_camera_render_border_source", ""),
            "camera_type": scene.camera.data.type,
            "fisheye_fov": round(float(getattr(scene.camera.data, "fisheye_fov", 0.0) or 0.0), 6),
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        try:
            render.unregister()
        except Exception:
            pass
        try:
            bname.unregister()
        except Exception:
            pass


if __name__ == "__main__":
    main()
