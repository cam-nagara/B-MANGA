"""詳細設定の実データ向けスナップショット／復元アダプター。

PropertyGroupはRNAを再帰走査し、管理Object、Grease Pencil内容、効果線の
保存メタデータと表示生成を別アダプターで補う。復元順は通常値から派生実体へ
なるよう、レジストリの逆順復元契約に合わせて登録する。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

try:
    import bpy
except ModuleNotFoundError:  # 純Pythonテストで汎用RNA処理を検証可能にする
    bpy = None  # type: ignore[assignment]

from .detail_dialog import DetailTarget
from .detail_dialog_state import DEFAULT_DETAIL_STATE_REGISTRY, DetailStateRegistry


class RNAStateRestoreError(RuntimeError):
    """復元できなかったRNA項目をまとめて示す。"""

    def __init__(self, failures: list[tuple[str, BaseException]]) -> None:
        self.failures = tuple(failures)
        super().__init__(
            ", ".join(
                f"{name}: {type(error).__name__}: {error}" for name, error in failures
            )
        )


@dataclass(frozen=True, slots=True)
class RNAState:
    fields: tuple[tuple[str, str, Any], ...]
    custom_properties: tuple[tuple[str, Any], ...]
    preserved_custom_properties: tuple[str, ...] = ()


_PAGE_ROOT_EXCLUSIONS = frozenset({"original_pages", "comas", "balloons", "texts"})
_RASTER_INDEPENDENT_CUSTOM_PROPERTIES = frozenset({"bmanga_raster_dirty"})
_OBJECT_ATTRS = (
    "name",
    "hide_viewport",
    "hide_render",
    "hide_select",
    "location",
    "rotation_mode",
    "rotation_euler",
    "rotation_quaternion",
    "scale",
)


def snapshot_rna_state(
    subject,
    *,
    max_depth: int = 6,
    excluded: frozenset[str] | set[str] = frozenset(),
    preserved_custom: frozenset[str] | set[str] = frozenset(),
) -> RNAState | None:
    """書込み可能なRNA値、PropertyGroup、編集可能Collectionを取得する。"""

    if subject is None or max_depth < 0:
        return None
    properties = _rna_properties(subject)
    if properties is None:
        return None
    fields: list[tuple[str, str, Any]] = []
    for prop in properties:
        captured = _capture_rna_property(subject, prop, max_depth, excluded)
        if captured is not None:
            fields.append(captured)
    preserved = tuple(sorted(str(name) for name in preserved_custom))
    custom = tuple(
        sorted(
            (name, value)
            for name, value in _capture_custom_properties(subject).items()
            if name not in preserved_custom
        )
    )
    return RNAState(tuple(fields), custom, preserved)


def _capture_rna_property(subject, prop, depth: int, excluded) -> tuple | None:
    name = str(getattr(prop, "identifier", "") or "")
    if not name or name in excluded or name in {"rna_type", "id_data"}:
        return None
    if bool(getattr(prop, "is_readonly", False)) or _subject_property_readonly(subject, name):
        return None
    try:
        value = getattr(subject, name)
    except (AttributeError, ReferenceError):
        return None
    prop_type = str(getattr(prop, "type", "") or "")
    if prop_type == "COLLECTION":
        return _capture_collection(name, value, depth)
    if prop_type == "POINTER":
        return _capture_pointer(name, value, depth)
    return name, "value", _clone_value(value)


def _subject_property_readonly(subject, name: str) -> bool:
    checker = getattr(subject, "is_property_readonly", None)
    if not callable(checker):
        return False
    try:
        return bool(checker(name))
    except (TypeError, ReferenceError):
        return False


def _capture_collection(name: str, value, depth: int) -> tuple | None:
    if depth <= 0 or not hasattr(value, "clear") or not hasattr(value, "add"):
        return None
    items = []
    for item in value:
        state = snapshot_rna_state(item, max_depth=depth - 1)
        if state is None:
            return None
        items.append(state)
    return name, "collection", tuple(items)


def _capture_pointer(name: str, value, depth: int) -> tuple:
    if value is None:
        return name, "reference", None
    if depth > 0 and _is_property_group(value):
        state = snapshot_rna_state(value, max_depth=depth - 1)
        if state is not None:
            return name, "pointer", state
    return name, "reference", value


def restore_rna_state(subject, state: RNAState | None) -> None:
    """全項目の復元を試み、失敗があれば最後にまとめて通知する。"""

    if subject is None or state is None:
        return
    failures: list[tuple[str, BaseException]] = []
    for name, state_type, value in state.fields:
        try:
            _restore_rna_field(subject, name, state_type, value)
        except Exception as exc:
            failures.append((name, exc))
    try:
        _restore_custom_properties(
            subject,
            dict(state.custom_properties),
            preserved=frozenset(state.preserved_custom_properties),
        )
    except Exception as exc:
        failures.append(("custom_properties", exc))
    if failures:
        raise RNAStateRestoreError(failures)


def replace_target_rna_snapshot_value(
    payload,
    path: tuple[str, ...],
    expected: Any,
    replacement: Any,
) -> tuple[Any, bool]:
    """``rna_values`` 退避片のdata内にある既知の1値だけを置き換える。"""

    keys = tuple(str(name or "").strip() for name in path)
    if not keys or any(not name for name in keys):
        raise ValueError("RNA snapshot path is required")
    if not isinstance(payload, dict):
        return payload, False
    updated, changed = _replace_rna_state_value(
        payload.get("data"),
        keys,
        expected,
        replacement,
        match_expected=True,
    )
    if not changed:
        return payload, False
    result = dict(payload)
    result["data"] = updated
    return result, True


def set_target_rna_snapshot_value(
    payload,
    path: tuple[str, ...],
    replacement: Any,
) -> tuple[Any, bool]:
    """``rna_values`` 退避片の既知の1値を現在値にかかわらず置き換える。"""

    keys = tuple(str(name or "").strip() for name in path)
    if not keys or any(not name for name in keys):
        raise ValueError("RNA snapshot path is required")
    if not isinstance(payload, dict):
        return payload, False
    updated, changed = _replace_rna_state_value(
        payload.get("data"),
        keys,
        None,
        replacement,
        match_expected=False,
    )
    if not changed:
        return payload, False
    result = dict(payload)
    result["data"] = updated
    return result, True


def replace_preset_reference_snapshot(
    payload,
    expected: str,
    replacement: str,
    *,
    balloon_outline_json: str = "",
) -> tuple[Any, bool]:
    """固定対象の保存プリセット参照だけをキャンセル基準上で確定する。"""

    if not isinstance(payload, dict):
        return payload, False
    current = str(payload.get("preset_name", "") or "").strip()
    if current != str(expected or "").strip():
        return payload, False
    result = dict(payload)
    result["preset_name"] = str(replacement or "").strip()
    outline = str(balloon_outline_json or "")
    if not result["preset_name"] and outline:
        result["custom_outline_json"] = outline
    return result, True


def _replace_rna_state_value(
    state: RNAState | None,
    path: tuple[str, ...],
    expected: Any,
    replacement: Any,
    *,
    match_expected: bool,
) -> tuple[RNAState | None, bool]:
    if state is None:
        return state, False
    head, *tail = path
    fields = list(state.fields)
    for index, (name, state_type, value) in enumerate(fields):
        if name != head:
            continue
        if not tail:
            if state_type != "value" or (match_expected and value != expected):
                return state, False
            fields[index] = (name, state_type, deepcopy(replacement))
        else:
            if state_type != "pointer" or not isinstance(value, RNAState):
                return state, False
            nested, changed = _replace_rna_state_value(
                value,
                tuple(tail),
                expected,
                replacement,
                match_expected=match_expected,
            )
            if not changed:
                return state, False
            fields[index] = (name, state_type, nested)
        return (
            RNAState(
                tuple(fields),
                state.custom_properties,
                state.preserved_custom_properties,
            ),
            True,
        )
    return state, False


def _restore_rna_field(subject, name: str, state_type: str, value) -> None:
    if state_type == "value":
        setattr(subject, name, _clone_value(value))
        return
    if state_type == "reference":
        setattr(subject, name, value)
        return
    destination = getattr(subject, name)
    if state_type == "pointer":
        restore_rna_state(destination, value)
        return
    if state_type == "collection":
        destination.clear()
        for item_state in value:
            restore_rna_state(destination.add(), item_state)
        return
    raise ValueError(f"unknown RNA state type: {state_type}")


def _rna_properties(subject):
    rna = getattr(subject, "bl_rna", None)
    return getattr(rna, "properties", None) if rna is not None else None


def _is_property_group(value) -> bool:
    if bpy is not None:
        try:
            return isinstance(value, bpy.types.PropertyGroup)
        except Exception:
            # 破棄済みRNA参照では型判定自体が失敗するため、テスト用印へfallback。
            pass
    return bool(getattr(value, "__detail_property_group__", False))


def _clone_value(value):
    if hasattr(value, "to_dict"):
        return {str(k): _clone_value(v) for k, v in value.to_dict().items()}
    if hasattr(value, "to_list"):
        return [_clone_value(item) for item in value.to_list()]
    if _is_math_sequence(value):
        return tuple(_clone_value(item) for item in value)
    try:
        return deepcopy(value)
    except Exception:
        # bpyの一部RNA値はdeepcopy非対応。復元時に代入可能な参照値を保持する。
        return value


def _is_math_sequence(value) -> bool:
    module = str(getattr(type(value), "__module__", "") or "")
    return module.startswith("mathutils")


def _capture_custom_properties(subject) -> dict[str, Any]:
    if not hasattr(subject, "keys") or not hasattr(subject, "get"):
        return {}
    captured = {}
    try:
        keys = tuple(subject.keys())
    except (TypeError, ReferenceError):
        return {}
    for key in keys:
        text = str(key)
        if text == "_RNA_UI":
            continue
        captured[text] = _clone_value(subject.get(key))
    return captured


def _restore_custom_properties(
    subject,
    values: dict[str, Any],
    *,
    preserved: frozenset[str] = frozenset(),
) -> None:
    if not hasattr(subject, "keys") or not hasattr(subject, "__setitem__"):
        return
    current = {str(key) for key in subject.keys() if str(key) != "_RNA_UI"}
    for key in current - set(values) - set(preserved):
        del subject[key]
    for key, value in values.items():
        existing = subject.get(key) if key in current else None
        if isinstance(value, dict) and _is_custom_property_group(existing):
            _restore_custom_properties(existing, value)
            continue
        try:
            subject[key] = _clone_value(value)
        except TypeError:
            # Blenderの既存IDPropertyGroupはdictで上書きできない。
            # 型が変わった項目だけ一度削除し、元の型で再作成する。
            if key not in current:
                raise
            del subject[key]
            subject[key] = _clone_value(value)


def _is_custom_property_group(value) -> bool:
    return (
        value is not None
        and hasattr(value, "keys")
        and hasattr(value, "get")
        and hasattr(value, "__setitem__")
        and hasattr(value, "__delitem__")
    )


def make_actual_detail_state_registry() -> DetailStateRegistry:
    registry = DetailStateRegistry()
    register_actual_detail_state_adapters(registry)
    return registry


def register_actual_detail_state_adapters(registry: DetailStateRegistry) -> None:
    """通常値→Object→GP→効果線派生実体の順で復元されるよう登録する。"""

    registry.register("fill", "gradient_curve", _capture_fill_curve, _restore_fill_curve)
    registry.register("effect", "effect_runtime", _capture_effect_runtime, _restore_effect_runtime)
    registry.register(
        "text",
        "linked_balloon",
        _capture_text_linked_balloon,
        _restore_text_linked_balloon,
    )
    registry.register("gp", "gp_content", _capture_gp_content, _restore_gp_content)
    registry.register("effect", "gp_content", _capture_gp_content, _restore_gp_content)
    registry.register("*", "managed_object", _capture_managed_object, _restore_managed_object)
    # RNA復元後に適用するため、その直前へ登録する（復元は登録の逆順）。
    registry.register(
        "*",
        "preset_reference",
        _capture_preset_reference,
        _restore_preset_reference,
    )
    registry.register("*", "rna_values", _capture_target_rna, _restore_target_rna)


def _capture_fill_curve(target: DetailTarget):
    from . import fill_real_object

    return fill_real_object.get_gradient_curve_points(target.stable_id)


def _restore_fill_curve(target: DetailTarget, payload) -> None:
    from . import fill_real_object

    fill_real_object.set_gradient_curve_points(target.stable_id, payload)


def _capture_text_linked_balloon(target: DetailTarget):
    """リンク選択が変更し得る親フキダシも同じ取消境界へ含める。"""

    from . import text_balloon_link

    scene = getattr(target.data, "id_data", None)
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    balloon = text_balloon_link.find_linked_balloon(
        work,
        target.data,
        stable_id=target.stable_id,
    )
    if balloon is None:
        return None
    return {"balloon": balloon, "state": snapshot_rna_state(balloon)}


def _restore_text_linked_balloon(_target: DetailTarget, payload) -> None:
    if not payload:
        return
    balloon = payload.get("balloon")
    restore_rna_state(balloon, payload.get("state"))
    if balloon is not None:
        from . import balloon_curve_object

        balloon_curve_object.on_balloon_entry_changed(balloon)


def _capture_preset_reference(target: DetailTarget):
    if target.kind == "coma":
        border = getattr(target.data, "border", None)
        return {
            "preset_name": str(getattr(border, "preset_name", "") or ""),
        }
    if target.kind == "balloon":
        return {
            "preset_name": str(
                getattr(target.data, "custom_preset_name", "") or ""
            ),
            "custom_outline_json": str(
                getattr(target.data, "custom_outline_json", "") or ""
            ),
        }
    return None


def _restore_preset_reference(target: DetailTarget, payload) -> None:
    if not isinstance(payload, dict):
        return
    preset_name = str(payload.get("preset_name", "") or "")
    if target.kind == "coma":
        border = getattr(target.data, "border", None)
        if border is not None:
            border.preset_name = preset_name
        return
    if target.kind != "balloon":
        return
    from . import balloon_curve_object

    with balloon_curve_object.suspend_auto_sync():
        target.data.custom_outline_json = str(
            payload.get("custom_outline_json", "") or ""
        )
        target.data.custom_preset_name = preset_name


def _capture_target_rna(target: DetailTarget):
    exclusions = _PAGE_ROOT_EXCLUSIONS if target.kind == "page" else frozenset()
    preserved_custom = (
        _RASTER_INDEPENDENT_CUSTOM_PROPERTIES
        if target.kind == "raster"
        else frozenset()
    )
    return {
        "data": snapshot_rna_state(
            target.data,
            excluded=exclusions,
            preserved_custom=preserved_custom,
        ),
        "params": snapshot_rna_state(target.params) if target.params is not target.data else None,
    }


def _restore_target_rna(target: DetailTarget, payload) -> None:
    effect_op = _effect_operator() if target.kind == "effect" else None
    scene = getattr(target.params, "id_data", None) if target.params is not None else None
    if effect_op is not None:
        effect_op._set_scene_params_syncing(scene, True)
    try:
        restore_rna_state(target.data, payload.get("data"))
        if target.params is not target.data:
            restore_rna_state(target.params, payload.get("params"))
    finally:
        if effect_op is not None:
            effect_op._set_scene_params_syncing(scene, False)


def _capture_managed_object(target: DetailTarget):
    obj = target.object_ref
    if obj is None:
        return None
    values = {}
    for name in _OBJECT_ATTRS:
        try:
            values[name] = _clone_value(getattr(obj, name))
        except (AttributeError, ReferenceError):
            continue
    custom = {key: value for key, value in _capture_custom_properties(obj).items() if key.startswith("bmanga_")}
    try:
        selected = bool(obj.select_get())
    except Exception:
        # ViewLayer外など選択状態を取得できないObjectは未選択として退避する。
        selected = False
    return {"values": values, "custom": custom, "selected": selected}


def _restore_managed_object(target: DetailTarget, payload) -> None:
    obj = target.object_ref
    if obj is None or payload is None:
        return
    for name, value in payload["values"].items():
        try:
            setattr(obj, name, _clone_value(value))
        except (AttributeError, TypeError):
            continue
    _restore_bmanga_custom_properties(obj, payload["custom"])
    try:
        obj.select_set(bool(payload["selected"]))
    except Exception:
        # ViewLayer外のObjectでも、選択以外の管理状態は復元済みなので継続する。
        pass


def _restore_bmanga_custom_properties(obj, values) -> None:
    current = {str(key) for key in obj.keys() if str(key).startswith("bmanga_")}
    for key in current - set(values):
        del obj[key]
    for key, value in values.items():
        obj[key] = _clone_value(value)


def _capture_gp_content(target: DetailTarget):
    obj = target.object_ref
    data = getattr(obj, "data", None) if obj is not None else None
    layers = getattr(data, "layers", None) if data is not None else None
    if layers is None:
        return None
    active = getattr(layers, "active", None)
    return {
        "layers": tuple(_capture_gp_layer(layer) for layer in layers),
        "active": str(getattr(active, "name", "") or ""),
        "materials": _capture_gp_materials(obj),
    }


def _capture_gp_layer(layer):
    frames = tuple(_capture_gp_frame(frame) for frame in getattr(layer, "frames", ()))
    return {
        "name": str(getattr(layer, "name", "") or "content"),
        "rna": snapshot_rna_state(layer, excluded={"frames", "parent_group"}),
        "frames": frames,
    }


def _capture_gp_frame(frame):
    drawing = getattr(frame, "drawing", None)
    strokes = tuple(_capture_gp_stroke(stroke) for stroke in getattr(drawing, "strokes", ()))
    return {
        "number": int(getattr(frame, "frame_number", 0) or 0),
        "rna": snapshot_rna_state(frame, excluded={"drawing"}),
        "strokes": strokes,
    }


def _capture_gp_stroke(stroke):
    known = {}
    for name in ("cyclic", "material_index", "start_cap", "end_cap", "softness"):
        try:
            known[name] = _clone_value(getattr(stroke, name))
        except (AttributeError, ReferenceError):
            continue
    return {
        "rna": snapshot_rna_state(stroke, excluded={"points"}),
        "known": known,
        "points": tuple(_capture_gp_point(point) for point in getattr(stroke, "points", ())),
    }


def _capture_gp_point(point):
    known = {}
    for name in ("position", "radius", "opacity", "rotation", "vertex_color"):
        try:
            known[name] = _clone_value(getattr(point, name))
        except (AttributeError, ReferenceError):
            continue
    return {"rna": snapshot_rna_state(point), "known": known}


def _restore_gp_content(target: DetailTarget, payload) -> None:
    obj = target.object_ref
    if obj is None or payload is None:
        return
    layers = getattr(getattr(obj, "data", None), "layers", None)
    if layers is None:
        return
    _clear_gp_layers(layers)
    restored = {}
    pending_frames = []
    for layer_state in payload["layers"]:
        layer = _new_gp_layer(layers, layer_state["name"])
        restore_rna_state(layer, layer_state["rna"])
        restored[layer_state["name"]] = layer
        pending_frames.append((layer, layer_state["frames"]))
    for layer, frame_states in pending_frames:
        _restore_gp_frames(layer, frame_states)
    # Blenderはレイヤー／ストローク再生成中に既定Materialを追加することが
    # ある。描画を全て戻した後でスロットを復元し、余分な材質を残さない。
    _restore_gp_materials(obj, payload.get("materials"))
    active = restored.get(payload.get("active"))
    if active is not None:
        try:
            layers.active = active
        except Exception:
            # 復元後に参照が無効なら、内容の復元を優先してactive指定だけ省略する。
            pass


def _clear_gp_layers(layers) -> None:
    for layer in reversed(tuple(layers)):
        layers.remove(layer)


def _new_gp_layer(layers, name: str):
    try:
        return layers.new(name, set_active=True)
    except TypeError:
        return layers.new(name)


def _restore_gp_frames(layer, frame_states) -> None:
    from . import gpencil

    for frame_state in frame_states:
        frame = layer.frames.new(frame_number=int(frame_state["number"]))
        restore_rna_state(frame, frame_state["rna"])
        drawing = getattr(frame, "drawing", None)
        for stroke_state in frame_state["strokes"]:
            _restore_gp_stroke(gpencil, drawing, stroke_state)


def _restore_gp_stroke(gpencil, drawing, state) -> None:
    point_states = state["points"]
    positions = [item["known"].get("position", (0.0, 0.0, 0.0)) for item in point_states]
    if not positions or drawing is None:
        return
    before = len(getattr(drawing, "strokes", ()))
    cyclic = bool(state["known"].get("cyclic", False))
    material = int(state["known"].get("material_index", 0) or 0)
    if not gpencil.add_stroke_to_drawing(drawing, positions, cyclic=cyclic, material_index=material):
        raise RuntimeError("Grease Pencilストロークを復元できませんでした")
    stroke = drawing.strokes[before]
    restore_rna_state(stroke, state["rna"])
    _restore_gp_point_known(stroke, state["known"])
    for point, point_state in zip(stroke.points, point_states, strict=True):
        restore_rna_state(point, point_state["rna"])
        _restore_gp_point_known(point, point_state["known"])


def _restore_gp_point_known(point, values) -> None:
    for name, value in values.items():
        try:
            setattr(point, name, _clone_value(value))
        except (AttributeError, TypeError):
            continue


def _capture_gp_materials(obj):
    slots = getattr(getattr(obj, "data", None), "materials", None)
    if slots is None:
        return None
    refs = tuple(material for material in slots)
    states = []
    for material in refs:
        if material is None:
            states.append(None)
            continue
        states.append(
            {
                "diffuse": _clone_value(getattr(material, "diffuse_color", (1, 1, 1, 1))),
                "gp": snapshot_rna_state(getattr(material, "grease_pencil", None)),
                "gp_known": _capture_gp_material_known(material),
            }
        )
    return {
        "refs": refs,
        "states": tuple(states),
        "active_index": int(getattr(obj, "active_material_index", 0) or 0),
    }


def _restore_gp_materials(obj, payload) -> None:
    if payload is None:
        return
    slots = getattr(getattr(obj, "data", None), "materials", None)
    if slots is None:
        return
    while len(slots):
        before = len(slots)
        try:
            slots.pop(index=before - 1)
        except TypeError:
            slots.pop()
        if len(slots) >= before:
            raise RuntimeError("手描きレイヤーのMaterialスロットを復元できませんでした")
    for material in payload["refs"]:
        slots.append(material)
    for material, state in zip(payload["refs"], payload["states"], strict=True):
        if material is None or state is None:
            continue
        material.diffuse_color = state["diffuse"]
        restore_rna_state(getattr(material, "grease_pencil", None), state["gp"])
        _restore_gp_material_known(material, state.get("gp_known", {}))
    if len(slots):
        try:
            obj.active_material_index = max(
                0,
                min(int(payload.get("active_index", 0) or 0), len(slots) - 1),
            )
        except (AttributeError, TypeError, ValueError):
            pass


def _capture_gp_material_known(material) -> dict[str, Any]:
    style = getattr(material, "grease_pencil", None)
    if style is None:
        return {}
    values = {}
    for name in ("color", "fill_color", "show_stroke", "show_fill"):
        try:
            value = getattr(style, name)
            values[name] = (
                bool(value)
                if name in {"show_stroke", "show_fill"}
                else tuple(float(component) for component in value)
            )
        except (AttributeError, ReferenceError):
            continue
    return values


def _restore_gp_material_known(material, values) -> None:
    style = getattr(material, "grease_pencil", None)
    if style is None:
        return
    for name, value in values.items():
        try:
            setattr(style, name, _clone_value(value))
        except (AttributeError, TypeError):
            continue


def _capture_effect_runtime(target: DetailTarget):
    effect_op = _effect_operator()
    obj = target.object_ref
    if obj is None:
        return None
    scene = getattr(target.params, "id_data", None) if target.params is not None else None
    return {
        "meta": deepcopy(effect_op._effect_meta(obj)),
        # invoke時の対象読込より前の共有編集設定。対象の表示実体を元設定で
        # 再生成した後、最後にここへ戻すことで別効果線の設定を汚染しない。
        "shared_params": snapshot_rna_state(target.params),
        "active_layer_kind": str(
            getattr(scene, "bmanga_active_layer_kind", "") or ""
        ),
        "active_effect_layer_name": str(
            getattr(scene, "bmanga_active_effect_layer_name", "") or ""
        ),
    }


def _restore_effect_runtime(target: DetailTarget, payload) -> None:
    obj = target.object_ref
    if obj is None or payload is None:
        return
    effect_op = _effect_operator()
    effect_op._write_effect_meta(obj, deepcopy(payload["meta"]))
    from . import layer_object_model

    layer = layer_object_model.content_layer(obj)
    bounds = effect_op.effect_layer_bounds(obj, layer)
    if layer is not None and bounds is not None and bpy is not None:
        effect_op._load_layer_params_to_scene(bpy.context, obj, layer)
        effect_op._write_effect_strokes(
            bpy.context,
            obj,
            layer,
            bounds,
            params_override=target.params,
            propagate_link=False,
        )
    scene = getattr(target.params, "id_data", None) if target.params is not None else None
    was_syncing = effect_op._scene_params_syncing(scene)
    effect_op._set_scene_params_syncing(scene, True)
    try:
        restore_rna_state(target.params, payload.get("shared_params"))
    finally:
        effect_op._set_scene_params_syncing(scene, was_syncing)
    if scene is not None:
        if hasattr(scene, "bmanga_active_layer_kind"):
            scene.bmanga_active_layer_kind = payload.get("active_layer_kind", "")
        if hasattr(scene, "bmanga_active_effect_layer_name"):
            scene.bmanga_active_effect_layer_name = payload.get(
                "active_effect_layer_name", ""
            )
    _restore_effect_profile_nodes(target.params)


def _restore_effect_profile_nodes(params) -> None:
    if params is None:
        return
    from . import effect_inout_curve

    effect_inout_curve.restore_ui_nodes_from_params(params)


def _effect_operator():
    from ..operators import effect_line_op

    return effect_line_op


ACTUAL_DETAIL_STATE_REGISTRY = DEFAULT_DETAIL_STATE_REGISTRY
_registered = {
    (adapter.kind, adapter.name)
    for adapter in getattr(ACTUAL_DETAIL_STATE_REGISTRY, "_adapters", ())
}
if not _registered:
    register_actual_detail_state_adapters(ACTUAL_DETAIL_STATE_REGISTRY)


__all__ = [
    "ACTUAL_DETAIL_STATE_REGISTRY",
    "RNAState",
    "RNAStateRestoreError",
    "make_actual_detail_state_registry",
    "replace_preset_reference_snapshot",
    "replace_target_rna_snapshot_value",
    "set_target_rna_snapshot_value",
    "register_actual_detail_state_adapters",
    "restore_rna_state",
    "snapshot_rna_state",
]
