"""Blender実機: 保存中プロセス終了後も未保存ラスター画素を復旧する。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_raster_crash_parent"
SENTINEL = "BMANGA_RASTER_CRASH_VERIFY_OK"


def _load_addon(package_name: str):
    spec = importlib.util.spec_from_file_location(
        package_name,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _write_child(path: Path, source: str) -> None:
    path.write_text(source.lstrip(), encoding="utf-8")


def _run_child(script: Path, environment: dict[str, str]):
    return subprocess.run(
        [
            bpy.app.binary_path,
            "--background",
            "--factory-startup",
            "--python",
            str(script),
        ],
        cwd=str(ROOT),
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=480,
        check=False,
    )


def _prepare_baseline(temp_root: Path) -> tuple[Path, Path, str]:
    from bmanga_raster_crash_parent.core.work import get_work
    from bmanga_raster_crash_parent.operators import raster_layer_op
    from bmanga_raster_crash_parent.utils import lifecycle_checkpoint

    work_dir = temp_root / "RasterCrash.bmanga"
    assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
    assert bpy.ops.bmanga.open_page_file(index=0) == {"FINISHED"}
    scene = bpy.context.scene
    work = get_work(bpy.context)
    page = work.pages[0]
    page.detail_loaded = True
    raster_id = "raster_crash_recovery"
    entry = scene.bmanga_raster_layers.add()
    entry.id = raster_id
    entry.title = "Crash Recovery"
    entry.parent_kind = "page"
    entry.parent_key = str(page.id)
    entry.filepath_rel = f"raster/{raster_id}.png"
    entry.image_name = f"BManga_{raster_id}"
    entry.dpi = 72
    image = bpy.data.images.new(
        entry.image_name,
        width=4,
        height=4,
        alpha=True,
    )
    image.pixels[:] = [0.1, 0.2, 0.3, 1.0] * 16
    image.update()
    raster_layer_op.mark_raster_dirty(entry)
    baseline = lifecycle_checkpoint.checkpoint_current(
        bpy.context,
        reason="raster crash baseline",
        force_native=True,
    )
    assert baseline.succeeded, baseline
    return work_dir, Path(bpy.data.filepath), raster_id


def _child_environment(
    work_dir: Path,
    page_blend: Path,
    raster_id: str,
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "BMANGA_CRASH_ROOT": str(ROOT),
            "BMANGA_CRASH_WORK": str(work_dir),
            "BMANGA_CRASH_PAGE": str(page_blend),
            "BMANGA_CRASH_RASTER": raster_id,
        }
    )
    return environment


def _crash_child_source() -> str:
    return r"""
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import bpy

root = Path(os.environ["BMANGA_CRASH_ROOT"])
work_dir = Path(os.environ["BMANGA_CRASH_WORK"])
page_blend = Path(os.environ["BMANGA_CRASH_PAGE"])
raster_id = os.environ["BMANGA_CRASH_RASTER"]
package = "bmanga_raster_crash_writer"
spec = importlib.util.spec_from_file_location(
    package,
    root / "__init__.py",
    submodule_search_locations=[str(root)],
)
module = importlib.util.module_from_spec(spec)
sys.modules[package] = module
assert spec.loader is not None
spec.loader.exec_module(module)
module.register()
assert "FINISHED" in bpy.ops.wm.open_mainfile(
    filepath=str(page_blend),
    load_ui=False,
)
from bmanga_raster_crash_writer.operators import raster_layer_op
from bmanga_raster_crash_writer.utils import handlers

entry = next(
    value
    for value in bpy.context.scene.bmanga_raster_layers
    if str(value.id) == raster_id
)
image = raster_layer_op.ensure_raster_image(
    bpy.context,
    entry,
    create_missing=False,
)
assert image is not None
image.pixels[:] = [0.83, 0.14, 0.27, 1.0] * 16
image.update()
raster_layer_op.mark_raster_dirty(entry)
assert handlers._begin_native_save_guard(str(page_blend)) is True
handlers._prepare_native_save_sidecars()
assert handlers.save_scene_work_to_disk(
    bpy.context,
    reason="crash after raster snapshot seal",
    strict_rasters=True,
    refresh_runtime=False,
)
handlers._mark_native_save_metadata_result(True)
token = handlers._native_save_token
assert token is not None and token.recovery_path is not None
# Blender本体が新世代mainfileを公開した直後を模擬する。save_post前に終了する。
shutil.copy2(token.recovery_path, token.source)
os._exit(79)
"""


def _verify_child_source() -> str:
    return r"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import bpy

root = Path(os.environ["BMANGA_CRASH_ROOT"])
work_dir = Path(os.environ["BMANGA_CRASH_WORK"])
page_blend = Path(os.environ["BMANGA_CRASH_PAGE"])
raster_id = os.environ["BMANGA_CRASH_RASTER"]
package = "bmanga_raster_crash_reader"
spec = importlib.util.spec_from_file_location(
    package,
    root / "__init__.py",
    submodule_search_locations=[str(root)],
)
module = importlib.util.module_from_spec(spec)
sys.modules[package] = module
assert spec.loader is not None
spec.loader.exec_module(module)
module.register()
from bmanga_raster_crash_reader.io import native_save_guard

restored = native_save_guard.recover_pending_native_saves(work_dir)
assert page_blend in restored, restored
assert "FINISHED" in bpy.ops.wm.open_mainfile(
    filepath=str(page_blend),
    load_ui=False,
)
from bmanga_raster_crash_reader.operators import raster_layer_op
from bmanga_raster_crash_reader.utils import lifecycle_checkpoint

entry = next(
    value
    for value in bpy.context.scene.bmanga_raster_layers
    if str(value.id) == raster_id
)
image = raster_layer_op.ensure_raster_image(
    bpy.context,
    entry,
    create_missing=False,
)
assert image is not None
# BlenderのPNG保存経路がImageを8bit量子化する環境を1段階だけ許容する。
assert abs(float(image.pixels[0]) - 0.83) < 5.0e-3, image.pixels[0]
assert bool(entry.get("bmanga_raster_dirty", False))
snapshot_base = work_dir / ".bmanga-save-recovery-v1" / "raster-snapshots"
snapshot_journals = tuple(
    snapshot_base.glob("*/raster-snapshot-journal.json")
)
assert len(snapshot_journals) == 1, snapshot_journals
assert json.loads(
    snapshot_journals[0].read_text(encoding="utf-8")
)["status"] == "hydrated"
retry = lifecycle_checkpoint.checkpoint_current(
    bpy.context,
    reason="retry recovered raster save",
    force_native=True,
)
assert retry.succeeded, retry
assert not bool(entry.get("bmanga_raster_dirty", False))
assert not snapshot_base.exists(), snapshot_base
print("BMANGA_RASTER_CRASH_VERIFY_OK", flush=True)
"""


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon(PACKAGE)
    addon_registered = True
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_raster_crash_"))
    succeeded = False
    try:
        work_dir, page_blend, raster_id = _prepare_baseline(temp_root)
        addon.unregister()
        addon_registered = False
        bpy.ops.wm.read_factory_settings(use_empty=True)
        environment = _child_environment(work_dir, page_blend, raster_id)
        crash_script = temp_root / "crash_writer.py"
        verify_script = temp_root / "crash_reader.py"
        _write_child(crash_script, _crash_child_source())
        _write_child(verify_script, _verify_child_source())

        crashed = _run_child(crash_script, environment)
        assert crashed.returncode == 79, (
            crashed.returncode,
            crashed.stdout,
            crashed.stderr,
        )
        snapshot_base = (
            work_dir / ".bmanga-save-recovery-v1" / "raster-snapshots"
        )
        assert tuple(snapshot_base.glob("*/raster-snapshot-journal.json"))

        verified = _run_child(verify_script, environment)
        assert verified.returncode == 0, (
            verified.returncode,
            verified.stdout,
            verified.stderr,
        )
        assert "BMANGA_RASTER_CRASH_VERIFY_OK" in verified.stdout, (
            verified.stdout,
            verified.stderr,
        )
        succeeded = True
        print(SENTINEL, flush=True)
    finally:
        if addon_registered:
            try:
                addon.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if succeeded:
            shutil.rmtree(temp_root, ignore_errors=False)
        else:
            print(f"FAILED_TEMP_ROOT={temp_root}", flush=True)


if __name__ == "__main__":
    main()
