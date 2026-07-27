"""コマファイルのページ概要カメラ下絵を再利用する判定。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


SCENE_SIGNATURE_PROP = "bmanga_coma_overview_signature_v2"
SIGNATURE_VERSION = 2


def _path_state(path: Path | None) -> tuple[int, int]:
    if path is None:
        return 0, 0
    try:
        stat = Path(path).stat()
        return int(stat.st_mtime_ns), int(stat.st_size)
    except OSError:
        return 0, 0


def build_signature(scene, work) -> str:
    """下絵の構成・元画像・ユーザー設定をまとめた安定シグネチャを返す。"""

    if scene is None or work is None:
        return ""
    try:
        from ..io import schema
        from . import page_preview_object, paths

        work_dir = Path(str(getattr(work, "work_dir", "") or ""))
        current_page_id = str(
            getattr(scene, "bmanga_current_coma_page_id", "") or ""
        )
        current_coma_id = str(
            getattr(scene, "bmanga_current_coma_id", "") or ""
        )
        settings = getattr(scene, "bmanga_coma_camera_settings", None)
        setting_names = (
            "name_bg_images_opacity",
            "name_visible",
            "bg_images_scale",
            "own_page_opacity",
            "own_page_visible",
            "koma_bg_images_opacity",
            "koma_visible",
            "koma_depth",
        )
        pages = []
        for index, page in enumerate(getattr(work, "pages", ()) or ()):
            page_id = str(getattr(page, "id", "") or "")
            variant = (
                page_preview_object.PREVIEW_RENDER_VARIANT_DETAIL
                if page_id == current_page_id
                else page_preview_object.PREVIEW_RENDER_VARIANT_WORK
            )
            preview_path = page_preview_object._preview_png_path(
                work,
                page_id,
                scene=scene,
                variant=variant,
            )
            pages.append(
                {
                    "index": index,
                    "page": schema.page_to_dict(page),
                    "preview": _path_state(preview_path),
                }
            )
        cache_dir = paths.assets_dir(work_dir) / "_coma_bg_cache"
        content_states = {
            side: _path_state(
                cache_dir / f"page_content_{side}_{current_page_id}.png"
            )
            for side in ("back", "front")
        }
        payload = {
            "version": SIGNATURE_VERSION,
            "current_page_id": current_page_id,
            "current_coma_id": current_coma_id,
            "paper": schema.paper_to_dict(getattr(work, "paper", None)),
            "pages": pages,
            "content": content_states,
            "settings": {
                name: getattr(settings, name, None)
                for name in setting_names
            },
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def backgrounds_current(scene, signature: str, marker_prop: str) -> bool:
    """保存済みカメラ下絵が現在の元データと一致しているか。"""

    if scene is None or not signature:
        return False
    try:
        if str(scene.get(SCENE_SIGNATURE_PROP, "") or "") != signature:
            return False
        camera = getattr(scene, "camera", None)
        backgrounds = getattr(
            getattr(camera, "data", None),
            "background_images",
            (),
        )
        for background in backgrounds or ():
            image = getattr(background, "image", None)
            if image is not None and bool(image.get(marker_prop, False)):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def record(scene, signature: str) -> None:
    if scene is None or not signature:
        return
    try:
        scene[SCENE_SIGNATURE_PROP] = signature
    except Exception:  # noqa: BLE001
        pass


def invalidate(scene) -> None:
    if scene is None:
        return
    try:
        scene.pop(SCENE_SIGNATURE_PROP, None)
    except Exception:  # noqa: BLE001
        pass
