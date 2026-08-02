"""ページ／コマ遷移の差分キャッシュと更新検知をBlender実機で確認。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_file_transition_cache"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def _module(name: str):
    return sys.modules[f"{PACKAGE}.{name}"]


def _configure_fast_preview(scene, work) -> None:
    scene.bmanga_page_preview_resolution_percentage = 2.0
    work.page_preview_scale_percentage = 2.0
    work.auto_render_coma_thumb_on_return = False


def _assert_preview_variants(work, page) -> None:
    from PIL import Image

    preview = _module("utils.page_preview_object")
    scene = bpy.context.scene
    index = int(work.active_page_index)
    work_path = preview.ensure_preview_png(
        work,
        page,
        index,
        current=True,
        scene=scene,
        force=True,
        variant=preview.PREVIEW_RENDER_VARIANT_WORK,
    )
    detail_path = preview.ensure_preview_png(
        work,
        page,
        index,
        current=True,
        scene=scene,
        force=True,
        variant=preview.PREVIEW_RENDER_VARIANT_DETAIL,
    )
    assert work_path is not None and detail_path is not None
    assert work_path != detail_path
    assert work_path.name == "page_preview.png"
    assert detail_path.name == "page_preview.detail.png"
    with Image.open(work_path) as image:
        assert image.info[preview.PREVIEW_RENDER_VARIANT_KEY] == "work"
    with Image.open(detail_path) as image:
        assert image.info[preview.PREVIEW_RENDER_VARIANT_KEY] == "detail"
    before = (work_path.stat().st_mtime_ns, detail_path.stat().st_mtime_ns)
    preview.ensure_preview_png(
        work,
        page,
        index,
        current=True,
        scene=scene,
        force=False,
        variant=preview.PREVIEW_RENDER_VARIANT_WORK,
    )
    preview.ensure_preview_png(
        work,
        page,
        index,
        current=True,
        scene=scene,
        force=False,
        variant=preview.PREVIEW_RENDER_VARIANT_DETAIL,
    )
    after = (work_path.stat().st_mtime_ns, detail_path.stat().st_mtime_ns)
    assert after == before, "未変更プレビューが再生成されました"


def _assert_sidecar_change_invalidates(work_dir: Path) -> None:
    cache = _module("utils.sidecar_load_cache")
    paths = _module("utils.paths")
    blend_path = Path(bpy.data.filepath)
    scene = bpy.context.scene
    assert cache.current(scene, work_dir, blend_path)
    page_id = str(scene.bmanga_current_page_id)
    page_json = paths.page_meta_path(work_dir, page_id)
    original = page_json.read_text(encoding="utf-8")
    data = json.loads(original)
    data["_transitionCacheProbe"] = True
    page_json.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        assert not cache.current(scene, work_dir, blend_path)
    finally:
        page_json.write_text(original, encoding="utf-8")
    assert cache.current(scene, work_dir, blend_path)


def _assert_embedded_revision_binding(work_dir: Path, work, page) -> None:
    domain_projection = _module("io.domain_projection")
    domain_runtime = _module("io.domain_runtime")
    handlers = _module("utils.handlers")
    repository = domain_runtime.repository_for(work_dir)
    project_document = repository.load_project()
    page_uid = domain_projection.ensure_page_uid(
        page,
        project_document.project_uid,
    )
    page_document = repository.load_page(page_uid)
    work[domain_projection.PROJECT_REVISION_PROP] = (
        project_document.revision + 100
    )
    page[domain_projection.PAGE_REVISION_PROP] = page_document.revision + 100
    handlers._bind_embedded_domain_identifiers(  # noqa: SLF001
        work,
        project_document,
        (page_document,),
    )
    assert (
        int(work.get(domain_projection.PROJECT_REVISION_PROP, -1))
        == project_document.revision
    )
    assert (
        int(page.get(domain_projection.PAGE_REVISION_PROP, -1))
        == page_document.revision
    )


def main() -> None:
    module = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_transition_cache_"))
    try:
        module = _load_addon()
        work_dir = temp_root / "TransitionCache.bmanga"
        assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        _configure_fast_preview(bpy.context.scene, work)
        assert bpy.ops.bmanga.open_page_file(index=0) == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        page = work.pages[work.active_page_index]
        _configure_fast_preview(bpy.context.scene, work)
        _assert_preview_variants(work, page)
        assert bpy.ops.bmanga.work_save() == {"FINISHED"}
        _assert_sidecar_change_invalidates(work_dir)
        _assert_embedded_revision_binding(work_dir, work, page)

        runtime = _module("utils.file_transition_runtime")
        runtime.arm_scene(bpy.context.scene)
        assert not runtime.scene_content_dirty()
        probe_mesh = bpy.data.meshes.new("BMangaTransitionDirtyProbe")
        probe = bpy.data.objects.new("BMangaTransitionDirtyProbe", probe_mesh)
        bpy.context.scene.collection.objects.link(probe)
        bpy.context.view_layer.update()
        runtime._on_depsgraph_update_post(  # noqa: SLF001
            bpy.context.scene,
            SimpleNamespace(updates=(SimpleNamespace(id=probe),)),
        )
        assert runtime.scene_content_dirty(), "実オブジェクト変更を検出できません"
        runtime.mark_scene_clean()
        assert not runtime.scene_content_dirty()
        probe.location.x += 2.0
        assert runtime.scene_content_dirty(), "即時の座標変更を検出できません"
        runtime.mark_scene_clean()
        material = bpy.data.materials.new("BMangaTransitionDirtyMaterial")
        probe.data.materials.append(material)
        runtime.mark_scene_clean()
        material.diffuse_color = (0.1, 0.6, 0.3, 1.0)
        assert runtime.scene_content_dirty(), "即時の材質変更を検出できません"

        print("BMANGA_FILE_TRANSITION_CACHE_OK", flush=True)
    finally:
        if module is not None:
            module.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


main()
