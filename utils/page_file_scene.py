"""ページ一覧ファイルとページ用blendファイルの判定ヘルパ."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import bpy

from . import paths

ROLE_WORK = "work"
ROLE_PAGE = "page"
ROLE_COMA = "coma"
ROLE_UNKNOWN = "unknown"


def find_work_root(blend_path: Path) -> Path | None:
    p = Path(blend_path).parent
    for _ in range(6):
        if p.suffix == paths.BNAME_DIR_SUFFIX:
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


def relative_parts(blend_path: Path, work_dir: Path | None = None) -> tuple[str, ...]:
    blend_path = Path(blend_path)
    root = Path(work_dir) if work_dir is not None else find_work_root(blend_path)
    if root is None:
        return ()
    try:
        return blend_path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return ()


def role_from_parts(parts: tuple[str, ...]) -> tuple[str, str, str]:
    if len(parts) == 1 and parts[0] == paths.WORK_BLEND_NAME:
        return ROLE_WORK, "", ""
    if (
        len(parts) == 2
        and paths.is_valid_page_id(parts[0])
        and parts[1] == paths.PAGE_BLEND_NAME
    ):
        return ROLE_PAGE, parts[0], ""
    if (
        len(parts) == 3
        and paths.is_valid_page_id(parts[0])
        and paths.is_valid_coma_id(parts[1])
        and parts[2] == f"{parts[1]}.blend"
    ):
        return ROLE_COMA, parts[0], parts[1]
    return ROLE_UNKNOWN, "", ""


def role_from_path(blend_path: Path, work_dir: Path | None = None) -> tuple[str, str, str]:
    return role_from_parts(relative_parts(Path(blend_path), work_dir))


def current_role(context=None) -> tuple[str, str, str]:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    work = getattr(scene, "bname_work", None) if scene is not None else None
    work_dir_text = str(getattr(work, "work_dir", "") or "")
    work_dir = Path(work_dir_text) if work_dir_text else None
    filepath = str(getattr(bpy.data, "filepath", "") or "")
    if not filepath:
        return ROLE_UNKNOWN, "", ""
    return role_from_path(Path(filepath), work_dir)


def current_page_id(scene=None) -> str:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return ""
    page_id = str(getattr(scene, "bname_current_page_id", "") or "")
    return page_id if paths.is_valid_page_id(page_id) else ""


def find_page_index(work, page_id: str) -> int:
    if work is None or not paths.is_valid_page_id(page_id):
        return -1
    for index, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") == page_id:
            return index
    return -1


def set_work_list_state(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    try:
        from ..core.mode import MODE_PAGE, set_mode

        set_mode(MODE_PAGE, context)
    except Exception:  # noqa: BLE001
        pass
    scene.bname_current_page_id = ""
    scene.bname_current_coma_id = ""
    scene.bname_current_coma_page_id = ""
    if hasattr(scene, "bname_overview_mode"):
        scene.bname_overview_mode = True
    if hasattr(scene, "bname_active_layer_kind"):
        scene.bname_active_layer_kind = "page"


def set_page_edit_state(context, page_id: str) -> bool:
    if not paths.is_valid_page_id(page_id):
        return False
    scene = getattr(context, "scene", None)
    work = getattr(scene, "bname_work", None) if scene is not None else None
    if scene is None or work is None:
        return False
    index = find_page_index(work, page_id)
    if index < 0:
        return False
    try:
        from ..core.mode import MODE_PAGE, set_mode

        set_mode(MODE_PAGE, context)
    except Exception:  # noqa: BLE001
        pass
    work.active_page_index = index
    scene.bname_current_page_id = page_id
    scene.bname_current_coma_id = ""
    scene.bname_current_coma_page_id = ""
    if hasattr(scene, "bname_overview_mode"):
        scene.bname_overview_mode = True
    if hasattr(scene, "bname_active_layer_kind"):
        scene.bname_active_layer_kind = "page"
    return True


def is_page_edit_scene(scene=None) -> bool:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return False
    role, page_id, _coma_id = current_role(bpy.context)
    if role == ROLE_PAGE and paths.is_valid_page_id(page_id):
        return True
    if role == ROLE_WORK:
        return False
    return bool(current_page_id(scene))


def is_work_list_scene(scene=None) -> bool:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return False
    role, _page_id, _coma_id = current_role(bpy.context)
    if role == ROLE_WORK:
        return True
    filepath = str(getattr(bpy.data, "filepath", "") or "")
    return (
        (not filepath)
        and bool(getattr(scene, "bname_overview_mode", False))
        and not current_page_id(scene)
    )


def structural_page_filter(scene=None) -> set[str] | None:
    role, page_id_from_path, _coma_id = current_role(bpy.context)
    if role == ROLE_PAGE and paths.is_valid_page_id(page_id_from_path):
        return {page_id_from_path}
    page_id = current_page_id(scene)
    if page_id and is_page_edit_scene(scene):
        return {page_id}
    return None


def content_page_filter(scene=None) -> set[str] | None:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return None
    role, page_id_from_path, _coma_id = current_role(bpy.context)
    if role == ROLE_PAGE and paths.is_valid_page_id(page_id_from_path):
        return {page_id_from_path}
    page_id = current_page_id(scene)
    if page_id and is_page_edit_scene(scene):
        return {page_id}
    if is_work_list_scene(scene):
        return set()
    return None


class PageSubsetWork(SimpleNamespace):
    """全作品データから指定ページだけを見せる軽量 proxy."""

    def __init__(self, work, page_ids: set[str]):
        super().__init__()
        self._source_work = work
        pages_with_indices = [
            (index, page)
            for index, page in enumerate(getattr(work, "pages", []) or [])
            if str(getattr(page, "id", "") or "") in page_ids
        ]
        self.pages = [page for _index, page in pages_with_indices]
        self._source_page_indices = [index for index, _page in pages_with_indices]
        self.active_page_index = 0 if self.pages else -1
        self.loaded = bool(getattr(work, "loaded", False))

    def original_page_index(self, page_index: int) -> int:
        """この軽量 work 内の page_index を元の作品内 page_index へ戻す."""
        try:
            index = int(page_index)
        except (TypeError, ValueError):
            return -1
        if 0 <= index < len(self._source_page_indices):
            return int(self._source_page_indices[index])
        return -1

    def __getattr__(self, name: str):
        return getattr(self._source_work, name)


def work_for_pages(work, page_ids: set[str] | None):
    if page_ids is None:
        return work
    return PageSubsetWork(work, page_ids)


def _object_page_id(obj) -> str:
    parent_key = str(obj.get("bname_parent_key", "") or "")
    if ":" in parent_key:
        return parent_key.split(":", 1)[0]
    if paths.is_valid_page_id(parent_key):
        return parent_key
    for prop in (
        "bname_paper_bg_page_id",
        "bname_paper_guide_page_id",
    ):
        page_id = str(obj.get(prop, "") or "")
        if paths.is_valid_page_id(page_id):
            return page_id
    for prop in (
        "bname_coma_plane_owner_id",
        "bname_coma_border_owner_id",
        "bname_coma_white_margin_owner_id",
    ):
        owner = str(obj.get(prop, "") or "")
        if ":" in owner:
            page_id = owner.split(":", 1)[0]
            if paths.is_valid_page_id(page_id):
                return page_id
    return ""


_CONTENT_KINDS = {
    "balloon",
    "text",
    "image",
    "raster",
    "gp",
    "effect",
    "effect_display",
    "effect_frame_source",
    "effect_shape_source",
    "effect_density_source",
}


def _object_is_page_content(obj) -> bool:
    kind = str(obj.get("bname_kind", "") or "")
    if kind in _CONTENT_KINDS:
        return True
    for prop in (
        "bname_balloon_fill_kind",
        "bname_balloon_source_kind",
        "bname_balloon_clip_mask_kind",
    ):
        if str(obj.get(prop, "") or ""):
            return True
    return False


def purge_page_content_data(scene, keep_page_id: str = "") -> int:
    """ページ編集に不要な中身データを取り除く.

    ``keep_page_id`` が空なら全ページの中身を取り除く。ページ ID が渡された
    場合は、そのページ以外の中身だけを取り除く。
    """
    keep_page_id = str(keep_page_id or "")
    if keep_page_id and not paths.is_valid_page_id(keep_page_id):
        return 0
    removed = 0
    for obj in list(bpy.data.objects):
        if not _object_is_page_content(obj):
            continue
        obj_page_id = _object_page_id(obj)
        if not keep_page_id or (obj_page_id and obj_page_id != keep_page_id):
            data = getattr(obj, "data", None)
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
                removed += 1
            except Exception:  # noqa: BLE001
                continue
            if data is not None and getattr(data, "users", 0) == 0:
                for datablocks in (
                    bpy.data.meshes,
                    bpy.data.curves,
                    getattr(bpy.data, "grease_pencils", []),
                ):
                    try:
                        if data.name in datablocks:
                            datablocks.remove(data)
                            break
                    except Exception:  # noqa: BLE001
                        pass
    return removed


def purge_other_page_data(scene, page_id: str) -> int:
    """ページ用blendから対象外ページの編集実体を取り除く."""
    if not paths.is_valid_page_id(page_id):
        return 0
    removed = purge_page_content_data(scene, page_id)

    def _collection_page_id(coll) -> str:
        coll_id = str(coll.get("bname_id", "") or "")
        coll_kind = str(coll.get("bname_kind", "") or "")
        if coll_kind == "page" and paths.is_valid_page_id(coll_id):
            return coll_id
        if coll_kind == "coma" and ":" in coll_id:
            candidate = coll_id.split(":", 1)[0]
            if paths.is_valid_page_id(candidate):
                return candidate
        parent_key = str(coll.get("bname_parent_key", "") or "")
        if ":" in parent_key:
            parent_key = parent_key.split(":", 1)[0]
        if paths.is_valid_page_id(parent_key):
            return parent_key
        if paths.is_valid_page_id(coll.name):
            return coll.name
        return ""

    collections = sorted(
        list(bpy.data.collections),
        key=lambda coll: 1 if str(coll.get("bname_kind", "") or "") == "page" else 0,
    )
    for coll in collections:
        coll_page_id = _collection_page_id(coll)
        if coll_page_id and coll_page_id != page_id:
            try:
                bpy.data.collections.remove(coll, do_unlink=True)
                removed += 1
            except Exception:  # noqa: BLE001
                pass
    return removed


_WORK_LIST_RUNTIME_KIND_PROPS = {
    "bname_coma_plane_kind",
    "bname_coma_mask_kind",
    "bname_coma_border_kind",
    "bname_coma_white_margin_kind",
    "bname_paper_bg_kind",
    "bname_paper_guide_kind",
    "bname_work_info_text_kind",
}

_WORK_LIST_RUNTIME_OBJECT_NAMES = {
    "bname_master_sketch",
    "BName_EffectLines",
}


def _object_is_work_list_runtime(obj) -> bool:
    if obj.name in _WORK_LIST_RUNTIME_OBJECT_NAMES:
        return True
    if _object_is_page_content(obj):
        return True
    if str(obj.get("bname_kind", "") or "") == "page_preview":
        return False
    return any(str(obj.get(prop, "") or "") for prop in _WORK_LIST_RUNTIME_KIND_PROPS)


def _collection_is_work_list_runtime(coll) -> bool:
    kind = str(coll.get("bname_kind", "") or "")
    if kind in {"page", "coma", "folder"}:
        return True
    coll_id = str(coll.get("bname_id", "") or "")
    if kind == "page_preview":
        return False
    if paths.is_valid_page_id(coll_id) or paths.is_valid_page_id(coll.name):
        return True
    if ":" in coll_id:
        page_id, _rest = coll_id.split(":", 1)
        return paths.is_valid_page_id(page_id)
    parent_key = str(coll.get("bname_parent_key", "") or "")
    if ":" in parent_key:
        page_id, _rest = parent_key.split(":", 1)
        return paths.is_valid_page_id(page_id)
    return False


def purge_work_list_runtime_data(scene) -> int:
    """ページ一覧ファイルに残ってはいけないページ実体を取り除く."""
    _ = scene
    removed = 0
    for obj in list(bpy.data.objects):
        if not _object_is_work_list_runtime(obj):
            continue
        data = getattr(obj, "data", None)
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
        except Exception:  # noqa: BLE001
            continue
        if data is not None and getattr(data, "users", 0) == 0:
            for datablocks in (
                bpy.data.meshes,
                bpy.data.curves,
                getattr(bpy.data, "grease_pencils", []),
                bpy.data.fonts,
            ):
                try:
                    if data.name in datablocks:
                        datablocks.remove(data)
                        break
                except Exception:  # noqa: BLE001
                    pass
    collections = sorted(
        list(bpy.data.collections),
        key=lambda coll: 1 if str(coll.get("bname_kind", "") or "") == "page" else 0,
    )
    for coll in collections:
        if not _collection_is_work_list_runtime(coll):
            continue
        try:
            bpy.data.collections.remove(coll, do_unlink=True)
            removed += 1
        except Exception:  # noqa: BLE001
            pass
    return removed
