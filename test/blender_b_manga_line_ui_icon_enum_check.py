"""Blender実機用: B-MANGA LinerのUIアイコン指定をRNA許可値と照合する."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
ADDON_ROOT = ROOT / "addons" / "b_manga_line"
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402


def _enum_values(owner, function_name: str, parameter_name: str) -> set[str]:
    function = owner.bl_rna.functions[function_name]
    parameter = function.parameters[parameter_name]
    return {item.identifier for item in parameter.enum_items}


def _literal_keyword(call: ast.Call, name: str):
    for keyword in call.keywords:
        if keyword.arg != name:
            continue
        if isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _check_icons() -> None:
    layout_functions = bpy.types.UILayout.bl_rna.functions
    confirm_icons = _enum_values(
        bpy.types.WindowManager,
        "invoke_confirm",
        "icon",
    )
    checked = []
    invalid = []
    for path in sorted(ADDON_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Attribute):
                continue
            icon = _literal_keyword(call, "icon")
            if not isinstance(icon, str):
                continue
            function_name = call.func.attr
            if function_name == "invoke_confirm":
                valid = confirm_icons
            elif function_name in layout_functions:
                function = layout_functions[function_name]
                if "icon" not in function.parameters:
                    continue
                valid = _enum_values(bpy.types.UILayout, function_name, "icon")
            else:
                continue
            item = (path.name, call.lineno, function_name, icon)
            checked.append(item)
            if icon not in valid:
                invalid.append(item)
    assert checked, "アイコン指定を1件も検査できませんでした"
    assert not invalid, f"Blender 5.1で無効なUIアイコン指定: {invalid}"
    assert "WARNING" not in _enum_values(bpy.types.UILayout, "label", "icon")
    assert "WARNING" in confirm_icons
    print(f"[PASS] {len(checked)} UI icon literals match Blender RNA enums")


class _StrictLayout:
    def __init__(self):
        self.valid_label_icons = _enum_values(bpy.types.UILayout, "label", "icon")
        self.labels = []

    def label(self, *, text="", icon="NONE", **_kwargs):
        assert icon in self.valid_label_icons, (text, icon)
        self.labels.append((text, icon))

    def prop(self, *_args, **_kwargs):
        return None


class _ReflectDialogProbe:
    def __init__(self):
        self.layout = _StrictLayout()
        self.reflect_scope = "ALL"

    @staticmethod
    def _initial_reflect_summary(_objects):
        return {
            "objects": 150,
            "applied": 0,
            "intersection": 150,
            "uniform": 150,
        }


def _check_initial_reflect_dialog_draw() -> None:
    from b_manga_line import operators

    probe = _ReflectDialogProbe()
    operators.BMANGA_LINE_OT_reflect_all.draw(probe, bpy.context)
    assert len(probe.layout.labels) == 3, probe.layout.labels


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _check_icons()
        _check_initial_reflect_dialog_draw()
    finally:
        b_manga_line.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
