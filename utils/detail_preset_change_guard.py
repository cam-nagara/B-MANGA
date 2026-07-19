"""詳細設定でプリセットが上書きする値だけを比較する変更検知。"""

from __future__ import annotations

import json
from typing import Any


_META_KEYS = frozenset(
    {
        "schemaVersion",
        "schema_version",
        "presetType",
        "presetName",
        "description",
    }
)


def capture_preset_settings(target, preset_type: str) -> tuple[str, Any]:
    """現在値を、選択プリセットへ保存される項目だけの不変値へ変換する。"""

    kind = str(preset_type or "").strip()
    if kind == "border":
        from ..io import border_presets

        payload = border_presets.preset_dict_from_coma(target.data, "", "")
        border = payload.get("border")
        if isinstance(border, dict):
            border.pop("presetName", None)
    elif kind == "text":
        from ..io import text_presets

        payload = text_presets.snapshot_from_entry(target.data)
    elif kind == "effect_line":
        from ..io import effect_line_presets

        payload = effect_line_presets.preset_dict_from_params(target.params, "", "")
    elif kind == "fill":
        from ..io import fill_presets

        payload = fill_presets.snapshot_from_entry(target.data)
    elif kind == "gradient":
        from ..io import gradient_presets

        payload = gradient_presets.snapshot_from_entry(target.data)
    elif kind == "image_path":
        from ..io import image_path_presets

        payload = image_path_presets.preset_dict_from_entry(target.data, "", "")
    elif kind == "balloon":
        from ..io import balloon_presets

        shape = str(getattr(target.data, "shape", "") or "")
        payload = {
            "shape": shape,
            "custom_outline": (
                _json_value(getattr(target.data, "custom_outline_json", ""))
                if shape == "custom"
                else None
            ),
            # 形状/線種/色などスタイル全体もプリセット保存対象 (2026-07-20 拡張)。
            # これが無いと shape=="custom" 以外のスタイル変更 (線色・角丸など)
            # が「未保存の変更」として検知できない。
            "style": balloon_presets.snapshot_style_from_entry(target.data),
        }
    else:
        raise ValueError(f"変更検知に未対応のプリセット種別です: {kind}")
    return kind, _freeze(_without_metadata(payload))


def preset_settings_changed(baseline: object, current: object) -> bool:
    """同じ保存対象値へ戻した場合は、変更なしとして扱う。"""

    return baseline != current


def _without_metadata(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if key not in _META_KEYS}


def _json_value(value: object) -> object:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (str, int, bool, type(None))):
        return value
    return str(value)


__all__ = ["capture_preset_settings", "preset_settings_changed"]
