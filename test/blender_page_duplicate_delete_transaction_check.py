"""Blender実機: ページ複製の実体維持と削除/複製の失敗時復元を確認。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_page_tx_test",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_page_tx_test"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _page_index(work, page_id: str) -> int:
    for index, page in enumerate(work.pages):
        if str(page.id) == page_id:
            return index
    raise AssertionError(f"ページが見つかりません: {page_id}")


def _page(work, page_id: str):
    return work.pages[_page_index(work, page_id)]


def _coma_id(page) -> str:
    if not page.comas:
        raise AssertionError("検証用コマがありません")
    coma = page.comas[0]
    return str(getattr(coma, "coma_id", "") or getattr(coma, "id", "") or "")


def _open_page(page_id: str):
    from bmanga_page_tx_test.core.work import get_work
    from bmanga_page_tx_test.utils import page_file_scene

    work = get_work(bpy.context)
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=_page_index(work, page_id))
    assert result == {"FINISHED"}, (page_id, result)
    assert page_file_scene.is_page_edit_scene(bpy.context.scene)
    return get_work(bpy.context)


def _close_page() -> None:
    result = bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
    assert "FINISHED" in result, result


def _add_gp(parent_key: str) -> str:
    from bmanga_page_tx_test.utils import gp_object_layer, gpencil, layer_object_model

    stable_id = layer_object_model.make_stable_id("gp")
    obj = gp_object_layer.create_layer_gp_object(
        scene=bpy.context.scene,
        bmanga_id=stable_id,
        title="複製確認用の手描き",
        z_index=220,
        parent_kind="coma",
        parent_key=parent_key,
    )
    layer = layer_object_model.content_layer(obj)
    frame = gpencil.ensure_active_frame(layer)
    assert frame is not None and frame.drawing is not None
    assert gpencil.add_stroke_to_drawing(
        frame.drawing,
        [(0.01, 0.02, 0.0), (0.03, 0.04, 0.0), (0.05, 0.02, 0.0)],
    )
    return stable_id


def _add_effect(parent_key: str) -> str:
    from bmanga_page_tx_test.operators import effect_line_op

    obj, layer = effect_line_op._create_effect_layer(
        bpy.context,
        (20.0, 30.0, 50.0, 40.0),
        parent_key=parent_key,
    )
    assert obj is not None and layer is not None
    return str(obj.get("bmanga_id", "") or "")


def _add_balloon_text(page, parent_key: str) -> tuple[str, str]:
    from bmanga_page_tx_test.operators import balloon_op

    balloon = balloon_op._create_balloon_entry(
        bpy.context,
        page,
        shape="ellipse",
        x=24.0,
        y=36.0,
        w=45.0,
        h=28.0,
        parent_kind="coma",
        parent_key=parent_key,
    )
    balloon.id = "page_tx_balloon"
    text = page.texts.add()
    text.id = "page_tx_text"
    text.body = "複製後も残る文字"
    text.x_mm = 30.0
    text.y_mm = 42.0
    text.width_mm = 30.0
    text.height_mm = 20.0
    text.parent_kind = "coma"
    text.parent_key = parent_key
    text.parent_balloon_id = balloon.id
    balloon.text_id = text.id
    return str(balloon.id), str(text.id)


def _set_link_group(page_id: str, ids: dict[str, str]) -> None:
    group = "page_tx_source_group"
    mapping = {
        f"gp:{ids['gp']}": group,
        f"effect:{ids['effect']}": group,
        f"balloon:{page_id}:{ids['balloon']}": group,
        f"text:{page_id}:{ids['text']}": group,
    }
    bpy.context.scene["bmanga_layer_link_groups"] = json.dumps(
        mapping, ensure_ascii=False, separators=(",", ":")
    )


def _create_source_content(page_id: str) -> dict[str, str]:
    work = _open_page(page_id)
    page = _page(work, page_id)
    coma_id = _coma_id(page)
    parent_key = f"{page_id}:{coma_id}"
    ids = {
        "gp": _add_gp(parent_key),
        "effect": _add_effect(parent_key),
    }
    ids["balloon"], ids["text"] = _add_balloon_text(page, parent_key)
    ids["coma"] = coma_id
    _set_link_group(page_id, ids)
    bpy.context.view_layer.update()
    _close_page()
    return ids


def _managed_ids(kind: str, parent_key: str) -> list[str]:
    return sorted(
        str(obj.get("bmanga_id", "") or "")
        for obj in bpy.data.objects
        if str(obj.get("bmanga_kind", "") or "") == kind
        and str(obj.get("bmanga_parent_key", "") or "") == parent_key
        and bool(obj.get("bmanga_managed", False))
    )


def _assert_target_content(
    target_page_id: str,
    source_ids: dict[str, str],
    expected_ids: dict[str, str] | None = None,
) -> dict[str, str]:
    work = _open_page(target_page_id)
    page = _page(work, target_page_id)
    coma_id = _coma_id(page)
    assert coma_id == source_ids["coma"]
    parent_key = f"{target_page_id}:{coma_id}"
    gp_ids = _managed_ids("gp", parent_key)
    effect_ids = _managed_ids("effect", parent_key)
    assert len(gp_ids) == 1, gp_ids
    assert len(effect_ids) == 1, effect_ids
    assert gp_ids[0] != source_ids["gp"]
    assert effect_ids[0] != source_ids["effect"]
    if expected_ids is not None:
        assert gp_ids[0] == expected_ids["gp"]
        assert effect_ids[0] == expected_ids["effect"]
    balloon = next(item for item in page.balloons if str(item.id) == source_ids["balloon"])
    text = next(item for item in page.texts if str(item.id) == source_ids["text"])
    assert str(balloon.parent_key) == parent_key
    assert str(text.parent_key) == parent_key
    assert str(balloon.text_id) == source_ids["text"]
    assert str(text.parent_balloon_id) == source_ids["balloon"]
    raw_links = str(bpy.context.scene.get("bmanga_layer_link_groups", "") or "")
    links = json.loads(raw_links)
    target_uids = {
        f"gp:{gp_ids[0]}",
        f"effect:{effect_ids[0]}",
        f"balloon:{target_page_id}:{source_ids['balloon']}",
        f"text:{target_page_id}:{source_ids['text']}",
    }
    groups = {str(links.get(uid, "")) for uid in target_uids}
    assert len(groups) == 1 and "" not in groups, (links, target_uids)
    result = {"gp": gp_ids[0], "effect": effect_ids[0]}
    _close_page()
    return result


def _page_dir(work_dir: Path, page_id: str) -> Path:
    project = json.loads((work_dir / "project.json").read_text(encoding="utf-8"))
    for uid, summary in project["pages"].items():
        if summary["displayId"] == page_id:
            return work_dir / "pages" / uid
    raise AssertionError(f"page UIDが見つかりません: {page_id}")


def _file_bytes(work_dir: Path) -> tuple[bytes, tuple[bytes, ...]]:
    page_jsons = tuple(
        path.read_bytes()
        for path in sorted((work_dir / "pages").glob("page_*/page.json"))
    )
    return (work_dir.joinpath("project.json").read_bytes(), page_jsons)


def _tree_digest(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


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


def _page_ids(work) -> list[str]:
    return [str(page.id) for page in work.pages]


def _fail_work_save_once(work_dir: Path, callback):
    from bmanga_page_tx_test.io import domain_runtime

    repository = domain_runtime.repository_for(work_dir)
    original = repository.checkpoint

    def fail(*_args, **_kwargs):
        raise RuntimeError("PAGE_TX_EXPECTED_SAVE_FAILURE")

    repository.checkpoint = fail
    try:
        callback()
    finally:
        repository.checkpoint = original


def _expect_injected_save_failure(operator) -> None:
    try:
        result = operator()
    except RuntimeError as exc:
        assert "PAGE_TX_EXPECTED_SAVE_FAILURE" in str(exc), exc
        return
    assert result == {"CANCELLED"}, result


def _setup_source(temp_root: Path) -> tuple[Path, str, dict[str, str], str]:
    from bmanga_page_tx_test.core.work import get_work

    work_dir = temp_root / "PageTransaction.bmanga"
    assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
    work = get_work(bpy.context)
    work.work_info.page_number_end = 2
    bpy.context.view_layer.update()
    source_page_id = str(work.pages[0].id)
    source_ids = _create_source_content(source_page_id)
    source_blend = _page_dir(work_dir, source_page_id) / "page.blend"
    assert source_blend.is_file()
    digest = hashlib.sha256(source_blend.read_bytes()).hexdigest()
    return work_dir, source_page_id, source_ids, digest


def _duplicate_and_verify(
    work_dir: Path, source_page_id: str, source_ids: dict[str, str], source_hash: str
) -> tuple[str, dict[str, str]]:
    from bmanga_page_tx_test.core.work import get_work

    work = get_work(bpy.context)
    work.active_page_index = _page_index(work, source_page_id)
    assert bpy.ops.bmanga.page_duplicate("EXEC_DEFAULT") == {"FINISHED"}
    work = get_work(bpy.context)
    target_page_id = str(work.pages[work.active_page_index].id)
    assert target_page_id != source_page_id
    assert (_page_dir(work_dir, target_page_id) / "page.blend").is_file()
    target_ids = _assert_target_content(target_page_id, source_ids)
    _assert_target_content(target_page_id, source_ids, target_ids)
    source_blend = _page_dir(work_dir, source_page_id) / "page.blend"
    assert hashlib.sha256(source_blend.read_bytes()).hexdigest() == source_hash
    return target_page_id, target_ids


def _run_page_process(
    temp_root: Path,
    work_dir: Path,
    *,
    operation: str,
    crash_after_native: bool,
) -> subprocess.CompletedProcess[str]:
    script = temp_root / f"page_{operation}_{int(crash_after_native)}.py"
    script.write_text(
        """
import importlib.util
import os
from pathlib import Path
import sys
import bpy

root = Path(os.environ["BMANGA_PAGE_TX_ROOT"])
work_dir = Path(os.environ["BMANGA_PAGE_TX_WORK"])
operation = os.environ["BMANGA_PAGE_TX_OPERATION"]
crash = os.environ["BMANGA_PAGE_TX_CRASH"] == "1"
name = "bmanga_page_tx_test"
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
if crash:
    from bmanga_page_tx_test.io import native_tree_transaction

    method_name = (
        "apply_additions" if operation == "duplicate" else "apply_removals"
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

work = bpy.context.scene.bmanga_work
if operation == "duplicate":
    work.active_page_index = next(
        index for index, page in enumerate(work.pages)
        if str(page.id) == "p0001"
    )
    result = bpy.ops.bmanga.page_duplicate("EXEC_DEFAULT")
else:
    work.active_page_index = next(
        index for index, page in enumerate(work.pages)
        if str(page.id) != "p0001"
    )
    result = bpy.ops.bmanga.page_remove("EXEC_DEFAULT")
if result != {"FINISHED"}:
    print("PAGE_PROCESS_RESULT=" + repr(result), flush=True)
    os._exit(77)
bpy.ops.wm.save_as_mainfile(
    filepath=str(work_dir / "work.blend"),
    check_existing=False,
)
os._exit(0)
""".lstrip(),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.update(
        {
            "BMANGA_PAGE_TX_ROOT": str(ROOT),
            "BMANGA_PAGE_TX_WORK": str(work_dir),
            "BMANGA_PAGE_TX_OPERATION": operation,
            "BMANGA_PAGE_TX_CRASH": "1" if crash_after_native else "0",
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
        timeout=240,
        check=False,
    )


def _recover_process_crash(work_dir: Path) -> None:
    from bmanga_page_tx_test.bmanga_core.domain_repository import (
        ProjectRepository,
    )
    from bmanga_page_tx_test.io import native_tree_transaction

    repository = ProjectRepository(work_dir)
    repository.recover()
    assert native_tree_transaction.recover_pending_native_transactions(
        work_dir,
        repository=repository,
    ) == 1
    assert not tuple((work_dir / "journal").glob("native-op-*.json"))


def _exercise_process_crash_recovery(
    temp_root: Path,
    work_dir: Path,
) -> None:
    assert "FINISHED" in bpy.ops.wm.save_as_mainfile(
        filepath=str(work_dir / "work.blend"),
        check_existing=False,
    )
    initial_project = json.loads(
        (work_dir / "project.json").read_text(encoding="utf-8")
    )
    initial_page_count = len(initial_project["pages"])
    before_duplicate = _persistent_digest(work_dir)
    crashed = _run_page_process(
        temp_root,
        work_dir,
        operation="duplicate",
        crash_after_native=True,
    )
    assert crashed.returncode == 79, crashed.stdout + crashed.stderr
    assert tuple((work_dir / "journal").glob("native-op-*.json"))
    _recover_process_crash(work_dir)
    assert _persistent_digest(work_dir) == before_duplicate

    retried = _run_page_process(
        temp_root,
        work_dir,
        operation="duplicate",
        crash_after_native=False,
    )
    assert retried.returncode == 0, retried.stdout + retried.stderr
    project = json.loads((work_dir / "project.json").read_text(encoding="utf-8"))
    assert len(project["pages"]) == initial_page_count + 1

    before_delete = _persistent_digest(work_dir)
    crashed = _run_page_process(
        temp_root,
        work_dir,
        operation="delete",
        crash_after_native=True,
    )
    assert crashed.returncode == 79, crashed.stdout + crashed.stderr
    assert tuple((work_dir / "journal").glob("native-op-*.json"))
    _recover_process_crash(work_dir)
    assert _persistent_digest(work_dir) == before_delete

    retried = _run_page_process(
        temp_root,
        work_dir,
        operation="delete",
        crash_after_native=False,
    )
    assert retried.returncode == 0, retried.stdout + retried.stderr
    project = json.loads((work_dir / "project.json").read_text(encoding="utf-8"))
    assert len(project["pages"]) == initial_page_count


def _assert_duplicate_rollback(
    work_dir: Path, source_page_id: str, source_hash: str
) -> None:
    from bmanga_page_tx_test.core.work import get_work

    work = get_work(bpy.context)
    work.active_page_index = _page_index(work, source_page_id)
    before_ids = _page_ids(work)
    before_json = _file_bytes(work_dir)
    before_dirs = sorted(path.name for path in (work_dir / "pages").iterdir() if path.is_dir())
    _fail_work_save_once(
        work_dir,
        lambda: _expect_injected_save_failure(
            lambda: bpy.ops.bmanga.page_duplicate("EXEC_DEFAULT")
        )
    )
    work = get_work(bpy.context)
    assert _page_ids(work) == before_ids
    assert _file_bytes(work_dir) == before_json
    assert sorted(path.name for path in (work_dir / "pages").iterdir() if path.is_dir()) == before_dirs
    source_blend = _page_dir(work_dir, source_page_id) / "page.blend"
    assert hashlib.sha256(source_blend.read_bytes()).hexdigest() == source_hash


def _assert_delete_rollback(
    work_dir: Path, target_page_id: str, source_ids: dict[str, str], target_ids: dict[str, str]
) -> None:
    from bmanga_page_tx_test.core.work import get_work

    work = get_work(bpy.context)
    work.active_page_index = _page_index(work, target_page_id)
    target_dir = _page_dir(work_dir, target_page_id)
    before_ids = _page_ids(work)
    before_json = _file_bytes(work_dir)
    before_tree = _tree_digest(target_dir)
    _fail_work_save_once(
        work_dir,
        lambda: _expect_injected_save_failure(
            lambda: bpy.ops.bmanga.page_remove("EXEC_DEFAULT")
        )
    )
    work = get_work(bpy.context)
    assert _page_ids(work) == before_ids
    assert _file_bytes(work_dir) == before_json
    assert target_dir.is_dir() and _tree_digest(target_dir) == before_tree
    _assert_target_content(target_page_id, source_ids, target_ids)


def _delete_and_verify(
    work_dir: Path, source_page_id: str, target_page_id: str, source_hash: str
) -> None:
    from bmanga_page_tx_test.core.work import get_work

    work = get_work(bpy.context)
    work.active_page_index = _page_index(work, target_page_id)
    assert bpy.ops.bmanga.page_remove("EXEC_DEFAULT") == {"FINISHED"}
    work = get_work(bpy.context)
    assert target_page_id not in _page_ids(work)
    assert not any(
        summary["displayId"] == target_page_id
        for summary in json.loads(
            (work_dir / "project.json").read_text(encoding="utf-8")
        )["pages"].values()
    )
    assert source_page_id in _page_ids(work)
    source_blend = _page_dir(work_dir, source_page_id) / "page.blend"
    assert hashlib.sha256(source_blend.read_bytes()).hexdigest() == source_hash


def main() -> None:
    module = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_tx_"))
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        crash_work, _source, _ids, _digest = _setup_source(
            temp_root / "process_crash"
        )
        _exercise_process_crash_recovery(temp_root, crash_work)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        work_dir, source_page_id, source_ids, source_hash = _setup_source(temp_root)
        target_page_id, target_ids = _duplicate_and_verify(
            work_dir, source_page_id, source_ids, source_hash
        )
        _assert_duplicate_rollback(work_dir, source_page_id, source_hash)
        _assert_delete_rollback(work_dir, target_page_id, source_ids, target_ids)
        _delete_and_verify(work_dir, source_page_id, target_page_id, source_hash)
        print("BMANGA_PAGE_DUPLICATE_DELETE_TRANSACTION_OK")
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
