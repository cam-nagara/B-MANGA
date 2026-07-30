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
    project_digest = _canonical_json_digest(paths.project_meta_path(work_dir))
    if not project_digest:
        return ""
    parts.append(("project", project_digest))
    page_uid = ""
    if len(rel.parts) >= 3 and rel.parts[0] == paths.PAGES_DIR_NAME:
        candidate = str(rel.parts[1])
        if paths.is_valid_page_uid(candidate):
            page_uid = candidate
    if page_uid:
        page_digest = _canonical_json_digest(
            paths.page_meta_path(work_dir, page_uid),
        )
        if not page_digest:
            return ""
        parts.append((page_uid, page_digest))
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
