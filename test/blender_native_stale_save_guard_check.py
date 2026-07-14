"""Blender 5.1実機: 旧セッションのネイティブ保存から現行pageを保護する。"""

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
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_page(path: Path, label: str, version: int) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene["native_guard_payload"] = label
    scene["bmanga_detail_data_version"] = version
    path.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)
    assert "FINISHED" in result


def _create_work(root: Path) -> tuple[Path, tuple[Path, Path]]:
    work = root / "NativeSaveGuard.bmanga"
    pages = (work / "p0001" / "page.blend", work / "p0002" / "page.blend")
    for index, page in enumerate(pages, 1):
        _create_page(page, f"CURRENT-{index}", 1)
    _write_json(
        work / "work.json",
        {"schemaVersion": 9, "detailDataVersion": 1, "title": "保存保護"},
    )
    _write_json(
        work / "pages.json",
        {
            "schemaVersion": 2,
            "pages": [
                {"id": "p0001", "title": "1ページ", "dirRel": "p0001"},
                {"id": "p0002", "title": "2ページ", "dirRel": "p0002"},
            ],
        },
    )
    return work, pages


def _create_legacy_work(root: Path) -> tuple[Path, Path]:
    work = root / "LegacyNativeSave.bmanga"
    page = work / "p0001" / "page.blend"
    _create_page(page, "LEGACY-ORIGINAL", 0)
    _write_json(work / "work.json", {"schemaVersion": 9, "detailDataVersion": 0})
    _write_json(
        work / "pages.json",
        {
            "schemaVersion": 2,
            "pages": [{"id": "p0001", "title": "旧ページ", "dirRel": "p0001"}],
        },
    )
    return work, page


def _make_open_scene_stale(page: Path, label: str) -> None:
    result = bpy.ops.wm.open_mainfile(filepath=str(page), load_ui=False)
    assert "FINISHED" in result
    scene = bpy.context.scene
    scene["native_guard_payload"] = label
    scene["bmanga_detail_data_version"] = 0


def _non_project_save_does_not_arm_guard(root: Path) -> None:
    """アドオン登録中の通常blend保存へ作品用保護を持ち込まない。"""

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
    """作品を別名保存しても、切替前のpageをトランザクション対象にしない。"""

    handlers = importlib.import_module(f"{MODULE_NAME}.utils.handlers")
    guard = importlib.import_module(
        f"{MODULE_NAME}.io.project_content_native_save_guard"
    )
    result = bpy.ops.wm.open_mainfile(filepath=str(page), load_ui=False)
    assert "FINISHED" in result
    original = _sha256(page)
    outside = root / "project-save-as-copy.blend"
    bpy.context.scene["native_guard_payload"] = "SAVE-AS-OUTSIDE"
    result = bpy.ops.wm.save_as_mainfile(filepath=str(outside), compress=False)
    assert "FINISHED" in result
    assert outside.is_file(), "別名保存先が作成されていません"
    assert page.is_file() and _sha256(page) == original, "元のpageが変更されました"
    assert handlers._native_save_token is None
    assert not guard.find_pending_native_save_journals(work)


def _normal_save_is_restored(page: Path) -> None:
    original = _sha256(page)
    _make_open_scene_stale(page, "STALE-NORMAL")
    result = bpy.ops.wm.save_as_mainfile(filepath=str(page), compress=False)
    assert "FINISHED" in result
    assert _sha256(page) == original, "save_post後に現行pageが復元されていません"


def _crashed_save_is_restored_on_load(page: Path) -> None:
    handlers = importlib.import_module(f"{MODULE_NAME}.utils.handlers")
    guard = importlib.import_module(
        f"{MODULE_NAME}.io.project_content_native_save_guard"
    )
    original = _sha256(page)
    _make_open_scene_stale(page, "STALE-CRASH")
    save_post = handlers._bmanga_on_save_post
    bpy.app.handlers.save_post.remove(save_post)
    try:
        result = bpy.ops.wm.save_as_mainfile(filepath=str(page), compress=False)
        assert "FINISHED" in result
        assert _sha256(page) != original, "save_postを外した失敗注入が成立していません"
        token = handlers._native_save_token
        assert token is not None and token.requires_restore
        # プロセス強制終了ならOSが解放する部分だけを模擬し、復元は行わない。
        guard._release(token)
        handlers._native_save_token = None
    finally:
        if save_post not in bpy.app.handlers.save_post:
            bpy.app.handlers.save_post.append(save_post)

    result = bpy.ops.wm.open_mainfile(filepath=str(page), load_ui=False)
    assert "FINISHED" in result
    assert _sha256(page) == original, "次回load_postで現行pageが復旧されていません"


def _previous_token_is_rearmed_for_the_next_save(page: Path) -> None:
    handlers = importlib.import_module(f"{MODULE_NAME}.utils.handlers")
    original = _sha256(page)
    _make_open_scene_stale(page, "STALE-FIRST-SAVE")
    save_post = handlers._bmanga_on_save_post
    bpy.app.handlers.save_post.remove(save_post)
    try:
        result = bpy.ops.wm.save_as_mainfile(filepath=str(page), compress=False)
        assert "FINISHED" in result
        assert handlers._native_save_token is not None
    finally:
        if save_post not in bpy.app.handlers.save_post:
            bpy.app.handlers.save_post.append(save_post)

    # 前回tokenをsave_preで復旧しても、この2回目の本体保存自体は止まらない。
    # 今回用guardが再armされていなければ、ここで古い画面が上書きしてしまう。
    bpy.context.scene["native_guard_payload"] = "STALE-SECOND-SAVE"
    result = bpy.ops.wm.save_as_mainfile(filepath=str(page), compress=False)
    assert "FINISHED" in result
    assert _sha256(page) == original, "前回token復旧直後の再保存が保護されていません"


def _atomic_raster_failure_preserves_original(page: Path) -> None:
    raster_module = importlib.import_module(f"{MODULE_NAME}.operators.raster_layer_op")
    baseline = importlib.import_module(
        f"{MODULE_NAME}.io.project_content_save_baseline"
    )
    result = bpy.ops.wm.open_mainfile(filepath=str(page), load_ui=False)
    assert "FINISHED" in result
    scene = bpy.context.scene
    entry = scene.bmanga_raster_layers.add()
    entry.id = "atomic_failure_probe"
    entry.image_name = "AtomicFailureProbeImage"
    entry.filepath_rel = "raster/atomic_failure_probe.png"
    image = bpy.data.images.new(entry.image_name, width=2, height=2, alpha=True)
    image.pixels[:] = [0.25, 0.5, 0.75, 1.0] * 4
    image.update()
    entry["bmanga_raster_dirty"] = True
    work_dir = Path(scene.bmanga_work.work_dir)
    png_path = work_dir / entry.filepath_rel
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
    assert png_path.read_bytes() == original, "失敗時に既存PNGが変化しました"
    assert bool(entry.get("bmanga_raster_dirty", False)), "失敗後にdirtyが消えました"


def _legacy_save_policy_is_correct(work: Path, page: Path) -> None:
    _make_open_scene_stale(page, "LEGACY-UNSAVED-EDIT")
    before = _sha256(page)
    result = bpy.ops.wm.save_as_mainfile(filepath=str(page), compress=False)
    assert "FINISHED" in result
    allowed = _sha256(page)
    assert allowed != before, "旧版0同士の未保存内容をCtrl+Sで残せません"

    # 未完了トランザクションがある場合だけ、同じ版0でも保存結果を復元する。
    journal = (
        work.parent
        / f".{work.name}.detail-data-migration-v1"
        / "interrupted-probe"
        / "migration-journal.json"
    )
    _write_json(journal, {"status": "interrupted"})
    bpy.context.scene["native_guard_payload"] = "MUST-NOT-OVERWRITE"
    bpy.context.scene["bmanga_detail_data_version"] = 0
    result = bpy.ops.wm.save_as_mainfile(filepath=str(page), compress=False)
    assert "FINISHED" in result
    assert _sha256(page) == allowed, "未完了移行中のCtrl+Sがpageを変更しました"


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="bmanga_native_save_guard_", dir=r"C:\tmp"))
    addon = None
    succeeded = False
    try:
        work, pages = _create_work(root)
        legacy_work, legacy_page = _create_legacy_work(root)
        addon = _load_addon()
        _non_project_save_does_not_arm_guard(root)
        _project_save_as_outside_does_not_arm_wrong_source(root, work, pages[0])
        _legacy_save_policy_is_correct(legacy_work, legacy_page)
        _normal_save_is_restored(pages[0])
        _atomic_raster_failure_preserves_original(pages[0])
        _crashed_save_is_restored_on_load(pages[1])
        _previous_token_is_rearmed_for_the_next_save(pages[0])
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
