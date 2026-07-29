"""Golden artifactの提案、別承認、完全性検証。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1


def _inside_root(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"golden path escapes root: {relative}") from exc
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def propose(
    root: Path,
    paths: Iterable[str],
    *,
    requested_by: str,
    created_at: str,
) -> dict[str, Any]:
    """現在のartifact hashをpending proposalに固定する。"""

    if not requested_by or not created_at:
        raise ValueError("requested_by and created_at are required")
    artifacts = []
    for relative in sorted(set(paths)):
        path = _inside_root(root, relative)
        if not path.is_file():
            raise FileNotFoundError(path)
        artifacts.append(
            {
                "path": relative.replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    if not artifacts:
        raise ValueError("at least one golden artifact is required")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pending",
        "requested_by": requested_by,
        "created_at": created_at,
        "artifacts": artifacts,
    }


def _verify_artifacts(root: Path, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported golden schema")
    artifacts = payload.get("artifacts", ())
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("golden registry has no artifacts")
        return errors
    for row in artifacts:
        relative = str(row.get("path", ""))
        try:
            path = _inside_root(root, relative)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not path.is_file():
            errors.append(f"missing golden artifact: {relative}")
            continue
        if int(row.get("bytes", -1)) != path.stat().st_size:
            errors.append(f"golden size differs: {relative}")
        if str(row.get("sha256", "")) != sha256(path):
            errors.append(f"golden hash differs: {relative}")
    return errors


def verify(root: Path, payload: dict[str, Any]) -> list[str]:
    """承認状態、承認記録、missing、hash、size差分を返す。空なら一致。"""

    errors: list[str] = []
    if payload.get("status") != "approved":
        errors.append("golden registry is not approved")
    approval = payload.get("approval")
    if (
        not isinstance(approval, dict)
        or not str(approval.get("id", ""))
        or not str(approval.get("approved_at", ""))
    ):
        errors.append("golden registry has no approval record")
    errors.extend(_verify_artifacts(root, payload))
    return errors


def approve(
    root: Path,
    proposal: dict[str, Any],
    *,
    approval_id: str,
    approved_at: str,
) -> dict[str, Any]:
    """pending proposalを、別の明示承認記録付きregistryへ変換する。"""

    if proposal.get("status") != "pending":
        raise ValueError("only a pending golden proposal can be approved")
    if not approval_id or not approved_at:
        raise ValueError("approval_id and approved_at are required")
    errors = _verify_artifacts(root, proposal)
    if errors:
        raise ValueError("\n".join(errors))
    result = dict(proposal)
    result["status"] = "approved"
    result["approval"] = {
        "id": approval_id,
        "approved_at": approved_at,
    }
    return result
