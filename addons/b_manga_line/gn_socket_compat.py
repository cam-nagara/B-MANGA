"""GN モディファイア入力 / Compare・Random Value ノードソケットの Blender 版互換ヘルパー.

Blender 5.2 LTS で以下の破壊的変更が入った:
- GN モディファイア入力への `modifier[identifier] = value` 形式の代入が完全廃止された
  (TypeError)。新形式は `modifier.properties.inputs[identifier]` から項目を取得し、
  型によって RNA構造体 (`.value` 属性) か素の IDPropertyGroup (`["value"]` 添字) の
  どちらかで返ってくる (5.2.0 実機で両方の形が確認済みのため、両方を試す)。
- FunctionNodeCompare / FunctionNodeRandomValue の型別ソケット (A_INT/B_INT,
  Min_002/Max_002 等) が撤廃され、A/B・Min/Max/ID/Seed・Value の共通ソケットへ統合された。

本モジュールの関数は Blender 5.1 以前・5.2 LTS 以降のどちらでも同じ結果になるよう
両対応する。b_manga_line は B-MANGA 本体と独立したアドオンのため、本体側の
utils/geometry_nodes_bridge.py には依存させず、このファイルへ集約する。
"""

from __future__ import annotations

from typing import Any


def set_gn_modifier_input(modifier, identifier: str, value: Any) -> bool:
    """GN モディファイア入力への値書込み (Blender 5.1以前 / 5.2 LTS以降の両対応)."""
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
    try:
        modifier[identifier] = value
        return True
    except Exception:  # noqa: BLE001
        return False


def get_gn_modifier_input(modifier, identifier: str, default: Any = None) -> Any:
    """GN モディファイア入力の現在値読取り (Blender 5.1以前 / 5.2 LTS以降の両対応)."""
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
    try:
        return modifier[identifier]
    except Exception:  # noqa: BLE001
        return default


def compare_operand_socket(node, name: str):
    """FunctionNodeCompare の A/B 入力を、有効なソケットの表示名で解決する.

    Blender 5.1 以前は型ごとに専用ソケット (A_INT/B_INT 等) が常設され、対応する型だけ
    enabled=True になる。5.2 以降は型ごとの専用ソケットが撤廃され A/B の2本だけになる。
    どちらの版でも「有効なソケットの中から表示名で選ぶ」ことで同一コードが動く。
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
