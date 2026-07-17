"""Blender実機: 今回の修正がページ用blend(ROLE_PAGE)の既存挙動を壊していないか回帰確認する。"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
MOD_NAME = "bmanga_dev_page_role_regress"


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


_load_addon()
page_file_scene = _sub("utils.page_file_scene")

tmp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_regress_"))
work_dir = tmp_root / "test059.bmanga"
page_path = work_dir / "p0001" / "page.blend"
page_path.parent.mkdir(parents=True, exist_ok=True)

bpy.ops.wm.save_as_mainfile(filepath=str(page_path), check_existing=False)

scene = bpy.context.scene

role, page_id, coma_id = page_file_scene.current_role(bpy.context)
print(f"role={role!r} page_id={page_id!r} coma_id={coma_id!r}")
assert role == page_file_scene.ROLE_PAGE

structural = page_file_scene.structural_page_filter(scene)
content = page_file_scene.content_page_filter(scene)
coma_runtime = page_file_scene.coma_runtime_page_filter(scene)
print(f"structural={structural!r} content={content!r} coma_runtime={coma_runtime!r}")
assert structural == {"p0001"}
assert content == {"p0001"}, f"page.blend should still materialize its own page content, got {content}"
assert coma_runtime == {"p0001"}

is_edit = page_file_scene.is_page_edit_scene(scene)
print(f"is_page_edit_scene={is_edit}")
assert is_edit is True

print("OK: page.blend (ROLE_PAGE) behavior unchanged.")
