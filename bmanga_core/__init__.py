"""Blenderへ依存しないB-MANGA application core。

このpackageは通常のPythonだけでimportできる。Blender adapterからも同じ
観測・失敗注入契約を利用し、将来のCommand/Transaction実装へ引き継ぐ。
"""

from .faults import (
    FaultInjectedError,
    FaultPoint,
    arm_fault,
    check_fault,
    configure_faults_from_environment,
)
from .observability import emit_event, operation_span
from .domain_ids import UIDKind, new_uid, validate_uid
from .domain_model import PageDocument, ProjectDocument
from .domain_repository import ProjectRepository
from .domain_store import DomainStore

configure_faults_from_environment()

__all__ = (
    "FaultInjectedError",
    "FaultPoint",
    "arm_fault",
    "check_fault",
    "configure_faults_from_environment",
    "emit_event",
    "DomainStore",
    "PageDocument",
    "ProjectDocument",
    "ProjectRepository",
    "UIDKind",
    "new_uid",
    "operation_span",
    "validate_uid",
)
