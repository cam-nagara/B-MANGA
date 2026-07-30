"""Blender 5.2 LTS専用のGN modifier／node socket helper。"""

from __future__ import annotations

from typing import Any


def set_gn_modifier_input(modifier, identifier: str, value: Any) -> bool:
    """Blender 5.2のGN modifier入力へ値を書き込む。"""
    if not identifier:
        return False
    properties = getattr(modifier, "properties", None)
    if properties is not None:
        try:
            item = properties.inputs[identifier]
        except Exception:  # noqa: BLE001
            item = None
        if item is not None:
            try:
                item.value = value
                return True
            except Exception:  # noqa: BLE001
                pass
            try:
                item["value"] = value
                return True
            except Exception:  # noqa: BLE001
                pass
    return False


def get_gn_modifier_input(modifier, identifier: str, default: Any = None) -> Any:
    """Blender 5.2のGN modifier入力を読み取る。"""
    if not identifier:
        return default
    properties = getattr(modifier, "properties", None)
    if properties is not None:
        try:
            item = properties.inputs[identifier]
        except Exception:  # noqa: BLE001
            item = None
        if item is not None:
            try:
                return item.value
            except Exception:  # noqa: BLE001
                pass
            try:
                return item["value"]
            except Exception:  # noqa: BLE001
                pass
    return default


def compare_operand_socket(node, name: str):
    """FunctionNodeCompare の A/B 入力を、有効なソケットの表示名で解決する.

    5.2の共通A/B socketを表示名で解決する。
    """
    for socket in node.inputs:
        if getattr(socket, "enabled", True) and str(getattr(socket, "name", "") or "") == name:
            return socket
    raise KeyError(name)


def random_value_operand_socket(node, name: str):
    """FunctionNodeRandomValue の Min/Max/ID/Seed 入力を、有効なソケットの表示名で解決する."""
    for socket in node.inputs:
        if getattr(socket, "enabled", True) and str(getattr(socket, "name", "") or "") == name:
            return socket
    raise KeyError(name)


def random_value_output_socket(node):
    """FunctionNodeRandomValue の Value 出力を、有効なソケットの中から解決する."""
    for socket in node.outputs:
        if getattr(socket, "enabled", True) and str(getattr(socket, "name", "") or "") == "Value":
            return socket
    return node.outputs[0]
