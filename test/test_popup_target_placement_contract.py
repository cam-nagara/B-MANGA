"""対象を隠さないポップアップ配置とルビ統合の静的契約。"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_all_operator_property_dialogs_use_target_aware_helper() -> None:
    offenders = []
    for path in sorted((ROOT / "operators").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "window_manager.invoke_props_dialog(" in source:
            offenders.append(path.name)
    assert offenders == []


def test_target_bound_confirmations_and_context_menus_use_helper() -> None:
    confirm_offenders = []
    menu_offenders = []
    for directory in (ROOT / "operators", ROOT / "ui"):
        for path in sorted(directory.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "window_manager.invoke_confirm(" in source:
                confirm_offenders.append(path.relative_to(ROOT).as_posix())
            if "bpy.ops.wm.call_menu(" in source:
                menu_offenders.append(path.relative_to(ROOT).as_posix())
    assert confirm_offenders == []
    assert menu_offenders == []


def test_dialog_invokes_do_not_reference_an_undefined_event() -> None:
    offenders = []
    for path in sorted((ROOT / "operators").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "invoke":
                continue
            arguments = {argument.arg for argument in node.args.args}
            assigned = {
                name.id
                for name in ast.walk(node)
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
            }
            for name in ast.walk(node):
                if (
                    isinstance(name, ast.Name)
                    and isinstance(name.ctx, ast.Load)
                    and name.id in {"event", "_event"}
                    and name.id not in arguments | assigned
                ):
                    offenders.append((path.name, node.lineno, name.id))
    assert offenders == []


def test_ruby_dialog_is_integrated_into_selection_settings() -> None:
    selection_source = (ROOT / "operators" / "text_selection_style_op.py").read_text(encoding="utf-8")
    legacy_source = (ROOT / "operators" / "text_ruby_op.py").read_text(encoding="utf-8")
    panel_source = (ROOT / "panels" / "balloon_panel.py").read_text(encoding="utf-8")
    keymap_source = (ROOT / "keymap" / "keymap.py").read_text(encoding="utf-8")

    assert 'ruby_text: StringProperty(name="ルビ"' in selection_source
    assert 'ruby_style: EnumProperty(name="ルビ種類"' in selection_source
    assert "self._original_spans = text_style.all_spans_snapshot(entry)" in selection_source
    assert "text_style.restore_all_spans(entry, snapshot)" in selection_source
    assert 'bpy.ops.bmanga.text_selection_style_popup(' in legacy_source
    assert "invoke_props_dialog" not in legacy_source
    assert 'row.operator("bmanga.text_selection_style_popup"' in panel_source
    assert "bmanga.text_ruby_add_dialog" not in keymap_source
