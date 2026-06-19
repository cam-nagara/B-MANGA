"""Blender 実機用: 旧アーキ (``__papers__`` / ``__masks__``) からの自動 migration 回帰テスト.

ユーザー報告 (2026-05-03): 古い work.blend を開いても旧 ``__papers__`` /
``__masks__`` Collection が残ったまま、 paper_bg がページ Collection 直下に
来ない / coma_plane が生成されない。

この test は ``mirror_work_to_outliner`` (load_post の主役) を呼んだ後で:
- ``__papers__`` Collection と配下の ``page_paper_bg_*`` が **purge** される
- ``__masks__`` Collection と配下の旧 ``page_mask_*`` が purge され、現在の
  非表示 ``coma_mask_*`` は Boolean 参照として再生成される
- 各ページ Collection 直下に ``page_paper_bg_<page>`` が生成される
- 各コマ Collection 直下に ``coma_plane_<page>_<coma>`` が生成される
を検証する。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _create_legacy_collections(scene, page_id: str, coma_id: str) -> None:
    """旧アーキの ``__papers__`` / ``__masks__`` Collection と配下 Object を捏造."""
    papers_coll = bpy.data.collections.new("__papers__")
    papers_coll["bmanga_kind"] = "papers_root"
    papers_coll["bmanga_id"] = "__papers_root__"
    scene.collection.children.link(papers_coll)
    paper_bg_mesh = bpy.data.meshes.new(f"paper_bg_mesh_main")
    paper_bg = bpy.data.objects.new(f"page_paper_bg_{page_id}", paper_bg_mesh)
    paper_bg["bmanga_paper_bg_kind"] = "page"
    paper_bg["bmanga_paper_bg_page_id"] = page_id
    papers_coll.objects.link(paper_bg)

    masks_coll = bpy.data.collections.new("__masks__")
    masks_coll["bmanga_kind"] = "masks_root"
    masks_coll["bmanga_id"] = "__masks_root__"
    scene.collection.children.link(masks_coll)
    page_mask_mesh = bpy.data.meshes.new(f"page_mask_mesh_{page_id}")
    page_mask = bpy.data.objects.new(f"page_mask_{page_id}", page_mask_mesh)
    page_mask["bmanga_mask_kind"] = "page"
    page_mask["bmanga_mask_owner_id"] = page_id
    masks_coll.objects.link(page_mask)
    coma_mask_mesh = bpy.data.meshes.new(f"coma_mask_mesh_{page_id}_{coma_id}")
    coma_mask = bpy.data.objects.new(f"coma_mask_{page_id}_{coma_id}", coma_mask_mesh)
    coma_mask["bmanga_mask_kind"] = "coma"
    coma_mask["bmanga_mask_owner_id"] = f"{page_id}:{coma_id}"
    masks_coll.objects.link(coma_mask)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_legacy_migration_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        # まず legacy 状態を捏造 → そのあと bmanga.work_new で新アーキで作品作成
        # → mirror_work_to_outliner が走る → migration が起きるかを検証
        scene = bpy.context.scene
        # bmanga.work_new の前段階で legacy Collection を捏造する必要がある。
        # bmanga.work_new 内で mirror_work_to_outliner が呼ばれる流れを利用。
        # (factory_settings 直後は scene.collection.children に "Collection" のみ)

        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "Migration.bmanga"))
        assert result == {"FINISHED"}, result

        from bmanga_dev.core.work import get_work
        from bmanga_dev.utils import layer_object_sync as los
        from bmanga_dev.utils import paper_bg_object as pbg
        from bmanga_dev.utils import mask_object as mo
        from bmanga_dev.utils import coma_plane as cp

        work = get_work(bpy.context)
        assert work is not None
        page = work.pages[0]
        assert len(page.comas) >= 1
        coma = page.comas[0]

        # ここで legacy 状態を捏造して mirror を再実行 (旧 work.blend を開いた相当)
        _create_legacy_collections(scene, page.id, coma.id)
        # 確認: legacy が確かに存在する
        assert bpy.data.collections.get("__papers__") is not None
        assert bpy.data.collections.get("__masks__") is not None
        assert bpy.data.objects.get(f"page_paper_bg_{page.id}") is not None
        assert bpy.data.objects.get(f"page_mask_{page.id}") is not None
        assert bpy.data.objects.get(f"coma_mask_{page.id}_{coma.id}") is not None

        # mirror_work_to_outliner を再実行 → migration を期待
        los.mirror_work_to_outliner(scene, work)

        # ---- 検証 ----
        # 1. __masks__ Collection は完全 purge
        assert bpy.data.collections.get("__masks__") is None, (
            f"__masks__ should be purged but exists. children={bpy.data.collections}"
        )
        # 2. page_mask_* Object と旧 mask custom property は完全 purge。
        # coma_mask_* は現在の Boolean 参照用の非表示実体なので存在が正しい。
        for obj in bpy.data.objects:
            assert not obj.name.startswith("page_mask_"), obj.name
            assert obj.get(mo.PROP_MASK_KIND) not in {"page", "coma"}, obj.name
        mask_obj = cp.find_coma_mask_object(page.id, coma.id)
        assert mask_obj is not None, "current coma_mask should be regenerated"
        assert mask_obj.hide_viewport is True
        assert mask_obj.hide_render is True
        assert mask_obj.hide_select is True
        assert mask_obj.get(cp.PROP_COMA_MASK_KIND) == "coma_mask"
        # 3. coma_plane_<page>_<coma> がコマ Collection 直下にある
        plane_obj = cp.find_coma_plane_object(page.id, coma.id)
        assert plane_obj is not None, "coma_plane should be created during mirror"
        # 4. page_paper_bg_<page> はページ Collection 直下にある (__papers__ 内ではない)
        paper_bg_obj = bpy.data.objects.get(f"{pbg.PAPER_BG_NAME_PREFIX}{page.id}")
        assert paper_bg_obj is not None
        # 全 users_collection を見て、 __papers__ には属していないこと
        for c in paper_bg_obj.users_collection:
            assert c.name != "__papers__", (
                f"paper_bg should not be in __papers__ anymore: {[c.name for c in paper_bg_obj.users_collection]}"
            )
        # かつ、 ページ Collection (bmanga_id == page.id) 直下にあること
        in_page_coll = any(
            str(c.get("bmanga_id", "") or "") == page.id
            for c in paper_bg_obj.users_collection
        )
        assert in_page_coll, (
            f"paper_bg should be in page Collection: {[c.name for c in paper_bg_obj.users_collection]}"
        )
        # 5. __papers__ Collection も完全 purge
        assert bpy.data.collections.get("__papers__") is None, (
            f"__papers__ should be purged"
        )
    finally:
        if mod is not None:
            mod.unregister()

    print("BMANGA_LEGACY_MIGRATION_OK")


if __name__ == "__main__":
    main()
