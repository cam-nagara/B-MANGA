"""Domain投影収集失敗とUID衝突を削除として保存しない。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_phase3_projection_failure"
SENTINEL = "BMANGA_PHASE3_PROJECTION_FAILURE_CHECK_OK"


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


def _raises(message: str, callback) -> None:
    try:
        callback()
    except RuntimeError as exc:
        assert message in str(exc)
        return
    raise AssertionError(f"expected RuntimeError containing {message!r}")


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    try:
        from bmanga_phase3_projection_failure.bmanga_core.domain_ids import (
            UIDKind,
            derived_uid,
        )
        from bmanga_phase3_projection_failure.bmanga_core.domain_model import (
            DomainLink,
            DomainNode,
        )
        from bmanga_phase3_projection_failure.core.work import get_work
        from bmanga_phase3_projection_failure.io import (
            blend_io,
            domain_projection,
            domain_projection_preservation,
            page_io,
        )
        from bmanga_phase3_projection_failure.utils import (
            handlers,
            layer_links,
            layer_object_model,
            object_state_sync,
            paths,
        )

        with tempfile.TemporaryDirectory(
            prefix="bmanga_phase3_projection_failure_"
        ) as temp:
            work_dir = Path(temp) / "ProjectionFailure.bmanga"
            assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
            work = get_work(bpy.context)
            assert work is not None and work.loaded and len(work.pages) == 1
            page = work.pages[0]
            if not bool(page.detail_loaded):
                page_io.load_page_json(work_dir, page)
            page_io.save_page_json(work_dir, page)
            page_path = paths.page_meta_path(work_dir, page.id)
            before = page_path.read_bytes()

            original_context = page_io._context_for_page
            original_iter = layer_object_model.iter_layer_objects
            try:
                page_io._context_for_page = lambda _page: bpy.context
                layer_object_model.iter_layer_objects = (
                    lambda _kind: (_ for _ in ()).throw(RuntimeError("gp fault"))
                )
                _raises(
                    "Native layer collection failed",
                    lambda: page_io.save_page_json(work_dir, page),
                )
            finally:
                page_io._context_for_page = original_context
                layer_object_model.iter_layer_objects = original_iter
            assert page_path.read_bytes() == before

            original_context = page_io._context_for_page
            original_load_map = layer_links.load_map_strict
            try:
                page_io._context_for_page = lambda _page: bpy.context
                layer_links.load_map_strict = lambda _context: (
                    _ for _ in ()
                ).throw(RuntimeError("link fault"))
                _raises(
                    "Domain link collection failed",
                    lambda: page_io.save_page_json(work_dir, page),
                )
            finally:
                page_io._context_for_page = original_context
                layer_links.load_map_strict = original_load_map
            assert page_path.read_bytes() == before

            original_context = page_io._context_for_page
            original_raw_links = str(
                bpy.context.scene.get(layer_links.LINK_PROP, "") or ""
            )
            try:
                page_io._context_for_page = lambda _page: bpy.context
                bpy.context.scene[layer_links.LINK_PROP] = "{broken-json"
                _raises(
                    "Domain link collection failed",
                    lambda: page_io.save_page_json(work_dir, page),
                )
            finally:
                page_io._context_for_page = original_context
                bpy.context.scene[layer_links.LINK_PROP] = original_raw_links
            assert page_path.read_bytes() == before

            project_uid = domain_projection.ensure_project_uid(work)
            page_uid = domain_projection.ensure_page_uid(page, project_uid)
            duplicate_uid = derived_uid(UIDKind.NODE, page_uid, "duplicate")
            try:
                domain_projection.page_document_from_payload(
                    project_uid=project_uid,
                    page_uid=page_uid,
                    revision=0,
                    work_payload={},
                    page_payload={
                        "id": str(page.id),
                        "balloons": [
                            {"id": "balloon_a", "nodeUid": duplicate_uid},
                            {"id": "balloon_b", "nodeUid": duplicate_uid},
                        ],
                    },
                )
            except ValueError as exc:
                assert "duplicate Domain node UID" in str(exc)
            else:
                raise AssertionError("duplicate Domain node UID was accepted")

            from bmanga_phase3_projection_failure.io import domain_runtime

            repository = domain_runtime.repository_for(work_dir)
            project_document = repository.load_project()
            page_document = repository.load_page(page_uid)

            preserved_native_uid = derived_uid(
                UIDKind.NODE,
                page_uid,
                "native-parent-change",
            )
            merged_native = (
                domain_projection_preservation.merge_payload_values(
                    (
                        {
                            "id": "native_parent_change",
                            "nodeUid": "",
                            "parentKey": "p0001:c02",
                        },
                    ),
                    (
                        {
                            "id": "native_parent_change",
                            "nodeUid": preserved_native_uid,
                            "parentKey": "p0001:c01",
                        },
                    ),
                )
            )
            assert len(merged_native) == 1
            assert merged_native[0]["nodeUid"] == preserved_native_uid
            assert merged_native[0]["parentKey"] == "p0001:c02"

            project_document.settings["domainOnlyProject"] = {
                "future": True
            }
            page_document.settings["domainOnlyPage"] = {"future": True}
            gp_uid = derived_uid(UIDKind.NODE, page_uid, "contextless-gp")
            link_uid = derived_uid(UIDKind.LINK, page_uid, "contextless-link")
            page_document.nodes[gp_uid] = DomainNode(
                gp_uid,
                "gp",
                "contextless_gp",
                native_uid="contextless_gp",
            )
            page_document.children[gp_uid] = []
            page_document.children[page_document.root_uid].append(gp_uid)
            managed_link_nodes = []
            for suffix in ("two", "three"):
                uid = derived_uid(
                    UIDKind.NODE,
                    page_uid,
                    f"contextless-gp-{suffix}",
                )
                display_id = f"contextless_gp_{suffix}"
                page_document.nodes[uid] = DomainNode(
                    uid,
                    "gp",
                    display_id,
                    native_uid=display_id,
                )
                page_document.children[uid] = []
                page_document.children[
                    page_document.root_uid
                ].append(uid)
                managed_link_nodes.append((uid, display_id))
            page_document.links[link_uid] = DomainLink(
                link_uid,
                "layer_group",
                (gp_uid,),
            )
            extension_uid = derived_uid(
                UIDKind.NODE,
                page_uid,
                "domain-only-extension",
            )
            extension_link_uid = derived_uid(
                UIDKind.LINK,
                page_uid,
                "domain-only-extension",
            )
            page_document.nodes[extension_uid] = DomainNode(
                extension_uid,
                "future-extension",
                "future_extension",
                settings={"future": True},
            )
            page_document.children[extension_uid] = []
            page_document.children[page_document.root_uid].append(
                extension_uid
            )
            page_document.links[extension_link_uid] = DomainLink(
                extension_link_uid,
                "future-link",
                (extension_uid,),
            )
            repository.checkpoint(project_document, (page_document,))
            project_document = repository.load_project()
            page_document = repository.load_page(page_uid)
            domain_runtime.install_store(
                work_dir,
                project_document,
                (page_document,),
            )

            # Domain→Blenderの実再投影を通しても、UI管理外linkはSceneの
            # linked-duplicate mapへ変質させない。
            domain_projection.apply_page_document(
                page,
                page_document,
                context=bpy.context,
            )
            projected_mapping = layer_links.load_map_strict(bpy.context)
            assert f"gp:contextless_gp" not in projected_mapping
            assert all(
                group
                not in {
                    f"domain_{link_uid}",
                    f"domain_{extension_link_uid}",
                }
                for group in projected_mapping.values()
            )

            original_context = page_io._context_for_page
            try:
                page_io._context_for_page = lambda _page: None
                page_io.save_page_json(work_dir, page)
            finally:
                page_io._context_for_page = original_context
            contextless_saved = repository.load_page(page_uid)
            assert gp_uid in contextless_saved.nodes
            assert link_uid in contextless_saved.links
            assert extension_uid in contextless_saved.nodes
            assert extension_link_uid in contextless_saved.links
            assert contextless_saved.settings["domainOnlyPage"] == {
                "future": True
            }
            assert repository.load_project().settings[
                "domainOnlyProject"
            ] == {"future": True}

            # 実ページcontextから保存してもUIに無いDomain拡張を削除しない。
            original_context = page_io._context_for_page
            try:
                page_io._context_for_page = lambda _page: bpy.context
                page_io.save_page_json(work_dir, page)
            finally:
                page_io._context_for_page = original_context
            context_saved = repository.load_page(page_uid)
            assert extension_uid in context_saved.nodes
            assert extension_link_uid in context_saved.links
            assert context_saved.nodes[extension_uid].settings == {
                "future": True
            }

            managed_mapping = {
                f"gp:{display_id}": "managed_unlink_probe"
                for _uid, display_id in managed_link_nodes
            }
            original_context = page_io._context_for_page
            try:
                page_io._context_for_page = lambda _page: bpy.context
                layer_links._save_map(bpy.context, managed_mapping)
                page_io.save_page_json(work_dir, page)
                linked = repository.load_page(page_uid)
                assert len(
                    [
                        value
                        for value in linked.links.values()
                        if value.kind == "linked-duplicate"
                    ]
                ) == 1
                assert layer_links.unlink_uids(
                    bpy.context,
                    list(managed_mapping),
                ) == 2
                page_io.save_page_json(work_dir, page)
            finally:
                page_io._context_for_page = original_context
            unlinked = repository.load_page(page_uid)
            assert not [
                value
                for value in unlinked.links.values()
                if value.kind == "linked-duplicate"
            ], "explicitly removed UI link was restored from Domain"
            assert link_uid in unlinked.links
            assert extension_link_uid in unlinked.links
            before = page_path.read_bytes()

            original_sync = object_state_sync.sync_from_blender_object
            original_log_exception = handlers._logger.exception
            try:
                handlers._logger.exception = lambda *_args, **_kwargs: None
                object_state_sync.sync_from_blender_object = (
                    lambda *_args: (_ for _ in ()).throw(
                        RuntimeError("object fault")
                    )
                )
                assert not handlers.save_scene_work_to_disk(
                    bpy.context,
                    reason="projection fault test",
                )
            finally:
                object_state_sync.sync_from_blender_object = original_sync
                handlers._logger.exception = original_log_exception
            assert page_path.read_bytes() == before

            # save_pre内のDomain失敗はBlender本体保存の成功扱いへ漏らさず、
            # 呼び出し元へFalseを返して新規page.blendもrollbackする。
            page_blend = paths.page_blend_path(work_dir, page.id)
            page_blend.unlink(missing_ok=True)
            original_sync = object_state_sync.sync_from_blender_object
            original_log_exception = handlers._logger.exception
            try:
                handlers._logger.exception = lambda *_args, **_kwargs: None
                object_state_sync.sync_from_blender_object = (
                    lambda *_args: (_ for _ in ()).throw(
                        RuntimeError("native object fault")
                    )
                )
                assert not blend_io.save_page_blend(work_dir, page.id)
            finally:
                object_state_sync.sync_from_blender_object = original_sync
                handlers._logger.exception = original_log_exception
            assert not page_blend.exists()
            assert page_path.read_bytes() == before

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
