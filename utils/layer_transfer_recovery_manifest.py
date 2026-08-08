"""ページ間移送journalの厳格schema・fingerprint契約。"""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import re

from ..io import save_baseline
from . import cross_page_stage, json_io, paths


VERSION = 2
ACTIVE_PHASES = frozenset({"preparing", "prepared"})
TARGET_SAVED_PHASE = "target_saved"
TERMINAL_PHASES = frozenset({"rollback_applied", "committed"})
PHASES = ACTIVE_PHASES | {TARGET_SAVED_PHASE} | TERMINAL_PHASES
_STAGE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_FP_KEYS = frozenset({"exists", "digest", "size", "mtime_ns"})
_FILE_KEYS = frozenset({
    "relative_path",
    "backup_name",
    "existed",
    "backup_fingerprint",
    "allowed_current_fingerprints",
})
_COMA_KEYS = frozenset({"source_id", "target_id", "source_existed"})
_STAGE_KEYS = frozenset({"entry", "entry_hash", "entry_token"})
_MANIFEST_KEYS = frozenset({
    "version",
    "phase",
    "stage_id",
    "source_page_id",
    "target_page_id",
    "target_stage",
    "files",
    "coma_moves",
})


class RecoveryManifestError(ValueError):
    """復旧journalが安全に自動適用できない。"""


def fingerprint_dict(path: Path) -> dict:
    value = save_baseline.fingerprint(path)
    return {
        "exists": value.exists,
        "digest": value.digest,
        "size": value.size,
        "mtime_ns": value.mtime_ns,
    }


def required_paths(work_dir: Path, source_page_id: str) -> tuple[Path, ...]:
    return (
        paths.page_blend_path(work_dir, source_page_id).resolve(),
        paths.page_meta_path(work_dir, source_page_id).resolve(),
        paths.project_meta_path(work_dir).resolve(),
    )


def build(
    work_dir: Path,
    recovery_dir: Path,
    source_page_id: str,
    target_page_id: str,
    stage_id: str,
    coma_moves,
    backup: dict[Path, Path | None],
    target_stage: dict,
    *,
    phase: str,
    allowed_by_path: dict[Path, list[dict]] | None = None,
) -> dict:
    normalized_backup = {path.resolve(): saved for path, saved in backup.items()}
    allowed = allowed_by_path or {
        path.resolve(): [fingerprint_dict(path)]
        for path in required_paths(work_dir, source_page_id)
    }
    return {
        "version": VERSION,
        "phase": phase,
        "stage_id": stage_id,
        "source_page_id": source_page_id,
        "target_page_id": target_page_id,
        "target_stage": target_stage,
        "files": [
            _file_record(
                work_dir,
                recovery_dir,
                path,
                normalized_backup[path],
                allowed,
            )
            for path in required_paths(work_dir, source_page_id)
        ],
        "coma_moves": [
            {
                "source_id": move.source_id,
                "target_id": move.target_id,
                "source_existed": paths.coma_dir(
                    work_dir,
                    source_page_id,
                    move.source_id,
                ).is_dir(),
            }
            for move in coma_moves
        ],
    }


def _file_record(
    work_dir: Path,
    recovery_dir: Path,
    path: Path,
    saved: Path | None,
    allowed_by_path: dict[Path, list[dict]],
) -> dict:
    resolved = path.resolve()
    return {
        "relative_path": resolved.relative_to(work_dir.resolve()).as_posix(),
        "backup_name": (
            saved.resolve().relative_to(recovery_dir.resolve()).as_posix()
            if saved is not None
            else ""
        ),
        "existed": saved is not None,
        "backup_fingerprint": (
            fingerprint_dict(saved) if saved is not None else _missing_fingerprint()
        ),
        "allowed_current_fingerprints": list(allowed_by_path[resolved]),
    }


def append_current_state(manifest_path: Path, work_dir: Path, manifest: dict) -> None:
    records = _validated_records(
        work_dir,
        manifest_path.parent,
        manifest,
        require_backups=True,
    )
    changed = False
    for destination, record, _saved in records:
        current = fingerprint_dict(destination)
        allowed = record["allowed_current_fingerprints"]
        if current not in allowed:
            allowed.append(current)
            changed = True
    if changed:
        json_io.write_json(manifest_path, manifest)


def replace_backup(
    manifest_path: Path,
    work_dir: Path,
    manifest: dict,
    backup: dict[Path, Path | None],
    *,
    phase: str,
) -> None:
    if phase not in ACTIVE_PHASES:
        raise RecoveryManifestError("invalid active recovery phase")
    records = _validated_records(
        work_dir,
        manifest_path.parent,
        manifest,
        require_backups=True,
    )
    normalized_backup = {path.resolve(): saved for path, saved in backup.items()}
    for destination, record, _saved in records:
        saved = normalized_backup[destination]
        record["backup_name"] = (
            saved.resolve().relative_to(manifest_path.parent.resolve()).as_posix()
            if saved is not None
            else ""
        )
        record["existed"] = saved is not None
        record["backup_fingerprint"] = (
            fingerprint_dict(saved) if saved is not None else _missing_fingerprint()
        )
    manifest["phase"] = phase
    json_io.write_json(manifest_path, manifest)


def set_terminal(manifest_path: Path, manifest: dict, phase: str) -> None:
    if phase not in TERMINAL_PHASES:
        raise RecoveryManifestError("invalid terminal recovery phase")
    manifest["phase"] = phase
    json_io.write_json(manifest_path, manifest)


def set_target_saved(manifest_path: Path, manifest: dict) -> None:
    """移送先Domain/nativeの保存証明をjournalへ耐久化する."""

    phase = manifest.get("phase")
    if phase == TARGET_SAVED_PHASE:
        return
    if phase not in ACTIVE_PHASES:
        raise RecoveryManifestError("invalid target-saved recovery phase")
    manifest["phase"] = TARGET_SAVED_PHASE
    json_io.write_json(manifest_path, manifest)


def validate(
    work_dir: Path,
    recovery_dir: Path,
    manifest: object,
) -> tuple[dict, dict[Path, Path | None]]:
    manifest = validate_schema(work_dir, recovery_dir, manifest)
    records = _validated_records(
        work_dir,
        recovery_dir,
        manifest,
        require_backups=True,
    )
    return manifest, {destination: saved for destination, _record, saved in records}


def validate_schema(
    work_dir: Path,
    recovery_dir: Path,
    manifest: object,
) -> dict:
    """terminal cleanupにも使える、backup実体非依存の厳格schema検証。"""

    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise RecoveryManifestError("invalid recovery manifest keys")
    if manifest["version"] != VERSION:
        raise RecoveryManifestError("unsupported recovery manifest version")
    if manifest["phase"] not in PHASES:
        raise RecoveryManifestError("invalid recovery phase")
    stage_id = manifest["stage_id"]
    if (
        not isinstance(stage_id, str)
        or not _STAGE_RE.fullmatch(stage_id)
        or recovery_dir.name != stage_id
    ):
        raise RecoveryManifestError("invalid recovery stage identity")
    source_id = manifest["source_page_id"]
    target_id = manifest["target_page_id"]
    if not isinstance(source_id, str) or not isinstance(target_id, str):
        raise RecoveryManifestError("invalid recovery page identity")
    paths.validate_page_id(source_id)
    paths.validate_page_id(target_id)
    if source_id == target_id:
        raise RecoveryManifestError("recovery pages must differ")
    expected_source_dir = paths.page_dir(work_dir, source_id).resolve()
    if recovery_dir.parent.parent.resolve() != expected_source_dir:
        raise RecoveryManifestError("recovery journal is under the wrong source page")
    _validate_target_stage(
        manifest["target_stage"],
        stage_id,
        target_id,
    )
    _validated_records(
        work_dir,
        recovery_dir,
        manifest,
        require_backups=False,
    )
    _validate_coma_moves(manifest["coma_moves"])
    return manifest


def _validate_target_stage(value: object, stage_id: str, target_id: str) -> None:
    if not isinstance(value, dict) or set(value) != _STAGE_KEYS:
        raise RecoveryManifestError("invalid target stage snapshot")
    entry = value["entry"]
    entry_hash = value["entry_hash"]
    entry_token = value["entry_token"]
    if (
        not isinstance(entry, dict)
        or str(entry.get("stage_id", "") or "") != stage_id
        or str(entry.get("target_page_id", "") or "") != target_id
        or str(entry.get("state", "") or "") != "prepared"
        or not isinstance(entry_hash, str)
        or not isinstance(entry_token, str)
    ):
        raise RecoveryManifestError("invalid target stage identity")
    try:
        encoded = json.dumps(
            entry,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RecoveryManifestError("target stage is not canonical JSON") from exc
    if (
        hashlib.sha256(encoded).hexdigest() != entry_hash
        or cross_page_stage._entry_token("asset", entry) != entry_token
    ):
        raise RecoveryManifestError("target stage fingerprint mismatch")


def _validated_records(
    work_dir: Path,
    recovery_dir: Path,
    manifest: dict,
    *,
    require_backups: bool,
) -> list[tuple[Path, dict, Path | None]]:
    source_id = manifest.get("source_page_id")
    if not isinstance(source_id, str):
        raise RecoveryManifestError("invalid source page identity")
    expected = set(required_paths(work_dir, source_id))
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(expected):
        raise RecoveryManifestError("required recovery files are missing")
    result = []
    seen = set()
    for record in files:
        if not isinstance(record, dict) or set(record) != _FILE_KEYS:
            raise RecoveryManifestError("invalid recovery file record")
        relative = record["relative_path"]
        if not isinstance(relative, str):
            raise RecoveryManifestError("invalid recovery relative path")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RecoveryManifestError("unsafe recovery destination")
        destination = (work_dir / relative_path).resolve()
        if destination not in expected or destination in seen:
            raise RecoveryManifestError("unexpected or duplicate recovery destination")
        seen.add(destination)
        existed = record["existed"]
        backup_name = record["backup_name"]
        if not isinstance(existed, bool) or not isinstance(backup_name, str):
            raise RecoveryManifestError("invalid recovery backup identity")
        backup_fp = _validate_fingerprint(record["backup_fingerprint"])
        allowed = record["allowed_current_fingerprints"]
        if not isinstance(allowed, list) or not allowed:
            raise RecoveryManifestError("recovery allowed fingerprints are missing")
        record["allowed_current_fingerprints"] = [
            _validate_fingerprint(value) for value in allowed
        ]
        saved = _validated_backup(
            recovery_dir,
            backup_name,
            existed,
            require_exists=require_backups,
        )
        if not require_backups:
            result.append((destination, record, saved))
            continue
        actual_backup_fp = (
            fingerprint_dict(saved) if saved is not None else _missing_fingerprint()
        )
        if actual_backup_fp != backup_fp:
            raise RecoveryManifestError("recovery backup fingerprint mismatch")
        result.append((destination, record, saved))
    if seen != expected:
        raise RecoveryManifestError("required recovery destinations differ")
    return result


def _validated_backup(
    recovery_dir: Path,
    backup_name: str,
    existed: bool,
    *,
    require_exists: bool,
) -> Path | None:
    if not existed:
        if backup_name:
            raise RecoveryManifestError("nonexistent backup has a name")
        return None
    relative = Path(backup_name)
    if not backup_name or relative.is_absolute() or ".." in relative.parts:
        raise RecoveryManifestError("unsafe recovery backup path")
    saved = (recovery_dir / relative).resolve()
    if not saved.is_relative_to(recovery_dir.resolve()):
        raise RecoveryManifestError("recovery backup escapes recovery directory")
    if require_exists and not saved.is_file():
        raise RecoveryManifestError("recovery backup is missing")
    return saved


def _validate_fingerprint(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != _FP_KEYS:
        raise RecoveryManifestError("invalid recovery fingerprint")
    exists = value["exists"]
    digest = value["digest"]
    size = value["size"]
    mtime_ns = value["mtime_ns"]
    if (
        not isinstance(exists, bool)
        or not isinstance(digest, str)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or isinstance(mtime_ns, bool)
        or not isinstance(mtime_ns, int)
        or size < 0
        or mtime_ns < 0
    ):
        raise RecoveryManifestError("invalid recovery fingerprint types")
    if exists:
        if not re.fullmatch(r"[0-9a-f]{64}|invalid-path", digest):
            raise RecoveryManifestError("invalid recovery fingerprint digest")
    elif digest or size or mtime_ns:
        raise RecoveryManifestError("nonexistent fingerprint has metadata")
    return dict(value)


def _missing_fingerprint() -> dict:
    return {"exists": False, "digest": "", "size": 0, "mtime_ns": 0}


def _validate_coma_moves(value: object) -> None:
    if not isinstance(value, list):
        raise RecoveryManifestError("coma_moves must be an array")
    identities = set()
    for record in value:
        if not isinstance(record, dict) or set(record) != _COMA_KEYS:
            raise RecoveryManifestError("invalid coma move record")
        source_id = record["source_id"]
        target_id = record["target_id"]
        source_existed = record["source_existed"]
        if (
            not isinstance(source_id, str)
            or not isinstance(target_id, str)
            or not isinstance(source_existed, bool)
        ):
            raise RecoveryManifestError("invalid coma move types")
        paths.validate_coma_id(source_id)
        paths.validate_coma_id(target_id)
        if (
            source_id == target_id
            or source_id in identities
            or target_id in identities
        ):
            raise RecoveryManifestError("duplicate coma move identity")
        identities.update((source_id, target_id))


def assert_current_allowed(
    work_dir: Path,
    recovery_dir: Path,
    manifest: dict,
) -> None:
    for destination, record, _saved in _validated_records(
        work_dir,
        recovery_dir,
        manifest,
        require_backups=True,
    ):
        if fingerprint_dict(destination) not in record["allowed_current_fingerprints"]:
            raise RecoveryManifestError(
                f"recovery destination was externally updated: {destination}"
            )


__all__ = (
    "ACTIVE_PHASES",
    "RecoveryManifestError",
    "TARGET_SAVED_PHASE",
    "TERMINAL_PHASES",
    "append_current_state",
    "assert_current_allowed",
    "build",
    "fingerprint_dict",
    "replace_backup",
    "set_target_saved",
    "set_terminal",
    "validate",
    "validate_schema",
)
