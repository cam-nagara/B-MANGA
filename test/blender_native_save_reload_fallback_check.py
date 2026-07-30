"""Blender 5.2実機: 復旧後の自動再読込とフォールバックの実機検証。

強制終了後の復旧が新規作成ページを削除した直後に同じファイルの再読込を
予約すると、対象が存在せずENOENTで行き止まりダイアログになっていた回帰
に対する修正 (``_reload_fallback_target`` / ``_reload_missing_target`` /
``_native_save_reload_tick``) を、実際のBlenderファイル開閉で検証する。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_native_save_reload_fallback_test"
PROJECT_UID = "project_33333333333333333333333333333333"
PAGE_UID = "page_33333333333333333333333333333333"
PAGE_UID_2 = "page_55555555555555555555555555555555"
ROOT_UID = "node_33333333333333333333333333333333"
ROOT_UID_2 = "node_55555555555555555555555555555555"
COMA_UID = "coma_33333333333333333333333333333333"
COMA_NODE_UID = "node_44444444444444444444444444444444"

_counters = {"checks": 0}


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


def _save_probe_blend(path: Path, value: str) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.scene["bmanga_reload_fallback_probe"] = value
    result = bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)
    assert "FINISHED" in result


def _project_document(*, with_page: bool) -> dict:
    pages = {}
    order = []
    if with_page:
        summary = {
            "uid": PAGE_UID,
            "displayId": "p0001",
            "displayNumber": 1,
            "title": "1ページ",
            "spread": False,
            "sourcePageUids": [],
            "settings": {},
        }
        pages[PAGE_UID] = summary
        order.append(PAGE_UID)
    return {
        "schema": "bmanga.project",
        "schemaVersion": 1,
        "projectUid": PROJECT_UID,
        "revision": 0,
        "settings": {},
        "pageOrder": order,
        "pages": pages,
    }


def _page_document() -> dict:
    return {
        "schema": "bmanga.page",
        "schemaVersion": 1,
        "projectUid": PROJECT_UID,
        "pageUid": PAGE_UID,
        "revision": 0,
        "settings": {},
        "tree": {
            "rootUid": ROOT_UID,
            "nodes": {
                ROOT_UID: {
                    "uid": ROOT_UID,
                    "kind": "page",
                    "displayId": "p0001",
                    "title": "1ページ",
                    "settings": {},
                    "nativeUid": "",
                },
                COMA_NODE_UID: {
                    "uid": COMA_NODE_UID,
                    "kind": "coma",
                    "displayId": "c01",
                    "title": "コマ1",
                    "settings": {},
                    "nativeUid": COMA_UID,
                }
            },
            "children": {ROOT_UID: [COMA_NODE_UID], COMA_NODE_UID: []},
        },
        "links": {},
    }


def _create_work(root: Path) -> tuple[Path, Path, Path]:
    """work.blendだけを実体化した新Domain作品を作る(page.blendは無し)。"""

    work = root / "ReloadFallback.bmanga"
    _write_json(work / "project.json", _project_document(with_page=True))
    page_dir = work / "pages" / PAGE_UID
    _write_json(page_dir / "page.json", _page_document())
    work_blend = work / "work.blend"
    page = page_dir / "page.blend"
    _save_probe_blend(work_blend, "work")
    return work, work_blend, page


def _open(path: Path, value: str) -> None:
    result = bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
    assert "FINISHED" in result
    assert Path(bpy.data.filepath).resolve() == path.resolve(), (
        f"{path} を開いたはずがbpy.data.filepathが一致しません: {bpy.data.filepath}"
    )
    assert bpy.context.scene.get("bmanga_reload_fallback_probe") == value


def _check(condition: bool, message: str) -> None:
    _counters["checks"] += 1
    assert condition, message


def _case_fallback_target(handlers, work_blend: Path, page: Path) -> None:
    """(A) _reload_fallback_target: page→work.blend、work.blend自身→None."""

    fallback_for_page = handlers._reload_fallback_target(page)
    _check(
        fallback_for_page is not None
        and fallback_for_page.resolve() == work_blend.resolve(),
        "存在しないpage.blendのフォールバックがwork.blendを指しません",
    )
    fallback_for_work = handlers._reload_fallback_target(work_blend)
    _check(
        fallback_for_work is None,
        "work.blend自身のフォールバックがNoneではありません",
    )


def _case_retry_before_limit(handlers, work_blend: Path, page: Path) -> None:
    """(B) 存在しないpage.blendは上限未満ならリトライ間隔を返し、何も開かない."""

    _open(work_blend, "work")
    generation = handlers._native_save_reload_generation
    state = {"attempts": 0}
    assert not page.is_file()
    result = handlers._native_save_reload_tick(page, generation, state)
    _check(
        result == handlers._NATIVE_SAVE_RELOAD_RETRY_INTERVAL,
        "リトライ間隔が返っていません",
    )
    _check(
        Path(bpy.data.filepath).resolve() == work_blend.resolve(),
        "リトライ中に別ファイルが開かれました",
    )
    _check(state["attempts"] == 1, "attemptsが加算されていません")


def _case_fallback_opens_work_blend_at_limit(handlers, work_blend: Path, page: Path) -> None:
    """(C) 上限到達でフォールバック(work.blend)を開く."""

    _open(work_blend, "work")
    generation = handlers._native_save_reload_generation
    state = {"attempts": handlers._NATIVE_SAVE_RELOAD_MAX_ATTEMPTS - 1}
    assert not page.is_file()
    result = handlers._native_save_reload_tick(page, generation, state)
    _check(result is None, "上限到達時の戻り値がNoneではありません")
    _check(
        Path(bpy.data.filepath).resolve() == work_blend.resolve(),
        "上限到達後にwork.blendへフォールバックしていません",
    )


def _case_generation_mismatch_does_nothing(handlers, work_blend: Path, page: Path) -> None:
    """(D) 世代不一致なら何も開かずNoneを返す."""

    _open(work_blend, "work")
    stale_generation = handlers._native_save_reload_generation + 1
    state = {"attempts": 0}
    result = handlers._native_save_reload_tick(page, stale_generation, state)
    _check(result is None, "世代不一致時の戻り値がNoneではありません")
    _check(
        Path(bpy.data.filepath).resolve() == work_blend.resolve(),
        "世代不一致にもかかわらずファイルが開かれました",
    )
    _check(state["attempts"] == 0, "世代不一致でもattemptsが加算されています")


def _case_existing_target_is_opened(handlers, work_blend: Path, page: Path) -> None:
    """(E) 再読込対象が実在すればそのファイルを開く."""

    page.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(work_blend, page)
    _open(work_blend, "work")
    generation = handlers._native_save_reload_generation
    state = {"attempts": 0}
    assert page.is_file()
    result = handlers._native_save_reload_tick(page, generation, state)
    _check(result is None, "存在するファイルの再読込後の戻り値がNoneではありません")
    _check(
        Path(bpy.data.filepath).resolve() == page.resolve(),
        "存在するpage.blendへ再読込されていません",
    )


def _case_changed_file_cancels_stale_timer(handlers, work_blend: Path, page: Path) -> None:
    """(F) 待機中に別blendへ移動したら、古い予約は現在画面を奪わない."""

    page.unlink(missing_ok=True)
    other = work_blend.with_name("other.blend")
    shutil.copy2(work_blend, other)
    _open(other, "work")
    state = {"attempts": 0, "origin": str(work_blend)}
    result = handlers._native_save_reload_tick(
        page, handlers._native_save_reload_generation, state,
    )
    _check(result is None, "別ファイル移動後も古いタイマーが継続しました")
    _check(state["attempts"] == 0, "別ファイル移動後にattemptsが加算されました")
    _check(Path(bpy.data.filepath).resolve() == other.resolve(), "古いタイマーが別ファイルを開きました")


def _case_transient_open_failure_retries(handlers, work_blend: Path, page: Path) -> None:
    """(G) 実在ファイルの一時読込失敗も上限内では再試行する."""

    shutil.copy2(work_blend, page)
    _open(work_blend, "work")
    original = handlers._open_native_reload_target

    def _fail_once(_path):
        raise OSError("temporary sharing violation")

    handlers._open_native_reload_target = _fail_once
    try:
        state = {"attempts": 0, "origin": str(work_blend)}
        result = handlers._native_save_reload_tick(
            page, handlers._native_save_reload_generation, state,
        )
    finally:
        handlers._open_native_reload_target = original
    _check(result == handlers._NATIVE_SAVE_RELOAD_RETRY_INTERVAL, "一時読込失敗を再試行しません")
    _check(state["attempts"] == 1, "一時読込失敗でattemptsが加算されません")
    _check(Path(bpy.data.filepath).resolve() == work_blend.resolve(), "一時失敗中に画面が移動しました")
    result = handlers._native_save_reload_tick(
        page, handlers._native_save_reload_generation, state,
    )
    _check(result is None, "再試行成功後の戻り値がNoneではありません")
    _check(Path(bpy.data.filepath).resolve() == page.resolve(), "再試行で対象を開けません")


def _case_work_open_preflight_restores_missing_work_blend(work_op, native_guard, root: Path) -> None:
    """(H) work.blend退避直後の異常終了も「作品を開く」入口で復旧する."""

    work = root / "OpenPreflight.bmanga"
    _write_json(work / "project.json", _project_document(with_page=False))
    source = work / "work.blend"
    source.write_bytes(b"latest-work")
    token = native_guard.begin_native_save(source)
    _check(token is not None and token.requires_restore, "中断保存の復旧トークンを作れません")
    _check(not source.exists(), "退避直後のwork.blend欠落状態を再現できません")
    recovery_root = work / ".bmanga-save-recovery-v1"
    _check(
        token.journal_path is not None and recovery_root in token.journal_path.parents,
        "保存復旧記録が作品フォルダー内にありません",
    )
    legacy_base = work.parent / f".{work.name}.native-save-recovery-v1"
    _check(not legacy_base.exists(), "作品フォルダー外に旧形式の復旧先を作成しました")
    native_guard._release(token)
    recovered, error = work_op._recover_selected_work_before_open(work)
    _check(recovered and not error and source.read_bytes() == b"latest-work", "作品を開く前にwork.blendを復旧できません")
    _check(not recovery_root.exists(), "復旧完了後も空の内部復旧フォルダーが残りました")


def _case_work_open_stops_when_current_save_fails(
    work_op,
    work_blend: Path,
    root: Path,
) -> None:
    """(I) 現在作品の保存失敗時は次作品を一切読み込まない。"""

    _open(work_blend, "work")
    current_work = bpy.context.scene.bmanga_work
    current_work.loaded = True
    current_work.work_dir = str(work_blend.parent)
    target = root / "NeverOpened.bmanga"
    target.mkdir()
    opened = {"called": False}
    original_save = work_op.blend_io.save_work_blend
    original_load = work_op.work_io.load_work_json
    work_op.blend_io.save_work_blend = lambda *_args, **_kwargs: False
    work_op.work_io.load_work_json = lambda *_args, **_kwargs: opened.__setitem__(
        "called",
        True,
    )
    try:
        try:
            result = bpy.ops.bmanga.work_open(
                "EXEC_DEFAULT",
                filepath=str(target),
            )
        except RuntimeError as exc:
            _check(
                "現在の作品を保存できないため" in str(exc),
                "保存失敗の理由が利用者へ通知されませんでした",
            )
        else:
            _check(
                result == {"CANCELLED"},
                "保存失敗でも作品切替が成功扱いになりました",
            )
    finally:
        work_op.blend_io.save_work_blend = original_save
        work_op.work_io.load_work_json = original_load
    _check(not opened["called"], "保存失敗後に次作品を読み込みました")
    _check(
        Path(bpy.data.filepath).resolve() == work_blend.resolve(),
        "保存失敗後に現在の作品画面を離れました",
    )


def _case_external_copy_cannot_start_work_save(
    work_op,
    handlers,
    work_blend: Path,
    root: Path,
) -> None:
    """(J) 作品外コピーではJSON/Blendの保存入口へ一切到達しない。"""

    _open(work_blend, "work")
    current_work = bpy.context.scene.bmanga_work
    current_work.loaded = True
    current_work.work_dir = str(work_blend.parent)
    project_json = work_blend.parent / "project.json"
    page_json = work_blend.parent / "pages" / PAGE_UID / "page.json"
    external_dir = root / "ExternalCopy"
    external_dir.mkdir()
    external_blend = external_dir / "work-copy.blend"
    canonical_before = {
        project_json: project_json.read_bytes(),
        page_json: page_json.read_bytes(),
    }
    result = bpy.ops.wm.save_as_mainfile(
        filepath=str(external_blend),
        check_existing=False,
        compress=False,
    )
    assert "FINISHED" in result
    _check(
        not current_work.loaded,
        "作品外へのSave As後も外部コピーがB-MANGA正本として有効です",
    )
    _check(
        project_json.read_bytes() == canonical_before[project_json],
        "作品外へのSave Asがproject.jsonを書き換えました",
    )
    _check(
        page_json.read_bytes() == canonical_before[page_json],
        "作品外へのSave Asがpage.jsonを書き換えました",
    )
    # 保存済み外部Blendに古いloaded=Trueが埋め込まれていても、Operator入口の
    # パスガードが書込みを拒否することを再現する。
    current_work.loaded = True
    _check(
        work_op._canonical_mainfile_role(
            work_blend.parent,
            work_blend,
        )[0] == "work",
        "正規のwork.blendが作品外と誤判定されました",
    )
    sibling = work_blend.parent.with_name(f"{work_blend.parent.name}-copy")
    _check(
        work_op._canonical_mainfile_role(
            work_blend.parent,
            sibling / "work.blend",
        )[0] == "unknown",
        "同名接頭辞の別フォルダーを作品内と誤判定しました",
    )
    before = {
        project_json: project_json.read_bytes(),
        page_json: page_json.read_bytes(),
        external_blend: external_blend.read_bytes(),
    }
    calls = {"metadata": 0, "native": 0}
    original_metadata_save = handlers.save_scene_work_to_disk
    original_native_saves = (
        work_op.blend_io.save_work_blend,
        work_op.blend_io.save_page_blend,
        work_op.blend_io.save_coma_blend,
    )

    def _unexpected_metadata_save(*_args, **_kwargs):
        calls["metadata"] += 1
        raise AssertionError("外部コピーからJSON保存入口へ到達しました")

    def _unexpected_native_save(*_args, **_kwargs):
        calls["native"] += 1
        raise AssertionError("外部コピーからBlend保存入口へ到達しました")

    handlers.save_scene_work_to_disk = _unexpected_metadata_save
    (
        work_op.blend_io.save_work_blend,
        work_op.blend_io.save_page_blend,
        work_op.blend_io.save_coma_blend,
    ) = (
        _unexpected_native_save,
        _unexpected_native_save,
        _unexpected_native_save,
    )
    try:
        try:
            result = bpy.ops.bmanga.work_save("EXEC_DEFAULT")
        except RuntimeError as exc:
            _check(
                "正規の作品ファイルではないコピーからは保存できません" in str(exc),
                "外部コピー拒否の理由が利用者へ通知されませんでした",
            )
        else:
            _check(
                result == {"CANCELLED"},
                "外部コピーからの作品保存が成功扱いになりました",
            )
    finally:
        handlers.save_scene_work_to_disk = original_metadata_save
        (
            work_op.blend_io.save_work_blend,
            work_op.blend_io.save_page_blend,
            work_op.blend_io.save_coma_blend,
        ) = original_native_saves
    _check(calls == {"metadata": 0, "native": 0}, "外部コピーから保存処理を開始しました")
    _check(project_json.read_bytes() == before[project_json], "project.jsonを書き換えました")
    _check(page_json.read_bytes() == before[page_json], "page.jsonを書き換えました")
    _check(external_blend.read_bytes() == before[external_blend], "外部Blendを書き換えました")
    _check(
        Path(bpy.data.filepath).resolve() == external_blend.resolve(),
        "拒否後に外部コピーから別ファイルへ移動しました",
    )


def _case_internal_named_copies_cannot_start_work_save(
    work_op,
    handlers,
    work_blend: Path,
) -> None:
    """(K) work/page/comaの作品内別名コピーも正本を一切変更しない。"""

    work_dir = work_blend.parent
    page_blend = work_dir / "pages" / PAGE_UID / "page.blend"
    coma_blend = (
        work_dir
        / "pages"
        / PAGE_UID
        / "comas"
        / COMA_UID
        / "scene.blend"
    )
    page_blend.parent.mkdir(parents=True, exist_ok=True)
    coma_blend.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(work_blend, page_blend)
    shutil.copy2(work_blend, coma_blend)
    project_json = work_dir / "project.json"
    page_json = work_dir / "pages" / PAGE_UID / "page.json"
    protected_paths = (
        project_json,
        page_json,
        work_blend,
        page_blend,
        coma_blend,
    )
    roles = (
        (work_blend, work_dir / "work-copy.blend", "work"),
        (page_blend, page_blend.with_name("page-copy.blend"), "page"),
        (coma_blend, coma_blend.with_name("scene-copy.blend"), "coma"),
    )

    for canonical, copy_path, expected_role in roles:
        _open(canonical, "work")
        current_work = bpy.context.scene.bmanga_work
        current_work.loaded = True
        current_work.work_dir = str(work_dir)
        role = work_op._canonical_mainfile_role(work_dir, canonical)
        _check(
            role[0] == expected_role,
            f"正規{expected_role}ファイルを分類できません: {role}",
        )
        protected_before = {
            path: path.read_bytes()
            for path in protected_paths
        }
        calls = {"metadata": 0, "native": 0}
        original_metadata_save = handlers.save_scene_work_to_disk
        original_native_saves = (
            work_op.blend_io.save_work_blend,
            work_op.blend_io.save_page_blend,
            work_op.blend_io.save_coma_blend,
        )

        def _unexpected_metadata_save(*_args, **_kwargs):
            calls["metadata"] += 1
            raise AssertionError("作品内別名コピーからJSON保存入口へ到達しました")

        def _unexpected_native_save(*_args, **_kwargs):
            calls["native"] += 1
            raise AssertionError("作品内別名コピーからBlend保存入口へ到達しました")

        handlers.save_scene_work_to_disk = _unexpected_metadata_save
        try:
            result = bpy.ops.wm.save_as_mainfile(
                filepath=str(copy_path),
                check_existing=False,
                compress=False,
            )
            assert "FINISHED" in result
            _check(
                calls["metadata"] == 0,
                f"{expected_role}別名Save AsでJSON保存を開始しました",
            )
            _check(
                not current_work.loaded,
                f"{expected_role}別名Save As後も正本として有効です",
            )
            _check(
                all(path.read_bytes() == protected_before[path] for path in protected_paths),
                f"{expected_role}別名Save Asが正規ファイルを書き換えました",
            )
            _open(copy_path, "work")
            reopened_work = bpy.context.scene.bmanga_work
            _check(
                not reopened_work.loaded,
                f"{expected_role}別名コピーの再読込後も正本として有効です",
            )
            reopened_work.loaded = True
            reopened_work.work_dir = str(work_dir)
            (
                work_op.blend_io.save_work_blend,
                work_op.blend_io.save_page_blend,
                work_op.blend_io.save_coma_blend,
            ) = (
                _unexpected_native_save,
                _unexpected_native_save,
                _unexpected_native_save,
            )
            try:
                result = bpy.ops.bmanga.work_save("EXEC_DEFAULT")
            except RuntimeError as exc:
                _check(
                    "正規の作品ファイルではないコピーからは保存できません"
                    in str(exc),
                    f"{expected_role}別名コピー拒否の理由が通知されません",
                )
            else:
                _check(
                    result == {"CANCELLED"},
                    f"{expected_role}別名コピーからの保存が成功扱いです",
                )
        finally:
            handlers.save_scene_work_to_disk = original_metadata_save
            (
                work_op.blend_io.save_work_blend,
                work_op.blend_io.save_page_blend,
                work_op.blend_io.save_coma_blend,
            ) = original_native_saves
        _check(
            calls == {"metadata": 0, "native": 0},
            f"{expected_role}別名コピーから保存処理を開始しました",
        )
        _check(
            all(path.read_bytes() == protected_before[path] for path in protected_paths),
            f"{expected_role}別名コピー拒否後に正規ファイルが変化しました",
        )
        _check(
            Path(bpy.data.filepath).resolve() == copy_path.resolve(),
            f"{expected_role}別名コピー拒否後に別ファイルへ移動しました",
        )


def _case_cross_canonical_save_as_is_restored(
    work_op,
    handlers,
    work_blend: Path,
) -> None:
    """(L) 異なる正規role/UIDへの直接Save Asは保存先と元画面を復元する。"""

    work_dir = work_blend.parent
    project_json = work_dir / "project.json"
    project = json.loads(project_json.read_text(encoding="utf-8"))
    project["pageOrder"].append(PAGE_UID_2)
    project["pages"][PAGE_UID_2] = {
        "uid": PAGE_UID_2,
        "displayId": "p0002",
        "displayNumber": 2,
        "title": "2ページ",
        "spread": False,
        "sourcePageUids": [],
        "settings": {},
    }
    _write_json(project_json, project)
    page2_dir = work_dir / "pages" / PAGE_UID_2
    _write_json(
        page2_dir / "page.json",
        {
            "schema": "bmanga.page",
            "schemaVersion": 1,
            "projectUid": PROJECT_UID,
            "pageUid": PAGE_UID_2,
            "revision": 0,
            "settings": {},
            "tree": {
                "rootUid": ROOT_UID_2,
                "nodes": {
                    ROOT_UID_2: {
                        "uid": ROOT_UID_2,
                        "kind": "page",
                        "displayId": "p0002",
                        "title": "2ページ",
                        "settings": {},
                        "nativeUid": "",
                    },
                },
                "children": {ROOT_UID_2: []},
            },
            "links": {},
        },
    )
    page1_blend = work_dir / "pages" / PAGE_UID / "page.blend"
    page2_blend = page2_dir / "page.blend"
    coma_blend = (
        work_dir
        / "pages"
        / PAGE_UID
        / "comas"
        / COMA_UID
        / "scene.blend"
    )
    shutil.copy2(work_blend, page2_blend)
    _check(
        work_op._canonical_mainfile_role(work_dir, page2_blend)[0] == "page",
        "2ページ目の正規page.blendをDomain登録済みとして分類できません",
    )
    protected_paths = (
        project_json,
        work_dir / "pages" / PAGE_UID / "page.json",
        page2_dir / "page.json",
        work_blend,
        page1_blend,
        page2_blend,
        coma_blend,
    )
    pairs = (
        (work_blend, page1_blend, "work→page"),
        (page1_blend, page2_blend, "page1→page2"),
        (page2_blend, coma_blend, "page→coma"),
        (coma_blend, work_blend, "coma→work"),
    )

    for source, target, label in pairs:
        _open(source, "work")
        current_work = bpy.context.scene.bmanga_work
        current_work.loaded = True
        current_work.work_dir = str(work_dir)
        bpy.context.scene["bmanga_reload_fallback_probe"] = f"cross-{label}"
        before = {path: path.read_bytes() for path in protected_paths}
        calls = {"metadata": 0}
        scheduled: list[Path] = []
        original_metadata_save = handlers.save_scene_work_to_disk
        original_schedule = handlers._schedule_native_save_reload

        def _unexpected_metadata_save(*_args, **_kwargs):
            calls["metadata"] += 1
            raise AssertionError(f"{label}でJSON保存入口へ到達しました")

        def _capture_reload(path, **_kwargs):
            scheduled.append(Path(path).resolve())

        handlers.save_scene_work_to_disk = _unexpected_metadata_save
        handlers._schedule_native_save_reload = _capture_reload
        try:
            result = bpy.ops.wm.save_as_mainfile(
                filepath=str(target),
                check_existing=False,
                compress=False,
            )
            assert "FINISHED" in result
        finally:
            handlers.save_scene_work_to_disk = original_metadata_save
            handlers._schedule_native_save_reload = original_schedule
        _check(
            scheduled == [source.resolve()],
            f"{label}拒否後の再読込先が保存元ではありません: {scheduled}",
        )
        _check(calls["metadata"] == 0, f"{label}で作品情報を書き始めました")
        _check(
            target.read_bytes() == before[target],
            f"{label}で保存先の正規Blendを上書きしました",
        )
        _check(
            all(path.read_bytes() == before[path] for path in protected_paths),
            f"{label}で正規作品ファイルのいずれかを変更しました",
        )
        _check(
            handlers._native_save_token is None,
            f"{label}拒否後も保存トークンが残りました",
        )
        _check(
            Path(bpy.data.filepath).resolve() == target.resolve(),
            f"{label}のBlender本体保存完了前提を再現できません",
        )
        _open(source, "work")


EXPECTED_CHECK_COUNT = 92


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_native_reload_fallback_"))
    succeeded = False
    try:
        addon = _load_addon()
        handlers = importlib.import_module(f"{MODULE_NAME}.utils.handlers")
        work_op = importlib.import_module(f"{MODULE_NAME}.operators.work_op")
        native_guard = importlib.import_module(f"{MODULE_NAME}.io.native_save_guard")
        page_preview = importlib.import_module(
            f"{MODULE_NAME}.utils.page_preview_object"
        )
        # この検査は再読込先の選択だけを対象とする。work.blend読込のたびに
        # 非同期preview生成を起動すると、意図的なbaseline競合ケースへ混入する。
        page_preview.sync_page_previews = lambda *_args, **_kwargs: None
        _work, work_blend, page = _create_work(temp_root)

        _case_fallback_target(handlers, work_blend, page)
        _case_retry_before_limit(handlers, work_blend, page)
        _case_fallback_opens_work_blend_at_limit(handlers, work_blend, page)
        _case_generation_mismatch_does_nothing(handlers, work_blend, page)
        _case_existing_target_is_opened(handlers, work_blend, page)
        _case_changed_file_cancels_stale_timer(handlers, work_blend, page)
        _case_transient_open_failure_retries(handlers, work_blend, page)
        _case_work_open_preflight_restores_missing_work_blend(work_op, native_guard, temp_root)
        _case_work_open_stops_when_current_save_fails(
            work_op,
            work_blend,
            temp_root,
        )
        _case_external_copy_cannot_start_work_save(
            work_op,
            handlers,
            work_blend,
            temp_root,
        )
        _case_internal_named_copies_cannot_start_work_save(
            work_op,
            handlers,
            work_blend,
        )
        _case_cross_canonical_save_as_is_restored(
            work_op,
            handlers,
            work_blend,
        )

        assert _counters["checks"] == EXPECTED_CHECK_COUNT, (
            f"検証アサートの実行数が想定と異なります: {_counters['checks']}"
            f" (期待 {EXPECTED_CHECK_COUNT})"
        )
        succeeded = True
        print("BMANGA_NATIVE_SAVE_RELOAD_FALLBACK_CHECK_OK", flush=True)
    except Exception:
        traceback.print_exc()
        succeeded = False
    finally:
        try:
            bpy.ops.wm.read_factory_settings(use_empty=True)
        except Exception:
            pass
        if addon is not None:
            try:
                addon.unregister()
            except Exception:
                pass
        if succeeded:
            shutil.rmtree(temp_root, ignore_errors=True)
        else:
            print(f"FAILED_TEMP_ROOT={temp_root}")
    if os.environ.get("BMANGA_CERT_WRAPPED") == "1":
        if not succeeded:
            raise RuntimeError("native save/reload fallback check failed")
        return
    os._exit(0 if succeeded else 1)


if __name__ == "__main__":
    main()
