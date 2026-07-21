"""詳細設定ダイアログのBlender非依存契約。

このモジュールは対象、編集モード、レイアウト、セッション境界だけを扱う。
描画や ``bpy`` の状態探索は行わず、入口で確定した対象を開いている間ずっと保持する。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

DEFAULT_COLUMN_WIDTH = 260
DEFAULT_COLUMN_GAP = 12
DEFAULT_OUTER_PADDING = 24
DEFAULT_SCREEN_MARGIN = 32

class DetailContractError(ValueError):
    """詳細設定の公開契約に違反した時の例外。"""

class DetailTargetNotFoundError(LookupError):
    """指定された安定IDから対象を解決できなかった。"""

class DetailSessionClosedError(RuntimeError):
    """終了済みセッションへ変更を加えようとした。"""

class DetailMode(str, Enum):
    ACTUAL = "actual"
    PRESET = "preset"

class DetailSessionStatus(str, Enum):
    OPEN = "open"
    COMMITTED = "committed"
    CANCELLED = "cancelled"
    RESTORE_FAILED = "restore_failed"

class DetailActionBoundary(str, Enum):
    TRANSACTIONAL = "transactional"
    INDEPENDENT_IMMEDIATE = "independent_immediate"
    EXCLUDED = "excluded"

@dataclass(frozen=True, slots=True)
class DetailLayoutProfile:
    """種別ごとの安全な最大列数と等幅列の寸法。"""

    max_columns: int
    preferred_column_width: int = DEFAULT_COLUMN_WIDTH
    column_gap: int = DEFAULT_COLUMN_GAP
    outer_padding: int = DEFAULT_OUTER_PADDING

    def __post_init__(self) -> None:
        if self.max_columns < 1:
            raise DetailContractError("max_columns must be at least 1")
        for name in ("preferred_column_width", "outer_padding"):
            if getattr(self, name) < 1:
                raise DetailContractError(f"{name} must be positive")
        if self.column_gap < 0:
            raise DetailContractError("column_gap cannot be negative")

    def fixed_dimensions(
        self,
        available_width: int | None,
        *,
        screen_margin: int = DEFAULT_SCREEN_MARGIN,
    ) -> tuple[int, int]:
        """最大列数から外枠幅と等幅の列幅を一度だけ決定する。"""

        overhead = self._overhead()
        preferred_width = overhead + self.preferred_column_width * self.max_columns
        if available_width is None:
            return preferred_width, self.preferred_column_width
        if available_width < 1 or screen_margin < 0:
            raise DetailContractError("available width and screen margin are invalid")
        width_cap = available_width - (screen_margin * 2)
        if width_cap <= overhead:
            raise DetailContractError("available width cannot contain the configured columns")
        if preferred_width <= width_cap:
            return preferred_width, self.preferred_column_width
        equal_column_width = (width_cap - overhead) // self.max_columns
        if equal_column_width < 1:
            raise DetailContractError("available width produces an empty column")
        return overhead + equal_column_width * self.max_columns, equal_column_width

    def _overhead(self) -> int:
        return (self.outer_padding * 2) + self.column_gap * (self.max_columns - 1)


_PROFILE_COLUMNS = {
    "page": 1,
    "coma": 2,
    "gp": 2,
    "gp_tool": 2,
    "layer_folder": 1,
    "image": 2,
    "image_path": 2,
    "raster": 2,
    "fill": 2,
    "balloon": 3,
    "text": 2,
    "effect": 3,
    "balloon_tail": 2,
    "balloon_shape": 1,
}

DETAIL_LAYOUT_PROFILES: Mapping[str, DetailLayoutProfile] = MappingProxyType(
    {kind: DetailLayoutProfile(max_columns=count) for kind, count in _PROFILE_COLUMNS.items()}
)

DETAIL_KIND_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "border": "coma",
        "gradient": "fill",
        "solid_fill": "fill",
        "pattern_curve": "image_path",
        "tail": "balloon_tail",
    }
)

PRESET_KIND_TO_DETAIL_KIND: Mapping[str, str] = MappingProxyType(
    {
        "border": "coma",
        "text": "text",
        "effect_line": "effect",
        "fill": "fill",
        "gradient": "fill",
        "image_path": "image_path",
        "balloon": "balloon_shape",
        "tail": "balloon_tail",
        "gp_tool": "gp_tool",
    }
)

PRESET_TYPE_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "effect": "effect_line",
        "solid_fill": "fill",
        "pattern_curve": "image_path",
        "balloon_tail": "tail",
        "balloon_shape": "balloon",
    }
)


def normalize_detail_kind(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    normalized = DETAIL_KIND_ALIASES.get(normalized, normalized)
    if normalized not in DETAIL_LAYOUT_PROFILES:
        raise DetailContractError(f"unsupported detail kind: {kind!r}")
    return normalized


def normalize_detail_mode(mode: DetailMode | str) -> DetailMode:
    try:
        return mode if isinstance(mode, DetailMode) else DetailMode(str(mode))
    except ValueError as exc:
        raise DetailContractError(f"unsupported detail mode: {mode!r}") from exc


@dataclass(frozen=True, slots=True)
class DetailTarget:
    """入口で確定し、ダイアログ終了まで再探索しない対象。"""

    kind: str
    stable_id: str
    stack_uid: str | None
    data: Any
    object_ref: Any = None
    params: Any = None
    namespace: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", normalize_detail_kind(self.kind))
        stable_id = str(self.stable_id or "").strip()
        if not stable_id:
            raise DetailContractError("stable_id is required")
        object.__setattr__(self, "stable_id", stable_id)
        stack_uid = str(self.stack_uid or "").strip() or None
        object.__setattr__(self, "stack_uid", stack_uid)
        if self.data is None:
            raise DetailContractError("target data is required")
        if self.kind == "effect" and self.params is None:
            raise DetailContractError("effect detail requires explicit params")
        namespace = str(self.namespace or "").strip().lower() or None
        object.__setattr__(self, "namespace", namespace)


@dataclass(frozen=True, slots=True)
class DetailLayoutSpec:
    """invokeとdrawで共有する、固定外枠と可変表示列の仕様。"""

    kind: str
    mode: DetailMode
    max_columns: int
    column_count: int
    dialog_width: int
    column_width: int
    column_gap: int
    outer_padding: int
    available_width: int | None
    screen_margin: int
    section_columns: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", normalize_detail_kind(self.kind))
        object.__setattr__(self, "mode", normalize_detail_mode(self.mode))
        profile = DETAIL_LAYOUT_PROFILES[self.kind]
        if self.max_columns != profile.max_columns:
            raise DetailContractError("max_columns must match the fixed kind profile")
        if self.column_gap != profile.column_gap or self.outer_padding != profile.outer_padding:
            raise DetailContractError("layout spacing must match the fixed kind profile")
        if not 1 <= self.column_count <= self.max_columns:
            raise DetailContractError("column_count must be within the kind maximum")
        if self.column_width < 1 or self.dialog_width < 1:
            raise DetailContractError("layout widths must be positive")
        if self.column_gap < 0 or self.outer_padding < 0:
            raise DetailContractError("layout spacing cannot be negative")
        expected_width, expected_column_width = profile.fixed_dimensions(
            self.available_width,
            screen_margin=self.screen_margin,
        )
        if self.dialog_width != expected_width or self.column_width != expected_column_width:
            raise DetailContractError("layout widths must come from the fixed kind profile")
        normalized_sections = tuple(tuple(str(name) for name in group) for group in self.section_columns)
        if len(normalized_sections) != self.column_count:
            raise DetailContractError("section_columns must match the visible column count")
        object.__setattr__(self, "section_columns", normalized_sections)

    def with_current_columns(
        self,
        column_count: int,
        section_columns: Sequence[Sequence[str]] | None = None,
    ) -> "DetailLayoutSpec":
        """外枠寸法を変えず、現在表示する列と内容だけを切り替える。"""

        sections = _normalize_section_columns(column_count, section_columns)
        return DetailLayoutSpec(
            kind=self.kind,
            mode=self.mode,
            max_columns=self.max_columns,
            column_count=column_count,
            dialog_width=self.dialog_width,
            column_width=self.column_width,
            column_gap=self.column_gap,
            outer_padding=self.outer_padding,
            available_width=self.available_width,
            screen_margin=self.screen_margin,
            section_columns=sections,
        )

@dataclass(frozen=True, slots=True)
class DetailActionSpec:
    """詳細画面に置く操作のキャンセル境界。"""

    action_id: str
    boundary: DetailActionBoundary
    requires_confirmation: bool = False
    undo_supported: bool = False
    reports_result: bool = True
    closes_parent_before_run: bool = False
    invalidates_target: bool = False

    def __post_init__(self) -> None:
        action_id = str(self.action_id or "").strip()
        if not action_id:
            raise DetailContractError("action_id is required")
        object.__setattr__(self, "action_id", action_id)
        try:
            boundary = (
                self.boundary
                if isinstance(self.boundary, DetailActionBoundary)
                else DetailActionBoundary(str(self.boundary))
            )
        except ValueError as exc:
            raise DetailContractError(f"unsupported action boundary: {self.boundary!r}") from exc
        object.__setattr__(self, "boundary", boundary)
        if boundary is DetailActionBoundary.INDEPENDENT_IMMEDIATE and not self.reports_result:
            raise DetailContractError("independent immediate actions must report their result")
        if self.closes_parent_before_run and boundary is not DetailActionBoundary.INDEPENDENT_IMMEDIATE:
            raise DetailContractError("only independent actions may close the parent first")
        if self.invalidates_target and not self.closes_parent_before_run:
            raise DetailContractError("target-invalidating actions must close the parent first")

    @property
    def parent_cancel_restores(self) -> bool:
        return self.boundary is DetailActionBoundary.TRANSACTIONAL


# 共通詳細画面へ置ける可能性がある操作は、必ずここへ一件ずつ登録する。
# 描画側の分類漏れを許さないため、prefix や正規表現による暗黙分類は行わない。
DETAIL_ACTION_SPECS: Mapping[str, DetailActionSpec] = MappingProxyType(
    {
        # 親ダイアログの開始snapshotへ含める、Undoを持たない子操作。
        "bmanga.detail_tail_add": DetailActionSpec(
            "bmanga.detail_tail_add", DetailActionBoundary.TRANSACTIONAL
        ),
        "bmanga.detail_tail_remove": DetailActionSpec(
            "bmanga.detail_tail_remove", DetailActionBoundary.TRANSACTIONAL
        ),
        "bmanga.detail_tail_preset_apply": DetailActionSpec(
            "bmanga.detail_tail_preset_apply", DetailActionBoundary.TRANSACTIONAL
        ),
        "bmanga.detail_text_ruby_add": DetailActionSpec(
            "bmanga.detail_text_ruby_add", DetailActionBoundary.TRANSACTIONAL
        ),
        "bmanga.detail_text_ruby_clear": DetailActionSpec(
            "bmanga.detail_text_ruby_clear", DetailActionBoundary.TRANSACTIONAL
        ),
        "bmanga.detail_text_linked_balloon_set": DetailActionSpec(
            "bmanga.detail_text_linked_balloon_set",
            DetailActionBoundary.TRANSACTIONAL,
        ),
        "bmanga.detail_preset_apply": DetailActionSpec(
            "bmanga.detail_preset_apply", DetailActionBoundary.TRANSACTIONAL
        ),
        # 線幅グラフの「適用」ボタン。確定先は開始時snapshotへ含まれる
        # 対象自身のパラメータ (in_percent等) のため、親のCancelで通常の
        # プロパティ編集と同じく復元される。
        "bmanga.effect_profile_graph_apply": DetailActionSpec(
            "bmanga.effect_profile_graph_apply", DetailActionBoundary.TRANSACTIONAL
        ),
        # 親キャンセルとは独立して結果を残す明示操作。
        "bmanga.detail_raster_paint_enter": DetailActionSpec(
            "bmanga.detail_raster_paint_enter",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
            closes_parent_before_run=True,
        ),
        "bmanga.detail_raster_save_png": DetailActionSpec(
            "bmanga.detail_raster_save_png",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
        ),
        "bmanga.balloon_regenerate_keep_edit": DetailActionSpec(
            "bmanga.balloon_regenerate_keep_edit",
            DetailActionBoundary.EXCLUDED,
        ),
        "bmanga.balloon_regenerate_discard_edit": DetailActionSpec(
            "bmanga.balloon_regenerate_discard_edit",
            DetailActionBoundary.EXCLUDED,
        ),
        "bmanga.preset_detail_edit": DetailActionSpec(
            "bmanga.preset_detail_edit",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
        ),
        "bmanga.detail_preset_rename": DetailActionSpec(
            "bmanga.detail_preset_rename",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
        ),
        "bmanga.detail_preset_duplicate": DetailActionSpec(
            "bmanga.detail_preset_duplicate",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
        ),
        "bmanga.detail_preset_delete": DetailActionSpec(
            "bmanga.detail_preset_delete",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
            requires_confirmation=True,
        ),
        "bmanga.detail_preset_add": DetailActionSpec(
            "bmanga.detail_preset_add",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
        ),
        "bmanga.detail_preset_move": DetailActionSpec(
            "bmanga.detail_preset_move",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
        ),
        # 旧プリセット管理入口も境界を明示する。共通描画は上の固定対象入口を使う。
        "bmanga.border_preset_add_local": DetailActionSpec(
            "bmanga.border_preset_add_local", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.border_preset_rename": DetailActionSpec(
            "bmanga.border_preset_rename", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.border_preset_duplicate": DetailActionSpec(
            "bmanga.border_preset_duplicate", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.border_preset_delete": DetailActionSpec(
            "bmanga.border_preset_delete",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
            requires_confirmation=True,
        ),
        "bmanga.border_preset_move": DetailActionSpec(
            "bmanga.border_preset_move", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.image_path_preset_add_local": DetailActionSpec(
            "bmanga.image_path_preset_add_local", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.image_path_preset_rename": DetailActionSpec(
            "bmanga.image_path_preset_rename", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.image_path_preset_duplicate": DetailActionSpec(
            "bmanga.image_path_preset_duplicate", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.image_path_preset_delete": DetailActionSpec(
            "bmanga.image_path_preset_delete",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
            requires_confirmation=True,
        ),
        "bmanga.image_path_preset_move": DetailActionSpec(
            "bmanga.image_path_preset_move", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.text_preset_add_local": DetailActionSpec(
            "bmanga.text_preset_add_local", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.text_preset_rename": DetailActionSpec(
            "bmanga.text_preset_rename", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.text_preset_duplicate": DetailActionSpec(
            "bmanga.text_preset_duplicate", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.text_preset_delete": DetailActionSpec(
            "bmanga.text_preset_delete",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
            requires_confirmation=True,
        ),
        "bmanga.text_preset_move": DetailActionSpec(
            "bmanga.text_preset_move", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.balloon_preset_add_local": DetailActionSpec(
            "bmanga.balloon_preset_add_local", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.balloon_preset_rename": DetailActionSpec(
            "bmanga.balloon_preset_rename", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.balloon_preset_duplicate": DetailActionSpec(
            "bmanga.balloon_preset_duplicate", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.balloon_preset_delete": DetailActionSpec(
            "bmanga.balloon_preset_delete",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
            requires_confirmation=True,
        ),
        "bmanga.balloon_preset_move": DetailActionSpec(
            "bmanga.balloon_preset_move", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.effect_line_preset_add_local": DetailActionSpec(
            "bmanga.effect_line_preset_add_local", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.effect_line_preset_rename": DetailActionSpec(
            "bmanga.effect_line_preset_rename", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.effect_line_preset_duplicate": DetailActionSpec(
            "bmanga.effect_line_preset_duplicate", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.effect_line_preset_delete": DetailActionSpec(
            "bmanga.effect_line_preset_delete",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
            requires_confirmation=True,
        ),
        "bmanga.effect_line_preset_move": DetailActionSpec(
            "bmanga.effect_line_preset_move", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.fill_preset_add_local": DetailActionSpec(
            "bmanga.fill_preset_add_local", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.fill_preset_rename": DetailActionSpec(
            "bmanga.fill_preset_rename", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.fill_preset_duplicate": DetailActionSpec(
            "bmanga.fill_preset_duplicate", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.fill_preset_delete": DetailActionSpec(
            "bmanga.fill_preset_delete",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
            requires_confirmation=True,
        ),
        "bmanga.fill_preset_move": DetailActionSpec(
            "bmanga.fill_preset_move", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.gradient_preset_add_local": DetailActionSpec(
            "bmanga.gradient_preset_add_local", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.gradient_preset_rename": DetailActionSpec(
            "bmanga.gradient_preset_rename", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.gradient_preset_duplicate": DetailActionSpec(
            "bmanga.gradient_preset_duplicate", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        "bmanga.gradient_preset_delete": DetailActionSpec(
            "bmanga.gradient_preset_delete",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
            requires_confirmation=True,
        ),
        "bmanga.gradient_preset_move": DetailActionSpec(
            "bmanga.gradient_preset_move", DetailActionBoundary.INDEPENDENT_IMMEDIATE
        ),
        # 共通詳細画面では安全な境界を定義できないため、別入口だけに置く操作。
        "bmanga.effect_line_base_path_edit": DetailActionSpec("bmanga.effect_line_base_path_edit", DetailActionBoundary.EXCLUDED),
        "bmanga.effect_line_generate": DetailActionSpec("bmanga.effect_line_generate", DetailActionBoundary.EXCLUDED),
        "bmanga.raster_layer_resample": DetailActionSpec("bmanga.raster_layer_resample", DetailActionBoundary.EXCLUDED),
        "bmanga.raster_layer_set_bit_depth": DetailActionSpec("bmanga.raster_layer_set_bit_depth", DetailActionBoundary.EXCLUDED),
        "bmanga.coma_to_polygon": DetailActionSpec("bmanga.coma_to_polygon", DetailActionBoundary.EXCLUDED),
        "bmanga.coma_to_rect": DetailActionSpec("bmanga.coma_to_rect", DetailActionBoundary.EXCLUDED),
        "bmanga.coma_merge_selected": DetailActionSpec("bmanga.coma_merge_selected", DetailActionBoundary.EXCLUDED),
        "bmanga.coma_edit_vertices": DetailActionSpec("bmanga.coma_edit_vertices", DetailActionBoundary.EXCLUDED),
        "bmanga.image_layer_add": DetailActionSpec("bmanga.image_layer_add", DetailActionBoundary.EXCLUDED),
        "bmanga.image_layer_remove": DetailActionSpec("bmanga.image_layer_remove", DetailActionBoundary.EXCLUDED),
        "bmanga.raster_layer_add": DetailActionSpec("bmanga.raster_layer_add", DetailActionBoundary.EXCLUDED),
        "bmanga.raster_layer_remove": DetailActionSpec("bmanga.raster_layer_remove", DetailActionBoundary.EXCLUDED),
        "bmanga.balloon_add": DetailActionSpec("bmanga.balloon_add", DetailActionBoundary.EXCLUDED),
        "bmanga.balloon_remove": DetailActionSpec("bmanga.balloon_remove", DetailActionBoundary.EXCLUDED),
        "bmanga.balloon_merge_selected": DetailActionSpec("bmanga.balloon_merge_selected", DetailActionBoundary.EXCLUDED),
        "bmanga.balloon_register_selected_curve": DetailActionSpec("bmanga.balloon_register_selected_curve", DetailActionBoundary.EXCLUDED),
        "bmanga.balloon_move": DetailActionSpec("bmanga.balloon_move", DetailActionBoundary.EXCLUDED),
        "bmanga.text_add": DetailActionSpec("bmanga.text_add", DetailActionBoundary.EXCLUDED),
        "bmanga.text_remove": DetailActionSpec("bmanga.text_remove", DetailActionBoundary.EXCLUDED),
        # UNDO付き旧子操作を共通画面へ戻さないための明示的な拒否項目。
        "bmanga.balloon_tail_add_target": DetailActionSpec(
            "bmanga.balloon_tail_add_target", DetailActionBoundary.EXCLUDED
        ),
        "bmanga.balloon_tail_remove": DetailActionSpec(
            "bmanga.balloon_tail_remove", DetailActionBoundary.EXCLUDED
        ),
        "bmanga.balloon_tail_preset_apply": DetailActionSpec(
            "bmanga.balloon_tail_preset_apply", DetailActionBoundary.EXCLUDED
        ),
        "bmanga.balloon_tail_preset_save": DetailActionSpec(
            "bmanga.balloon_tail_preset_save",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
        ),
        "bmanga.balloon_tail_preset_delete": DetailActionSpec(
            "bmanga.balloon_tail_preset_delete",
            DetailActionBoundary.INDEPENDENT_IMMEDIATE,
            requires_confirmation=True,
        ),
        "bmanga.text_ruby_add_dialog": DetailActionSpec(
            "bmanga.text_ruby_add_dialog", DetailActionBoundary.EXCLUDED
        ),
        "bmanga.text_ruby_clear_inline": DetailActionSpec(
            "bmanga.text_ruby_clear_inline", DetailActionBoundary.EXCLUDED
        ),
    }
)


def get_detail_action_spec(operator_id: str) -> DetailActionSpec:
    """共通詳細画面の操作分類を返し、未分類なら描画前に拒否する。"""

    key = str(operator_id or "").strip()
    # Blenderは実行中のOperatorの ``self.bl_idname`` を
    # ``BMANGA_OT_xxx`` 形式で返すことがある。登録表の
    # ``bmanga.xxx`` と同じIDとして扱い、分類漏れと誤認しない。
    if "." not in key and "_OT_" in key:
        namespace, action = key.split("_OT_", 1)
        key = f"{namespace.lower()}.{action.lower()}"
    try:
        return DETAIL_ACTION_SPECS[key]
    except KeyError as exc:
        raise DetailContractError(f"unclassified detail operator: {operator_id!r}") from exc


@dataclass(frozen=True, slots=True)
class DetailActionRecord:
    spec: DetailActionSpec
    target_identity: "DetailTargetIdentity"
    result: Any = None


@dataclass(frozen=True, slots=True)
class DetailTargetIdentity:
    """独立即時操作へ渡す、変更可能なデータを含まない固定対象情報。"""

    kind: str
    stable_id: str
    stack_uid: str | None

    @classmethod
    def from_target(cls, target: DetailTarget) -> "DetailTargetIdentity":
        return cls(target.kind, target.stable_id, target.stack_uid)


@runtime_checkable
class DetailSnapshotProtocol(Protocol):
    target_identity: DetailTargetIdentity
    fragments: tuple[Any, ...]


TargetLivenessValidator = Callable[[DetailTargetIdentity], bool]
_SESSION_CONSTRUCTION_KEY = object()


class DetailSession:
    """対象と外枠幅を固定し、表示列だけを更新できる編集セッション。"""

    __slots__ = (
        "_token",
        "_target",
        "_mode",
        "_opening_snapshot",
        "_layout",
        "_preset_type",
        "_preset_selection",
        "_preset_baseline",
        "_status",
        "_target_validator",
        "_restore_error",
        "_transactional_actions",
        "_independent_actions",
    )

    def __init__(
        self,
        *,
        token: str,
        target: DetailTarget,
        mode: DetailMode,
        opening_snapshot: DetailSnapshotProtocol,
        layout: DetailLayoutSpec,
        target_validator: TargetLivenessValidator,
        _construction_key: object,
        preset_type: str | None = None,
        preset_selection: str | None = None,
    ) -> None:
        token = str(token or "").strip()
        if not token:
            raise DetailContractError("session token is required")
        if _construction_key is not _SESSION_CONSTRUCTION_KEY:
            raise DetailContractError("DetailSession must be opened through begin_detail_session")
        if target.kind != layout.kind or normalize_detail_mode(mode) is not layout.mode:
            raise DetailContractError("session target, mode, and layout do not match")
        if not isinstance(opening_snapshot, DetailSnapshotProtocol):
            raise DetailContractError("opening_snapshot does not satisfy the detail snapshot protocol")
        if opening_snapshot.target_identity != DetailTargetIdentity.from_target(target):
            raise DetailContractError("opening_snapshot belongs to a different target")
        if not callable(target_validator):
            raise DetailContractError("target_validator must be callable")
        self._token = token
        self._target = target
        self._mode = normalize_detail_mode(mode)
        self._opening_snapshot = opening_snapshot
        self._layout = layout
        self._preset_type = None if preset_type is None else str(preset_type)
        self._preset_selection = preset_selection
        self._preset_baseline = None
        self._status = DetailSessionStatus.OPEN
        self._target_validator = target_validator
        self._restore_error: BaseException | None = None
        self._transactional_actions: list[DetailActionRecord] = []
        self._independent_actions: list[DetailActionRecord] = []

    @property
    def token(self) -> str:
        return self._token

    @property
    def target(self) -> DetailTarget:
        return self._target

    @property
    def mode(self) -> DetailMode:
        return self._mode

    @property
    def opening_snapshot(self) -> DetailSnapshotProtocol | None:
        return self._opening_snapshot

    @property
    def layout(self) -> DetailLayoutSpec:
        return self._layout

    @property
    def preset_selection(self) -> str | None:
        return self._preset_selection

    @property
    def preset_type(self) -> str | None:
        return self._preset_type

    @property
    def preset_baseline(self):
        return self._preset_baseline

    @property
    def status(self) -> DetailSessionStatus:
        return self._status

    @property
    def restore_error(self) -> BaseException | None:
        return self._restore_error

    @property
    def transactional_actions(self) -> tuple[DetailActionRecord, ...]:
        return tuple(self._transactional_actions)

    @property
    def independent_actions(self) -> tuple[DetailActionRecord, ...]:
        return tuple(self._independent_actions)

    def set_current_columns(
        self,
        column_count: int,
        section_columns: Sequence[Sequence[str]] | None = None,
    ) -> DetailLayoutSpec:
        self.require_open()
        self.validate_target()
        self._layout = self._layout.with_current_columns(column_count, section_columns)
        return self._layout

    def set_preset_selection(self, preset_name: str | None) -> None:
        self.require_open()
        self.validate_target()
        self._preset_selection = None if preset_name is None else str(preset_name)

    def set_preset_context(self, preset_type: str | None, preset_name: str | None) -> None:
        self.require_open()
        self.validate_target()
        self._preset_type = None if preset_type is None else str(preset_type)
        self._preset_selection = None if preset_name is None else str(preset_name)

    def set_preset_baseline(self, baseline) -> None:
        """最後に適用・保存したプリセット設定を切替確認の比較基準にする。"""

        self.require_open()
        self.validate_target()
        self._preset_baseline = baseline

    def replace_opening_snapshot(self, snapshot: DetailSnapshotProtocol) -> None:
        """独立即時操作で確定した最小状態だけをキャンセル基準へ反映する。"""

        self.require_open()
        self.validate_target()
        if not isinstance(snapshot, DetailSnapshotProtocol):
            raise DetailContractError(
                "opening_snapshot does not satisfy the detail snapshot protocol"
            )
        if snapshot.target_identity != DetailTargetIdentity.from_target(self.target):
            raise DetailContractError("opening_snapshot belongs to a different target")
        self._opening_snapshot = snapshot

    def record_action(self, spec: DetailActionSpec, result: Any = None) -> DetailActionRecord:
        self.require_open()
        if spec.boundary is DetailActionBoundary.EXCLUDED:
            raise DetailContractError("excluded actions cannot run inside a detail dialog")
        record = DetailActionRecord(spec, DetailTargetIdentity.from_target(self.target), result)
        if spec.boundary is DetailActionBoundary.TRANSACTIONAL:
            self._transactional_actions.append(record)
        else:
            self._independent_actions.append(record)
        return record

    def record_closed_independent_action(
        self,
        spec: DetailActionSpec,
        result: Any = None,
    ) -> DetailActionRecord:
        if self._status not in {DetailSessionStatus.COMMITTED, DetailSessionStatus.CANCELLED}:
            raise DetailSessionClosedError("parent must be committed or cancelled first")
        if spec.boundary is not DetailActionBoundary.INDEPENDENT_IMMEDIATE:
            raise DetailContractError("closed-session actions must be independent")
        record = DetailActionRecord(spec, DetailTargetIdentity.from_target(self.target), result)
        self._independent_actions.append(record)
        return record

    def validate_target(self) -> None:
        identity = DetailTargetIdentity.from_target(self.target)
        try:
            is_alive = bool(self._target_validator(identity))
        except Exception as exc:
            raise DetailTargetNotFoundError(identity.stable_id) from exc
        if not is_alive:
            raise DetailTargetNotFoundError(identity.stable_id)

    def require_open(self) -> None:
        if self._status is not DetailSessionStatus.OPEN:
            raise DetailSessionClosedError(f"detail session is {self._status.value}")

    def mark_committed(self) -> None:
        self._finish(DetailSessionStatus.COMMITTED)

    def mark_cancelled(self) -> None:
        if self._status not in {DetailSessionStatus.OPEN, DetailSessionStatus.RESTORE_FAILED}:
            raise DetailSessionClosedError(f"detail session is {self._status.value}")
        self._status = DetailSessionStatus.CANCELLED
        self._opening_snapshot = None
        self._preset_baseline = None
        self._restore_error = None

    def mark_restore_failed(self, error: BaseException) -> None:
        if self._status not in {DetailSessionStatus.OPEN, DetailSessionStatus.RESTORE_FAILED}:
            raise DetailSessionClosedError(f"detail session is {self._status.value}")
        self._status = DetailSessionStatus.RESTORE_FAILED
        self._restore_error = error

    def require_cancellable(self) -> None:
        if self._status not in {DetailSessionStatus.OPEN, DetailSessionStatus.RESTORE_FAILED}:
            raise DetailSessionClosedError(f"detail session is {self._status.value}")

    def _finish(self, status: DetailSessionStatus) -> None:
        self.require_open()
        self._status = status
        self._opening_snapshot = None
        self._preset_baseline = None
        self._restore_error = None


TargetResolver = Callable[[str], DetailTarget | None]


def resolve_detail_target_from_stack(stack_uid: str, resolver: TargetResolver) -> DetailTarget:
    key = _required_key(stack_uid, "stack_uid")
    target = _resolve_target(key, resolver)
    if target.stack_uid != key:
        raise DetailContractError("stack resolver returned a different target")
    return target


def resolve_detail_target_from_object(stable_id: str, resolver: TargetResolver) -> DetailTarget:
    key = _required_key(stable_id, "stable_id")
    target = _resolve_target(key, resolver)
    if target.stable_id != key:
        raise DetailContractError("object resolver returned a different target")
    return target


def resolve_preset_detail_target(
    preset_type: str,
    preset_name: str,
    data: Any,
    *,
    params: Any = None,
) -> DetailTarget:
    raw_preset_key = str(preset_type or "").strip().lower()
    preset_key = PRESET_TYPE_ALIASES.get(raw_preset_key, raw_preset_key)
    kind = PRESET_KIND_TO_DETAIL_KIND.get(preset_key)
    if kind is None:
        raise DetailContractError(f"unsupported preset type: {preset_type!r}")
    name = _required_key(preset_name, "preset_name")
    return DetailTarget(
        kind=kind,
        stable_id=f"preset:{preset_key}:{name}",
        stack_uid=None,
        data=data,
        object_ref=None,
        params=params,
        namespace=preset_key,
    )


def current_column_count_for_target(target: DetailTarget, mode: DetailMode | str) -> int:
    """明示ヒントを優先し、線種から現在表示する列数を決める。"""

    normalize_detail_mode(mode)
    profile = DETAIL_LAYOUT_PROFILES[target.kind]
    explicit = _explicit_column_hint(target)
    if explicit is not None:
        return _bounded_column_count(explicit, profile.max_columns)
    if target.kind == "effect":
        effect_type = str(_read_value(target.params, "effect_type", "focus") or "focus")
        return min(_EFFECT_COLUMNS.get(effect_type, 1), profile.max_columns)
    if target.kind == "balloon":
        return min(_balloon_column_count(target.data), profile.max_columns)
    # 線種による可変分割を持たない種別は、drawerの
    # primary/secondary配置をそのまま使う。外枠だけ2列幅にして
    # 内容を1列に縦積みすると、右半分が空白になるため。
    return profile.max_columns


def resolve_detail_layout(
    target: DetailTarget,
    mode: DetailMode | str,
    *,
    current_columns: int | None = None,
    section_columns: Sequence[Sequence[str]] | None = None,
    available_width: int | None = None,
    screen_margin: int = DEFAULT_SCREEN_MARGIN,
) -> DetailLayoutSpec:
    normalized_mode = normalize_detail_mode(mode)
    profile = DETAIL_LAYOUT_PROFILES[target.kind]
    visible_columns = (
        current_column_count_for_target(target, normalized_mode)
        if current_columns is None
        else _bounded_column_count(current_columns, profile.max_columns)
    )
    dialog_width, column_width = profile.fixed_dimensions(
        available_width,
        screen_margin=screen_margin,
    )
    return DetailLayoutSpec(
        kind=target.kind,
        mode=normalized_mode,
        max_columns=profile.max_columns,
        column_count=visible_columns,
        dialog_width=dialog_width,
        column_width=column_width,
        column_gap=profile.column_gap,
        outer_padding=profile.outer_padding,
        available_width=available_width,
        screen_margin=screen_margin,
        section_columns=_normalize_section_columns(visible_columns, section_columns),
    )


def _new_detail_session(
    target: DetailTarget,
    mode: DetailMode | str,
    opening_snapshot: DetailSnapshotProtocol,
    *,
    target_validator: TargetLivenessValidator,
    token: str | None = None,
    preset_type: str | None = None,
    preset_selection: str | None = None,
    current_columns: int | None = None,
    section_columns: Sequence[Sequence[str]] | None = None,
    available_width: int | None = None,
    screen_margin: int = DEFAULT_SCREEN_MARGIN,
) -> DetailSession:
    normalized_mode = normalize_detail_mode(mode)
    layout = resolve_detail_layout(
        target,
        normalized_mode,
        current_columns=current_columns,
        section_columns=section_columns,
        available_width=available_width,
        screen_margin=screen_margin,
    )
    session = DetailSession(
        token=token or uuid4().hex,
        target=target,
        mode=normalized_mode,
        opening_snapshot=opening_snapshot,
        layout=layout,
        target_validator=target_validator,
        _construction_key=_SESSION_CONSTRUCTION_KEY,
        preset_type=preset_type,
        preset_selection=preset_selection,
    )
    session.validate_target()
    return session


_EFFECT_COLUMNS = {
    "speed": 2,
    "beta_flash": 2,
    "focus": 3,
    "uni_flash": 3,
    "white_outline": 3,
}

_BALLOON_THREE_COLUMN_STYLES = {
    "focus",
    "flash",
    "uni_flash",
    "beta_flash",
    "white_outline",
}


def _balloon_column_count(data: Any) -> int:
    line_style = str(_read_value(data, "line_style", "") or "").strip().lower()
    shape = str(_read_value(data, "shape", "") or "").strip().lower()
    if line_style in _BALLOON_THREE_COLUMN_STYLES or shape in _BALLOON_THREE_COLUMN_STYLES:
        return 3
    return 1


def _explicit_column_hint(target: DetailTarget) -> int | None:
    for source in (target.params, target.data):
        for name in ("detail_column_count", "visible_detail_columns"):
            value = _read_value(source, name, None)
            if value is not None and not isinstance(value, bool):
                return int(value)
    return None


def _read_value(source: Any, name: str, default: Any) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _bounded_column_count(value: int, maximum: int) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise DetailContractError(f"invalid column count: {value!r}") from exc
    if not 1 <= count <= maximum:
        raise DetailContractError(f"column count must be between 1 and {maximum}")
    return count


def _normalize_section_columns(
    column_count: int,
    section_columns: Sequence[Sequence[str]] | None,
) -> tuple[tuple[str, ...], ...]:
    if section_columns is None:
        return tuple(() for _ in range(column_count))
    normalized = tuple(tuple(str(name) for name in group) for group in section_columns)
    if len(normalized) != column_count:
        raise DetailContractError("section_columns must match the visible column count")
    return normalized


def _required_key(value: str, label: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise DetailContractError(f"{label} is required")
    return key


def _resolve_target(key: str, resolver: TargetResolver) -> DetailTarget:
    target = resolver(key)
    if target is None:
        raise DetailTargetNotFoundError(key)
    if not isinstance(target, DetailTarget):
        raise DetailContractError("resolver must return DetailTarget or None")
    return target


__all__ = [
    "DEFAULT_COLUMN_GAP", "DEFAULT_COLUMN_WIDTH", "DEFAULT_OUTER_PADDING",
    "DEFAULT_SCREEN_MARGIN", "DETAIL_KIND_ALIASES", "DETAIL_LAYOUT_PROFILES",
    "DETAIL_ACTION_SPECS", "PRESET_KIND_TO_DETAIL_KIND", "PRESET_TYPE_ALIASES",
    "DetailActionBoundary", "DetailActionRecord", "DetailActionSpec",
    "DetailContractError", "DetailLayoutProfile", "DetailLayoutSpec", "DetailMode",
    "DetailSession", "DetailSessionClosedError", "DetailSessionStatus",
    "DetailSnapshotProtocol", "DetailTarget", "DetailTargetIdentity",
    "DetailTargetNotFoundError", "TargetLivenessValidator",
    "current_column_count_for_target", "get_detail_action_spec",
    "normalize_detail_kind", "normalize_detail_mode", "resolve_detail_layout",
    "resolve_detail_target_from_object", "resolve_detail_target_from_stack",
    "resolve_preset_detail_target",
]
