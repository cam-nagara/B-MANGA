"""Blender実機: 同一ページの全コマNative操作を強制終了後に復旧・再試行する。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_coma_native_crash_test"
OPERATIONS = ("duplicate", "delete", "merge", "template", "split", "knife")
ADDITION_OPERATIONS = {"duplicate", "split", "knife"}


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _persistent_digest(work_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(work_dir).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(work_dir.rglob("*"))
        if path.is_file()
        and (
            path.name == "project.json"
            or path.relative_to(work_dir).parts[0] in {"pages", "assets"}
        )
    }


def _prepare_work(temp_root: Path, operation: str) -> Path:
    from bmanga_coma_native_crash_test.io import (
        domain_projection,
        domain_projection_ids,
        page_io,
    )
    from bmanga_coma_native_crash_test.io.save_baseline import (
        record_successful_tree_change,
    )
    from bmanga_coma_native_crash_test.operators import coma_op
    from bmanga_coma_native_crash_test.utils import paths

    work_dir = temp_root / f"ComaNative-{operation}.bmanga"
    assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    if not bool(page.detail_loaded):
        page_io.load_page_json(work_dir, page)
    while len(page.comas) < 2:
        coma_op.create_basic_frame_coma(work, page, work_dir)
    page.comas[0].rect_x_mm = 15.0
    page.comas[0].rect_y_mm = 20.0
    page.comas[0].rect_width_mm = 60.0
    page.comas[0].rect_height_mm = 80.0
    page.comas[1].rect_x_mm = 85.0
    page.comas[1].rect_y_mm = 20.0
    page.comas[1].rect_width_mm = 60.0
    page.comas[1].rect_height_mm = 80.0
    page.active_coma_index = 0
    work.active_page_index = 0
    page_io.save_page_json(work_dir, page)

    project_uid = domain_projection.ensure_project_uid(work)
    page_uid = domain_projection.ensure_page_uid(page, project_uid)
    native_dirs = []
    for entry in page.comas:
        coma_uid = domain_projection_ids.ensure_coma_uid(entry, page_uid)
        native_dir = (
            work_dir
            / paths.PAGES_DIR_NAME
            / page_uid
            / paths.COMAS_DIR_NAME
            / coma_uid
        )
        native_dir.mkdir(parents=True, exist_ok=True)
        (native_dir / "scene.blend").write_bytes(
            f"native:{entry.coma_id}".encode("ascii")
        )
        native_dirs.append(native_dir)
    record_successful_tree_change(*native_dirs)
    assert "FINISHED" in bpy.ops.wm.save_as_mainfile(
        filepath=str(work_dir / "work.blend"),
        check_existing=False,
    )
    return work_dir


def _child_source() -> str:
    return """
import importlib.util
import os
from pathlib import Path
import sys
import bpy

root = Path(os.environ["BMANGA_COMA_NATIVE_ROOT"])
work_dir = Path(os.environ["BMANGA_COMA_NATIVE_WORK"])
operation = os.environ["BMANGA_COMA_NATIVE_OPERATION"]
crash = os.environ["BMANGA_COMA_NATIVE_CRASH"] == "1"
name = "bmanga_coma_native_crash_test"
spec = importlib.util.spec_from_file_location(
    name,
    root / "__init__.py",
    submodule_search_locations=[str(root)],
)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
assert spec.loader is not None
spec.loader.exec_module(module)
module.register()
assert "FINISHED" in bpy.ops.wm.open_mainfile(
    filepath=str(work_dir / "work.blend"),
    load_ui=False,
)
from bmanga_coma_native_crash_test.io import page_io
from bmanga_coma_native_crash_test.operators import (
    coma_knife_cut_op,
    coma_op,
    coma_split_op,
)

work = bpy.context.scene.bmanga_work
page = work.pages[0]
if not bool(page.detail_loaded):
    page_io.load_page_json(work_dir, page)
work.active_page_index = 0
page.active_coma_index = 0
if crash:
    from bmanga_coma_native_crash_test.io import native_tree_transaction

    method_name = (
        "apply_additions"
        if operation in {"duplicate", "split", "knife"}
        else "apply_removals"
    )
    original = getattr(
        native_tree_transaction.NativeTreeTransaction,
        method_name,
    )

    def terminate_after_native(self):
        original(self)
        os._exit(79)

    setattr(
        native_tree_transaction.NativeTreeTransaction,
        method_name,
        terminate_after_native,
    )

if operation == "duplicate":
    result = bpy.ops.bmanga.coma_duplicate("EXEC_DEFAULT")
elif operation == "delete":
    result = bpy.ops.bmanga.coma_remove("EXEC_DEFAULT")
elif operation == "merge":
    refs = [
        (0, page, index, page.comas[index])
        for index in range(2)
    ]
    coma_op.object_selection.selected_coma_refs = lambda _context: refs
    result = bpy.ops.bmanga.coma_merge_selected(
        "EXEC_DEFAULT",
        border_mode="merge",
    )
elif operation == "template":
    result = bpy.ops.bmanga.coma_split_template(
        "EXEC_DEFAULT",
        rows=1,
        cols=2,
        clear_existing=True,
        target_page_id=str(page.id),
        target_coma_index=0,
    )
else:
    target = page.comas[0]
    cut_x = float(target.rect_x_mm) + float(target.rect_width_mm) * 0.5
    point_a = (cut_x, float(target.rect_y_mm) - 5.0)
    point_b = (
        cut_x,
        float(target.rect_y_mm) + float(target.rect_height_mm) + 5.0,
    )
    if operation == "split":
        result = coma_split_op._do_split(
            bpy.context,
            work,
            page,
            0,
            work_dir,
            point_a,
            point_b,
        )
    else:
        result = coma_knife_cut_op._apply_cut_to_coma(
            work,
            page,
            0,
            work_dir,
            point_a,
            point_b,
        )
if result not in (True, {"FINISHED"}):
    print("COMA_NATIVE_RESULT=" + repr(result), flush=True)
    os._exit(77)
bpy.ops.wm.save_as_mainfile(
    filepath=str(work_dir / "work.blend"),
    check_existing=False,
)
os._exit(0)
""".lstrip()


def _run_child(
    temp_root: Path,
    work_dir: Path,
    operation: str,
    *,
    crash: bool,
) -> subprocess.CompletedProcess[str]:
    script = temp_root / f"coma_{operation}_{int(crash)}.py"
    script.write_text(_child_source(), encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        {
            "BMANGA_COMA_NATIVE_ROOT": str(ROOT),
            "BMANGA_COMA_NATIVE_WORK": str(work_dir),
            "BMANGA_COMA_NATIVE_OPERATION": operation,
            "BMANGA_COMA_NATIVE_CRASH": "1" if crash else "0",
        }
    )
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
        timeout=300,
        check=False,
    )


def _coma_count(work_dir: Path) -> int:
    from bmanga_coma_native_crash_test.bmanga_core.domain_repository import (
        ProjectRepository,
    )

    repository = ProjectRepository(work_dir)
    project = repository.load_project()
    page = repository.load_page(project.pages[0].uid)
    return sum(node.kind == "coma" for node in page.nodes.values())


def _exercise_operation(
    temp_root: Path,
    operation: str,
) -> None:
    from bmanga_coma_native_crash_test.bmanga_core.domain_repository import (
        ProjectRepository,
    )
    from bmanga_coma_native_crash_test.io import native_tree_transaction

    bpy.ops.wm.read_factory_settings(use_empty=True)
    work_dir = _prepare_work(temp_root / operation, operation)
    before = _persistent_digest(work_dir)
    crashed = _run_child(
        temp_root,
        work_dir,
        operation,
        crash=True,
    )
    assert crashed.returncode == 79, crashed.stdout + crashed.stderr
    assert tuple((work_dir / "journal").glob("native-op-*.json"))
    repository = ProjectRepository(work_dir)
    repository.recover()
    assert native_tree_transaction.recover_pending_native_transactions(
        work_dir,
        repository=repository,
    ) == 1
    assert _persistent_digest(work_dir) == before
    assert not tuple((work_dir / "journal").glob("native-op-*.json"))
    assert not tuple((work_dir / "journal").glob(".coma-operation-*"))

    retried = _run_child(
        temp_root,
        work_dir,
        operation,
        crash=False,
    )
    assert retried.returncode == 0, retried.stdout + retried.stderr
    expected = 1 if operation in {"delete", "merge"} else 3
    assert _coma_count(work_dir) == expected, operation
    assert not tuple((work_dir / "journal").glob("native-op-*.json"))


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_native_crash_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        for operation in OPERATIONS:
            _exercise_operation(temp_root, operation)
        print("BMANGA_COMA_NATIVE_TRANSACTION_CRASH_OK", flush=True)
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    import traceback

    try:
        main()
        os._exit(0)
    except Exception:
        traceback.print_exc()
        os._exit(1)
