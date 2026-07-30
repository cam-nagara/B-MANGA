"""Domain treeと表示ページ番号の投影補助。"""

from __future__ import annotations

from ..bmanga_core.domain_model import PageDocument


def parent_map(document: PageDocument) -> dict[str, str]:
    return {
        child: parent
        for parent, children in document.children.items()
        for child in children
    }


def tree_order(document: PageDocument) -> list[str]:
    result: list[str] = []

    def visit(uid: str) -> None:
        for child in document.children[uid]:
            result.append(child)
            visit(child)

    visit(document.root_uid)
    return result


def display_number(display_id: str) -> int:
    head = str(display_id or "").split("-", 1)[0].lstrip("p")
    try:
        return max(1, int(head))
    except ValueError:
        raise ValueError(f"invalid page display ID: {display_id!r}") from None


__all__ = ("display_number", "parent_map", "tree_order")
