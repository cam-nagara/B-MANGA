"""PropertyGroup投影で使うDomain UIDの採番と保持。"""

from __future__ import annotations

from typing import Any

from ..bmanga_core.domain_ids import UIDKind, derived_uid, is_uid, new_uid


PROJECT_UID_PROP = "bmanga_domain_project_uid"
PROJECT_REVISION_PROP = "bmanga_domain_project_revision"
PAGE_UID_PROP = "bmanga_domain_page_uid"
PAGE_REVISION_PROP = "bmanga_domain_page_revision"
COMA_UID_PROP = "bmanga_domain_coma_uid"
NODE_UID_PROP = "bmanga_domain_node_uid"
SOURCE_PAGE_UIDS_PROP = "bmanga_domain_source_page_uids"


def custom_get(owner, key: str, default: Any = None) -> Any:
    try:
        return owner.get(key, default)
    except (AttributeError, TypeError):
        return default


def custom_set(owner, key: str, value: Any) -> None:
    try:
        owner[key] = value
    except (AttributeError, TypeError) as exc:
        raise RuntimeError(f"PropertyGroup does not support {key}") from exc


def ensure_project_uid(work) -> str:
    value = str(custom_get(work, PROJECT_UID_PROP, "") or "")
    if not is_uid(value, UIDKind.PROJECT):
        value = new_uid(UIDKind.PROJECT)
        custom_set(work, PROJECT_UID_PROP, value)
    return value


def ensure_page_uid(page, project_uid: str) -> str:
    value = str(custom_get(page, PAGE_UID_PROP, "") or "")
    if not is_uid(value, UIDKind.PAGE):
        display_id = str(getattr(page, "id", "") or "").strip()
        value = (
            derived_uid(UIDKind.PAGE, project_uid, display_id)
            if display_id
            else new_uid(UIDKind.PAGE)
        )
        custom_set(page, PAGE_UID_PROP, value)
    return value


def ensure_coma_uid(coma, page_uid: str) -> str:
    value = str(custom_get(coma, COMA_UID_PROP, "") or "")
    if not is_uid(value, UIDKind.COMA):
        display_id = str(
            getattr(coma, "coma_id", "") or getattr(coma, "id", "") or ""
        ).strip()
        value = (
            derived_uid(UIDKind.COMA, page_uid, display_id)
            if display_id
            else new_uid(UIDKind.COMA)
        )
        custom_set(coma, COMA_UID_PROP, value)
    return value
