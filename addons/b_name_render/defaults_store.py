"""プリセットの既定コマンド構成をユーザー設定ファイルへ保存/読込する.

「初期設定に登録」で現在のコマンド構成を Blender のユーザー設定フォルダに
JSON で保存し、どの .blend からでも共通の既定として使う。
「初期設定に戻す」はこの保存値へ戻す (未保存なら組み込み既定)。
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy

_FILE_NAME = "b_name_render_preset_defaults.json"

# JSON にそのまま入る型のみ保存対象 (Pointer/Collection は対象外)
_SKIP_PROPS = {"rna_type"}


def _store_path() -> Path:
    cfg = bpy.utils.user_resource("CONFIG", create=True)
    return Path(cfg) / _FILE_NAME


def _command_to_dict(command) -> dict:
    data: dict = {}
    for prop in command.bl_rna.properties:
        ident = prop.identifier
        if ident in _SKIP_PROPS or getattr(prop, "is_readonly", False):
            continue
        if prop.type in {"POINTER", "COLLECTION"}:
            continue
        try:
            data[ident] = getattr(command, ident)
        except Exception:  # noqa: BLE001
            pass
    return data


def _apply_dict(command, data: dict) -> None:
    for key, value in data.items():
        if not hasattr(command, key):
            continue
        try:
            setattr(command, key, value)
        except Exception:  # noqa: BLE001
            pass


def load_all() -> dict:
    path = _store_path()
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def get_preset_default(preset_name: str) -> list[dict] | None:
    entry = load_all().get(preset_name)
    if isinstance(entry, list):
        return entry
    return None


def save_preset_default(preset_name: str, preset) -> Path:
    data = load_all()
    data[preset_name] = [_command_to_dict(c) for c in preset.commands]
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def apply_commands(preset, command_dicts: list[dict]) -> None:
    preset.commands.clear()
    for d in command_dicts:
        item = preset.commands.add()
        _apply_dict(item, d)
    # 選択コマンド index は WindowManager 側 (ここでは触らない)。
