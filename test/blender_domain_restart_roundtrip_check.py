"""Blender 5.2を別processで再起動し、新Domain作品の完全一致を検証する。"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback
import uuid

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_domain_restart_test"
STAGE_ENV = "BMANGA_DOMAIN_RESTART_STAGE"
WORK_ENV = "BMANGA_DOMAIN_RESTART_WORK"
EXPECTED_ENV = "BMANGA_DOMAIN_RESTART_EXPECTED"
CHILD_SENTINEL = "BMANGA_DOMAIN_RESTART_CHILD_OK"
PARENT_SENTINEL = "BMANGA_DOMAIN_RESTART_ROUNDTRIP_OK"


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


def _project(work_dir: Path) -> dict:
    return json.loads((work_dir / "project.json").read_text(encoding="utf-8"))


def _page_location(work_dir: Path, display_id: str) -> tuple[str, Path]:
    project = _project(work_dir)
    for page_uid, summary in project["pages"].items():
        if summary["displayId"] == display_id:
            return page_uid, work_dir / "pages" / page_uid
    raise AssertionError(f"page UID not found: {display_id}")


def _add_restart_probe() -> tuple[str, str]:
    from bmanga_domain_restart_test.core.work import get_work

    work = get_work(bpy.context)
    assert work is not None and work.loaded
    page = work.pages[work.active_page_index]
    page.title = "再起動ページ"
    coma = page.comas[0]
    coma_id = str(getattr(coma, "coma_id", "") or getattr(coma, "id", "") or "")
    parent_key = f"{page.id}:{coma_id}"
    balloon = page.balloons.add()
    balloon.id = "restart_balloon"
    balloon.title = "再起動フキダシ"
    balloon.parent_kind = "coma"
    balloon.parent_key = parent_key
    balloon.x_mm = 24.5
    balloon.y_mm = 31.25
    balloon.width_mm = 48.0
    balloon.height_mm = 29.0
    text = page.texts.add()
    text.id = "restart_text"
    text.title = "再起動テキスト"
    text.body = "Blender再起動後も完全一致"
    text.parent_kind = "coma"
    text.parent_key = parent_key
    text.parent_balloon_id = balloon.id
    balloon.text_id = text.id
    return str(balloon.id), str(text.id)


def _create_expected(root: Path) -> tuple[Path, Path]:
    from bmanga_domain_restart_test.core.work import get_work

    work_dir = root / "RestartRoundtrip.bmanga"
    assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
    work = get_work(bpy.context)
    work.work_info.work_name = "再起動完全一致"
    assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {"FINISHED"}
    balloon_id, text_id = _add_restart_probe()
    assert bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT") == {"FINISHED"}
    work = get_work(bpy.context)
    work.work_info.work_name = "再起動完全一致"
    assert "FINISHED" in bpy.ops.wm.save_as_mainfile(
        filepath=str(work_dir / "work.blend"),
        check_existing=False,
    )
    page_uid, page_dir = _page_location(work_dir, "p0001")
    expected = root / "expected"
    expected.mkdir()
    shutil.copy2(work_dir / "project.json", expected / "project.json")
    shutil.copy2(page_dir / "page.json", expected / "page.json")
    (expected / "identity.json").write_text(
        json.dumps(
            {
                "pageUid": page_uid,
                "balloonId": balloon_id,
                "textId": text_id,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return work_dir, expected


def _verify_after_restart() -> None:
    work_dir = Path(os.environ[WORK_ENV]).resolve(strict=True)
    expected = Path(os.environ[EXPECTED_ENV]).resolve(strict=True)
    identity = json.loads(
        (expected / "identity.json").read_text(encoding="utf-8")
    )
    before_project = (expected / "project.json").read_bytes()
    before_page = (expected / "page.json").read_bytes()
    addon = _load_addon()
    # process異常終了でPREPARED journalだけ残った状態を作る。blend内cacheと
    # sidecar本体は一致したままなので、埋込cache経路自身がrecover必須。
    from bmanga_domain_restart_test.bmanga_core.domain_model import (
        canonical_json_bytes,
    )
    from bmanga_domain_restart_test.bmanga_core.domain_repository import (
        ProjectRepository,
    )

    interrupted_repository = ProjectRepository(work_dir)
    interrupted_project = interrupted_repository.load_project()
    interrupted_project.settings["interruptedProbe"] = True
    interrupted_repository._prepare(
        uuid.uuid4().hex,
        {
            interrupted_repository.project_path:
                canonical_json_bytes(interrupted_project)
        },
    )
    assert any(interrupted_repository.journal_dir.glob("checkpoint-*.json"))
    try:
        result = bpy.ops.wm.open_mainfile(
            filepath=str(work_dir / "work.blend"),
            load_ui=False,
        )
        assert "FINISHED" in result
        from bmanga_domain_restart_test.core.work import get_work
        from bmanga_domain_restart_test.io import domain_projection

        work = get_work(bpy.context)
        assert work is not None and work.loaded
        assert work.work_info.work_name == "再起動完全一致"
        assert len(work.pages) == 1
        page = work.pages[0]
        assert str(page.get(domain_projection.PAGE_UID_PROP, "")) == identity["pageUid"]
        page_uid, page_dir = _page_location(work_dir, "p0001")
        assert page_uid == identity["pageUid"]
        assert (work_dir / "project.json").read_bytes() == before_project
        assert (page_dir / "page.json").read_bytes() == before_page
        assert not any(
            (work_dir / "journal").glob("checkpoint-*.json")
        ), "embedded cache path did not recover Repository journal"
        # 埋込PropertyGroup cacheを採用した起動でもRepositoryの観測hashを
        # 初期化し、別画面更新を未観測として上書きしてはならない。
        from bmanga_domain_restart_test.bmanga_core.domain_repository import (
            RepositoryConflictError,
        )
        from bmanga_domain_restart_test.io import page_io

        external = json.loads(before_project.decode("utf-8"))
        external["settings"]["externalConflictProbe"] = "別画面更新"
        (work_dir / "project.json").write_text(
            json.dumps(external, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            page_io.save_pages_json(work_dir, work)
        except RepositoryConflictError:
            pass
        else:
            raise AssertionError("embedded cache overwrote an external update")
        assert json.loads(
            (work_dir / "project.json").read_text(encoding="utf-8")
        )["settings"]["externalConflictProbe"] == "別画面更新"
        (work_dir / "project.json").write_bytes(before_project)
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {"FINISHED"}
        work = get_work(bpy.context)
        page = work.pages[0]
        assert page.title == "再起動ページ"
        balloon = next(
            entry for entry in page.balloons
            if str(entry.id) == identity["balloonId"]
        )
        text = next(
            entry for entry in page.texts
            if str(entry.id) == identity["textId"]
        )
        assert text.body == "Blender再起動後も完全一致"
        assert balloon.text_id == text.id
        assert text.parent_balloon_id == balloon.id
        assert balloon.parent_key == text.parent_key
        before_document = json.loads(before_page.decode("utf-8"))
        before_coma_uid = next(
            uid
            for uid, node in before_document["tree"]["nodes"].items()
            if node["kind"] == "coma"
        )
        coma = page.comas[0]
        assert str(coma.get(domain_projection.NODE_UID_PROP, "")) == before_coma_uid
        coma.coma_id = "c09"
        from bmanga_domain_restart_test.utils import layer_links

        page_io.save_page_json(work_dir, page)
        renumbered = json.loads((page_dir / "page.json").read_text(encoding="utf-8"))
        renumbered_coma = next(
            (uid, node)
            for uid, node in renumbered["tree"]["nodes"].items()
            if node["kind"] == "coma"
        )
        assert renumbered_coma[0] == before_coma_uid
        assert renumbered_coma[1]["displayId"] == "c09"

        layer_links._save_map(
            bpy.context,
            {
                f"balloon:{page.id}:{balloon.id}": "restart_link_probe",
                f"text:{page.id}:{text.id}": "restart_link_probe",
            },
        )
        page_io.save_page_json(work_dir, page)
        linked_once = json.loads((page_dir / "page.json").read_text(encoding="utf-8"))
        link_uids = tuple(linked_once["links"])
        assert len(link_uids) == 1
        page_io.save_page_json(work_dir, page)
        linked_twice = json.loads((page_dir / "page.json").read_text(encoding="utf-8"))
        assert tuple(linked_twice["links"]) == link_uids
        assert not (work_dir / "work.json").exists()
        assert not (work_dir / "pages.json").exists()
        assert "detail_data_migrate" not in dir(bpy.ops.bmanga)
        # 埋込cache採用後のRepository復旧が壊れていれば、古いPropertyGroupを
        # 操作可能にせず明示的な未読込状態へ落とす。
        current_blend = Path(bpy.data.filepath)
        assert "FINISHED" in bpy.ops.wm.save_as_mainfile(
            filepath=str(current_blend),
            check_existing=False,
        )
        broken_journal = (
            work_dir / "journal" / f"checkpoint-{uuid.uuid4().hex}.json"
        )
        broken_journal.write_bytes(b"{broken")
        from bmanga_domain_restart_test.utils import handlers, sidecar_load_cache

        sidecar_load_cache.current = lambda *_args, **_kwargs: True
        handlers._logger.exception = lambda *_args, **_kwargs: None
        handlers._show_native_save_notice = lambda **_kwargs: None
        result = bpy.ops.wm.open_mainfile(
            filepath=str(current_blend),
            load_ui=False,
        )
        assert "FINISHED" in result
        failed_work = get_work(bpy.context)
        assert failed_work is not None
        assert not failed_work.loaded, (
            "破損Repository journalで埋込cacheが操作可能なままです"
        )
        assert broken_journal.is_file(), (
            "不明な復旧記録を黙って削除しました"
        )
        print(CHILD_SENTINEL, flush=True)
    finally:
        addon.unregister()


def _run_parent() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    root = Path(tempfile.mkdtemp(prefix="bmanga_domain_restart_"))
    addon = _load_addon()
    succeeded = False
    try:
        work_dir, expected = _create_expected(root)
        addon.unregister()
        addon = None
        bpy.ops.wm.read_factory_settings(use_empty=True)
        environment = dict(os.environ)
        environment.update(
            {
                STAGE_ENV: "verify",
                WORK_ENV: str(work_dir),
                EXPECTED_ENV: str(expected),
            }
        )
        completed = subprocess.run(
            [
                bpy.app.binary_path,
                "--background",
                "--factory-startup",
                "--python",
                str(Path(__file__).resolve()),
            ],
            cwd=str(ROOT),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        output = completed.stdout + "\n" + completed.stderr
        assert completed.returncode == 0, output
        assert CHILD_SENTINEL in output, output
        succeeded = True
        print(PARENT_SENTINEL, flush=True)
    finally:
        if addon is not None:
            addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if succeeded:
            shutil.rmtree(root, ignore_errors=False)
        else:
            print(f"FAILED_TEMP_ROOT={root}", flush=True)


def main() -> None:
    try:
        if os.environ.get(STAGE_ENV) == "verify":
            _verify_after_restart()
        else:
            _run_parent()
    except Exception:
        traceback.print_exc()
        if os.environ.get("BMANGA_CERT_WRAPPED") == "1":
            raise
        os._exit(1)


if __name__ == "__main__":
    main()
