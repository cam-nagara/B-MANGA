"""認定manifestと実行結果の小さな値オブジェクト。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


RUN_MODES = {
    "blender_headless",
    "blender_ui",
    "blender_wrapper",
    "python_pytest",
    "python_script",
    "support",
    "historical",
}


@dataclass(frozen=True)
class Case:
    test_id: str
    source: str
    source_sha256: str
    mode: str
    required: bool
    timeout_seconds: int
    run_order: int = 100
    completion_token: str = ""
    args: tuple[str, ...] = ()
    blender_args: tuple[str, ...] = ()
    reason: str = ""
    review: str = ""
    phase0_category: str = ""
    artifacts: tuple[dict[str, Any], ...] = ()
    expected_tracebacks: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Case":
        return cls(
            test_id=str(raw["test_id"]),
            source=str(raw["source"]),
            source_sha256=str(raw["source_sha256"]),
            mode=str(raw["mode"]),
            required=bool(raw["required"]),
            timeout_seconds=int(raw["timeout_seconds"]),
            run_order=int(raw.get("run_order", 100)),
            completion_token=str(raw.get("completion_token", "")),
            args=tuple(str(value) for value in raw.get("args", ())),
            blender_args=tuple(
                str(value) for value in raw.get("blender_args", ())
            ),
            reason=str(raw.get("reason", "")),
            review=str(raw.get("review", "")),
            phase0_category=str(raw.get("phase0_category", "")),
            artifacts=tuple(raw.get("artifacts", ())),
            expected_tracebacks=tuple(raw.get("expected_tracebacks", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Result:
    test_id: str
    source: str
    mode: str
    required: bool
    status: str
    seconds: float
    source_sha256: str
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    reason: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
