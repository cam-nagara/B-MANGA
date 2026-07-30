"""パス構築ユーティリティ.

作品フォルダ (.bmanga) 直下・ページディレクトリ・コマファイルの相対パスを
一元的に構築する。.bmanga フォルダの命名規則 (4.1-4.4) を 1 箇所に集約。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

try:
    from ..bmanga_core.domain_ids import UIDKind, derived_uid, is_uid, validate_uid
except ImportError:  # pure testで単独moduleとして読む場合
    from bmanga_core.domain_ids import UIDKind, derived_uid, is_uid, validate_uid

PROJECT_META_NAME = "project.json"
WORK_BLEND_NAME = "work.blend"
PAGES_DIR_NAME = "pages"
PAGE_META_NAME = "page.json"
PAGE_BLEND_NAME = "page.blend"
PAGE_ASSETS_DIR_NAME = "assets"
COMAS_DIR_NAME = "comas"
COMA_BLEND_NAME = "scene.blend"
COMA_PREVIEW_NAME = "preview.png"
ASSETS_DIR_NAME = "assets"
ASSETS_TEMPLATES_DIR = "templates"
ASSETS_BRUSHES_DIR = "brushes"
ASSETS_MODELS_DIR = "models"
ASSETS_BALLOONS_DIR = "balloons"
ASSETS_BORDERS_DIR = "borders"
ASSETS_EFFECTS_DIR = "effects"
ASSETS_TAILS_DIR = "tails"
SCENARIO_DIR_NAME = "scenario"
SCENARIO_FILE_NAME = "imported.json"
EXPORTS_DIR_NAME = "exports"
RASTER_DIR_NAME = "raster"
RASTER_TRASH_DIR_NAME = ".trash"

BMANGA_DIR_SUFFIX = ".bmanga"

# 単ページ ("p0001") と見開き ("p0020-0021") のみ許可
_PAGE_ID_RE = re.compile(r"^p\d{4}(-\d{4})?$")
_COMA_ID_RE = re.compile(r"^c\d{2}$")


class WorkPathBoundaryError(RuntimeError):
    """作品root外へ解決される物理パスを拒否する。"""


def assert_work_owned_path(work_dir: Path, path: Path) -> Path:
    """junction/symlink解決後も``path``が作品root内であることを保証する。"""

    root = Path(work_dir).resolve(strict=False)
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorkPathBoundaryError(
            f"work path escapes project root: {candidate}"
        ) from exc
    return candidate


def _work_owned_path(work_dir: Path, *parts: str) -> Path:
    return assert_work_owned_path(
        work_dir,
        Path(work_dir).joinpath(*parts),
    )


def is_valid_page_id(page_id: str) -> bool:
    return isinstance(page_id, str) and bool(_PAGE_ID_RE.match(page_id))


def is_valid_page_uid(value: str) -> bool:
    return is_uid(value, UIDKind.PAGE)


def is_valid_coma_uid(value: str) -> bool:
    return is_uid(value, UIDKind.COMA)


def is_valid_coma_id(coma_id: str) -> bool:
    if not isinstance(coma_id, str) or not _COMA_ID_RE.match(coma_id):
        return False
    try:
        return 1 <= int(coma_id[1:]) <= 99
    except ValueError:
        return False


def validate_page_id(page_id: str) -> str:
    """不正な page_id ならエラー。呼び出し側はパス結合前に必ず通すこと."""
    if not is_valid_page_id(page_id):
        raise ValueError(f"invalid page_id: {page_id!r}")
    return page_id


def validate_coma_id(coma_id: str) -> str:
    if not is_valid_coma_id(coma_id):
        raise ValueError(f"invalid coma_id: {coma_id!r}")
    return coma_id


def format_page_id(index: int) -> str:
    """ページ番号を 4 桁ゼロパディング ID に変換 (例: 1 → "p0001")."""
    if index < 1 or index > 9999:
        raise ValueError(f"page index must be 1..9999: {index}")
    return f"p{index:04d}"


def format_spread_id(left: int, right: int) -> str:
    """見開きページの ID を生成 (例: 20, 21 → "p0020-0021")."""
    left_id = format_page_id(left)
    right_num = format_page_id(right)[1:]
    return f"{left_id}-{right_num}"


def format_coma_id(index: int) -> str:
    """コマ ID を 2 桁ゼロパディングで生成 (例: 1 → "c01")."""
    if index < 1 or index > 99:
        raise ValueError(f"coma index must be 1..99: {index}")
    return f"c{index:02d}"


def project_meta_path(work_dir: Path) -> Path:
    return _work_owned_path(work_dir, PROJECT_META_NAME)


def resolve_page_uid(work_dir: Path, page_ref: str) -> str:
    """内部UIDまたは表示IDからpage UIDを厳格に解決する。"""

    if is_uid(page_ref, UIDKind.PAGE):
        return validate_uid(page_ref, UIDKind.PAGE)
    display_id = validate_page_id(page_ref)
    project = _read_domain_json(
        project_meta_path(work_dir),
        "bmanga.project",
        work_dir=work_dir,
    )
    pages = project.get("pages")
    if not isinstance(pages, dict):
        raise ValueError("project.json pages must be an object")
    for uid, summary in pages.items():
        if not isinstance(summary, dict):
            raise ValueError("project.json page summary must be an object")
        if summary.get("displayId") == display_id:
            return validate_uid(uid, UIDKind.PAGE)
    project_uid = validate_uid(project.get("projectUid"), UIDKind.PROJECT)
    return derived_uid(UIDKind.PAGE, project_uid, display_id)


def page_display_id(work_dir: Path, page_uid: str) -> str:
    uid = validate_uid(page_uid, UIDKind.PAGE)
    project = _read_domain_json(
        project_meta_path(work_dir),
        "bmanga.project",
        work_dir=work_dir,
    )
    pages = project.get("pages")
    if not isinstance(pages, dict) or not isinstance(pages.get(uid), dict):
        raise KeyError(f"page UID is not registered: {uid}")
    return validate_page_id(str(pages[uid].get("displayId", "")))


def page_dir(work_dir: Path, page_ref: str) -> Path:
    page_uid = resolve_page_uid(work_dir, page_ref)
    return _work_owned_path(work_dir, PAGES_DIR_NAME, page_uid)


def page_meta_path(work_dir: Path, page_ref: str) -> Path:
    return assert_work_owned_path(
        work_dir,
        page_dir(work_dir, page_ref) / PAGE_META_NAME,
    )


def page_blend_path(work_dir: Path, page_ref: str) -> Path:
    """ページ用 .blend のパスをUID directory内へ構築する。"""

    return assert_work_owned_path(
        work_dir,
        page_dir(work_dir, page_ref) / PAGE_BLEND_NAME,
    )


def page_assets_dir(work_dir: Path, page_ref: str) -> Path:
    return assert_work_owned_path(
        work_dir,
        page_dir(work_dir, page_ref) / PAGE_ASSETS_DIR_NAME,
    )


def page_comas_dir(work_dir: Path, page_ref: str) -> Path:
    return assert_work_owned_path(
        work_dir,
        page_dir(work_dir, page_ref) / COMAS_DIR_NAME,
    )


def work_blend_path(work_dir: Path) -> Path:
    """作品マスター .blend のパス (``<work>.bmanga/work.blend``)."""
    return _work_owned_path(work_dir, WORK_BLEND_NAME)


def resolve_coma_uid(work_dir: Path, page_ref: str, coma_ref: str) -> str:
    """内部UIDまたは表示IDからcoma UIDを解決する。

    未checkpointの新規コマはpage UIDと表示IDから決定的に採番する。
    checkpoint後はpage.jsonに保存したnativeUidを正とする。
    """

    if is_uid(coma_ref, UIDKind.COMA):
        return validate_uid(coma_ref, UIDKind.COMA)
    coma_id = validate_coma_id(coma_ref)
    page_uid = resolve_page_uid(work_dir, page_ref)
    meta = page_meta_path(work_dir, page_uid)
    if meta.is_file():
        page = _read_domain_json(
            meta,
            "bmanga.page",
            work_dir=work_dir,
        )
        tree = page.get("tree")
        if not isinstance(tree, dict):
            raise ValueError("page.json tree must be an object")
        nodes = tree.get("nodes")
        if not isinstance(nodes, dict):
            raise ValueError("page.json nodes must be an object")
        for node in nodes.values():
            if not isinstance(node, dict):
                raise ValueError("page.json node must be an object")
            if node.get("kind") != "coma" or node.get("displayId") != coma_id:
                continue
            native_uid = node.get("nativeUid")
            if native_uid:
                return validate_uid(native_uid, UIDKind.COMA)
    return derived_uid(UIDKind.COMA, page_uid, coma_id)


def coma_display_id(work_dir: Path, page_ref: str, coma_uid: str) -> str:
    uid = validate_uid(coma_uid, UIDKind.COMA)
    page_uid = resolve_page_uid(work_dir, page_ref)
    page = _read_domain_json(
        page_meta_path(work_dir, page_uid),
        "bmanga.page",
        work_dir=work_dir,
    )
    tree = page.get("tree")
    if not isinstance(tree, dict):
        raise ValueError("page.json tree must be an object")
    nodes = tree.get("nodes")
    if not isinstance(nodes, dict):
        raise ValueError("page.json nodes must be an object")
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        if node.get("kind") == "coma" and node.get("nativeUid") == uid:
            return validate_coma_id(str(node.get("displayId", "")))
    raise KeyError(f"coma UID is not registered: {uid}")


def coma_dir(work_dir: Path, page_ref: str, coma_ref: str) -> Path:
    coma_uid = resolve_coma_uid(work_dir, page_ref, coma_ref)
    return assert_work_owned_path(
        work_dir,
        page_comas_dir(work_dir, page_ref) / coma_uid,
    )


def coma_blend_path(work_dir: Path, page_ref: str, coma_ref: str) -> Path:
    return assert_work_owned_path(
        work_dir,
        coma_dir(work_dir, page_ref, coma_ref) / COMA_BLEND_NAME,
    )


def coma_thumb_path(work_dir: Path, page_ref: str, coma_ref: str) -> Path:
    return assert_work_owned_path(
        work_dir,
        coma_dir(work_dir, page_ref, coma_ref) / COMA_PREVIEW_NAME,
    )


def coma_preview_path(work_dir: Path, page_ref: str, coma_ref: str) -> Path:
    return assert_work_owned_path(
        work_dir,
        coma_dir(work_dir, page_ref, coma_ref) / COMA_PREVIEW_NAME,
    )


def coma_passes_dir(work_dir: Path, page_ref: str, coma_ref: str) -> Path:
    return assert_work_owned_path(
        work_dir,
        coma_dir(work_dir, page_ref, coma_ref) / "passes",
    )


def coma_passes_cube_dir(work_dir: Path, page_id: str, coma_id: str) -> Path:
    return assert_work_owned_path(
        work_dir,
        coma_passes_dir(work_dir, page_id, coma_id) / "cube",
    )


def assets_dir(work_dir: Path) -> Path:
    return _work_owned_path(work_dir, ASSETS_DIR_NAME)


def scenario_dir(work_dir: Path) -> Path:
    return _work_owned_path(work_dir, SCENARIO_DIR_NAME)


def scenario_file(work_dir: Path) -> Path:
    return assert_work_owned_path(
        work_dir,
        scenario_dir(work_dir) / SCENARIO_FILE_NAME,
    )


def exports_dir(work_dir: Path) -> Path:
    return _work_owned_path(work_dir, EXPORTS_DIR_NAME)


def raster_dir(work_dir: Path) -> Path:
    return _work_owned_path(work_dir, RASTER_DIR_NAME)


def raster_trash_dir(work_dir: Path) -> Path:
    return assert_work_owned_path(
        work_dir,
        raster_dir(work_dir) / RASTER_TRASH_DIR_NAME,
    )


def raster_png_path(work_dir: Path, raster_id: str) -> Path:
    safe_id = re.sub(r"[^0-9a-fA-F]", "", str(raster_id or ""))[:12]
    if not safe_id:
        raise ValueError(f"invalid raster id: {raster_id!r}")
    return assert_work_owned_path(
        work_dir,
        raster_dir(work_dir) / f"{safe_id}.png",
    )


def ensure_bmanga_suffix(path: Path) -> Path:
    """``.bmanga`` 拡張子を持たせたディレクトリパスを返す (既に持っていればそのまま)."""
    p = Path(path)
    if p.suffix == BMANGA_DIR_SUFFIX:
        return p
    return p.with_suffix(BMANGA_DIR_SUFFIX)


def as_relative(path: Path, base: Path) -> Path:
    """base からの相対パスを返す。別ドライブ等で不可なら絶対パスを返す."""
    try:
        return Path(path).resolve().relative_to(Path(base).resolve())
    except ValueError:
        return Path(path).resolve()


def next_available_page_index(existing_ids: Iterable[str]) -> int:
    """既存ページ ID から空き番号の最小値を採番."""
    used: set[int] = set()
    for page_id in existing_ids:
        parts = str(page_id).split("-", 1)
        head = parts[0]
        if head.startswith("p"):
            head = head[1:]
        if head.isdigit():
            used.add(int(head))
        if len(parts) > 1 and parts[1].isdigit():
            used.add(int(parts[1]))
    i = 1
    while i in used:
        i += 1
    return i


def next_available_coma_index(existing_ids: Iterable[str]) -> int:
    """既存コマ ID から空き番号の最小値を採番."""
    used: set[int] = set()
    for coma_id in existing_ids:
        coma_id = str(coma_id)
        if is_valid_coma_id(coma_id):
            used.add(int(coma_id[1:]))
    i = 1
    while i in used:
        i += 1
    if i > 99:
        raise ValueError("coma count exceeds maximum c99")
    return i


def _read_domain_json(
    path: Path,
    expected_schema: str,
    *,
    work_dir: Path,
) -> dict:
    target = assert_work_owned_path(work_dir, path).resolve(strict=False)
    try:
        raw = target.read_bytes()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"required Domain file is missing: {path}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Domain JSON: {path}") from exc
    _validate_domain_json(data, target, expected_schema)
    return data


def _validate_domain_json(
    data: object,
    path: Path,
    expected_schema: str,
) -> None:
    if not isinstance(data, dict) or data.get("schema") != expected_schema:
        raise ValueError(f"旧形式または未対応形式です: {path.name}")
    if data.get("schemaVersion") != 1:
        raise ValueError(f"未対応schemaVersionです: {path.name}")
