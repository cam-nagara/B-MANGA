"""B-MANGA Line modifier stack ordering helpers."""

from __future__ import annotations

import bpy

from .core import (
    GN_MODIFIER_NAME,
    INTERSECTION_MODIFIER_NAME,
    INTERSECTION_MODIFIER_PREFIX,
    MODIFIER_NAME,
    OUTLINE_WIDTH_ATTR_MODIFIER_NAME,
    SELECTION_LINE_MODIFIER_NAME,
    SHEET_OUTLINE_MODIFIER_NAME,
)


def is_line_modifier_name(name: str) -> bool:
    """B-MANGA Line が生成するモディファイア名か返す."""
    return (
        name == SHEET_OUTLINE_MODIFIER_NAME
        or name == OUTLINE_WIDTH_ATTR_MODIFIER_NAME
        or name == MODIFIER_NAME
        or name == GN_MODIFIER_NAME
        or name == SELECTION_LINE_MODIFIER_NAME
        or name == INTERSECTION_MODIFIER_NAME
        or name.startswith(INTERSECTION_MODIFIER_PREFIX)
    )


def _line_modifier_order(mod: bpy.types.Modifier) -> tuple[int, str]:
    name = mod.name
    # シートのチューブは Solidify より前（境界辺を元メッシュから拾うため）
    if name == SHEET_OUTLINE_MODIFIER_NAME:
        return (0, name)
    if name == OUTLINE_WIDTH_ATTR_MODIFIER_NAME:
        return (1, name)
    if name == MODIFIER_NAME:
        return (2, name)
    if name == GN_MODIFIER_NAME:
        return (3, name)
    if name == SELECTION_LINE_MODIFIER_NAME:
        return (4, name)
    if name == INTERSECTION_MODIFIER_NAME or name.startswith(INTERSECTION_MODIFIER_PREFIX):
        return (5, name)
    return (99, name)


def _is_auto_smooth_modifier(mod: bpy.types.Modifier) -> bool:
    return mod.name == "Smooth by Angle" and mod.type == "NODES"


def replace_shared_node_tree(tree_name: str, old_tree, build_new_tree):
    """世代不一致の共有GNツリーを、参照モディファイアを保全しつつ再構築する.

    稜谷線・選択線・交差線の各GNツリーは、単一のデータブロック名（例
    "BML_InnerLines_Cached"）を全オブジェクトのモディファイアが共有する。
    旧世代のツリーを検出した際に単純に
    ``bpy.data.node_groups.remove(old_tree)`` してから作り直すと、
    Blenderの既定挙動（do_unlink=True）により、その旧ツリーを参照して
    いた**全オブジェクト**のモディファイアの ``node_group`` が None化
    される。ところが更新系オペレーターは選択中オブジェクトしか処理しない
    ため、選択していなかった他オブジェクトの線が理由表示なく消える
    （2026-07-09 徹底チェックで実機確認・AGENT_INBOX.md参照）。

    この関数は削除前に ``old_tree`` を参照する全 NODES モディファイアを
    シーン全体から収集し、``build_new_tree()`` で新ツリーを構築した後、
    収集したモディファイアを新ツリーへ張り替えてから旧ツリーを削除する。

    呼び出し側は既存どおり ``tree = bpy.data.node_groups.get(tree_name)``
    で現在のツリーを取得し、有効性チェックに失敗した場合にこの関数へ
    ``old_tree`` として渡す（``tree`` が最初から存在しない場合は
    ``old_tree=None`` でよく、単に ``build_new_tree()`` を呼ぶだけになる）。
    """
    referencing_mods: list[bpy.types.Modifier] = []
    if old_tree is not None:
        for obj in bpy.data.objects:
            for mod in obj.modifiers:
                if getattr(mod, "type", None) == "NODES" and mod.node_group == old_tree:
                    referencing_mods.append(mod)

    new_tree = build_new_tree()

    if old_tree is not None and old_tree != new_tree:
        bpy.data.node_groups.remove(old_tree)
        if new_tree.name != tree_name:
            new_tree.name = tree_name

    for mod in referencing_mods:
        try:
            if mod.node_group != new_tree:
                mod.node_group = new_tree
        except ReferenceError:
            continue

    return new_tree


def reorder_line_modifiers(obj: bpy.types.Object) -> None:
    """既存のメッシュ調整後に、アウトライン/内部線/交差線を安定配置する."""
    if obj.type != "MESH":
        return
    modifiers = list(obj.modifiers)
    line_mods = [mod for mod in modifiers if is_line_modifier_name(mod.name)]
    if not line_mods:
        return
    base_index = sum(
        1
        for mod in modifiers
        if not is_line_modifier_name(mod.name) and not _is_auto_smooth_modifier(mod)
    )
    for mod in sorted(line_mods, key=_line_modifier_order):
        try:
            current = list(obj.modifiers).index(mod)
        except ValueError:
            continue
        target = base_index
        base_index += 1
        if current != target:
            obj.modifiers.move(current, target)
