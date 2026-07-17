"""Blender実機: コマファイルでテキスト/フキダシ実体が生成されない(overlayのみ)ことを検証する。

utils/page_file_scene.py の structural_page_filter / content_page_filter /
coma_runtime_page_filter が ROLE_COMA を明示的に扱うようになったかを検証する。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
MOD_NAME = "bmanga_dev_koma_overlay_only"


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

tmp_root = Path(tempfile.mkdtemp(prefix="bmanga_koma_verify_"))
work_dir = tmp_root / "test059.bmanga"
coma_path = work_dir / "p0001" / "c01" / "c01.blend"
coma_path.parent.mkdir(parents=True, exist_ok=True)

bpy.ops.wm.save_as_mainfile(filepath=str(coma_path), check_existing=False)

scene = bpy.context.scene
scene.bmanga_current_page_id = "p0001"
if hasattr(scene, "bmanga_current_coma_id"):
    scene.bmanga_current_coma_id = "c01"
if hasattr(scene, "bmanga_current_coma_page_id"):
    scene.bmanga_current_coma_page_id = "p0001"

role, page_id, coma_id = page_file_scene.current_role(bpy.context)
print(f"role={role!r} page_id={page_id!r} coma_id={coma_id!r}")
assert role == page_file_scene.ROLE_COMA, f"expected ROLE_COMA, got {role}"
assert page_id == "p0001"

structural = page_file_scene.structural_page_filter(scene)
content = page_file_scene.content_page_filter(scene)
coma_runtime = page_file_scene.coma_runtime_page_filter(scene)
print(f"structural_page_filter={structural!r}")
print(f"content_page_filter={content!r}")
print(f"coma_runtime_page_filter={coma_runtime!r}")

assert structural == {"p0001"}, f"structural_page_filter should restrict to own page, got {structural}"
assert content == set(), f"content_page_filter must be empty set (overlay-only) in koma files, got {content}"
assert coma_runtime == {"p0001"}, f"coma_runtime_page_filter should keep own page's frame, got {coma_runtime}"

is_edit = page_file_scene.is_page_edit_scene(scene)
print(f"is_page_edit_scene={is_edit}")
assert is_edit is False, "koma file must not be treated as page-edit scene"

print(
    "OK: koma file content_page_filter is overlay-only (empty), "
    "structural/coma_runtime restricted to own page."
)
