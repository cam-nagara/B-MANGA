"""Outliner Object/Collection ミラーと差分検出 sync.

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` Phase 0/1。

責務:
    1. ``BNameWorkData`` (page / coma / layer_folder / image / raster / GP) を
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
    """B-Name operator 実行中の depsgraph 再帰を抑止するコンテキスト.

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
# **bname_id** キーで保持する。obj.name キーだとリネームでリーク + 同名再生成で
# 偶発継承する事故が起きるため安定 ID を採用。
_LAST_SNAPSHOT: dict[str, tuple] = {}


def _snapshot_key(obj: bpy.types.Object) -> str:
    """snapshot のキー。bname_id 優先、無ければ obj.name fallback."""
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


def prune_snapshots(valid_bname_ids: set[str]) -> int:
    """指定された有効 bname_id 以外の snapshot を削除. orphan 解消用."""
    stale = [k for k in _LAST_SNAPSHOT if not k.startswith("@name:") and k not in valid_bname_ids]
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
BNAME_Z_STEP_M = 0.01


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
    bname_id = on.get_bname_id(obj)
    title = str(obj.get(on.PROP_TITLE, "") or "")
    if kind and bname_id:
        on.assign_canonical_name(
            obj, kind=kind, z_index=int(z_index), sub_id=kind, title=title
        )


def _resolve_page_id_for_object(obj: bpy.types.Object) -> str:
    """Object が属するページの id を ``bname_parent_key`` から解決.

    parent_key の形式:
        - ``""`` → outside (ページなし)
        - ``"pNNNN"`` → page 直下
        - ``"pNNNN:cNN"`` → coma 配下 (コロン左側がページ)
        - その他 (folder_xxx) → folder Collection を引いて再帰解決
    """
    parent_key = str(obj.get("bname_parent_key", "") or "")
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

    folder_coll = _on.find_collection_by_bname_id(parent_key, kind="folder")
    if folder_coll is not None:
        pkey = str(folder_coll.get("bname_parent_key", "") or "")
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
    bname_id = str(obj.get(on.PROP_ID, "") or "")
    if not kind or not bname_id:
        return ""
    try:
        from . import layer_stack as ls

        if kind in {"balloon", "text"}:
            return ls.target_uid(kind, f"{page_id}:{bname_id}")
        if kind in {"image", "raster"}:
            return ls.target_uid(kind, bname_id)
        if kind == "effect":
            layers = getattr(getattr(obj, "data", None), "layers", None)
            if layers is None or len(layers) == 0:
                return ""
            return ls.target_uid("effect", ls._node_stack_key(layers[0]))
        if kind in {"gp", "gp_folder"}:
            return ls.target_uid(kind, bname_id)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _coma_stack_order(scene, coma_key: str) -> tuple[list[str], str]:
    try:
        from . import layer_stack as ls

        stack = getattr(scene, "bname_layer_stack", None)
        if stack is None:
            return [], ""
        preview_uid = ls.target_uid(ls.COMA_PREVIEW_KIND, ls.coma_preview_key(coma_key))
        order = [
            ls.stack_item_uid(item)
            for item in stack
            if str(getattr(item, "parent_key", "") or "") == coma_key
            and str(getattr(item, "kind", "") or "") in ls.COMA_REORDER_KINDS
        ]
        return order, preview_uid
    except Exception:  # noqa: BLE001
        return [], ""


def _assign_coma_item_z(scene, work, coma_key: str, coma, items: list[tuple[int, bpy.types.Object]]) -> int:
    page_id = coma_key.split(":", 1)[0]
    order, preview_uid = _coma_stack_order(scene, coma_key)
    if not order or preview_uid not in order:
        items.sort(key=lambda x: (x[0], x[1].name))
        count = len(items)
        updated = 0
        for rank, (_zi, obj) in enumerate(items, start=1):
            if _set_object_z(obj, coma_z_order.content_z(coma, rank, count)):
                updated += 1
        return updated

    index_by_uid = {uid: i for i, uid in enumerate(order)}
    preview_index = index_by_uid[preview_uid]
    front: list[tuple[int, int, bpy.types.Object]] = []
    back: list[tuple[int, int, bpy.types.Object]] = []
    fallback: list[tuple[int, int, bpy.types.Object]] = []
    for z_index, obj in items:
        uid = _stack_uid_for_coma_object(obj, page_id)
        stack_index = index_by_uid.get(uid)
        if stack_index is None:
            # レイヤーリストに現れない互換 Object は、従来どおり z_index が
            # 大きいものほど前面として扱う。リスト上の実アイテムより後ろの
            # グループに置きつつ、fallback 同士では降順に並べる。
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


def assign_per_page_z_ranks(scene, work) -> int:
    """各ページごとにレイヤーの ``location.z`` を表示順へリセット.

    ページ直下のレイヤーは従来どおりページ内 rank で並べる。コマ配下の
    レイヤーは、同じコマの用紙面と枠線の間に収め、コマ内容が枠線を
    手前から隠さないようにする。

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

    updated = 0
    for page_id, items in page_groups.items():
        items.sort(key=lambda x: (x[0], x[1].name))  # z_index 昇順, tie は name
        for rank, (_zi, obj) in enumerate(items, start=1):
            new_z = rank * BNAME_Z_STEP_M
            if _set_object_z(obj, new_z):
                updated += 1
    for coma_key, items in coma_items.items():
        coma = comas_by_key.get(coma_key)
        if coma is None:
            continue
        updated += _assign_coma_item_z(scene, work, coma_key, coma, items)
    return updated


# ---------- 作品全体の mirror 同期 (Phase 0 の中核) ----------


def _mirror_image_text_objects(scene, work) -> None:
    """全 BNameImageLayer / BNameTextEntry に対応する表示 Object を ensure."""
    try:
        from . import empty_layer_object as elo
        from . import image_real_object as iro
        from . import text_real_object as tro

        # 旧 Plane 方式の Object/Mesh/Material/Image を掃除 (Empty 化移行)
        try:
            elo.cleanup_legacy_plane_objects()
        except Exception:  # noqa: BLE001
            _logger.exception("legacy plane cleanup failed")

        # image_layers (scene 直下): Empty ではなく、透明画像付き平面として実体化する。
        iro.sync_all_image_real_objects(scene, work)

        # texts (page.texts): 空 Object ではなく、透明画像平面として実体化する。
        tro.sync_all_text_real_objects(scene, work)
    except Exception:  # noqa: BLE001
        _logger.exception("mirror image/text objects failed")


def mirror_work_to_outliner(scene: bpy.types.Scene, work) -> None:
    """``work`` の page/coma/folder 配列から Collection 階層を生成・整合.

    既存 Collection は ``bname_id`` で逆引きして再利用する。
    work が未ロード (``loaded`` False) の場合は何もしない (Outliner に
    意味のない空の B-Name 階層を作らない)。
    """
    if scene is None or work is None:
        return
    if not bool(getattr(work, "loaded", False)):
        return
    # コマ編集モード (cNN.blend が mainfile) では Outliner mirror をスキップする。
    # ここで B-Name root / 全ページ Collection を再構築すると、その後の
    # save_as_mainfile で cNN.blend に overview 構造が丸ごと書き込まれ、
    # 「コマファイルの中に B-Name コレクションが居座る」問題の真因になる。
    # 復帰経路: exit_coma_mode → work.blend を open → load_post で overview
    # モードになり、その load_post 内の mirror_work_to_outliner は再生成される。
    try:
        from ..core.mode import MODE_COMA, get_mode

        # NOTE: ``get_mode`` は ``context`` を受け取り ``context.scene`` を読む
        # API なので、scene そのものを渡すと内部で None 扱いされて MODE_PAGE
        # にフォールバックする。bpy.context を渡すか、Scene の bname_mode を
        # 直接見る必要がある。ここでは Scene プロパティを直接参照する。
        if str(getattr(scene, "bname_mode", "") or "") == MODE_COMA:
            return
    except Exception:  # noqa: BLE001
        pass
    with suppress_sync():
        om.ensure_root_collection(scene)
        om.ensure_outside_collection(scene)
        # 全テキストレイヤー集約用 Collection (B-Name 直下、最上位 z_index)
        om.ensure_text_collection(scene)
        om.ensure_work_info_collection(scene)
        for page in getattr(work, "pages", []):
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
            title = str(getattr(folder, "title", "") or folder_id)
            parent_key_raw = str(getattr(folder, "parent_key", "") or "")
            parent_kind, parent_key = _split_folder_parent(parent_key_raw)
            z_index = int(getattr(folder, "z_order", 0) or 0)
            om.ensure_folder_collection(
                scene,
                folder_id=folder_id,
                title=title,
                parent_kind=parent_kind,
                parent_key=parent_key,
                z_index=z_index,
            )
        # 画像 / テキストの表示 Object を ensure。
        # どちらも透明画像付き平面として実体化する。
        _mirror_image_text_objects(scene, work)

        # フキダシ Curve Object を ensure (entry.parent_key に基づき該当 page/coma
        # Collection 配下に置く)。 これを呼ばないと viewport 上では overlay 描画
        # されるだけで Outliner にはフキダシが現れず、 ユーザーから「コマの
        # 中に作成されない」 ように見える。
        try:
            from . import balloon_curve_object as _bco

            for page in getattr(work, "pages", []):
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

        # 用紙背景 (opaque Mesh) を全ページ分 ensure。BLENDED ラスター
        # 材質の depth 不在を補い、ラスター paint の上に被さらないように
        # する。GPU overlay 用紙塗りの代替。
        try:
            from . import paper_bg_object as _pbg

            _pbg.regenerate_all_paper_bgs(scene, work)
        except Exception:  # noqa: BLE001
            _logger.exception("mirror paper backgrounds failed")

        # コマ Collection 直下に coma_plane Mesh を ensure
        # (背景色 + Boolean マスク兼用 / 旧 __masks__ Collection の置き換え)
        try:
            from . import coma_plane as _cp

            _cp.regenerate_all_coma_planes(scene, work)
        except Exception:  # noqa: BLE001
            _logger.exception("mirror coma planes failed")

        # コマ枠線はオーバーレイだけに依存せず、カーブ Object として残す。
        try:
            from . import coma_border_object as _cbo

            _cbo.regenerate_all_coma_borders(scene, work)
        except Exception:  # noqa: BLE001
            _logger.exception("mirror coma borders failed")

        # 旧 __masks__ Collection (page_mask_*, coma_mask_* + __masks__ Coll
        # 自体) を強制 purge。 古い work.blend を開いた直後の自動 migration。
        try:
            from . import mask_object as _mo

            _mo.purge_legacy_masks_collection()
        except Exception:  # noqa: BLE001
            _logger.exception("mirror legacy __masks__ purge failed")

        # 最後にページごとの Z rank を再計算 (page 内 0.1 刻み、 ページ間
        # は独立)。 paper_bg は z=0、 各レイヤーは z=0.1, 0.2, 0.3, ...
        try:
            assign_per_page_z_ranks(scene, work)
        except Exception:  # noqa: BLE001
            _logger.exception("assign_per_page_z_ranks failed")

        # 用紙ガイド線群とセーフライン外塗りは、作品要素の実体が並んだ後に
        # z を決める。どちらもビュー上では最前面表示を使う。
        try:
            from . import paper_guide_object as _pgo

            _pgo.regenerate_all_paper_guides(scene, work)
        except Exception:  # noqa: BLE001
            _logger.exception("mirror paper guides failed")

        # 既存 raster Object の Boolean Intersect modifier の target を最新の
        # coma_mask Object に同期 (2026-05-04 案 1。 file ロード直後や
        # coma_mask が新規生成された直後に Boolean が orphan target を持つ
        # ことを防ぐ)。
        try:
            from . import mask_apply as _ma

            _ma.apply_masks_to_all_managed(scene)
        except Exception:  # noqa: BLE001
            _logger.exception("apply_masks_to_all_managed failed")

        # 旧設計の集約 GP Object (BName_EffectLines / BName_EffectLines_data)
        # は新設計 (1 effect = 1 GP Object @ コマ Collection) で完全置換された
        # ため、 viewport から強制 hide する。 中身 (effect_focus 等の旧 layer
        # stroke) は user data として残し、 ユーザーが必要なら manual で
        # 復活できる。 過去 file 互換のために削除はしない。
        try:
            for legacy_name in ("BName_EffectLines", "BName_EffectLines_data"):
                lo = bpy.data.objects.get(legacy_name)
                if lo is not None and not lo.hide_viewport:
                    lo.hide_viewport = True
                    lo.hide_render = True
        except Exception:  # noqa: BLE001
            _logger.exception("legacy effect lines hide failed")


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
    work = getattr(scene, "bname_work", None)
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
    bname_id: str,
    title: str,
    z_index: int,
    parent_kind: str,
    parent_key: str,
    folder_id: str = "",
    scene: Optional[bpy.types.Scene] = None,
    apply_page_offset: bool = True,
) -> None:
    """既存の Object に B-Name メタデータを書き込み、所属 Collection を整合.

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
        bname_id=bname_id,
        title=title,
        z_index=z_index,
        parent_key=parent_key,
        folder_id=folder_id,
    )
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
    """B-Name 管理 Object のうち、現所属 Collection が ``parent_key`` と
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
