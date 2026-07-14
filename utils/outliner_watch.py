"""Outliner D&D の検出と低頻度 sync (Phase 1).

計画書 ``docs/outliner_object_layer_plan_2026-04-30.md`` §5.2 を実装する。

Phase 1 では「**検出と警告ログ**」までを担当し、Outliner で D&D された Object
の親 Collection 変化を 5 秒以上の低頻度 timer scan で拾う。実 entry
(`BMangaImageLayer.parent_key` 等) への書戻しは Phase 3 (画像/raster Object
化完了) と同時にこの timer のコールバック内で行う想定。

**再帰抑止**: ``layer_object_sync.suppress_sync()`` ガードと差分キャッシュで
fire 数を最小化する (計画書 §5.3)。
"""

from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from . import log
from . import layer_object_sync as los
from . import object_naming as on
from . import outliner_model as om

_logger = log.get_logger(__name__)

# scan 間隔 (秒)。計画書 §5.3 で「1 秒以下にすると Undo 中に再帰する事例が
# あるため 5 秒以上推奨」としている。
SCAN_INTERVAL_SECONDS = 5.0

# scan の世代番号 (アドオン unregister 時に既存タイマーを失効させるため)
_scan_generation = 0

# 現世代の tick 関数参照 (timers.unregister 用)
_active_tick = None

# 前回 scan 時の entry 数 (page / coma / folder) スナップショット。
# (page_count, coma_total_count, folder_count) で表現し、追加削除を検出する。
_LAST_ENTRY_COUNTS: tuple[int, int, int] = (0, 0, 0)


def _collect_entry_counts(scene) -> tuple[int, int, int]:
    """work.pages / 各 page.comas / work.layer_folders の合計件数を返す."""
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None:
        return (0, 0, 0)
    pages = list(getattr(work, "pages", []))
    coma_total = sum(len(getattr(p, "comas", [])) for p in pages)
    folder_count = len(getattr(work, "layer_folders", []))
    return (len(pages), coma_total, folder_count)


def mark_entry_counts_synced(scene=None) -> None:
    """``mirror_work_to_outliner`` 完了時に呼び、scan の件数基準を最新化する.

    load_post やオペレータが mirror を済ませた直後に scan が同じ件数差を再検出して
    冗長に mirror を再実行すると、ビューポートが連続再描画され用紙ガイド線・効果線
    などの細線がちらつく。mirror 側からここを呼んでおくことで、scan はその mirror を
    冗長に繰り返さない (実際に件数が変わったときだけ反応する)。
    """
    global _LAST_ENTRY_COUNTS
    try:
        target_scene = scene if scene is not None else getattr(bpy.context, "scene", None)
        _LAST_ENTRY_COUNTS = _collect_entry_counts(target_scene)
    except Exception:  # noqa: BLE001
        pass


def _entry_targets_other_page(scene, entry) -> bool:
    """現在のpage.blend外へ移送済みの実体をOutliner変更として扱わない。"""
    from . import page_file_scene

    current_page_id = page_file_scene.current_page_id(scene)
    parent_kind = str(getattr(entry, "parent_kind", "") or "")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    if not current_page_id or parent_kind not in {"page", "coma"} or not parent_key:
        return False
    target_page_id = parent_key.split(":", 1)[0]
    return bool(target_page_id and target_page_id != current_page_id)


def _writeback_raster_parent(scene, obj, new_kind: str, new_key: str) -> bool:
    """Outliner D&D を ``BMangaRasterLayer`` に書き戻す (Phase 3a).

    対応する parent_kind マッピング:
        - "page" → ``parent_kind="page"``
        - "coma" → ``parent_kind="coma"``
        - "outside" / "none" → ``parent_kind="none"`` / ``parent_key=""``
        - "folder" → 未対応 (BMangaRasterLayer.parent_kind に folder enum が無い)。
                    警告ログのみ。

    Returns:
        書戻しを実行したら True。
    """
    raster_id = str(obj.get("bmanga_id", "") or "")
    if not raster_id:
        return False
    coll = getattr(scene, "bmanga_raster_layers", None)
    if coll is None:
        return False
    entry = None
    for e in coll:
        if str(getattr(e, "id", "") or "") == raster_id:
            entry = e
            break
    if entry is None:
        return False
    if _entry_targets_other_page(scene, entry):
        # ページ間移送直後は旧page.blend内の表示Objectだけが一時的に残る。
        # そのObjectの退避先を、移送済みentryへ逆流させない。
        los.update_snapshot(obj)
        return False
    if new_kind == "folder":
        _logger.warning(
            "raster %s: folder への移動は Phase 3a では未対応 (skip)", raster_id
        )
        return False
    if new_kind in {"outside", "none"}:
        new_parent_kind = "none"
        new_parent_key = ""
    elif new_kind in {"page", "coma"}:
        new_parent_kind = new_kind
        new_parent_key = new_key
    else:
        return False
    # 既に同値なら no-op (再帰検出を避ける)
    if (
        str(getattr(entry, "parent_kind", "") or "") == new_parent_kind
        and str(getattr(entry, "parent_key", "") or "") == new_parent_key
    ):
        return False
    with los.suppress_sync():
        try:
            entry.parent_kind = new_parent_kind
        except Exception:  # noqa: BLE001
            _logger.exception("raster writeback: parent_kind set failed")
            return False
        try:
            entry.parent_key = new_parent_key
        except Exception:  # noqa: BLE001
            _logger.exception("raster writeback: parent_key set failed")
            return False
        try:
            obj["bmanga_parent_key"] = new_parent_key
        except Exception:  # noqa: BLE001
            pass
        los.update_snapshot(obj)
        # マスク Modifier を新親に追従させる
        try:
            from . import mask_apply

            mask_apply.apply_mask_to_layer_object(obj)
        except Exception:  # noqa: BLE001
            pass
    try:
        for area in bpy.context.screen.areas if bpy.context.screen else ():
            if area.type in {"VIEW_3D", "PROPERTIES"}:
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    _logger.info(
        "raster writeback: %s parent → %s/%s",
        raster_id,
        new_parent_kind,
        new_parent_key,
    )
    return True


def _resolve_parent_kind_key_folder(new_kind: str, new_key: str) -> tuple[str, str, str]:
    """Outliner の new_kind/new_key を ``(parent_kind, parent_key, folder_key)``
    に変換する共通ヘルパ.

    image / balloon / text 系で folder への移動を扱う。folder の場合、
    フォルダ Collection の親 (page or coma) を逆引きして parent_kind に
    反映する。逆引きできなかった場合は ``("", "", "")`` を返し、呼出側で
    writeback を skip させる (entry.parent_kind="folder" は EnumProperty では
    無効値のため代入例外を防ぐ)。
    """
    if new_kind in {"outside", "none"}:
        return "none", "", ""
    if new_kind == "page":
        return "page", new_key, ""
    if new_kind == "coma":
        return "coma", new_key, ""
    if new_kind == "folder":
        folder_coll = on.find_collection_by_bmanga_id(new_key, kind="folder")
        if folder_coll is not None:
            for parent_coll in bpy.data.collections:
                if any(cc is folder_coll for cc in parent_coll.children):
                    pkind = on.get_kind(parent_coll)
                    if pkind in {"page", "coma"}:
                        return pkind, on.get_bmanga_id(parent_coll), new_key
        # 親の page/coma が辿れなかった: writeback skip
        return "", "", ""
    return "", "", ""


def _writeback_empty_layer_parent(
    scene, obj, kind: str, new_kind: str, new_key: str
) -> bool:
    """画像 / テキスト Empty の Outliner D&D を entry に書戻す.

    BMangaImageLayer / BMangaTextEntry の parent_kind / parent_key / folder_key
    を更新し、オーバーレイ描画でこの所属に基づく親子追従が反映されるように
    する。
    """
    from . import empty_layer_object as elo

    bid = str(obj.get("bmanga_id", "") or "")
    if not bid:
        return False
    if kind == "text":
        parent_coll = om.find_managed_parent_collection(obj)
        if parent_coll is not None and on.get_kind(parent_coll) == "text_root":
            # テキスト Collection は実体の保存場所であり、ページ/コマ所属は
            # entry 側の値が正。保存場所を親変更として書き戻さない。
            los.update_snapshot(obj)
            return False
    if kind == "image":
        entry = elo.find_image_entry(scene, bid)
        page = None
    else:  # text
        page, entry = elo.find_text_entry(scene, bid)
    if entry is None:
        return False
    if kind == "image" and _entry_targets_other_page(scene, entry):
        los.update_snapshot(obj)
        return False
    new_pk, new_pkey, new_fk = _resolve_parent_kind_key_folder(new_kind, new_key)
    if not new_pk:
        return False
    if (
        str(getattr(entry, "parent_kind", "") or "") == new_pk
        and str(getattr(entry, "parent_key", "") or "") == new_pkey
        and str(getattr(entry, "folder_key", "") or "") == new_fk
    ):
        return False
    with los.suppress_sync():
        try:
            entry.parent_kind = new_pk
            entry.parent_key = new_pkey
            entry.folder_key = new_fk
            obj["bmanga_parent_key"] = new_pkey
            obj["bmanga_folder_id"] = new_fk
            los.update_snapshot(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("%s writeback failed", kind)
            return False
    _logger.info(
        "%s writeback: %s parent → %s/%s folder=%s",
        kind, bid, new_pk, new_pkey, new_fk,
    )
    return True


def _writeback_image_path_parent(scene, obj, new_kind: str, new_key: str) -> bool:
    """パターンカーブ Object の Outliner D&D を entry に書き戻す."""
    from . import image_path_object

    bid = str(obj.get("bmanga_id", "") or "")
    if not bid:
        return False
    entry = image_path_object.find_image_path_entry(scene, bid)
    if entry is None:
        return False
    new_pk, new_pkey, new_fk = _resolve_parent_kind_key_folder(new_kind, new_key)
    if not new_pk:
        return False
    if (
        str(getattr(entry, "parent_kind", "") or "") == new_pk
        and str(getattr(entry, "parent_key", "") or "") == new_pkey
        and str(getattr(entry, "folder_key", "") or "") == new_fk
    ):
        return False

    work = getattr(scene, "bmanga_work", None)
    old_page = image_path_object.page_for_entry(scene, work, entry)
    old_ox, old_oy = image_path_object.entry_page_offset_mm(scene, work, entry, old_page)
    with los.suppress_sync(), image_path_object.suspend_auto_sync():
        try:
            entry.parent_kind = new_pk
            entry.parent_key = new_pkey
            entry.folder_key = new_fk
            new_page = image_path_object.page_for_entry(scene, work, entry)
            new_ox, new_oy = image_path_object.entry_page_offset_mm(scene, work, entry, new_page)
            image_path_object.translate_entry_points(entry, old_ox - new_ox, old_oy - new_oy)
            obj["bmanga_parent_key"] = new_pkey
            obj["bmanga_folder_id"] = new_fk
            los.update_snapshot(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("image path writeback failed")
            return False
    image_path_object.on_image_path_entry_changed(entry)
    _logger.info(
        "image path writeback: %s parent → %s/%s folder=%s",
        bid,
        new_pk,
        new_pkey,
        new_fk,
    )
    return True


def _writeback_effect_parent(scene, obj, new_kind: str, new_key: str) -> bool:
    """効果線 GP Object の Outliner D&D を反映 (Phase 5b).

    効果線は実 entry を持たず、Object custom property
    (``bmanga_parent_key``) のみが正。watch 検出時に Object 側を最新化する
    だけで write-back 完了。
    """
    new_pk, new_pkey, new_fk = _resolve_parent_kind_key_folder(new_kind, new_key)
    if not new_pk:
        return False
    if (
        str(obj.get("bmanga_parent_key", "") or "") == new_pkey
        and str(obj.get("bmanga_folder_id", "") or "") == new_fk
    ):
        return False
    with los.suppress_sync():
        try:
            obj["bmanga_parent_key"] = new_pkey
            obj["bmanga_folder_id"] = new_fk
            los.update_snapshot(obj)
        except Exception:  # noqa: BLE001
            _logger.exception("effect writeback failed")
            return False
        # GP マスクモディファイアを新親に追従させる
        try:
            from . import mask_apply

            mask_apply.apply_mask_to_layer_object(obj)
        except Exception:  # noqa: BLE001
            pass
    _logger.info(
        "effect writeback: %s parent → %s/%s folder=%s",
        obj.get("bmanga_id", ""), new_pk, new_pkey, new_fk,
    )
    return True


def _writeback_outliner_changes(scene) -> int:
    """Outliner で D&D された Object の Collection 移動を実 entry に書戻し.

    detect_outliner_changes が見つけた差分を kind ごとに対応する write-back
    関数に振分ける。 各 write-back は parent_kind/parent_key 更新と同時に
    mask_apply.apply_mask_to_layer_object を呼ぶため、 Boolean Intersect
    Modifier (target=coma_mask) も自動追従する。

    Returns: 書戻した件数。
    """
    if scene is None:
        return 0
    changes = los.detect_outliner_changes(scene)
    if not changes:
        return 0
    n = 0
    for obj, new_kind, new_key in changes:
        kind = str(obj.get("bmanga_kind", "") or "")
        ok = False
        if kind == "raster":
            ok = _writeback_raster_parent(scene, obj, new_kind, new_key)
        elif kind in {"effect", "gp"}:
            ok = _writeback_effect_parent(scene, obj, new_kind, new_key)
        elif kind == "image_path":
            ok = _writeback_image_path_parent(scene, obj, new_kind, new_key)
        elif kind in {"image", "text"}:
            ok = _writeback_empty_layer_parent(scene, obj, kind, new_kind, new_key)
        else:
            _logger.info(
                "outliner watch: %s (kind=%s) → %s/%s "
                "(write-back 未対応 kind)",
                obj.name, kind, new_kind, new_key,
            )
        if ok:
            n += 1
    return n


def _scan_once() -> float | None:
    """1 回分の scan。差分があれば実 entry へ反映する.

    対応 kind:
        - raster: BMangaRasterLayer.parent_kind/parent_key を書戻し
        - effect / gp: Object custom property を書戻し
    """
    global _LAST_ENTRY_COUNTS
    if los.is_sync_in_progress():
        return SCAN_INTERVAL_SECONDS
    try:
        scene = bpy.context.scene
        if scene is None:
            return SCAN_INTERVAL_SECONDS
        # entry 件数 (ページ/コマ/フォルダ) の増減を検出したら mirror 再走で
        # Outliner Collection 階層を最新化 (例: 枠線カットで新コマ追加直後)
        current_counts = _collect_entry_counts(scene)
        if current_counts != _LAST_ENTRY_COUNTS:
            work = getattr(scene, "bmanga_work", None)
            if work is not None and getattr(work, "loaded", False):
                try:
                    los.mirror_work_to_outliner(scene, work)
                    _logger.info(
                        "outliner watch: entry counts %s → %s, mirror 再走",
                        _LAST_ENTRY_COUNTS, current_counts,
                    )
                except Exception:  # noqa: BLE001
                    _logger.exception("outliner watch: mirror failed")
            _LAST_ENTRY_COUNTS = current_counts
        _writeback_outliner_changes(scene)
        # Empty Object (image/text) の location 変化を entry.x_mm/y_mm に
        # 書戻し (オーバーレイ描画位置に連動)
        try:
            from . import object_state_sync

            for obj in bpy.data.objects:
                object_state_sync.sync_from_blender_object(scene, obj)
        except Exception:  # noqa: BLE001
            _logger.exception("Blender object state → entry sync failed")
    except Exception:  # noqa: BLE001
        _logger.exception("outliner watch scan failed")
    return SCAN_INTERVAL_SECONDS


def _make_tick(generation: int):
    def _tick():
        if generation != _scan_generation:
            return None
        return _scan_once()

    return _tick


@persistent
def _on_load_post(_filepath: str) -> None:
    """.blend ロード後に scan timer を再起動 (load_post で世代が変わるため)."""
    schedule_watch_timer()


@persistent
def _on_depsgraph_update_post(scene, depsgraph) -> None:
    """depsgraph 更新ごとに 2 系統の即時同期を行う.

    1. Empty (image / text) を 3D ビューで G で動かしたとき、 5 秒間隔の
       timer scan を待たずにオーバーレイ描画位置を即時更新する。
    2. Outliner で Object を別 Collection (例: コマ Collection) にドラッグ
       したとき、 _writeback_outliner_changes 経由で parent_key 書戻し +
       mask_apply (Boolean Intersect Modifier 自動付与/付替え) を即時実行する。
       これにより「コマ Collection に Object を入れたらコママスクが即時に
       かかる」 操作感を実現する。

    再帰抑止: ``los.suppress_sync()`` ガードと entry 同値チェックで重複処理を
    防ぐ。
    """
    if los.is_sync_in_progress():
        return
    if scene is None:
        return
    try:
        from . import object_state_sync

        for update in depsgraph.updates:
            if not update.is_updated_transform and not update.is_updated_geometry:
                continue
            obj_id = update.id
            if not isinstance(obj_id, bpy.types.Object):
                continue
            if update.is_updated_geometry and not update.is_updated_transform:
                if str(obj_id.get("bmanga_kind", "") or "") != "effect_base_path":
                    continue
            if not object_state_sync.is_sync_candidate(obj_id):
                continue
            # 名前で生 Object を引き直す (depsgraph の id は eval 版の場合あり)
            real_obj = bpy.data.objects.get(obj_id.name)
            if real_obj is None:
                continue
            if not object_state_sync.is_sync_candidate(real_obj):
                continue
            object_state_sync.sync_from_blender_object(scene, real_obj)
    except Exception:  # noqa: BLE001
        _logger.exception("depsgraph_update_post object state sync failed")

    # Collection 移動の即時検出 → mask 自動追従
    try:
        _writeback_outliner_changes(scene)
    except Exception:  # noqa: BLE001
        _logger.exception("depsgraph_update_post outliner writeback failed")


def schedule_watch_timer() -> None:
    """timer を起動 (既存 timer は世代カウンタ + 明示 unregister で停止)."""
    global _scan_generation, _active_tick, _LAST_ENTRY_COUNTS
    # 既存 tick を unregister
    if _active_tick is not None:
        try:
            if bpy.app.timers.is_registered(_active_tick):
                bpy.app.timers.unregister(_active_tick)
        except Exception:  # noqa: BLE001
            pass
        _active_tick = None
    # entry 件数の基準を「いま」の値で初期化する。これをしないと _LAST_ENTRY_COUNTS が
    # 初期値 (0,0,0) のままで、ファイルを開いた直後の最初の scan が必ず件数差を検出し、
    # load_post で済んでいる mirror_work_to_outliner を冗長に再実行する。その mirror が
    # ビューポートを連続再描画させ、用紙ガイド線・効果線などの細線がちらつく原因になる。
    try:
        _LAST_ENTRY_COUNTS = _collect_entry_counts(getattr(bpy.context, "scene", None))
    except Exception:  # noqa: BLE001
        pass
    _scan_generation += 1
    gen = _scan_generation
    tick = _make_tick(gen)
    _active_tick = tick
    try:
        bpy.app.timers.register(
            tick,
            first_interval=SCAN_INTERVAL_SECONDS,
            persistent=True,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("outliner watch timer register failed")


def cancel_watch_timer() -> None:
    """既存 timer を実 unregister + 世代カウンタで失効させる."""
    global _scan_generation, _active_tick
    _scan_generation += 1
    if _active_tick is not None:
        try:
            if bpy.app.timers.is_registered(_active_tick):
                bpy.app.timers.unregister(_active_tick)
        except Exception:  # noqa: BLE001
            pass
        _active_tick = None
    los.clear_snapshots()


def register() -> None:
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    if _on_depsgraph_update_post not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update_post)
    schedule_watch_timer()


def unregister() -> None:
    cancel_watch_timer()
    if _on_load_post in bpy.app.handlers.load_post:
        try:
            bpy.app.handlers.load_post.remove(_on_load_post)
        except ValueError:
            pass
    if _on_depsgraph_update_post in bpy.app.handlers.depsgraph_update_post:
        try:
            bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update_post)
        except ValueError:
            pass
