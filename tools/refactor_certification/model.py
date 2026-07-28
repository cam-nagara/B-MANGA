"""Small serializable records for Phase 0 catalogs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Feature:
    feature_id: str
    kind: str
    target: str
    source: str
    symbol: str
    line: int
    label: str = ""
    aliases: list[str] = field(default_factory=list)
    bl_idname: str = ""
    field_id: str = ""
    field_aliases: list[str] = field(default_factory=list)
    property_type: str = ""
    ui_location: str = ""
    prerequisite_file_role: str = "unclassified"
    input_contract: str = "unclassified"
    success_contract: str = "unclassified"
    cancel_contract: str = "unclassified"
    undo_redo_contract: str = "unclassified"
    save_reload_contract: str = "unclassified"
    visual_expectation: str = "unclassified"
    artifact_expectation: str = "unclassified"
    performance_probe: str = "unclassified"
    metadata: dict[str, Any] = field(default_factory=dict)
    test_ids: list[str] = field(default_factory=list)
    test_evidence: dict[str, list[str]] = field(default_factory=dict)
    untested: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestCase:
    test_id: str
    source: str
    execution_kind: str
    entrypoint: bool
    entrypoint_kind: str
    audit_registered: bool
    audit_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
