"""Settings Contract生成・照合CLI。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .build import build_settings_contract, render_markdown, write_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    return parser


def _outputs(root: Path):
    return (
        root / "bmanga_core" / "settings_field_specs.json",
        root / "docs" / "refactor" / "phase2" / "settings_matrix.md",
        root / "docs" / "refactor" / "phase2" / "detail_ui_matrix.json",
    )


def _check(path: Path, expected: str) -> None:
    actual = path.read_text(encoding="utf-8") if path.is_file() else ""
    if actual != expected:
        raise ValueError(f"generated Settings Contract is stale: {path}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    registry, ui_matrix = build_settings_contract(root)
    registry_path, matrix_path, ui_path = _outputs(root)
    registry_text = __import__("json").dumps(
        registry,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    matrix_text = render_markdown(registry)
    ui_text = __import__("json").dumps(
        ui_matrix,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if args.check:
        _check(registry_path, registry_text)
        _check(matrix_path, matrix_text)
        _check(ui_path, ui_text)
    else:
        write_json(registry_path, registry)
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        matrix_path.write_text(matrix_text, encoding="utf-8")
        write_json(ui_path, ui_matrix)
    summary = registry["summary"]
    print(
        "BMANGA_SETTINGS_CONTRACT_OK "
        f"fields={summary['field_count']} "
        f"bindings={summary['property_binding_count']} "
        f"schema={summary['schema_field_count']} "
        f"preset={summary['preset_field_count']} "
        f"ui={ui_matrix['binding_count']}"
    )
    return 0
