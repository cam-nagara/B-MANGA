"""画像 / テキスト レイヤーの表示 Object 互換ヘルパ.

現在の画像 / テキストは透明画像付き Mesh 平面として Blender データに残す。
このモジュールは旧 Empty API からの呼び出しを新しい実体同期へ橋渡しする。

export pipeline (`io/export_pipeline.py`) は **PropertyGroup (BMangaImageLayer
/ BMangaTextEntry) を直接読んで Pillow 合成** しているため、Empty 化しても
PNG / PSD 出力結果には影響しない。

旧 Empty Object の役割:
    - `bmanga_kind` / `bmanga_id` / `bmanga_managed` / `bmanga_parent_key` /
      `bmanga_z_index` / `bmanga_title` を保持
    - location は entry の x_mm / y_mm から mm→m 換算で同期
    - empty_display_type で視認性確保 (PLAIN_AXES + 小さい size)
"""

from __future__ import annotations

import math
from typing import Optional

import bpy

from . import layer_object_sync as los
from . import log
from . import object_naming as on
from . import object_preserve
from .geom import mm_to_m

_logger = log.get_logger(__name__)

IMAGE_EMPTY_NAME_PREFIX = "image_"
TEXT_EMPTY_NAME_PREFIX = "text_"

# Empty 表示サイズ (m)。5mm 程度で 3D ビューでも視認可能。
# 0.001 (1mm) では点として見えず Empty を選択できない問題を回避。
_EMPTY_DISPLAY_SIZE = 0.005
_EMPTY_DISPLAY_TYPE = "PLAIN_AXES"


def _resolve_page_offset(scene, page) -> tuple[float, float]:
    """page の page_grid world オフセット (mm) を取得.

    bpy_struct ラッパは `is` 比較で別 instance を返すケースがあるため、
    page.id 文字列で逆引きする。
    """
    if scene is None or page is None:
        return (0.0, 0.0)
    work = getattr(scene, "bmanga_work", None)
    if work is None:
        return (0.0, 0.0)
    target_id = str(getattr(page, "id", "") or "")
    if not target_id:
        return (0.0, 0.0)
    page_idx = -1
    for i, p in enumerate(getattr(work, "pages", [])):
        if str(getattr(p, "id", "") or "") == target_id:
            page_idx = i
            break
    if page_idx < 0:
        return (0.0, 0.0)
    try:
        from . import page_grid as _pg

        return _pg.page_total_offset_mm(work, scene, page_idx)
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)


def _resolve_parent_for_entry(entry, page, folder_id: str) -> tuple[str, str, str]:
    entry_parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    entry_parent_key = str(getattr(entry, "parent_key", "") or "")
    entry_folder_id = folder_id or str(getattr(entry, "folder_key", "") or "")
    if entry_parent_kind in {"none", "outside"}:
        return "outside", "", ""
    if entry_parent_kind == "coma" and entry_parent_key:
        return "coma", entry_parent_key, entry_folder_id
    if entry_parent_kind == "folder":
        folder_key_value = entry_folder_id or entry_parent_key
        if folder_key_value:
            return "folder", folder_key_value, folder_key_value
    return (
        "page",
        entry_parent_key or str(getattr(page, "id", "") or ""),
        entry_folder_id,
    )


def _ensure_empty_object(name: str) -> bpy.types.Object:
    """Empty Object を ensure (既存があれば再利用)."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, None)
    # Empty として表示
    try:
        obj.empty_display_type = _EMPTY_DISPLAY_TYPE
        obj.empty_display_size = _EMPTY_DISPLAY_SIZE
    except Exception:  # noqa: BLE001
        pass
    return obj


def _stamp_and_link(
    obj: bpy.types.Object,
    *,
    kind: str,
    bmanga_id: str,
    title: str,
    z_index: int,
    parent_kind: str,
    parent_key: str,
    folder_id: str,
    scene: bpy.types.Scene,
) -> None:
    # Empty は entry.x_mm/y_mm を独自管理するので page_grid offset は適用しない
    los.stamp_layer_object(
        obj,
        kind=kind,
        bmanga_id=bmanga_id,
        title=title,
        z_index=z_index,
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=folder_id,
        scene=scene,
        apply_page_offset=False,
    )


def ensure_image_empty_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    """後方互換 API。現在は画像を実体付き表示 Object として同期する."""
    try:
        from . import image_real_object

        return image_real_object.ensure_image_real_object(
            scene=scene,
            entry=entry,
            page=page,
            folder_id=folder_id,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("ensure_image_real_object compatibility call failed")
        return None


def ensure_text_empty_object(
    *,
    scene: bpy.types.Scene,
    entry,
    page,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    """後方互換 API。現在はテキストを実体付き表示 Object として同期する."""
    try:
        from . import text_real_object

        return text_real_object.ensure_text_real_object(
            scene=scene,
            entry=entry,
            page=page,
            folder_id=folder_id,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("ensure_text_real_object compatibility call failed")
        return None


def cleanup_legacy_plane_objects() -> int:
    """旧 Plane 方式 (text_plane_*, image_plane_*) の Object を保持対象にする.

    古いファイルを開いた時やアドオンを再有効化した時に、実体を消さず
    B-MANGA の自動同期対象からだけ外す。戻り値は保持対象にした Object 数。
    """
    removed = 0
    legacy_obj_prefixes = ("text_plane_", "image_plane_", "balloon_plane_")

    for obj in list(bpy.data.objects):
        if any(obj.name.startswith(p) for p in legacy_obj_prefixes):
            try:
                if object_preserve.preserve_object(obj, "古いテキスト・画像・フキダシ実体を保持"):
                    removed += 1
            except Exception:  # noqa: BLE001
                _logger.exception("legacy plane preserve failed: %s", obj.name)

    return removed


def find_image_entry(scene, image_id: str):
    coll = getattr(scene, "bmanga_image_layers", None) if scene is not None else None
    if coll is None:
        return None
    for e in coll:
        if str(getattr(e, "id", "") or "") == image_id:
            return e
    return None


def find_text_entry(scene, text_id: str):
    try:
        from . import text_real_object

        page, entry = text_real_object.find_text_entry(scene, text_id)
        if entry is not None:
            return page, entry
    except Exception:  # noqa: BLE001
        pass
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None:
        return None, None
    for page in getattr(work, "pages", []):
        for entry in getattr(page, "texts", []):
            if str(getattr(entry, "id", "") or "") == text_id:
                return page, entry
    return None, None


# ---------- Empty.location → entry.x_mm/y_mm の双方向同期 ----------

def sync_entry_position_from_object(scene: bpy.types.Scene, obj: bpy.types.Object) -> bool:
    """Empty.location が変わったら対応 entry.x_mm/y_mm に書戻す.

    オーバーレイ描画は entry の x_mm/y_mm を読むため、Outliner 上 Empty を
    動かしても overlay 表示が連動するようにする。
    """
    if obj is None or not on.is_managed(obj):
        return False
    kind = on.get_kind(obj)
    if kind not in {"image", "text"}:
        return False
    bmanga_id = on.get_bmanga_id(obj)
    if not bmanga_id:
        return False

    new_x_mm = obj.location.x * 1000.0  # m → mm
    new_y_mm = obj.location.y * 1000.0

    if kind == "image":
        entry = find_image_entry(scene, bmanga_id)
        if entry is None:
            return False
        page = None
        try:
            work = getattr(scene, "bmanga_work", None)
            from . import image_real_object

            page = image_real_object.page_for_entry(scene, work, entry)
            ox_mm, oy_mm = image_real_object.entry_page_offset_mm(scene, work, entry)
            old_w = max(0.1, float(getattr(entry, "width_mm", 0.1) or 0.1))
            old_h = max(0.1, float(getattr(entry, "height_mm", 0.1) or 0.1))
            sx = float(getattr(obj.scale, "x", 1.0) or 1.0)
            sy = float(getattr(obj.scale, "y", 1.0) or 1.0)
            new_w = max(0.1, old_w * max(1.0e-6, abs(sx)))
            new_h = max(0.1, old_h * max(1.0e-6, abs(sy)))
            new_x_mm -= ox_mm + new_w * 0.5
            new_y_mm -= oy_mm + new_h * 0.5
            new_rotation = math.degrees(float(obj.rotation_euler[2]))
        except Exception:  # noqa: BLE001
            page = None
            old_w = max(0.1, float(getattr(entry, "width_mm", 0.1) or 0.1))
            old_h = max(0.1, float(getattr(entry, "height_mm", 0.1) or 0.1))
            new_w = old_w
            new_h = old_h
            new_rotation = float(getattr(entry, "rotation_deg", 0.0) or 0.0)
        # ページ/コマ所属なのに親ページを解決できない (親付け替えの途中状態や
        # 参照切れ) 間は書き戻さない。オフセット 0 で解釈した値を書き込むと
        # ページ幅単位の位置ドリフトが起こる
        # (docs/image_layer_xmm_origin_mismatch_investigation_2026-06-12.md)
        parent_kind = str(getattr(entry, "parent_kind", "") or "")
        if page is None and parent_kind not in {"", "none", "outside"}:
            return False
    else:  # text
        if obj.get("bmanga_text_preview_hidden", False):
            return False
        page, entry = find_text_entry(scene, bmanga_id)
        if entry is None:
            return False
        ox_mm, oy_mm = _resolve_page_offset(scene, page)
        new_x_mm -= ox_mm
        new_y_mm -= oy_mm
        old_w = max(0.1, float(getattr(entry, "width_mm", 0.1) or 0.1))
        old_h = max(0.1, float(getattr(entry, "height_mm", 0.1) or 0.1))
        sx = float(getattr(obj.scale, "x", 1.0) or 1.0)
        sy = float(getattr(obj.scale, "y", 1.0) or 1.0)
        new_w = max(0.1, old_w * max(1.0e-6, abs(sx)))
        new_h = max(0.1, old_h * max(1.0e-6, abs(sy)))
        new_rotation = 0.0
        # obj.location (mm換算・ページオフセット減算済みの new_x_mm/new_y_mm)
        # は entry.rotation_deg != 0 のとき「矩形中心軸で回転した後の左下」を
        # 保持している (utils/text_real_object.py の _apply_text_object_state /
        # _rotated_bottom_left_mm 参照)。entry.x_mm/y_mm は常に回転前の左下
        # なので、そのまま書き戻すとユーザーが実体オブジェクトを直接動かした
        # 時に位置が化ける (往復不整合)。書き戻す前に逆変換で回転前の値へ戻す。
        # scale (old_w/old_h) は回転オフセットの計算に使う幅・高さで、
        # obj.location (=局所原点の位置) 自体は scale の影響を受けない。
        from . import text_real_object

        entry_rotation = float(getattr(entry, "rotation_deg", 0.0) or 0.0)
        new_x_mm, new_y_mm = text_real_object.unrotate_bottom_left_mm(
            new_x_mm, new_y_mm, old_w, old_h, entry_rotation,
        )

    cur_x = float(getattr(entry, "x_mm", 0.0) or 0.0)
    cur_y = float(getattr(entry, "y_mm", 0.0) or 0.0)
    cur_w = float(getattr(entry, "width_mm", 0.0) or 0.0)
    cur_h = float(getattr(entry, "height_mm", 0.0) or 0.0)
    cur_rot = float(getattr(entry, "rotation_deg", 0.0) or 0.0)
    if (
        abs(cur_x - new_x_mm) < 1e-4
        and abs(cur_y - new_y_mm) < 1e-4
        and abs(cur_w - new_w) < 1e-4
        and abs(cur_h - new_h) < 1e-4
        and (kind != "image" or abs(cur_rot - new_rotation) < 1e-4)
    ):
        return False
    with los.suppress_sync():
        try:
            if kind == "image":
                from . import image_real_object

                with image_real_object.suspend_auto_sync():
                    entry.x_mm = new_x_mm
                    entry.y_mm = new_y_mm
                    entry.width_mm = new_w
                    entry.height_mm = new_h
                    entry.rotation_deg = new_rotation
                obj.scale.x = -1.0 if float(getattr(obj.scale, "x", 1.0) or 1.0) < 0.0 else 1.0
                obj.scale.y = -1.0 if float(getattr(obj.scale, "y", 1.0) or 1.0) < 0.0 else 1.0
                obj.scale.z = 1.0
                image_real_object.ensure_image_real_object(scene=scene, entry=entry, page=page)
            else:
                from . import text_real_object

                with text_real_object.suspend_auto_sync():
                    entry.x_mm = new_x_mm
                    entry.y_mm = new_y_mm
                    entry.width_mm = new_w
                    entry.height_mm = new_h
                obj.scale.x = -1.0 if float(getattr(obj.scale, "x", 1.0) or 1.0) < 0.0 else 1.0
                obj.scale.y = -1.0 if float(getattr(obj.scale, "y", 1.0) or 1.0) < 0.0 else 1.0
                obj.scale.z = 1.0
                text_real_object.ensure_text_real_object(scene=scene, entry=entry, page=page)
        except Exception:  # noqa: BLE001
            _logger.exception("sync entry position failed: %s", bmanga_id)
            return False
    return True
