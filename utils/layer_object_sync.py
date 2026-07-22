"""Outliner Object/Collection ミラーと差分検出 sync.

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` Phase 0/1。

責務:
    1. ``BMangaWorkData`` (page / coma / layer_folder / image / raster / GP) を
       読み、対応する Collection / Object を ``utils/outliner_model.py`` 経由で
       生成・整合させる (mirror)。
    2. depsgraph_update_post / msgbus / timer scan で Outliner D&D を検出し、
       Object の現所属 Collection から ``parent_kind`` / ``parent_key`` を
       逆方向に反映する (Phase 1 で実装、ここではフックの土台のみ)。
    3. 計画書 §5.3 の再帰抑止 guard を提供する。

Phase 0 では (1) と (3) を実装。(2) のフルパス detection は Phase 1 で拡張。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

import bpy

from . import coma_z_order
from . import log
from . import object_naming as on
from . import outliner_model as om

_logger = log.get_logger(__name__)


# ---------- 再帰抑止 guard (計画書 §5.3) ----------

_SYNC_IN_PROGRESS = False


@contextmanager
def suppress_sync():
    """B-MANGA operator 実行中の depsgraph 再帰を抑止するコンテキスト.

    使用例:
        with suppress_sync():
            obj.location.z = z

    ネストしても外側のフラグが立っている限り内側は no-op。
    """
    global _SYNC_IN_PROGRESS
    if _SYNC_IN_PROGRESS:
        yield
        return
    _SYNC_IN_PROGRESS = True
    try:
        yield
    finally:
        _SYNC_IN_PROGRESS = False


def is_sync_in_progress() -> bool:
    return _SYNC_IN_PROGRESS


# ---------- 差分キャッシュ (再 fire 抑止) ----------

# 前回 scan 時の (parent_collection_name, location_z, parent_key, folder_id) を
# **bmanga_id** キーで保持する。obj.name キーだとリネームでリーク + 同名再生成で
# 偶発継承する事故が起きるため安定 ID を採用。
_LAST_SNAPSHOT: dict[str, tuple] = {}


def _snapshot_key(obj: bpy.types.Object) -> str:
    """snapshot のキー。bmanga_id 優先、無ければ obj.name fallback."""
    bid = str(obj.get(on.PROP_ID, "") or "")
    if str(obj.get(on.PROP_KIND, "") or "") == "text" and bid:
        return f"{bid}|{obj.get(on.PROP_PARENT_KEY, '')}"
    return bid if bid else f"@name:{obj.name}"


def _snapshot_for(obj: bpy.types.Object) -> tuple:
    parent_coll = om.find_managed_parent_collection(obj)
    parent_name = parent_coll.name if parent_coll is not None else ""
    z = round(float(obj.location.z), 6)
    parent_key = obj.get(on.PROP_PARENT_KEY, "")
    folder_id = obj.get(on.PROP_FOLDER_ID, "")
    return (parent_name, z, str(parent_key), str(folder_id))


def has_changed(obj: bpy.types.Object) -> bool:
    snap = _snapshot_for(obj)
    return _LAST_SNAPSHOT.get(_snapshot_key(obj)) != snap


def update_snapshot(obj: bpy.types.Object) -> None:
    _LAST_SNAPSHOT[_snapshot_key(obj)] = _snapshot_for(obj)


def clear_snapshots() -> None:
    _LAST_SNAPSHOT.clear()


def prune_snapshots(valid_bmanga_ids: set[str]) -> int:
    """指定された有効 bmanga_id 以外の snapshot を削除. orphan 解消用."""
    stale = [k for k in _LAST_SNAPSHOT if not k.startswith("@name:") and k not in valid_bmanga_ids]
    for k in stale:
        del _LAST_SNAPSHOT[k]
    return len(stale)


# ---------- Z 座標と prefix (計画書 §4.2) ----------

# 1 ページ内のレイヤー 1 段あたりの Z オフセット (m)。0.01 (= 10mm)。
# **ページごとにリセット**して、各ページで rank 1, 2, 3, ... と順序を振る。
# rank 1 = z=0.01, rank 2 = z=0.02, ... 用紙 (paper_bg, z=0) は常に最下段。
# 旧仕様では z_index (10, 100, 1000+) をそのまま乗じていたため、 高 z_index
# レイヤー (フキダシ z_index=1010) が world z=101m に飛んでいた。
# 2026-05-04: ユーザー要望で 0.1 → 0.01 に縮小 (coma_plane Z も同期して
# 0.1 → 0.01 に変更し、 paper_bg Z=0 との隙間を維持)。
BMANGA_Z_STEP_M = 0.01


def z_for_index(z_index: int) -> float:
    """旧 API: z_index ベースの Z 値 (mirror_work_to_outliner 内では
    使われず、 ``assign_per_page_z_ranks`` の per-page rank が最終的な
    location.z を確定する)。stamp 直後の暫定値として残す。"""
    # 0.1 刻みだと z_index=1000+ で破綻するため、 0.0001 (0.1mm) を使う。
    # 最終 location.z は assign_per_page_z_ranks が上書きするので、 ここの
    # 値はクリッピング順序のシード以外には影響しない。
    return float(z_index) * 0.0001


def apply_z_index(obj: bpy.types.Object, z_index: int) -> None:
    """Object の ``location.z`` (暫定) と name prefix を ``z_index`` から再生成.

    最終的な ``location.z`` は ``assign_per_page_z_ranks`` で page 単位に
    rank ベースで上書きされる。
    """
    obj[on.PROP_Z_INDEX] = int(z_index)
    try:
        loc = obj.location
        obj.location = (loc.x, loc.y, z_for_index(z_index))
    except Exception:  # noqa: BLE001
        pass
    kind = on.get_kind(obj)
    bmanga_id = on.get_bmanga_id(obj)
    title = str(obj.get(on.PROP_TITLE, "") or "")
    if kind and bmanga_id:
        on.assign_canonical_name(
            obj, kind=kind, z_index=int(z_index), sub_id=kind, title=title
        )


def _resolve_page_id_for_object(obj: bpy.types.Object) -> str:
    """Object が属するページの id を ``bmanga_parent_key`` から解決.

    parent_key の形式:
        - ``""`` → outside (ページなし)
        - ``"pNNNN"`` → page 直下
        - ``"pNNNN:cNN"`` → coma 配下 (コロン左側がページ)
        - その他 (folder_xxx) → folder Collection を引いて再帰解決
    """
    parent_key = str(obj.get("bmanga_parent_key", "") or "")
    if not parent_key:
        return ""
    if ":" in parent_key:
        # pNNNN:cNN 形式 (coma)
        return parent_key.split(":", 1)[0]
    # pNNNN 形式 (page 直下)
    if parent_key.startswith("p") or parent_key.startswith("P"):
        return parent_key
    # folder の場合: Collection から親を辿る
    from . import object_naming as _on

    folder_coll = _on.find_collection_by_bmanga_id(parent_key, kind="folder")
    if folder_coll is not None:
        pkey = str(folder_coll.get("bmanga_parent_key", "") or "")
        if pkey:
            if ":" in pkey:
                return pkey.split(":", 1)[0]
            if pkey.startswith("p") or pkey.startswith("P"):
                return pkey
    return ""


def _semantic_parent_key_for_object(work, obj: bpy.types.Object) -> str:
    parent_key = str(obj.get(on.PROP_PARENT_KEY, "") or "")
    if not parent_key:
        return ""
    if ":" in parent_key:
        return parent_key
    if parent_key.startswith(("p", "P")):
        return parent_key
    try:
        from . import layer_folder
        from .layer_hierarchy import OUTSIDE_STACK_KEY

        if layer_folder.folder_exists(work, parent_key):
            semantic = layer_folder.semantic_parent_key_for_folder(work, parent_key)
            if semantic and semantic != OUTSIDE_STACK_KEY:
                return str(semantic)
            return ""
    except Exception:  # noqa: BLE001
        pass
    folder_id = str(obj.get(on.PROP_FOLDER_ID, "") or "")
    if folder_id:
        try:
            from . import layer_folder
            from .layer_hierarchy import OUTSIDE_STACK_KEY

            semantic = layer_folder.semantic_parent_key_for_folder(work, folder_id)
            if semantic and semantic != OUTSIDE_STACK_KEY:
                return str(semantic)
        except Exception:  # noqa: BLE001
            pass
    return parent_key


def _resolve_coma_key_for_object(work, obj: bpy.types.Object) -> str:
    semantic = _semantic_parent_key_for_object(work, obj)
    return semantic if ":" in semantic else ""


def _coma_lookup(work) -> dict[str, object]:
    lookup: dict[str, object] = {}
    for page in getattr(work, "pages", []) or []:
        page_id = str(getattr(page, "id", "") or "")
        if not page_id:
            continue
        for coma in getattr(page, "comas", []) or []:
            coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
            if coma_id:
                lookup[f"{page_id}:{coma_id}"] = coma
    return lookup


def _set_object_z(obj: bpy.types.Object, new_z: float) -> bool:
    changed = False
    try:
        loc = obj.location
        if abs(float(loc.z) - float(new_z)) > 1e-6:
            obj.location = (loc.x, loc.y, float(new_z))
            changed = True
    except Exception:  # noqa: BLE001
        pass
    if str(obj.get(on.PROP_KIND, "") or "") == "effect":
        try:
            from . import effect_line_object as _elo

            _elo.sync_effect_display_transform(obj)
        except Exception:  # noqa: BLE001
            pass
    return changed


def _stack_uid_for_coma_object(obj: bpy.types.Object, page_id: str) -> str:
    kind = str(obj.get(on.PROP_KIND, "") or "")
    bmanga_id = str(obj.get(on.PROP_ID, "") or "")
    if not kind or not bmanga_id:
        return ""
    try:
        from . import layer_stack as ls
        from . import layer_folder as lf

        if kind == "balloon":
            return ls.target_uid(kind, f"{page_id}:{bmanga_id}")
        if kind == "text":
            if bmanga_id.startswith(f"{page_id}:"):
                return ls.target_uid(kind, bmanga_id)
            return ls.target_uid(kind, f"{page_id}:{bmanga_id}")
        if kind in {"image", "image_path", "raster", "fill"}:
            return ls.target_uid(kind, bmanga_id)
        if kind == "effect":
            return ls.target_uid("effect", bmanga_id)
        if kind == "gp":
            return ls.target_uid("gp", bmanga_id)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def coma_stack_order(scene, work, coma_key: str) -> tuple[list[str], str]:
    """指定コマ配下のレイヤー一覧順 (stack_uid のリスト) と、そのコマの
    コマプレビュー行 uid を返す。io/export_stack_order.py が
    レイヤーリスト順→書き出し合成順の変換に使う公開API。"""
    try:
        from . import layer_stack as ls

        stack = getattr(scene, "bmanga_layer_stack", None)
        if stack is None:
            return [], ""
        preview_uid = ls.target_uid(ls.COMA_PREVIEW_KIND, ls.coma_preview_key(coma_key))
        containers = {
            str(getattr(item, "key", "") or ""): item
            for item in stack
            if str(getattr(item, "kind", "") or "") in {"layer_folder", "balloon_group"}
        }

        def _belongs_to_coma(item) -> bool:
            parent_key = str(getattr(item, "parent_key", "") or "")
            seen: set[str] = set()
            while parent_key and parent_key not in seen:
                if parent_key == coma_key:
                    return True
                seen.add(parent_key)
                parent = containers.get(parent_key)
                if parent is None:
                    try:
                        semantic = lf.semantic_parent_key_for_folder(work, parent_key)
                    except Exception:  # noqa: BLE001
                        semantic = ""
                    return semantic == coma_key
                parent_key = str(getattr(parent, "parent_key", "") or "")
            return False

        order = [
            ls.stack_item_uid(item)
            for item in stack
            if str(getattr(item, "kind", "") or "") in ls.COMA_REORDER_KINDS
            and _belongs_to_coma(item)
        ]
        return order, preview_uid
    except Exception:  # noqa: BLE001
        return [], ""


def _assign_coma_item_z(scene, work, coma_key: str, coma, items: list[tuple[int, bpy.types.Object]]) -> int:
    from . import layer_folder as lf

    page_id = coma_key.split(":", 1)[0]
    order, preview_uid = coma_stack_order(scene, work, coma_key)
    if not order:
        items.sort(key=lambda x: (x[0], x[1].name))
        count = len(items)
        updated = 0
        for rank, (_zi, obj) in enumerate(items, start=1):
            if _set_object_z(obj, coma_z_order.content_z(coma, rank, count)):
                updated += 1
        return updated

    index_by_uid = {uid: i for i, uid in enumerate(order)}
    container_index_by_key: dict[str, int] = {}
    stack = getattr(scene, "bmanga_layer_stack", None)
    if stack is not None:
        for item in stack:
            layer_stack_uid = ""
            try:
                from . import layer_stack as ls

                layer_stack_uid = ls.stack_item_uid(item)
            except Exception:  # noqa: BLE001
                pass
            if layer_stack_uid in index_by_uid:
                container_index_by_key[str(getattr(item, "key", "") or "")] = index_by_uid[layer_stack_uid]

    def _container_stack_index(key: str) -> int | None:
        """折り畳みで消えたフォルダ行を、一覧に残る最寄りの祖先へ寄せる。"""
        current = str(key or "")
        seen: set[str] = set()
        while current and current not in seen:
            found = container_index_by_key.get(current)
            if found is not None:
                return found
            seen.add(current)
            folder = lf.find_folder(work, current)
            if folder is None:
                break
            current = lf.folder_parent_key(folder)
        return None

    def _object_stack_index(obj):
        uid = _stack_uid_for_coma_object(obj, page_id)
        direct = index_by_uid.get(uid)
        if direct is not None:
            return direct
        folder_key = str(obj.get(on.PROP_FOLDER_ID, "") or "")
        folder_index = _container_stack_index(folder_key)
        if folder_index is not None:
            return folder_index
        return _container_stack_index(str(obj.get(on.PROP_PARENT_KEY, "") or ""))

    preview_index = index_by_uid.get(preview_uid)

    if preview_index is None:
        sorted_items: list[tuple[int, int, bpy.types.Object]] = []
        for z_index, obj in items:
            stack_index = _object_stack_index(obj)
            if stack_index is not None:
                sorted_items.append((stack_index, z_index, obj))
            else:
                sorted_items.append((10_000, -z_index, obj))
        sorted_items.sort(key=lambda x: (x[0], x[1], x[2].name))
        count = len(sorted_items)
        updated = 0
        for i, (_si, _zi, obj) in enumerate(sorted_items):
            rank = count - i
            if _set_object_z(obj, coma_z_order.content_z(coma, rank, count)):
                updated += 1
        return updated

    front: list[tuple[int, int, bpy.types.Object]] = []
    back: list[tuple[int, int, bpy.types.Object]] = []
    fallback: list[tuple[int, int, bpy.types.Object]] = []
    for z_index, obj in items:
        stack_index = _object_stack_index(obj)
        if stack_index is None:
            fallback.append((10_000, -z_index, obj))
        elif stack_index < preview_index:
            front.append((stack_index, z_index, obj))
        elif stack_index > preview_index:
            back.append((stack_index, z_index, obj))
        else:
            fallback.append((stack_index, z_index, obj))
    front.extend(fallback)
    front.sort(key=lambda x: (x[0], x[1], x[2].name))
    back.sort(key=lambda x: (x[0], x[1], x[2].name))
    updated = 0
    front_count = len(front)
    for i, (_stack_index, _zi, obj) in enumerate(front):
        rank = front_count - i
        if _set_object_z(obj, coma_z_order.content_z(coma, rank, front_count)):
            updated += 1
    back_count = len(back)
    for i, (_stack_index, _zi, obj) in enumerate(back, start=1):
        if _set_object_z(obj, coma_z_order.content_behind_plane_z(coma, i, back_count)):
            updated += 1
    return updated


def _page_stack_positions(scene, work, page_key: str):
    """ページレイヤーとコマ行の stack 位置を返す."""
    try:
        from . import layer_stack as ls
        from . import layer_folder as lf

        stack = getattr(scene, "bmanga_layer_stack", None)
        if stack is None:
            return {}, {}, []
        index_by_uid = {ls.stack_item_uid(item): i for i, item in enumerate(stack)}
        container_index_by_key = {
            str(getattr(item, "key", "") or ""): i
            for i, item in enumerate(stack)
            if str(getattr(item, "kind", "") or "") in {"layer_folder", "balloon_group"}
        }
        coma_lookup = _coma_lookup(work)
        coma_rows = [
            (i, coma_lookup[str(getattr(item, "key", "") or "")])
            for i, item in enumerate(stack)
            if str(getattr(item, "kind", "") or "") == "coma"
            and str(getattr(item, "key", "") or "").startswith(f"{page_key}:")
            and str(getattr(item, "key", "") or "") in coma_lookup
        ]

        def container_index(key: str) -> int | None:
            current = str(key or "")
            seen: set[str] = set()
            while current and current not in seen:
                found = container_index_by_key.get(current)
                if found is not None:
                    return found
                seen.add(current)
                folder = lf.find_folder(work, current)
                if folder is None:
                    break
                current = lf.folder_parent_key(folder)
            return None

        return index_by_uid, container_index, coma_rows
    except Exception:  # noqa: BLE001
        return {}, {}, []


def _page_item_z_positions(scene, work, page_id: str, items: list[tuple[int, bpy.types.Object]]):
    """一覧内のコマ行を境界としてページレイヤーの Z を割り当てる."""
    index_by_uid, container_index, coma_rows = _page_stack_positions(scene, work, page_id)
    if not coma_rows:
        return []

    segments: list[list[tuple[int, int, bpy.types.Object]]] = [
        [] for _unused in range(len(coma_rows) + 1)
    ]
    for z_index, obj in items:
        uid = _stack_uid_for_coma_object(obj, page_id)
        stack_index = index_by_uid.get(uid)
        if stack_index is None:
            stack_index = container_index(str(obj.get(on.PROP_FOLDER_ID, "") or ""))
        if stack_index is None:
            stack_index = container_index(str(obj.get(on.PROP_PARENT_KEY, "") or ""))
        # 一覧へまだ同期されていない新規項目は、従来契約どおり全コマの前面。
        effective_index = -1 if stack_index is None else stack_index
        segment_index = sum(1 for coma_index, _coma in coma_rows if coma_index < effective_index)
        segments[segment_index].append((effective_index, z_index, obj))

    positioned: list[tuple[bpy.types.Object, float]] = []
    for segment_index, segment in enumerate(segments):
        if not segment:
            continue
        segment.sort(key=lambda value: (value[0], value[1], value[2].name))
        count = len(segment)
        if segment_index == 0:
            lower = coma_z_order.border_z(coma_rows[0][1])
            for i, (_si, _zi, obj) in enumerate(segment):
                positioned.append((obj, lower + (count - i) * BMANGA_Z_STEP_M))
            continue
        upper = coma_z_order.group_back_z(coma_rows[segment_index - 1][1])
        lower = 0.0
        if segment_index < len(coma_rows):
            lower = coma_z_order.border_z(coma_rows[segment_index][1])
        span = max(0.0, upper - lower)
        for i, (_si, _zi, obj) in enumerate(segment):
            fraction = (count - i) / (count + 1)
            positioned.append((obj, lower + span * fraction))
    return positioned


def assign_per_page_z_ranks(scene, work) -> int:
    """各ページごとにレイヤーの ``location.z`` を表示順へリセット.

    ページ直下のレイヤーはレイヤー一覧内のコマ行を境界にして各コマ Z 帯の
    前・間・後へ並べる。コマ配下のレイヤーは、同じコマの用紙面と枠線の
    間に収め、コマ内容が枠線を手前から隠さないようにする。

    旧実装の z_index*0.1 では z_index=1010 のフキダシが z=101m に飛んで
    view_all 時に用紙が消える問題があったため、 per-page rank に変更。
    """
    if scene is None or work is None or not getattr(work, "loaded", False):
        return 0

    page_groups: dict[str, list] = {}
    coma_items: dict[str, list] = {}
    comas_by_key = _coma_lookup(work)
    for obj in bpy.data.objects:
        if not bool(obj.get(on.PROP_MANAGED, False)):
            continue
        page_id = _resolve_page_id_for_object(obj)
        if not page_id:
            continue
        z_index = int(obj.get(on.PROP_Z_INDEX, 0) or 0)
        coma_key = _resolve_coma_key_for_object(work, obj)
        if coma_key in comas_by_key:
            coma_items.setdefault(coma_key, []).append((z_index, obj))
            continue
        page_groups.setdefault(page_id, []).append((z_index, obj))

    page_front_base: dict[str, float] = {}
    for page in getattr(work, "pages", []) or []:
        page_id = str(getattr(page, "id", "") or "")
        if not page_id:
            continue
        front_z = 0.0
        for coma in getattr(page, "comas", []) or []:
            try:
                front_z = max(front_z, coma_z_order.border_z(coma))
            except Exception:  # noqa: BLE001
                pass
        page_front_base[page_id] = front_z

    updated = 0
    for page_id, items in page_groups.items():
        positioned = _page_item_z_positions(scene, work, page_id, items)
        if positioned:
            for obj, new_z in positioned:
                if _set_object_z(obj, new_z):
                    updated += 1
            continue
        items.sort(key=lambda x: (x[0], x[1].name))
        for rank, (_zi, obj) in enumerate(items, start=1):
            new_z = page_front_base.get(page_id, 0.0) + rank * BMANGA_Z_STEP_M
            if _set_object_z(obj, new_z):
                updated += 1
    for coma_key, items in coma_items.items():
        coma = comas_by_key.get(coma_key)
        if coma is None:
            continue
        updated += _assign_coma_item_z(scene, work, coma_key, coma, items)
    return updated


# ---------- 作品全体の mirror 同期 (Phase 0 の中核) ----------


def _page_filter_for_scene(scene) -> tuple[set[str] | None, set[str] | None]:
    """現在のファイル種別に応じた (構造ページ, 中身ページ) フィルタ."""
    try:
        from . import page_file_scene

        return (
            page_file_scene.structural_page_filter(scene),
            page_file_scene.content_page_filter(scene),
        )
    except Exception:  # noqa: BLE001
        return None, None


def _coma_runtime_page_filter_for_scene(scene) -> set[str] | None:
    try:
        from . import page_file_scene

        return page_file_scene.coma_runtime_page_filter(scene)
    except Exception:  # noqa: BLE001
        return None


def _iter_filtered_pages(work, page_filter: set[str] | None):
    for page in getattr(work, "pages", []) or []:
        page_id = str(getattr(page, "id", "") or "")
        if page_filter is not None and page_id not in page_filter:
            continue
        yield page


def _work_for_page_filter(work, page_filter: set[str] | None):
    if page_filter is None:
        return work
    try:
        from . import page_file_scene

        return page_file_scene.work_for_pages(work, page_filter)
    except Exception:  # noqa: BLE001
        return work


def _page_id_from_parent_key(parent_key: str, work=None) -> str:
    key = str(parent_key or "")
    if not key:
        return ""
    if ":" in key:
        return key.split(":", 1)[0]
    if key.startswith(("p", "P")):
        return key
    if work is not None:
        try:
            from . import layer_folder
            from .layer_hierarchy import OUTSIDE_STACK_KEY

            semantic = layer_folder.semantic_parent_key_for_folder(work, key)
            if semantic and semantic != OUTSIDE_STACK_KEY:
                return str(semantic).split(":", 1)[0]
        except Exception:  # noqa: BLE001
            pass
    return ""


def _entry_in_page_filter(entry, work, page_filter: set[str] | None) -> bool:
    if page_filter is None:
        return True
    if not page_filter:
        return False
    page_id = _page_id_from_parent_key(str(getattr(entry, "parent_key", "") or ""), work)
    if not page_id:
        # どのページにも属さない作品直下 (親なし) のレイヤーは、ページ編集中も
        # 表示対象にする (作品一覧は空集合フィルタで上の分岐に入る)
        return True
    return page_id in page_filter


def _purge_content_for_filter(scene, page_filter: set[str] | None) -> None:
    if page_filter is None:
        return
    keep_page_id = ""
    if len(page_filter) == 1:
        keep_page_id = next(iter(page_filter))
    try:
        from . import page_file_scene

        page_file_scene.purge_page_content_data(scene, keep_page_id)
    except Exception:  # noqa: BLE001
        _logger.exception("mirror content purge failed")


def _mirror_image_text_objects(scene, work, page_filter: set[str] | None = None) -> None:
    """全 BMangaImageLayer / BMangaTextEntry に対応する表示 Object を ensure."""
    try:
        from . import empty_layer_object as elo
        from . import image_path_object as ipo
        from . import image_real_object as iro
        from . import text_real_object as tro

        # 旧 Plane 方式の Object/Mesh/Material/Image を掃除 (Empty 化移行)
        try:
            elo.cleanup_legacy_plane_objects()
        except Exception:  # noqa: BLE001
            _logger.exception("legacy plane cleanup failed")

        # image_layers (scene 直下): Empty ではなく、透明画像付き平面として実体化する。
        if page_filter is None:
            iro.sync_all_image_real_objects(scene, work)
        elif page_filter:
            for entry in getattr(scene, "bmanga_image_layers", []) or []:
                if not _entry_in_page_filter(entry, work, page_filter):
                    continue
                page = iro.page_for_entry(scene, work, entry)
                iro.ensure_image_real_object(scene=scene, entry=entry, page=page)
            iro.cleanup_orphan_image_objects(scene)

        # image_path_layers (scene 直下): パスに沿った画像表示を実体化する。
        if page_filter is None:
            ipo.sync_all_image_path_objects(scene, work)
        elif page_filter:
            for entry in getattr(scene, "bmanga_image_path_layers", []) or []:
                if not _entry_in_page_filter(entry, work, page_filter):
                    continue
                page = ipo.page_for_entry(scene, work, entry)
                ipo.ensure_image_path_object(scene=scene, entry=entry, page=page)
            ipo.cleanup_orphan_image_path_objects(scene)

        # texts (page.texts): 空 Object ではなく、透明画像平面として実体化する。
        if page_filter is None:
            tro.sync_all_text_real_objects(scene, work)
        elif page_filter:
            for page in _iter_filtered_pages(work, page_filter):
                for entry in getattr(page, "texts", []) or []:
                    tro.ensure_text_real_object(scene=scene, entry=entry, page=page)
            # 作品直下 (親なし) のテキストもページ編集中は表示する
            for entry in getattr(work, "shared_texts", []) or []:
                tro.ensure_text_real_object(scene=scene, entry=entry, page=None)
            tro.cleanup_orphan_text_objects(scene, _work_for_page_filter(work, page_filter))

        # fill_layers (scene 直下): ベタ塗り/グラデーション平面として実体化する。
        from . import fill_real_object as fro

        if page_filter is None:
            fro.sync_all_fill_real_objects(scene, work)
        elif page_filter:
            for entry in getattr(scene, "bmanga_fill_layers", []) or []:
                if not _entry_in_page_filter(entry, work, page_filter):
                    continue
                page = fro.page_for_entry(scene, work, entry)
                fro.ensure_fill_real_object(scene=scene, entry=entry, page=page)
            fro.cleanup_orphan_fill_objects(scene)
    except Exception:  # noqa: BLE001
        _logger.exception("mirror image/text/fill/image path objects failed")


def _saved_runtime_objects_look_current(
    scene: bpy.types.Scene,
    work,
    page_filter: set[str] | None = None,
    coma_page_filter: set[str] | None = None,
) -> bool:
    """保存済み実体が揃っているなら、読み込み直後の全件再生成を省く."""
    if scene is None or work is None:
        return False
    try:
        from . import coma_border_object as _cbo
        from . import coma_plane as _cp
        from . import text_real_object as _tro
    except Exception:  # noqa: BLE001
        return False

    object_ids_by_kind: dict[str, set[str]] = {}
    plane_owners: set[str] = set()
    plane_objects_by_owner: dict[str, bpy.types.Object] = {}
    border_owners: set[str] = set()
    for obj in bpy.data.objects:
        kind = str(obj.get(on.PROP_KIND, "") or "")
        bmanga_id = str(obj.get(on.PROP_ID, "") or "")
        if kind and bmanga_id:
            object_ids_by_kind.setdefault(kind, set()).add(bmanga_id)
        owner = str(obj.get(_cp.PROP_COMA_PLANE_OWNER_ID, "") or "")
        if owner:
            plane_owners.add(owner)
            plane_objects_by_owner[owner] = obj
        owner = str(obj.get(_cbo.PROP_COMA_BORDER_OWNER_ID, "") or "")
        if owner:
            border_owners.add(owner)
    expected_comas: set[str] = set()
    expected_borders: set[str] = set()
    expected_brush_borders: set[str] = set()
    expected_brush_soft_masks: set[str] = set()
    expected_balloons: set[str] = set()
    expected_texts: set[str] = set()

    def _track_expected_border(owner_id: str, border) -> None:
        style = str(getattr(border, "style", "solid") or "solid")
        if style == "brush":
            expected_brush_borders.add(owner_id)
            if (
                bool(getattr(border, "visible", True))
                and max(0.0, float(getattr(border, "width_mm", 0.0) or 0.0)) > 0.0
                and max(0.0, float(getattr(border, "blur_amount", 0.0) or 0.0)) > 0.0
            ):
                expected_brush_soft_masks.add(owner_id)
        else:
            expected_borders.add(owner_id)

    for page in getattr(work, "pages", []) or []:
        page_id = str(getattr(page, "id", "") or "")
        if not page_id:
            continue
        if coma_page_filter is None or page_id in coma_page_filter:
            for coma in getattr(page, "comas", []) or []:
                coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
                if coma_id:
                    owner_id = f"{page_id}:{coma_id}"
                    expected_comas.add(owner_id)
                    border = getattr(coma, "border", None)
                    _track_expected_border(owner_id, border)
        page_content_expected = (
            page_filter is None
            or (bool(page_filter) and page_id in page_filter)
        )
        if page_content_expected:
            for entry in getattr(page, "balloons", []) or []:
                balloon_id = str(getattr(entry, "id", "") or "")
                if balloon_id:
                    expected_balloons.add(balloon_id)
            for entry in getattr(page, "texts", []) or []:
                text_id = str(getattr(entry, "id", "") or "")
                if text_id:
                    expected_texts.add(_tro.text_object_bmanga_id_for_values(page_id, text_id))
    # 作品直下 (親なし) の共有レイヤーもページ編集中は実体が必要。
    # ここに入れないと「ページ側は揃っている」と誤判定して全件ミラーが
    # スキップされ、作品直下へ移した直後のレイヤーが実体化されない。
    if page_filter is None or page_filter:
        for entry in getattr(work, "shared_balloons", []) or []:
            balloon_id = str(getattr(entry, "id", "") or "")
            if balloon_id:
                expected_balloons.add(balloon_id)
        for entry in getattr(work, "shared_texts", []) or []:
            text_id = str(getattr(entry, "id", "") or "")
            if text_id:
                expected_texts.add(
                    _tro.text_object_bmanga_id_for_values(_tro.OUTSIDE_PAGE_ID, text_id)
                )
    if coma_page_filter is None or coma_page_filter:
        for coma in getattr(work, "shared_comas", []) or []:
            coma_id = str(getattr(coma, "id", "") or getattr(coma, "coma_id", "") or "")
            if coma_id:
                owner_id = f"{_cp.OUTSIDE_PAGE_ID}:{coma_id}"
                expected_comas.add(owner_id)
                border = getattr(coma, "border", None)
                _track_expected_border(owner_id, border)
    if expected_comas and not expected_comas.issubset(plane_owners):
        return False
    if expected_borders and not expected_borders.issubset(border_owners):
        return False
    if expected_brush_borders and expected_brush_borders.intersection(border_owners):
        return False
    for owner_id in expected_brush_soft_masks:
        plane_obj = plane_objects_by_owner.get(owner_id)
        mesh = getattr(plane_obj, "data", None)
        attrs = getattr(mesh, "attributes", None)
        if attrs is None or attrs.get(_cp.COMA_PLANE_SOFT_MASK_ATTR) is None:
            return False
    if expected_balloons and not expected_balloons.issubset(object_ids_by_kind.get("balloon", set())):
        return False
    if expected_texts and not expected_texts.issubset(object_ids_by_kind.get("text", set())):
        return False

    if page_filter is None or page_filter:
        scene_raster_layers = getattr(scene, "bmanga_raster_layers", None)
        expected_rasters = {
            str(getattr(entry, "id", "") or "")
            for entry in (scene_raster_layers or [])
            if str(getattr(entry, "id", "") or "")
            and _entry_in_page_filter(entry, work, page_filter)
        }
        if expected_rasters and not expected_rasters.issubset(object_ids_by_kind.get("raster", set())):
            return False
        scene_image_layers = getattr(scene, "bmanga_image_layers", None)
        expected_images = {
            str(getattr(entry, "id", "") or "")
            for entry in (scene_image_layers or [])
            if str(getattr(entry, "id", "") or "")
            and _entry_in_page_filter(entry, work, page_filter)
        }
        if expected_images and not expected_images.issubset(object_ids_by_kind.get("image", set())):
            return False
        scene_image_path_layers = getattr(scene, "bmanga_image_path_layers", None)
        expected_image_paths = {
            str(getattr(entry, "id", "") or "")
            for entry in (scene_image_path_layers or [])
            if str(getattr(entry, "id", "") or "")
            and _entry_in_page_filter(entry, work, page_filter)
        }
        if expected_image_paths and not expected_image_paths.issubset(object_ids_by_kind.get("image_path", set())):
            return False
        scene_fill_layers = getattr(scene, "bmanga_fill_layers", None)
        expected_fills = {
            str(getattr(entry, "id", "") or "")
            for entry in (scene_fill_layers or [])
            if str(getattr(entry, "id", "") or "")
            and _entry_in_page_filter(entry, work, page_filter)
        }
        if expected_fills and not expected_fills.issubset(object_ids_by_kind.get("fill", set())):
            return False
    return True


def mirror_work_to_outliner(
    scene: bpy.types.Scene,
    work,
    *,
    allow_object_writeback: bool = True,
) -> None:
    """``work`` の page/coma/folder 配列から Collection 階層を生成・整合.

    既存 Collection は ``bmanga_id`` で逆引きして再利用する。
    work が未ロード (``loaded`` False) の場合は何もしない (Outliner に
    意味のない空の B-MANGA 階層を作らない)。
    """
    if scene is None or work is None:
        return
    if not bool(getattr(work, "loaded", False)):
        return
    # コマ編集モード (cNN.blend が mainfile) では Outliner mirror をスキップする。
    # ここで B-MANGA root / 全ページ Collection を再構築すると、その後の
    # save_as_mainfile で cNN.blend に overview 構造が丸ごと書き込まれ、
    # 「コマファイルの中に B-MANGA コレクションが居座る」問題の真因になる。
    # 復帰経路: exit_coma_mode → work.blend を open → load_post で overview
    # モードになり、その load_post 内の mirror_work_to_outliner は再生成される。
    try:
        from ..core.mode import MODE_COMA, get_mode

        # NOTE: ``get_mode`` は ``context`` を受け取り ``context.scene`` を読む
        # API なので、scene そのものを渡すと内部で None 扱いされて MODE_PAGE
        # にフォールバックする。bpy.context を渡すか、Scene の bmanga_mode を
        # 直接見る必要がある。ここでは Scene プロパティを直接参照する。
        if str(getattr(scene, "bmanga_mode", "") or "") == MODE_COMA:
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import page_file_scene

        if page_file_scene.is_work_list_scene(scene):
            with suppress_sync():
                om.ensure_root_collection(scene)
                om.ensure_outside_collection(scene)
                page_file_scene.purge_work_list_runtime_data(scene)
                try:
                    from . import page_preview_object

                    page_preview_object.sync_page_previews(bpy.context, work)
                    page_file_scene.purge_work_list_runtime_data(scene)
                except Exception:  # noqa: BLE001
                    _logger.exception("work list page previews failed")
                try:
                    from . import outliner_watch as _outliner_watch

                    _outliner_watch.mark_entry_counts_synced(scene)
                except Exception:  # noqa: BLE001
                    pass
            return
    except Exception:  # noqa: BLE001
        _logger.exception("work list lightweight mirror failed")
    structure_page_filter, content_page_filter = _page_filter_for_scene(scene)
    coma_runtime_page_filter = _coma_runtime_page_filter_for_scene(scene)
    structure_work = _work_for_page_filter(work, structure_page_filter)
    coma_runtime_work = _work_for_page_filter(work, coma_runtime_page_filter)
    include_content = content_page_filter is None or bool(content_page_filter)
    if _saved_runtime_objects_look_current(
        scene,
        structure_work,
        content_page_filter,
        coma_runtime_page_filter,
    ):
        try:
            from . import page_file_scene

            if structure_page_filter is not None and len(structure_page_filter) == 1:
                page_file_scene.purge_other_page_data(scene, next(iter(structure_page_filter)))
            else:
                page_file_scene.purge_coma_runtime_data(scene, coma_runtime_page_filter)
        except Exception:  # noqa: BLE001
            _logger.exception("mirror fast path coma runtime purge failed")
        try:
            from . import outliner_watch as _outliner_watch

            _outliner_watch.mark_entry_counts_synced(scene)
        except Exception:  # noqa: BLE001
            pass
        _purge_content_for_filter(scene, content_page_filter)
        return
    with suppress_sync():
        # 既存実体が Blender 標準機能で動かされていた場合は、B-MANGA 側の
        # 同期で上書きする前に現在の状態を作品データへ反映する。
        if allow_object_writeback:
            try:
                from . import history_runtime, object_state_sync

                if not history_runtime.is_restoring():
                    for obj in bpy.data.objects:
                        object_state_sync.sync_from_blender_object(scene, obj)
            except Exception:  # noqa: BLE001
                _logger.exception("mirror pre object state sync failed")
        om.ensure_root_collection(scene)
        om.ensure_outside_collection(scene)
        # 全テキストレイヤー集約用 Collection (B-MANGA 直下、最上位 z_index)
        om.ensure_text_collection(scene)
        om.ensure_work_info_collection(scene)
        for page in _iter_filtered_pages(work, structure_page_filter):
            page_id = str(getattr(page, "id", "") or "")
            if not page_id:
                continue
            title = str(getattr(page, "title", "") or "")
            om.ensure_page_collection(scene, page_id, title)
            for coma in getattr(page, "comas", []):
                coma_id = str(getattr(coma, "id", "") or "")
                if not coma_id:
                    continue
                coma_title = str(getattr(coma, "title", "") or "")
                om.ensure_coma_collection(scene, page_id, coma_id, coma_title)
        om.order_root_collections(scene)
        for folder in getattr(work, "layer_folders", []):
            folder_id = str(getattr(folder, "id", "") or "")
            if not folder_id:
                continue
            if structure_page_filter is not None:
                folder_page_id = _page_id_from_parent_key(
                    str(getattr(folder, "parent_key", "") or ""),
                    work,
                )
                if folder_page_id not in structure_page_filter:
                    continue
            title = str(getattr(folder, "title", "") or folder_id)
            parent_key_raw = str(getattr(folder, "parent_key", "") or "")
            parent_kind, parent_key = _split_folder_parent(parent_key_raw)
            z_index = int(getattr(folder, "z_order", 0) or 0)
            folder_collection = om.ensure_folder_collection(
                scene,
                folder_id=folder_id,
                title=title,
                parent_kind=parent_kind,
                parent_key=parent_key,
                z_index=z_index,
            )
            if folder_collection is not None:
                hidden = not bool(getattr(folder, "visible", True))
                folder_collection.hide_viewport = hidden
                folder_collection.hide_render = hidden
                if hasattr(folder_collection, "hide_select"):
                    folder_collection.hide_select = bool(
                        getattr(folder, "locked", False)
                    )
        # 画像 / テキストの表示 Object を ensure。
        # どちらも透明画像付き平面として実体化する。
        if include_content:
            _mirror_image_text_objects(scene, work, content_page_filter)

        # フキダシ Curve Object を ensure (entry.parent_key に基づき該当 page/coma
        # Collection 配下に置く)。 これを呼ばないと viewport 上では overlay 描画
        # されるだけで Outliner にはフキダシが現れず、 ユーザーから「コマの
        # 中に作成されない」 ように見える。
        try:
            from . import balloon_curve_object as _bco

            if include_content:
                for page in _iter_filtered_pages(work, content_page_filter):
                    for entry in getattr(page, "balloons", []):
                        try:
                            _bco.ensure_balloon_curve_object(
                                scene=scene, entry=entry, page=page,
                            )
                        except Exception:  # noqa: BLE001
                            _logger.exception(
                                "mirror balloon curve failed: %s",
                                getattr(entry, "id", ""),
                            )
                # 作品直下 (親なし) のフキダシは特定ページに属さないため、
                # ページ編集中 (フィルタあり) も表示する。作品一覧 (空集合) は
                # プレビュー専用なので作らない。
                if content_page_filter is None or content_page_filter:
                    for entry in getattr(work, "shared_balloons", []):
                        try:
                            _bco.ensure_balloon_curve_object(
                                scene=scene, entry=entry, page=None,
                            )
                        except Exception:  # noqa: BLE001
                            _logger.exception(
                                "mirror shared balloon curve failed: %s",
                                getattr(entry, "id", ""),
                            )
                _bco.cleanup_orphan_balloon_objects(scene)
        except Exception:  # noqa: BLE001
            _logger.exception("mirror balloon curve top-level failed")

        # コマ Collection 直下に coma_plane Mesh を ensure
        # (背景色 + Boolean マスク兼用 / 旧 __masks__ Collection の置き換え)
        try:
            from . import coma_plane as _cp
            from . import page_file_scene

            if coma_runtime_page_filter is not None and not coma_runtime_page_filter:
                page_file_scene.purge_coma_runtime_data(scene, set())
            else:
                _cp.regenerate_all_coma_planes(scene, coma_runtime_work)
        except Exception:  # noqa: BLE001
            _logger.exception("mirror coma planes failed")

        # コマ枠線はオーバーレイだけに依存せず、カーブ Object として残す。
        try:
            from . import coma_border_object as _cbo
            from . import page_file_scene

            if coma_runtime_page_filter is not None and not coma_runtime_page_filter:
                page_file_scene.purge_coma_runtime_data(scene, set())
            else:
                _cbo.regenerate_all_coma_borders(scene, coma_runtime_work)
        except Exception:  # noqa: BLE001
            _logger.exception("mirror coma borders failed")

        # 旧 __masks__ Collection (page_mask_*, coma_mask_* + __masks__ Coll
        # 自体) を強制 purge。 古い work.blend を開いた直後の自動 migration。
        try:
            from . import mask_object as _mo

            _mo.purge_legacy_masks_collection()
        except Exception:  # noqa: BLE001
            _logger.exception("mirror legacy __masks__ purge failed")

        # 最後にページごとの Z rank を再計算。ページ間は独立し、コマと
        # ページ直下レイヤーの相対順はレイヤー一覧を正本にする。
        try:
            assign_per_page_z_ranks(scene, structure_work)
        except Exception:  # noqa: BLE001
            _logger.exception("assign_per_page_z_ranks failed")

        _purge_content_for_filter(scene, content_page_filter)
        try:
            from . import page_file_scene

            if structure_page_filter is not None and len(structure_page_filter) == 1:
                page_file_scene.purge_other_page_data(scene, next(iter(structure_page_filter)))
            else:
                page_file_scene.purge_coma_runtime_data(scene, coma_runtime_page_filter)
        except Exception:  # noqa: BLE001
            _logger.exception("mirror post page runtime purge failed")

        # 既存 raster Object の Boolean Intersect modifier の target を最新の
        # coma_mask Object に同期 (2026-05-04 案 1。 file ロード直後や
        # coma_mask が新規生成された直後に Boolean が orphan target を持つ
        # ことを防ぐ)。
        try:
            from . import mask_apply as _ma

            _ma.apply_masks_to_all_managed(scene)
        except Exception:  # noqa: BLE001
            _logger.exception("apply_masks_to_all_managed failed")

    # mirror 完了。outliner_watch の定期 scan が同じ件数差を再検出して、ここで
    # 済ませた mirror を冗長に再実行 (→ ビューポート連続再描画 → 細線のちらつき) し
    # ないよう、scan の件数基準を最新化しておく。
    try:
        from . import outliner_watch as _outliner_watch

        _outliner_watch.mark_entry_counts_synced(scene)
    except Exception:  # noqa: BLE001
        pass


def _split_folder_parent(parent_key_raw: str) -> tuple[str, str]:
    """フォルダの ``parent_key`` 文字列を ``(parent_kind, parent_key)`` に分解.

    既存 ``utils/layer_reparent.py`` と同じ規約に揃える:
        - ``""`` -> outside
        - ``pNNNN`` -> page
        - ``pNNNN:cNN`` -> coma
        - その他 (folder_xxx) -> folder
    """
    if not parent_key_raw:
        return ("none", "")
    if ":" in parent_key_raw:
        return ("coma", parent_key_raw)
    if parent_key_raw.startswith("p") or parent_key_raw.startswith("P"):
        return ("page", parent_key_raw)
    return ("folder", parent_key_raw)


# ---------- Object 側ミラー (画像 / raster / GP の 3 種を Phase 0 で対応) ----------


def _resolve_page_world_offset_mm(scene, parent_key: str) -> tuple[float, float]:
    """parent_key の page 部分から page_grid の world オフセット (mm) を取得."""
    if scene is None or not parent_key:
        return (0.0, 0.0)
    page_id = parent_key.split(":", 1)[0] if parent_key else ""
    if not page_id:
        return (0.0, 0.0)
    work = getattr(scene, "bmanga_work", None)
    if work is None:
        return (0.0, 0.0)
    pages = list(getattr(work, "pages", []))
    page_idx = -1
    for i, p in enumerate(pages):
        if str(getattr(p, "id", "") or "") == page_id:
            page_idx = i
            break
    if page_idx < 0:
        return (0.0, 0.0)
    try:
        from . import page_grid as _pg

        return _pg.page_total_offset_mm(work, scene, page_idx)
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)


def stamp_layer_object(
    obj: bpy.types.Object,
    *,
    kind: str,
    bmanga_id: str,
    title: str,
    z_index: int,
    parent_kind: str,
    parent_key: str,
    folder_id: str = "",
    scene: Optional[bpy.types.Scene] = None,
    apply_page_offset: bool = True,
) -> None:
    """既存の Object に B-MANGA メタデータを書き込み、所属 Collection を整合.

    呼出側は既に ``bpy.data.objects.new()`` 等で Object を生成済みであることを
    前提とする。ここでは custom property 設定と link 整合を行う。

    ``apply_page_offset=True`` (既定) の場合、parent_key から所属ページを引いて
    page_grid 経由の world X/Y オフセットを Object.location.x/y に設定する。
    Mesh / Curve 頂点をページローカル座標で持っているレイヤーが、Page Browser
    モードの page グリッド上で正しい位置に重なるようにするため。
    オーバーレイ描画系で entry.x_mm/y_mm を独自管理する Empty レイヤーは
    apply_page_offset=False を渡して Object.location を別途制御する。
    """
    on.stamp_identity(
        obj,
        kind=kind,
        bmanga_id=bmanga_id,
        title=title,
        z_index=z_index,
        parent_key=parent_key,
        folder_id=folder_id,
    )
    if kind in {"gp", "effect"}:
        try:
            from . import layer_object_model

            layer_object_model.set_parent_key(obj, parent_key)
        except Exception:  # noqa: BLE001
            _logger.exception("stamp_layer_object: content parent metadata sync failed")
    on.assign_canonical_name(
        obj, kind=kind, z_index=z_index, sub_id=kind, title=title
    )
    apply_z_index(obj, z_index)
    # page world オフセットを X/Y に反映 (apply_z_index は Z のみ触る)
    if apply_page_offset and scene is not None:
        try:
            from .geom import mm_to_m as _mm_to_m

            ox_mm, oy_mm = _resolve_page_world_offset_mm(scene, parent_key)
            loc = obj.location
            obj.location = (
                _mm_to_m(ox_mm), _mm_to_m(oy_mm), loc.z
            )
        except Exception:  # noqa: BLE001
            _logger.exception("stamp_layer_object: page offset 設定失敗")
    if scene is not None:
        om.link_object_to_parent(
            scene, obj, parent_kind=parent_kind, parent_key=parent_key, folder_id=folder_id
        )
        try:
            from . import mask_apply

            mask_apply.apply_mask_to_layer_object(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("stamp_layer_object: mask apply failed")
    update_snapshot(obj)


def detect_outliner_changes(scene: bpy.types.Scene) -> list[tuple[bpy.types.Object, str, str]]:
    """B-MANGA 管理 Object のうち、現所属 Collection が ``parent_key`` と
    乖離しているものを返す.

    Phase 0 では呼出側 (timer scan) でこの戻り値を見て警告ログを出すだけ。
    Phase 1 で実反映 (entries の parent_key 書換え) を加える。

    Returns:
        ``[(obj, new_parent_kind, new_parent_key), ...]``。
    """
    if _SYNC_IN_PROGRESS:
        return []
    changes: list[tuple[bpy.types.Object, str, str]] = []
    for obj in on.iter_managed_objects():
        if not has_changed(obj):
            continue
        parent_coll = om.find_managed_parent_collection(obj)
        if parent_coll is None:
            update_snapshot(obj)
            continue
        new_kind, new_key = om.parent_key_from_collection(parent_coll)
        old_key = str(obj.get(on.PROP_PARENT_KEY, "") or "")
        if new_key != old_key:
            changes.append((obj, new_kind, new_key))
        update_snapshot(obj)
    return changes
