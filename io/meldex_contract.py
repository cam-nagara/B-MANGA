"""Meldex -> B-MANGA scenario contract v1/v2 validation."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

CONTRACT_NAME = "meldex-bmanga-scenario"
CONTRACT_VERSION = 1
LATEST_CONTRACT_VERSION = 2
SUPPORTED_CONTRACT_VERSIONS = (1, 2)
MAX_PAGES = 2000
MAX_ROWS = 100000
MAX_TEXT_CHARS = 2_000_000


class ContractError(ValueError):
    """Raised when a received scenario does not satisfy the contract."""


@dataclass(frozen=True)
class ScenarioRow:
    row_id: str
    type_name: str
    body: str
    rubies: tuple[dict[str, Any], ...]
    presentation: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScenarioPage:
    rows: tuple[ScenarioRow, ...]


@dataclass(frozen=True)
class ScenarioDocument:
    document_id: str
    pages: tuple[ScenarioPage, ...]
    version: int = CONTRACT_VERSION
    presentation: dict[str, Any] | None = None


def validate_payload(payload: Any) -> ScenarioDocument:
    if not isinstance(payload, dict):
        raise ContractError("payload must be an object")
    contract = payload.get("contract", payload.get("contractName"))
    version = payload.get("version", payload.get("contractVersion"))
    if contract != CONTRACT_NAME or type(version) is not int or version not in SUPPORTED_CONTRACT_VERSIONS:
        raise ContractError("unsupported contract")
    if version >= 2 and (
        payload.get("indexUnit") != "unicode-code-point" or payload.get("normalization") != "none"
    ):
        raise ContractError("unsupported ruby index unit or normalization")
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    document_id = payload.get("sourceDocumentId", source.get("documentId"))
    if not isinstance(document_id, str) or not document_id:
        raise ContractError("source document id is required")
    presentation = _validate_presentation(payload.get("presentation"), version)
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
            row = _validate_row(row_raw, version)
            if row.row_id in seen_ids:
                raise ContractError("row ids must be unique")
            seen_ids.add(row.row_id)
            row_count += 1
            text_chars += len(row.body)
            if row_count > MAX_ROWS or text_chars > MAX_TEXT_CHARS:
                raise ContractError("scenario is too large")
            rows.append(row)
        pages.append(ScenarioPage(tuple(rows)))
    return ScenarioDocument(document_id, tuple(pages), version, presentation)


def _validate_row(raw: Any, version: int) -> ScenarioRow:
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
    checked = tuple(_validate_ruby(ruby, len(body), version) for ruby in rubies)
    return ScenarioRow(row_id, type_name, body, checked, _validate_presentation(raw.get("presentation"), version))


def _validate_ruby(raw: Any, body_length: int, version: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContractError("ruby must be an object")
    start = _strict_int(raw.get("start"), "ruby range must be integers")
    length = _strict_int(raw.get("length"), "ruby range must be integers")
    text = raw.get("rubyText", raw.get("text"))
    style = str(raw.get("style", "group") or "group")
    if not isinstance(text, str) or not text or start < 0 or length < 1:
        raise ContractError("invalid ruby")
    if start + length > body_length or style not in {"mono", "group", "jukugo"}:
        raise ContractError("ruby range is outside body")
    result: dict[str, Any] = {"start": start, "length": length, "rubyText": text, "style": style}
    if version >= 2:
        origin = raw.get("origin", "manual")
        priority = raw.get("priority", 0)
        if origin not in {"manual", "shared-link-dictionary", "document-rule", "local-auto-dictionary"} or type(priority) is not int:
            raise ContractError("invalid ruby origin or priority")
        result.update(origin=origin, priority=priority)
        segments_raw = raw.get("segments", [])
        if not isinstance(segments_raw, list):
            raise ContractError("ruby segments must be an array")
        segments = tuple(_validate_segment(item, length) for item in segments_raw)
        if segments:
            result["segments"] = segments
    return result


def _validate_segment(raw: Any, parent_length: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContractError("ruby segment must be an object")
    start = _strict_int(raw.get("start"), "ruby segment range must be integers")
    length = _strict_int(raw.get("length"), "ruby segment range must be integers")
    text = raw.get("rubyText", raw.get("text"))
    if not isinstance(text, str) or not text or start < 0 or length < 1 or start + length > parent_length:
        raise ContractError("ruby segment is outside parent ruby")
    return {"start": start, "length": length, "rubyText": text}


def _validate_presentation(raw: Any, version: int) -> dict[str, Any] | None:
    if raw is None:
        return None
    if version < 2 or not isinstance(raw, dict):
        raise ContractError("presentation requires contract v2")
    ruby = raw.get("ruby")
    if not isinstance(ruby, dict):
        raise ContractError("presentation.ruby must be an object")
    allowed = {
        "writingMode", "sizePercent", "gapEm", "letterSpacingEm", "lineHeight",
        "align", "smallKana", "fontPreset",
    }
    if set(ruby) - allowed:
        raise ContractError("unsupported presentation.ruby field")
    checked: dict[str, Any] = {}
    if "writingMode" in ruby:
        checked["writingMode"] = _choice(ruby["writingMode"], {"horizontal", "vertical"}, "writingMode")
    if "sizePercent" in ruby:
        checked["sizePercent"] = _number(ruby["sizePercent"], 5.0, 200.0, "sizePercent")
    if "gapEm" in ruby:
        checked["gapEm"] = _number(ruby["gapEm"], -2.0, 4.0, "gapEm")
    if "letterSpacingEm" in ruby:
        checked["letterSpacingEm"] = _number(ruby["letterSpacingEm"], -0.5, 3.0, "letterSpacingEm")
    if "lineHeight" in ruby:
        checked["lineHeight"] = _number(ruby["lineHeight"], 0.5, 5.0, "lineHeight")
    if "align" in ruby:
        checked["align"] = _choice(ruby["align"], {"center", "start"}, "align")
    if "smallKana" in ruby:
        checked["smallKana"] = _choice(ruby["smallKana"], {"keep", "fullsize"}, "smallKana")
    if "fontPreset" in ruby:
        preset = ruby["fontPreset"]
        if preset not in {"inherit", "sans-jp", "serif-jp", "gothic-jp"}:
            raise ContractError("fontPreset must be a logical preset name")
        checked["fontPreset"] = preset
    return {"ruby": checked}


def _strict_int(value: Any, message: str) -> int:
    if type(value) is not int:
        raise ContractError(message)
    return value


def _number(value: Any, minimum: float, maximum: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ContractError(f"{name} is outside the supported range")
    return result


def _choice(value: Any, choices: set[str], name: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ContractError(f"invalid {name}")
    return value
