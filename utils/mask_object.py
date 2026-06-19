"""コマ/ページマスクの旧実装を coma_plane / paper_bg に統合した shim.

2026-05-02 リアーキテクチャで以下の方針に変更:

- **コマ平面 Mesh** (``utils/coma_plane.py``) がコマ Collection 直下に置かれ、
  ビューポート背景色 + Boolean マスク を兼用する。 旧 ``coma_mask_*`` Mesh
  は廃止。
- **ページマスク** は ``utils/paper_bg_object.py`` の paper_bg Mesh を Boolean
  reference にそのまま兼用する。 旧 ``page_mask_*`` Mesh は廃止。
- 旧 ``__masks__`` Collection は ``purge_legacy_masks_collection`` で削除。

本モジュールは旧 API を呼ぶ既存 operator (``coma_knife_cut_op`` /
``mask_object_op`` / ``repair_op``) のための薄い委譲レイヤとして残す:

- ``regenerate_all_masks(scene, work)``: paper_bg と coma_plane を再生成し、
  旧 ``__masks__`` 配下の Object / Collection を掃除する
- ``remove_orphan_masks(scene, work)``: 同上の掃除のみ

旧 prefix / 識別フラグ定数は legacy 検出用に残す (``__papers__`` の paper_bg
は別の prefix なので衝突しない)。
"""

from __future__ import annotations

import bpy

from . import log
from . import object_naming as on
from . import outliner_model as om

_logger = log.get_logger(__name__)

# 旧 __masks__ Collection の名前 / 識別子 (purge 用)
LEGACY_MASKS_COLLECTION_NAME = "__masks__"
LEGACY_MASKS_COLLECTION_BMANGA_ID = "__masks_root__"

# 旧 mask Object の name prefix (purge 用)
PAGE_MASK_NAME_PREFIX = "page_mask_"
COMA_MASK_NAME_PREFIX = "coma_mask_"
PAGE_MASK_MESH_PREFIX = "page_mask_mesh_"
COMA_MASK_MESH_PREFIX = "coma_mask_mesh_"

PROP_MASK_KIND = "bmanga_mask_kind"  # legacy: "page" | "coma"
PROP_MASK_OWNER_ID = "bmanga_mask_owner_id"


# ---------------- Backward-compatible shims ----------------


def regenerate_all_masks(scene: bpy.types.Scene, work) -> dict:
    """全マスクを再生成。 paper_bg と coma_plane に委譲し、 旧 __masks__ を掃除."""
    result = {"page_masks": 0, "coma_masks": 0}
    if scene is None or work is None:
        return result
    try:
        from . import paper_bg_object as _pbg

        result["page_masks"] = int(_pbg.regenerate_all_paper_bgs(scene, work) or 0)
    except Exception:  # noqa: BLE001
        _logger.exception("regenerate_all_masks: paper_bg failed")
    try:
        from . import coma_plane as _cp
        from . import page_file_scene

        page_filter = page_file_scene.coma_runtime_page_filter(scene)
        if page_filter is not None and not page_filter:
            page_file_scene.purge_coma_runtime_data(scene, set())
        else:
            mask_work = page_file_scene.work_for_pages(work, page_filter)
            result["coma_masks"] = int(_cp.regenerate_all_coma_planes(scene, mask_work) or 0)
    except Exception:  # noqa: BLE001
        _logger.exception("regenerate_all_masks: coma_plane failed")
    purge_legacy_masks_collection()
    return result


def remove_orphan_masks(scene: bpy.types.Scene, work) -> int:
    """旧 ``__masks__`` 配下の orphan Object と Mesh を一括削除. 戻り値: 削除件数."""
    _ = scene, work
    return purge_legacy_masks_collection()


# ---------------- Legacy purge ----------------


def purge_legacy_masks_collection() -> int:
    """旧 ``__masks__`` Collection と配下の Object / Mesh を全て削除する.

    旧アーキテクチャ (2026-05-02 以前) で生成された ``page_mask_*`` /
    ``coma_mask_*`` Object は不要になったため、 起動時に掃除する。
    戻り値: 削除した bpy.data エントリ件数。
    """
    removed = 0
    # Collection 配下の Object を全部削除
    coll = on.find_collection_by_bmanga_id(LEGACY_MASKS_COLLECTION_BMANGA_ID, kind="masks_root")
    if coll is None:
        coll = bpy.data.collections.get(LEGACY_MASKS_COLLECTION_NAME)
    if coll is not None:
        for obj in list(coll.objects):
            mesh_data = obj.data if obj.type == "MESH" else None
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
                removed += 1
            except Exception:  # noqa: BLE001
                pass
            if mesh_data is not None and mesh_data.users == 0:
                try:
                    bpy.data.meshes.remove(mesh_data)
                except Exception:  # noqa: BLE001
                    pass
        # Collection 自体を強制削除 (root + scene から外して do_unlink で remove)
        try:
            for parent in list(bpy.data.collections):
                if coll.name in parent.children:
                    try:
                        parent.children.unlink(coll)
                    except Exception:  # noqa: BLE001
                        pass
            scene = bpy.context.scene if bpy.context is not None else None
            if scene is not None and scene.collection is not None and coll.name in scene.collection.children:
                try:
                    scene.collection.children.unlink(coll)
                except Exception:  # noqa: BLE001
                    pass
            try:
                coll.use_fake_user = False
            except Exception:  # noqa: BLE001
                pass
            bpy.data.collections.remove(coll, do_unlink=True)
            removed += 1
        except Exception:  # noqa: BLE001
            _logger.exception("purge_legacy_masks_collection: remove __masks__ failed")
    # 名前 prefix で残っている孤立 Object も掃除
    for obj in list(bpy.data.objects):
        kind = obj.get(PROP_MASK_KIND)
        if kind not in {"page", "coma"}:
            continue
        if not (
            obj.name.startswith(PAGE_MASK_NAME_PREFIX)
            or obj.name.startswith(COMA_MASK_NAME_PREFIX)
        ):
            continue
        mesh_data = obj.data if obj.type == "MESH" else None
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
        except Exception:  # noqa: BLE001
            pass
        if mesh_data is not None and mesh_data.users == 0:
            try:
                bpy.data.meshes.remove(mesh_data)
            except Exception:  # noqa: BLE001
                pass
    # 名前 prefix で残っている orphan Mesh も掃除
    for mesh in list(bpy.data.meshes):
        if mesh.name.startswith(PAGE_MASK_MESH_PREFIX) or mesh.name.startswith(COMA_MASK_MESH_PREFIX):
            if mesh.users == 0:
                try:
                    bpy.data.meshes.remove(mesh)
                    removed += 1
                except Exception:  # noqa: BLE001
                    pass
    return removed


def register() -> None:
    pass


def unregister() -> None:
    pass
