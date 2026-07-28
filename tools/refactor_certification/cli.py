"""Command-line entry point for deterministic catalog generation."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .catalog import build_catalog
from .registry import write_registry
from .render import write_json, write_markdown


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the B-MANGA Phase 0 feature/test catalog."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--markdown-out", type=Path, required=True)
    parser.add_argument("--registry-out", type=Path)
    parser.add_argument(
        "--allow-unverified-ownership",
        action="store_true",
        help="fixture repositories only",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    catalog = build_catalog(args.root)
    summary = catalog["summary"]
    if (
        summary["unverified_property_group_ownership"]
        and not args.allow_unverified_ownership
    ):
        raise ValueError("unverified PropertyGroup ownership remains")
    if args.registry_out is not None:
        write_registry(catalog, args.registry_out)
    write_json(catalog, args.json_out)
    write_markdown(catalog, args.markdown_out)
    print(
        "REFACTOR_CERTIFICATION_CATALOG_OK "
        f"features={summary['feature_count']} "
        f"untested={summary['untested_feature_count']} "
        f"tests={summary['test_count']}"
    )
    return 0
