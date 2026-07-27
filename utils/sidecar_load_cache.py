"""blend内の保存済みPropertyGroupとJSON sidecarの一致判定。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy

from . import paths


SCENE_SIGNATURE_PROP = "bmanga_sidecar_signature_v1"
SIGNATURE_VERSION = 1


def _canonical_json_digest(path: Path, volatile: tuple[str, ...] = ()) -> str:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in volatile:
                data.pop(key, None)
        raw = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError):
        return ""


def signature_for_blend(work_dir: Path, blend_path: Path) -> str:
    """ファイル役割に必要なsidecarだけを正規化して署名する。"""

    work_dir = Path(work_dir)
    blend_path = Path(blend_path)
    parts = []
    try:
        rel = blend_path.resolve().relative_to(work_dir.resolve())
    except (OSError, ValueError):
        return ""
    work_digest = _canonical_json_digest(
        paths.work_meta_path(work_dir),
        ("lastSaved",),
    )
    pages_digest = _canonical_json_digest(
        paths.pages_meta_path(work_dir),
        ("lastModified",),
    )
    if not work_digest or not pages_digest:
        return ""
    parts.append(("work", work_digest))
    parts.append(("pages", pages_digest))
    page_id = ""
    if len(rel.parts) >= 2 and paths.is_valid_page_id(rel.parts[0]):
        page_id = str(rel.parts[0])
    if page_id:
        page_digest = _canonical_json_digest(
            paths.page_meta_path(work_dir, page_id),
        )
        if not page_digest:
            return ""
        parts.append((page_id, page_digest))
    payload = json.dumps(
        {
            "version": SIGNATURE_VERSION,
            "parts": parts,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record(scene=None, *, work_dir: Path | None = None, blend_path: Path | None = None) -> str:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return ""
    if work_dir is None:
        work = getattr(scene, "bmanga_work", None)
        text = str(getattr(work, "work_dir", "") or "")
        work_dir = Path(text) if text else None
    if blend_path is None:
        text = str(getattr(bpy.data, "filepath", "") or "")
        blend_path = Path(text) if text else None
    if work_dir is None or blend_path is None:
        return ""
    signature = signature_for_blend(work_dir, blend_path)
    if signature:
        try:
            scene[SCENE_SIGNATURE_PROP] = signature
        except Exception:  # noqa: BLE001
            return ""
    return signature


def current(scene, work_dir: Path, blend_path: Path) -> bool:
    if scene is None:
        return False
    expected = signature_for_blend(work_dir, blend_path)
    if not expected:
        return False
    try:
        saved = str(scene.get(SCENE_SIGNATURE_PROP, "") or "")
    except Exception:  # noqa: BLE001
        return False
    return saved == expected
