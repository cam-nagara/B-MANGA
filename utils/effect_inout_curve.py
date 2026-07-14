"""効果線の入り抜きカーブ共有ヘルパー."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import time

try:
    import bpy
except ModuleNotFoundError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]

DEFAULT_CURVE_TEXT = "0.0000,0.0000;1.0000,1.0000"
DEFAULT_POINTS: tuple[tuple[float, float], ...] = ((0.0, 0.0), (1.0, 1.0))
MATERIAL_NAME = "BManga_EffectLine_InOutCurve"
IN_NODE_NAME = "BManga_EffectLine_InCurve"
OUT_NODE_NAME = "BManga_EffectLine_OutCurve"
PROFILE_NODE_NAME = "BManga_EffectLine_ProfileCurve"
IN_SOURCE_PROP = "bmanga_effect_in_curve_source"
OUT_SOURCE_PROP = "bmanga_effect_out_curve_source"
PROFILE_SOURCE_PROP = "bmanga_effect_profile_curve_source"
WHITE_PROFILE_NODE_NAME = "BManga_EffectLine_WhiteProfileCurve"
BLACK_PROFILE_NODE_NAME = "BManga_EffectLine_BlackProfileCurve"
WHITE_PROFILE_SOURCE_PROP = "bmanga_effect_white_profile_curve_source"
BLACK_PROFILE_SOURCE_PROP = "bmanga_effect_black_profile_curve_source"
WHITE_PROFILE_FIELDS = {
    "in_percent": "white_outline_white_in_percent",
    "out_percent": "white_outline_white_out_percent",
    "range_mode": "white_outline_white_inout_range_mode",
    "in_range_percent": "white_outline_white_in_range_percent",
    "out_range_percent": "white_outline_white_out_range_percent",
    "in_range_mm": "white_outline_white_in_range_mm",
    "out_range_mm": "white_outline_white_out_range_mm",
    "in_curve": "white_outline_white_in_easing_curve",
    "out_curve": "white_outline_white_out_easing_curve",
}
BLACK_PROFILE_FIELDS = {
    "in_percent": "white_outline_black_in_percent",
    "out_percent": "white_outline_black_out_percent",
    "range_mode": "white_outline_black_inout_range_mode",
    "in_range_percent": "white_outline_black_in_range_percent",
    "out_range_percent": "white_outline_black_out_range_percent",
    "in_range_mm": "white_outline_black_in_range_mm",
    "out_range_mm": "white_outline_black_out_range_mm",
    "in_curve": "white_outline_black_in_easing_curve",
    "out_curve": "white_outline_black_out_easing_curve",
}
_EPSILON = 1.0e-6
_DEFAULT_PROFILE_FIELDS = {
    "in_percent": "in_percent",
    "out_percent": "out_percent",
    "in_start": "in_start_percent",
    "out_start": "out_start_percent",
    "in_curve": "in_easing_curve",
    "out_curve": "out_easing_curve",
}
_LIVE_PROFILE_REQUESTS: dict[str, tuple[object, dict[str, str], str, float]] = {}
_LIVE_PROFILE_RUNNING = False
_LIVE_PROFILE_TIMEOUT_SEC = 300.0


def parse_points(value: object) -> tuple[tuple[float, float], ...]:
    raw: list[tuple[float, float]] = []
    if isinstance(value, str):
        for part in value.split(";"):
            bits = [b.strip() for b in part.split(",")]
            if len(bits) != 2:
                continue
            try:
                raw.append((float(bits[0]), float(bits[1])))
            except ValueError:
                continue
    elif isinstance(value, Iterable):
        for item in value:
            try:
                x, y = item
                raw.append((float(x), float(y)))
            except Exception:  # noqa: BLE001
                continue
    return normalize_points(raw)


def normalize_points(points: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    cleaned = [(_clamp01(x), _clamp01(y)) for x, y in points]
    if len(cleaned) < 2:
        return DEFAULT_POINTS
    cleaned.sort(key=lambda p: p[0])
    if cleaned[0][0] > 1.0e-4:
        cleaned.insert(0, (0.0, cleaned[0][1]))
    else:
        cleaned[0] = (0.0, cleaned[0][1])
    if cleaned[-1][0] < 1.0 - 1.0e-4:
        cleaned.append((1.0, cleaned[-1][1]))
    else:
        cleaned[-1] = (1.0, cleaned[-1][1])
    deduped: list[tuple[float, float]] = []
    for x, y in cleaned:
        if deduped and abs(deduped[-1][0] - x) < 1.0e-4:
            deduped[-1] = (x, y)
        else:
            deduped.append((x, y))
    if len(deduped) > 16:
        step = (len(deduped) - 1) / 15.0
        deduped = [deduped[round(i * step)] for i in range(16)]
        deduped[0] = (0.0, deduped[0][1])
        deduped[-1] = (1.0, deduped[-1][1])
    return tuple(deduped) if len(deduped) >= 2 else DEFAULT_POINTS


def points_to_text(points: Sequence[tuple[float, float]]) -> str:
    return ";".join(f"{x:.4f},{y:.4f}" for x, y in normalize_points(points))


def evaluate(points: Sequence[tuple[float, float]], t: float) -> float:
    pts = normalize_points(points)
    t = _clamp01(t)
    if t <= pts[0][0]:
        return _clamp01(pts[0][1])
    for index in range(1, len(pts)):
        x0, y0 = pts[index - 1]
        x1, y1 = pts[index]
        if t <= x1:
            span = x1 - x0
            u = 0.0 if span <= 1.0e-9 else (t - x0) / span
            return _clamp01(y0 + (y1 - y0) * u)
    return _clamp01(pts[-1][1])


def profile_points_from_params(
    params,
    fields: Mapping[str, str] | None = None,
) -> tuple[tuple[float, float], ...]:
    """始点から終点までの線幅プロファイルをグラフ点列へ変換する."""
    attrs = _profile_fields(fields)
    in_frac = _percent_attr(params, attrs["in_percent"], 100.0)
    out_frac = _percent_attr(params, attrs["out_percent"], 0.0)
    d_in, d_out = _start_factors(params, attrs)
    in_curve = parse_points(getattr(params, attrs["in_curve"], DEFAULT_CURVE_TEXT))
    out_curve = parse_points(getattr(params, attrs["out_curve"], DEFAULT_CURVE_TEXT))

    points: list[tuple[float, float]] = [(0.0, _profile_value(in_frac, out_frac, d_in, d_out, in_curve, out_curve, 0.0))]
    if d_in > _EPSILON:
        for x, _y in in_curve:
            points.append((d_in * _clamp01(x), _profile_value(in_frac, out_frac, d_in, d_out, in_curve, out_curve, d_in * _clamp01(x))))
    else:
        points.append((0.0, _profile_value(in_frac, out_frac, d_in, d_out, in_curve, out_curve, 0.0)))
    plateau_start = d_in
    plateau_end = 1.0 - d_out
    if plateau_start <= plateau_end:
        points.append((plateau_start, _profile_value(in_frac, out_frac, d_in, d_out, in_curve, out_curve, plateau_start)))
        points.append((plateau_end, _profile_value(in_frac, out_frac, d_in, d_out, in_curve, out_curve, plateau_end)))
    if d_out > _EPSILON:
        for x, _y in out_curve:
            profile_x = 1.0 - (d_out * _clamp01(x))
            points.append((profile_x, _profile_value(in_frac, out_frac, d_in, d_out, in_curve, out_curve, profile_x)))
    points.append((1.0, _profile_value(in_frac, out_frac, d_in, d_out, in_curve, out_curve, 1.0)))
    return _limit_points(points)


def profile_points_to_params(
    params,
    points: Sequence[tuple[float, float]],
    fields: Mapping[str, str] | None = None,
) -> bool:
    """線幅グラフ点列から入り抜き数値と左右カーブを更新する."""
    if params is None:
        return False
    attrs = _profile_fields(fields)
    profile = normalize_points(points)
    if len(profile) < 2:
        return False
    in_frac = _clamp01(profile[0][1])
    out_frac = _clamp01(profile[-1][1])
    in_start, out_start = _profile_start_factors(profile)
    changed = False
    changed |= _set_percent_attr(params, attrs["in_percent"], in_frac * 100.0)
    changed |= _set_percent_attr(params, attrs["out_percent"], out_frac * 100.0)
    changed |= _set_range_attr(params, attrs, "in", in_start)
    changed |= _set_range_attr(params, attrs, "out", out_start)
    changed |= _set_text_attr(params, attrs["in_curve"], _in_curve_from_profile(profile, in_frac, in_start))
    changed |= _set_text_attr(params, attrs["out_curve"], _out_curve_from_profile(profile, out_frac, out_start))
    return changed


def ensure_ui_nodes(params):
    if bpy is None:
        return None, None
    mat = bpy.data.materials.get(MATERIAL_NAME) or bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    if nt is None:
        return None, None
    in_node = _ensure_node(nt, IN_NODE_NAME, "入りカーブ", getattr(params, "in_easing_curve", DEFAULT_CURVE_TEXT), mat, IN_SOURCE_PROP)
    out_node = _ensure_node(nt, OUT_NODE_NAME, "抜きカーブ", getattr(params, "out_easing_curve", DEFAULT_CURVE_TEXT), mat, OUT_SOURCE_PROP)
    return in_node, out_node


def ensure_profile_node(
    params,
    *,
    fields: Mapping[str, str] | None = None,
    node_name: str = PROFILE_NODE_NAME,
    source_prop: str = PROFILE_SOURCE_PROP,
    label: str = "線幅グラフ",
):
    if bpy is None:
        return None
    mat = bpy.data.materials.get(MATERIAL_NAME) or bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    if nt is None:
        return None
    display_points = flip_horizontal(profile_points_from_params(params, fields))
    return _ensure_node(nt, node_name, label, display_points, mat, source_prop)


def restore_ui_nodes_from_params(params) -> bool:
    """キャンセル後の保存値を全編集グラフへ強制的に戻す。"""

    if bpy is None or params is None:
        return False
    mat = bpy.data.materials.get(MATERIAL_NAME) or bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    if nt is None:
        return False
    specs = [
        (IN_NODE_NAME, "入りカーブ", getattr(params, "in_easing_curve", DEFAULT_CURVE_TEXT), IN_SOURCE_PROP),
        (OUT_NODE_NAME, "抜きカーブ", getattr(params, "out_easing_curve", DEFAULT_CURVE_TEXT), OUT_SOURCE_PROP),
        (PROFILE_NODE_NAME, "線幅グラフ", flip_horizontal(profile_points_from_params(params)), PROFILE_SOURCE_PROP),
    ]
    for fields, node_name, source_prop, label in (
        (WHITE_PROFILE_FIELDS, WHITE_PROFILE_NODE_NAME, WHITE_PROFILE_SOURCE_PROP, "白線幅グラフ"),
        (BLACK_PROFILE_FIELDS, BLACK_PROFILE_NODE_NAME, BLACK_PROFILE_SOURCE_PROP, "黒線幅グラフ"),
    ):
        if all(hasattr(params, attr) for attr in fields.values()):
            specs.append(
                (node_name, label, flip_horizontal(profile_points_from_params(params, fields)), source_prop)
            )
    for node_name, label, stored_points, source_prop in specs:
        node = _ensure_node(nt, node_name, label, stored_points, mat, source_prop)
        normalized = parse_points(stored_points)
        _apply_points_to_node(node, normalized)
        mat[source_prop] = points_to_text(normalized)
    return True


def get_ui_nodes():
    if bpy is None:
        return None, None
    mat = bpy.data.materials.get(MATERIAL_NAME)
    nt = getattr(mat, "node_tree", None) if mat is not None else None
    if nt is None:
        return None, None
    return _get_curve_node(nt, IN_NODE_NAME), _get_curve_node(nt, OUT_NODE_NAME)


def get_profile_node(node_name: str = PROFILE_NODE_NAME):
    if bpy is None:
        return None
    mat = bpy.data.materials.get(MATERIAL_NAME)
    nt = getattr(mat, "node_tree", None) if mat is not None else None
    if nt is None:
        return None
    return _get_curve_node(nt, node_name)


def sync_profile_node_bidirectional(
    params,
    *,
    fields: Mapping[str, str] | None = None,
    node_name: str = PROFILE_NODE_NAME,
    source_prop: str = PROFILE_SOURCE_PROP,
    label: str = "線幅グラフ",
) -> bool:
    """線幅グラフと数値を現在の差分方向に合わせて同期する."""
    changed = sync_profile_node_to_params(
        params, fields=fields, node_name=node_name, source_prop=source_prop
    )
    ensure_profile_node(
        params, fields=fields, node_name=node_name, source_prop=source_prop, label=label
    )
    return changed


def sync_all_profile_nodes_to_params(params) -> bool:
    """全体・白線・黒線の表示中グラフを対応プロパティへ確定する。"""
    changed = sync_profile_node_to_params(params)
    for fields, node_name, source_prop in (
        (WHITE_PROFILE_FIELDS, WHITE_PROFILE_NODE_NAME, WHITE_PROFILE_SOURCE_PROP),
        (BLACK_PROFILE_FIELDS, BLACK_PROFILE_NODE_NAME, BLACK_PROFILE_SOURCE_PROP),
    ):
        if all(hasattr(params, attr) for attr in fields.values()):
            changed |= sync_profile_node_to_params(
                params,
                fields=fields,
                node_name=node_name,
                source_prop=source_prop,
            )
    return changed


def sync_active_profile_nodes_to_params(params) -> bool:
    """現在の線種で画面に表示されるグラフだけを確定する。"""
    effect_type = str(getattr(params, "effect_type", "") or "")
    line_style = str(getattr(params, "line_style", "") or "")
    is_white_outline = effect_type == "white_outline" or line_style == "white_outline"
    changed = False
    if not is_white_outline or hasattr(params, "line_style"):
        changed |= sync_profile_node_to_params(params)
    if is_white_outline:
        for fields, node_name, source_prop in (
            (WHITE_PROFILE_FIELDS, WHITE_PROFILE_NODE_NAME, WHITE_PROFILE_SOURCE_PROP),
            (BLACK_PROFILE_FIELDS, BLACK_PROFILE_NODE_NAME, BLACK_PROFILE_SOURCE_PROP),
        ):
            if all(hasattr(params, attr) for attr in fields.values()):
                changed |= sync_profile_node_to_params(
                    params,
                    fields=fields,
                    node_name=node_name,
                    source_prop=source_prop,
                )
    return changed


def request_live_profile_sync(
    params,
    *,
    fields: Mapping[str, str] | None = None,
    node_name: str = PROFILE_NODE_NAME,
    source_prop: str = PROFILE_SOURCE_PROP,
) -> None:
    """CurveMapping の操作をプロパティへ反映する短周期同期を開始する."""
    if bpy is None or params is None:
        return
    global _LIVE_PROFILE_RUNNING
    _LIVE_PROFILE_REQUESTS[node_name] = (
        params,
        _profile_fields(fields),
        source_prop,
        time.monotonic(),
    )
    if _LIVE_PROFILE_RUNNING:
        return
    _LIVE_PROFILE_RUNNING = True
    try:
        bpy.app.timers.register(_live_profile_sync_tick, first_interval=0.15)
    except Exception:  # noqa: BLE001
        _LIVE_PROFILE_RUNNING = False


def release_live_profile_sync(params) -> None:
    """指定した詳細設定対象の短周期同期を終了する。"""

    if params is None:
        return
    for node_name, request in tuple(_LIVE_PROFILE_REQUESTS.items()):
        if request[0] is params:
            _LIVE_PROFILE_REQUESTS.pop(node_name, None)
    if _LIVE_PROFILE_REQUESTS:
        return
    global _LIVE_PROFILE_RUNNING
    _LIVE_PROFILE_RUNNING = False
    if bpy is None:
        return
    try:
        if bpy.app.timers.is_registered(_live_profile_sync_tick):
            bpy.app.timers.unregister(_live_profile_sync_tick)
    except Exception:  # noqa: BLE001
        pass


def sync_ui_nodes_to_params(params) -> bool:
    if bpy is None or params is None:
        return False
    mat = bpy.data.materials.get(MATERIAL_NAME)
    nt = getattr(mat, "node_tree", None) if mat is not None else None
    if nt is None:
        return False
    changed = False
    for node_name, attr, prop_name in (
        (IN_NODE_NAME, "in_easing_curve", IN_SOURCE_PROP),
        (OUT_NODE_NAME, "out_easing_curve", OUT_SOURCE_PROP),
    ):
        node = nt.nodes.get(node_name)
        if node is None or node.bl_idname != "ShaderNodeFloatCurve":
            continue
        text = points_to_text(read_node_points(node))
        if str(getattr(params, attr, "") or "") != text:
            setattr(params, attr, text)
            changed = True
        mat[prop_name] = text
    return changed


def _live_profile_sync_tick():
    global _LIVE_PROFILE_RUNNING
    if bpy is None:
        _LIVE_PROFILE_RUNNING = False
        return None
    now = time.monotonic()
    expired = [
        name for name, request in _LIVE_PROFILE_REQUESTS.items()
        if now - request[3] > _LIVE_PROFILE_TIMEOUT_SEC
    ]
    for name in expired:
        _LIVE_PROFILE_REQUESTS.pop(name, None)
    if not _LIVE_PROFILE_REQUESTS:
        _LIVE_PROFILE_RUNNING = False
        return None
    changed = False
    try:
        for node_name, request in tuple(_LIVE_PROFILE_REQUESTS.items()):
            params, fields, source_prop, _last_request = request
            existed = get_profile_node(node_name) is not None
            changed |= sync_profile_node_bidirectional(
                params,
                fields=fields,
                node_name=node_name,
                source_prop=source_prop,
            )
            if not existed and get_profile_node(node_name) is not None:
                # draw 中に作れなかったノードをここで新規作成できた。
                # グラフを表示するため再描画を要求する。
                changed = True
        if changed:
            screen = getattr(bpy.context, "screen", None)
            for area in getattr(screen, "areas", ()) or ():
                if area.type in {"VIEW_3D", "PROPERTIES", "OUTLINER"}:
                    area.tag_redraw()
    except ReferenceError:
        # 削除済み RNA だけを次回の描画要求で置き換えられるよう全同期先を破棄する。
        _LIVE_PROFILE_REQUESTS.clear()
    except Exception:  # noqa: BLE001
        pass
    return 0.15


def sync_profile_node_to_params(
    params,
    *,
    fields: Mapping[str, str] | None = None,
    node_name: str = PROFILE_NODE_NAME,
    source_prop: str = PROFILE_SOURCE_PROP,
) -> bool:
    if bpy is None or params is None:
        return False
    mat = bpy.data.materials.get(MATERIAL_NAME)
    nt = getattr(mat, "node_tree", None) if mat is not None else None
    if nt is None:
        return False
    node = nt.nodes.get(node_name)
    if node is None or node.bl_idname != "ShaderNodeFloatCurve":
        return False
    stored_points = flip_horizontal(profile_points_from_params(params, fields))
    stored_text = points_to_text(stored_points)
    last_source = str(mat.get(source_prop, "") or "")
    if last_source != stored_text:
        return False
    node_points = read_node_points(node)
    node_text = points_to_text(node_points)
    if node_text == last_source:
        return False
    changed = profile_points_to_params(params, flip_horizontal(node_points), fields)
    mat[source_prop] = points_to_text(
        flip_horizontal(profile_points_from_params(params, fields))
    )
    return changed


def flip_horizontal(
    points: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    """外端→内端の内部点列と、内端→外端の画面点列を相互変換する。"""
    return normalize_points([(1.0 - x, y) for x, y in points])


def _get_curve_node(nt, node_name: str):
    node = nt.nodes.get(node_name)
    if node is None or node.bl_idname != "ShaderNodeFloatCurve":
        return None
    return node


def read_node_points(node) -> tuple[tuple[float, float], ...]:
    try:
        curve = node.mapping.curves[0]
        return normalize_points([(float(point.location.x), float(point.location.y)) for point in curve.points])
    except Exception:  # noqa: BLE001
        return DEFAULT_POINTS


def _ensure_node(nt, node_name: str, label: str, stored_points: object, mat, source_prop: str):
    node = nt.nodes.get(node_name)
    if node is not None and node.bl_idname != "ShaderNodeFloatCurve":
        nt.nodes.remove(node)
        node = None
    if node is None:
        node = nt.nodes.new("ShaderNodeFloatCurve")
        node.name = node_name
    node.label = label
    stored_text = points_to_text(parse_points(stored_points))
    last_source = str(mat.get(source_prop, "") or "")
    points = read_node_points(node) if last_source == stored_text else parse_points(stored_text)
    _apply_points_to_node(node, points)
    mat[source_prop] = stored_text
    return node


def _apply_points_to_node(node, points: Sequence[tuple[float, float]]) -> None:
    normalized = normalize_points(points)
    try:
        mapping = node.mapping
        mapping.initialize()
        curve = mapping.curves[0]
        while len(curve.points) > 2:
            curve.points.remove(curve.points[-2])
        curve.points[0].location = normalized[0]
        curve.points[-1].location = normalized[-1]
        for x, y in normalized[1:-1]:
            curve.points.new(x, y)
        for point in curve.points:
            point.handle_type = "AUTO"
        mapping.update()
    except Exception:  # noqa: BLE001
        pass


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _profile_fields(fields: Mapping[str, str] | None) -> dict[str, str]:
    attrs = dict(_DEFAULT_PROFILE_FIELDS)
    if fields:
        attrs.update({str(key): str(value) for key, value in fields.items() if value})
    return attrs


def _percent_attr(params, attr: str, default: float) -> float:
    try:
        return _clamp01(float(getattr(params, attr, default)) / 100.0)
    except Exception:  # noqa: BLE001
        return _clamp01(float(default) / 100.0)


def _range_factor(params, fields: Mapping[str, str], side: str) -> float:
    range_attr = fields.get(f"{side}_range_percent")
    if range_attr:
        return _percent_attr(params, range_attr, 0.0)
    return _percent_attr(params, fields[f"{side}_start"], 0.0)


def _set_range_attr(
    params,
    fields: Mapping[str, str],
    side: str,
    factor: float,
) -> bool:
    mode_attr = fields.get("range_mode")
    changed = False
    if mode_attr and hasattr(params, mode_attr):
        if str(getattr(params, mode_attr, "percent") or "percent") != "percent":
            setattr(params, mode_attr, "percent")
            changed = True
    if fields.get(f"{side}_range_percent"):
        return _set_percent_attr(
            params, fields[f"{side}_range_percent"], factor * 100.0
        ) or changed
    return _set_percent_attr(
        params, fields[f"{side}_start"], factor * 100.0
    ) or changed


def _start_factors(params, fields: Mapping[str, str]) -> tuple[float, float]:
    d_in = _range_factor(params, fields, "in")
    d_out = _range_factor(params, fields, "out")
    has_explicit_ranges = bool(
        fields.get("in_range_percent") or fields.get("in_range_mm")
    )
    if not has_explicit_ranges and d_in + d_out > 1.0:
        excess = d_in + d_out - 1.0
        if d_in >= d_out:
            d_in = max(0.0, d_in - excess)
        else:
            d_out = max(0.0, d_out - excess)
    return d_in, d_out


def _profile_value(
    in_frac: float,
    out_frac: float,
    d_in: float,
    d_out: float,
    in_curve: Sequence[tuple[float, float]],
    out_curve: Sequence[tuple[float, float]],
    t: float,
) -> float:
    t = _clamp01(t)
    if d_in <= _EPSILON:
        vi = 1.0
    else:
        vi = in_frac + (1.0 - in_frac) * evaluate(in_curve, t / d_in)
    if d_out <= _EPSILON:
        vo = 1.0
    else:
        vo = out_frac + (1.0 - out_frac) * evaluate(out_curve, (1.0 - t) / d_out)
    return _clamp01(min(vi, vo))


def _profile_start_factors(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    profile = normalize_points(points)
    full_points = [p for p in profile if p[1] >= 0.995]
    if full_points:
        in_start = full_points[0][0]
        out_start = 1.0 - full_points[-1][0]
    elif len(profile) >= 4:
        in_start = profile[1][0]
        out_start = 1.0 - profile[-2][0]
    elif len(profile) >= 3:
        peak_index = max(range(len(profile)), key=lambda i: profile[i][1])
        in_start = profile[peak_index][0]
        out_start = 1.0 - profile[peak_index][0]
    else:
        in_start = 0.0
        out_start = 0.0
    in_start = _clamp01(in_start)
    out_start = _clamp01(out_start)
    if in_start + out_start > 1.0:
        scale = 1.0 / max(in_start + out_start, _EPSILON)
        in_start *= scale
        out_start *= scale
    return in_start, out_start


def _in_curve_from_profile(points: Sequence[tuple[float, float]], in_frac: float, in_start: float) -> str:
    if in_start <= _EPSILON or 1.0 - in_frac <= _EPSILON:
        return DEFAULT_CURVE_TEXT
    curve_points = []
    for x, y in normalize_points(points):
        if x <= in_start + 1.0e-4:
            curve_points.append((_clamp01(x / in_start), _clamp01((y - in_frac) / (1.0 - in_frac))))
    curve_points.append((0.0, 0.0))
    curve_points.append((1.0, 1.0))
    return points_to_text(curve_points)


def _out_curve_from_profile(points: Sequence[tuple[float, float]], out_frac: float, out_start: float) -> str:
    if out_start <= _EPSILON or 1.0 - out_frac <= _EPSILON:
        return DEFAULT_CURVE_TEXT
    curve_points = []
    out_begin = 1.0 - out_start
    for x, y in normalize_points(points):
        if x >= out_begin - 1.0e-4:
            curve_points.append((_clamp01((1.0 - x) / out_start), _clamp01((y - out_frac) / (1.0 - out_frac))))
    curve_points.append((0.0, 0.0))
    curve_points.append((1.0, 1.0))
    return points_to_text(curve_points)


def _limit_points(points: Sequence[tuple[float, float]], limit: int = 16) -> tuple[tuple[float, float], ...]:
    normalized = normalize_points(points)
    if len(normalized) <= limit:
        return normalized
    return normalize_points([
        (i / float(limit - 1), evaluate(normalized, i / float(limit - 1)))
        for i in range(limit)
    ])


def _set_percent_attr(params, attr: str, value: float) -> bool:
    if not hasattr(params, attr):
        return False
    value = max(0.0, min(100.0, float(value)))
    try:
        current = float(getattr(params, attr))
    except Exception:  # noqa: BLE001
        current = value + 1.0
    if abs(current - value) <= 1.0e-4:
        return False
    setattr(params, attr, value)
    return True


def _set_text_attr(params, attr: str, value: str) -> bool:
    if not hasattr(params, attr):
        return False
    current = str(getattr(params, attr, "") or "")
    if current == value:
        return False
    setattr(params, attr, value)
    return True
