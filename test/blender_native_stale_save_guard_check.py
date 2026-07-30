"""Blender 5.2実機: Domain作品を古い画面のネイティブ保存から保護する。"""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_native_stale_save_guard_test"
PROJECT_UID = "project_11111111111111111111111111111111"
PAGE_UIDS = (
    "page_11111111111111111111111111111111",
    "page_22222222222222222222222222222222",
)
ROOT_UIDS = (
    "node_11111111111111111111111111111111",
    "node_22222222222222222222222222222222",
)


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


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _save_probe_blend(path: Path, label: str) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene["native_guard_payload"] = label
    path.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)
    assert "FINISHED" in result


def _page_summary(index: int) -> dict:
    return {
        "uid": PAGE_UIDS[index],
        "displayId": f"p{index + 1:04d}",
        "displayNumber": index + 1,
        "title": f"{index + 1}ページ",
        "spread": False,
        "sourcePageUids": [],
        "settings": {},
    }


def _page_document(index: int) -> dict:
    root_uid = ROOT_UIDS[index]
    return {
        "schema": "bmanga.page",
        "schemaVersion": 1,
        "projectUid": PROJECT_UID,
        "pageUid": PAGE_UIDS[index],
        "revision": 0,
        "settings": {},
        "tree": {
            "rootUid": root_uid,
            "nodes": {
                root_uid: {
                    "uid": root_uid,
                    "kind": "page",
                    "displayId": f"p{index + 1:04d}",
                    "title": f"{index + 1}ページ",
                    "settings": {},
                    "nativeUid": "",
                }
            },
            "children": {root_uid: []},
        },
        "links": {},
    }


def _create_work(root: Path) -> tuple[Path, tuple[Path, Path], Path]:
    work = root / "NativeSaveGuard.bmanga"
    external = root / "external-current.blend"
    _save_probe_blend(external, "EXTERNAL-CURRENT")
    pages = tuple(
        work / "pages" / page_uid / "page.blend" for page_uid in PAGE_UIDS
    )
    _save_probe_blend(work / "work.blend", "WORK")
    for index, page in enumerate(pages, 1):
        _save_probe_blend(page, f"OPEN-BASELINE-{index}")
        _write_json(page.parent / "page.json", _page_document(index - 1))
    summaries = [_page_summary(0), _page_summary(1)]
    _write_json(
        work / "project.json",
        {
            "schema": "bmanga.project",
            "schemaVersion": 1,
            "projectUid": PROJECT_UID,
            "revision": 0,
            "settings": {},
            "pageOrder": list(PAGE_UIDS),
            "pages": {summary["uid"]: summary for summary in summaries},
        },
    )
    return work, pages, external


def _open_page(page: Path, label: str) -> None:
    result = bpy.ops.wm.open_mainfile(filepath=str(page), load_ui=False)
    assert "FINISHED" in result
    bpy.context.scene["native_guard_payload"] = label


def _make_open_scene_stale(page: Path, external: Path, label: str) -> str:
    _open_page(page, label)
    shutil.copy2(external, page)
    return _sha256(page)


def _non_project_save_does_not_arm_guard(root: Path) -> None:
    handlers = importlib.import_module(f"{MODULE_NAME}.utils.handlers")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    generic = root / "ordinary.blend"
    result = bpy.ops.wm.save_as_mainfile(filepath=str(generic), compress=False)
    assert "FINISHED" in result
    assert generic.is_file()
    assert handlers._native_save_token is None


def _project_save_as_outside_does_not_arm_wrong_source(
    root: Path,
    work: Path,
    page: Path,
) -> None:
    handlers = importlib.import_module(f"{MODULE_NAME}.utils.handlers")
    guard = importlib.import_module(f"{MODULE_NAME}.io.native_save_guard")
    _open_page(page, "SAVE-AS-OUTSIDE")
    original = _sha256(page)
    outside = root / "project-save-as-copy.blend"
    result = bpy.ops.wm.save_as_mainfile(filepath=str(outside), compress=False)
    assert "FINISHED" in result
    assert outside.is_file()
    assert page.is_file() and _sha256(page) == original
    assert handlers._native_save_token is None
    assert not guard.find_pending_native_save_journals(work)


def _normal_save_is_committed(page: Path) -> None:
    _open_page(page, "NORMAL-COMMIT")
    original = _sha256(page)
    result = bpy.ops.wm.save_as_mainfile(filepath=str(page), compress=False)
    assert "FINISHED" in result
    assert _sha256(page) != original, "競合のない通常保存が確定しませんでした"


def _conflicting_save_is_restored(page: Path, external: Path) -> None:
    current = _make_open_scene_stale(page, external, "STALE-NORMAL")
    result = bpy.ops.wm.save_as_mainfile(filepath=str(page), compress=False)
    assert "FINISHED" in result
    assert _sha256(page) == current, "外部更新後の古い画面が保存されました"


def _crashed_save_is_restored_on_load(page: Path, external: Path) -> None:
    handlers = importlib.import_module(f"{MODULE_NAME}.utils.handlers")
    guard = importlib.import_module(f"{MODULE_NAME}.io.native_save_guard")
    current = _make_open_scene_stale(page, external, "STALE-CRASH")
    save_post = handlers._bmanga_on_save_post
    bpy.app.handlers.save_post.remove(save_post)
    try:
        result = bpy.ops.wm.save_as_mainfile(filepath=str(page), compress=False)
        assert "FINISHED" in result
        assert _sha256(page) != current, "save_post除外の失敗注入が成立しません"
        token = handlers._native_save_token
        assert token is not None and token.requires_restore
        guard._release(token)
        handlers._native_save_token = None
    finally:
        if save_post not in bpy.app.handlers.save_post:
            bpy.app.handlers.save_post.append(save_post)

    result = bpy.ops.wm.open_mainfile(filepath=str(page), load_ui=False)
    assert "FINISHED" in result
    assert _sha256(page) == current, "次回load_postで外部更新版を復旧できません"


def _atomic_raster_failure_preserves_original(page: Path) -> None:
    raster_module = importlib.import_module(f"{MODULE_NAME}.operators.raster_layer_op")
    baseline = importlib.import_module(f"{MODULE_NAME}.io.save_baseline")
    _open_page(page, "RASTER-ATOMIC")
    scene = bpy.context.scene
    entry = scene.bmanga_raster_layers.add()
    entry.id = "atomic_failure_probe"
    entry.image_name = "AtomicFailureProbeImage"
    entry.filepath_rel = "raster/atomic_failure_probe.png"
    image = bpy.data.images.new(entry.image_name, width=2, height=2, alpha=True)
    image.pixels[:] = [0.25, 0.5, 0.75, 1.0] * 4
    image.update()
    entry["bmanga_raster_dirty"] = True
    png_path = Path(scene.bmanga_work.work_dir) / entry.filepath_rel
    png_path.parent.mkdir(parents=True, exist_ok=True)
    original = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHnOcQAAAAABJRU5ErkJggg=="
    )
    png_path.write_bytes(original)
    baseline.record_successful_write(png_path)

    validate = raster_module._validate_png_file
    raster_module._validate_png_file = lambda _path: (_ for _ in ()).throw(
        OSError("injected validation failure")
    )
    try:
        try:
            raster_module.save_raster_png(bpy.context, entry, force=False)
        except OSError as exc:
            assert "injected" in str(exc)
        else:
            raise AssertionError("PNG検証失敗が送出されませんでした")
    finally:
        raster_module._validate_png_file = validate
    assert png_path.read_bytes() == original
    assert bool(entry.get("bmanga_raster_dirty", False))


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="bmanga_native_save_guard_"))
    addon = None
    succeeded = False
    try:
        work, pages, external = _create_work(root)
        addon = _load_addon()
        _non_project_save_does_not_arm_guard(root)
        _project_save_as_outside_does_not_arm_wrong_source(root, work, pages[0])
        _normal_save_is_committed(pages[0])
        _conflicting_save_is_restored(pages[0], external)
        _atomic_raster_failure_preserves_original(pages[0])
        _crashed_save_is_restored_on_load(pages[1], external)
        succeeded = True
        print("BLENDER_NATIVE_STALE_SAVE_GUARD_OK")
    finally:
        if addon is not None:
            addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if succeeded:
            shutil.rmtree(root, ignore_errors=False)
        else:
            print(f"FAILED_TEMP_ROOT={root}")


if __name__ == "__main__":
    main()
