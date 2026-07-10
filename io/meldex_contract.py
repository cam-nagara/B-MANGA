"""Meldex -> B-MANGA scenario contract v1 validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CONTRACT_NAME = "meldex-bmanga-scenario"
CONTRACT_VERSION = 1
MAX_PAGES = 2000
MAX_ROWS = 100000
MAX_TEXT_CHARS = 2_000_000


class ContractError(ValueError):
    """Raised when a received scenario does not satisfy contract v1."""


@dataclass(frozen=True)
class ScenarioRow:
    row_id: str
    type_name: str
    body: str
    rubies: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ScenarioPage:
    rows: tuple[ScenarioRow, ...]


@dataclass(frozen=True)
class ScenarioDocument:
    document_id: str
    pages: tuple[ScenarioPage, ...]


def validate_payload(payload: Any) -> ScenarioDocument:
    if not isinstance(payload, dict):
        raise ContractError("payload must be an object")
    contract = payload.get("contract", payload.get("contractName"))
    version = payload.get("version", payload.get("contractVersion"))
    if contract != CONTRACT_NAME or type(version) is not int or version != CONTRACT_VERSION:
        raise ContractError("unsupported contract")
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    document_id = payload.get("sourceDocumentId", source.get("documentId"))
    if not isinstance(document_id, str) or not document_id:
        raise ContractError("source document id is required")
    pages_raw = payload.get("pages")
    if not isinstance(pages_raw, list) or len(pages_raw) > MAX_PAGES:
        raise ContractError("pages must be a bounded array")
    pages: list[ScenarioPage] = []
    row_count = 0
    text_chars = 0
    seen_ids: set[str] = set()
    for page_raw in pages_raw:
        if not isinstance(page_raw, dict):
            raise ContractError("page must be an object")
        rows_raw = page_raw.get("rows", [])
        if not isinstance(rows_raw, list):
            raise ContractError("rows must be an array")
        rows: list[ScenarioRow] = []
        for row_raw in rows_raw:
            row = _validate_row(row_raw)
            if row.row_id in seen_ids:
                raise ContractError("row ids must be unique")
            seen_ids.add(row.row_id)
            row_count += 1
            text_chars += len(row.body)
            if row_count > MAX_ROWS or text_chars > MAX_TEXT_CHARS:
                raise ContractError("scenario is too large")
            rows.append(row)
        pages.append(ScenarioPage(tuple(rows)))
    return ScenarioDocument(document_id, tuple(pages))


def _validate_row(raw: Any) -> ScenarioRow:
    if not isinstance(raw, dict):
        raise ContractError("row must be an object")
    row_id = raw.get("rowId")
    type_name = raw.get("type", "")
    body = raw.get("body", "")
    rubies = raw.get("rubies", [])
    if not isinstance(row_id, str) or not row_id:
        raise ContractError("row id is required")
    if not isinstance(type_name, str) or not isinstance(body, str):
        raise ContractError("row type and body must be strings")
    if not isinstance(rubies, list):
        raise ContractError("rubies must be an array")
    checked: list[dict[str, Any]] = []
    for ruby in rubies:
        checked.append(_validate_ruby(ruby, len(body)))
    return ScenarioRow(row_id, type_name, body, tuple(checked))


def _validate_ruby(raw: Any, body_length: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContractError("ruby must be an object")
    try:
        start = int(raw.get("start"))
        length = int(raw.get("length"))
    except (TypeError, ValueError) as exc:
        raise ContractError("ruby range must be integers") from exc
    text = raw.get("rubyText", raw.get("text"))
    style = str(raw.get("style", "group") or "group")
    if not isinstance(text, str) or not text or start < 0 or length < 1:
        raise ContractError("invalid ruby")
    if start + length > body_length or style not in {"mono", "group", "jukugo"}:
        raise ContractError("ruby range is outside body")
    return {"start": start, "length": length, "rubyText": text, "style": style}
