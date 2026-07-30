"""欠損・破損Domainを旧投影のまま操作可能にしない。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_phase3_strict_domain_load"
SENTINEL = "BMANGA_PHASE3_STRICT_DOMAIN_LOAD_OK"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _expect_load_failure(callback, work) -> None:
    try:
        callback()
    except RuntimeError as exc:
        assert (
            "required Domain page failed to load" in str(exc)
            or "required file is missing" in str(exc)
            or "invalid JSON" in str(exc)
        )
    else:
        raise AssertionError("invalid Domain page was accepted")
    assert not work.loaded


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    try:
        from bmanga_phase3_strict_domain_load.core.work import get_work
        from bmanga_phase3_strict_domain_load.io import (
            domain_projection,
            domain_runtime,
            page_io,
        )
        from bmanga_phase3_strict_domain_load.utils import (
            handlers,
            paths,
            sidecar_load_cache,
        )

        with tempfile.TemporaryDirectory(
            prefix="bmanga_phase3_strict_domain_load_"
        ) as temp:
            work_dir = Path(temp) / "StrictDomainLoad.bmanga"
            assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {
                "FINISHED"
            }
            assert bpy.ops.bmanga.open_page_file(
                "EXEC_DEFAULT",
                index=0,
            ) == {"FINISHED"}
            work = get_work(bpy.context)
            assert work is not None and work.loaded
            page = work.pages[0]
            page_io.save_page_json(work_dir, page)
            page_uid = domain_projection.ensure_page_uid(
                page,
                domain_projection.ensure_project_uid(work),
            )
            repository = domain_runtime.repository_for(work_dir)
            page_path = repository.page_path(page_uid)
            valid_page = page_path.read_bytes()
            current_blend = Path(bpy.data.filepath)
            assert current_blend.is_file()
            assert "FINISHED" in bpy.ops.wm.save_as_mainfile(
                filepath=str(current_blend),
                check_existing=False,
            )

            def open_embedded(path: Path) -> None:
                original_current = sidecar_load_cache.current
                original_notice = handlers._show_native_save_notice
                original_log_exception = handlers._logger.exception
                try:
                    sidecar_load_cache.current = (
                        lambda *_args, **_kwargs: True
                    )
                    handlers._show_native_save_notice = (
                        lambda **_kwargs: None
                    )
                    handlers._logger.exception = (
                        lambda *_args, **_kwargs: None
                    )
                    assert "FINISHED" in bpy.ops.wm.open_mainfile(
                        filepath=str(path),
                        load_ui=False,
                    )
                finally:
                    sidecar_load_cache.current = original_current
                    handlers._show_native_save_notice = original_notice
                    handlers._logger.exception = original_log_exception

            # ページ一覧の埋込cacheも、一覧だけ読めた時点では操作可能にしない。
            # 全page.jsonの存在・schema検証が通るまでloaded=Falseを保つ。
            work_blend = work_dir / "work.blend"
            page_path.unlink()
            open_embedded(work_blend)
            work = get_work(bpy.context)
            assert work is not None and not work.loaded
            assert not page_path.exists(), "work cache regenerated missing page"
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_bytes(valid_page)
            open_embedded(current_blend)
            work = get_work(bpy.context)
            assert work is not None and work.loaded

            # 埋込PropertyGroupが完全でも、正本page.jsonが無ければ失敗状態に
            # 落とし、旧cacheからコマを再生成・保存しない。
            page_path.unlink()
            open_embedded(current_blend)
            work = get_work(bpy.context)
            assert work is not None and not work.loaded
            assert not page_path.exists(), "missing Domain page was regenerated"

            # 通常のdisk同期も、欠損と壊れたJSONの両方でfail closedになる。
            _expect_load_failure(
                lambda: handlers.sync_scene_work_from_disk(
                    bpy.context,
                    work_dir,
                ),
                work,
            )
            page_path.parent.mkdir(parents=True, exist_ok=True)
            page_path.write_bytes(valid_page)
            work = handlers.sync_scene_work_from_disk(bpy.context, work_dir)
            assert work is not None and work.loaded

            page_path.write_bytes(b"{broken")
            _expect_load_failure(
                lambda: handlers.sync_scene_work_from_disk(
                    bpy.context,
                    work_dir,
                ),
                work,
            )
            assert page_path.read_bytes() == b"{broken"

            # Repositoryを経由しないensure_page_dirも、UID directoryの
            # junctionを辿って作品外へassets/comasを作成してはならない。
            redirected_uid = "page_" + "f" * 32
            redirected = work_dir / "pages" / redirected_uid
            outside = Path(temp) / "outside"
            outside.mkdir()
            if os.name == "nt":
                result = subprocess.run(
                    [
                        "cmd",
                        "/c",
                        "mklink",
                        "/J",
                        str(redirected),
                        str(outside),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="mbcs",
                    errors="replace",
                    check=False,
                )
                assert result.returncode == 0, result.stderr or result.stdout
            else:
                os.symlink(
                    outside,
                    redirected,
                    target_is_directory=True,
                )
            try:
                page_io.ensure_page_dir(work_dir, redirected_uid)
            except paths.WorkPathBoundaryError as exc:
                assert "escapes project root" in str(exc)
            else:
                raise AssertionError("page junction was accepted")
            assert not (outside / "assets").exists()
            assert not (outside / "comas").exists()

        print(SENTINEL, flush=True)
    finally:
        addon.unregister()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        if os.environ.get("BMANGA_CERT_WRAPPED") == "1":
            raise
        raise
