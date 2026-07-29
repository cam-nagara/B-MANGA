"""Golden proposal/approval/verification CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .golden import approve, propose, verify


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)

    proposal = commands.add_parser("propose")
    proposal.add_argument("--requested-by", required=True)
    proposal.add_argument("--created-at", required=True)
    proposal.add_argument("--out", type=Path, required=True)
    proposal.add_argument("artifacts", nargs="+")

    approval = commands.add_parser("approve")
    approval.add_argument("--proposal", type=Path, required=True)
    approval.add_argument("--approval-id", required=True)
    approval.add_argument("--approved-at", required=True)
    approval.add_argument("--out", type=Path, required=True)

    verification = commands.add_parser("verify")
    verification.add_argument("--registry", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    if args.command == "propose":
        payload = propose(
            root,
            args.artifacts,
            requested_by=args.requested_by,
            created_at=args.created_at,
        )
        _write_new(root / args.out, payload)
        print(f"BMANGA_GOLDEN_PROPOSAL_OK artifacts={len(payload['artifacts'])}")
        return 0
    if args.command == "approve":
        proposal = _read(root / args.proposal)
        if args.approval_id == proposal.get("requested_by"):
            raise ValueError("golden proposer cannot approve the same proposal")
        payload = approve(
            root,
            proposal,
            approval_id=args.approval_id,
            approved_at=args.approved_at,
        )
        _write_new(root / args.out, payload)
        print(f"BMANGA_GOLDEN_APPROVAL_OK artifacts={len(payload['artifacts'])}")
        return 0
    payload = _read(root / args.registry)
    errors = verify(root, payload)
    if errors:
        for error in errors:
            print(f"BMANGA_GOLDEN_ERROR {error}")
        return 1
    print(f"BMANGA_GOLDEN_VERIFY_OK artifacts={len(payload['artifacts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
