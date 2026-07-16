"""Blender実機用: INFO付きTipsの撤去とツールチップ移行を検証する。"""

from __future__ import annotations

import ast
import importlib.util
import sys
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE_ROOTS = (
    ROOT / "panels",
    ROOT / "operators",
    ROOT / "ui",
    ROOT / "addons" / "b_manga_line",
    ROOT / "addons" / "b_manga_render",
)


def _load_addon(package_name: str, root: Path):
    spec = importlib.util.spec_from_file_location(
        package_name,
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"アドオンを読み込めません: {root}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def _contains_info_icon(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant) and child.value == "INFO"
        for child in ast.walk(node)
    )


def _info_icon_offenders() -> list[str]:
    files = [ROOT / "preferences.py"]
    for source_root in UI_SOURCE_ROOTS:
        files.extend(
            path
            for path in source_root.rglob("*.py")
            if not path.name.startswith("_test")
        )

    offenders: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            icon = next((kw.value for kw in call.keywords if kw.arg == "icon"), None)
            if icon is not None and _contains_info_icon(icon):
                call_name = call.func.attr if isinstance(call.func, ast.Attribute) else "call"
                offenders.append(f"{path.relative_to(ROOT)}:{call.lineno} ({call_name})")
    return offenders


def _description(owner, prop_name: str) -> str:
    return str(owner.bl_rna.properties[prop_name].description or "")


def _enum_description(owner, prop_name: str, item_id: str) -> str:
    prop = owner.bl_rna.properties[prop_name]
    return next(
        str(item.description or "")
        for item in prop.enum_items
        if item.identifier == item_id
    )


def _assert_contains(actual: str, expected: str, target: str) -> None:
    if expected not in actual:
        raise AssertionError(f"{target} のツールチップに「{expected}」がありません: {actual}")


def _check_main_tooltips() -> None:
    package = "bmanga_dev_ui_tooltip_cleanup"
    addon = _load_addon(package, ROOT)
    try:
        balloon = sys.modules[f"{package}.core.balloon"].BMangaBalloonEntry
        border = sys.modules[f"{package}.core.coma_border"].BMangaComaBorder
        effect = sys.modules[f"{package}.core.effect_line"].BMangaEffectLineParams
        paper = sys.modules[f"{package}.core.paper"].BMangaPaperSettings
        work = sys.modules[f"{package}.core.work"].BMangaWorkData
        preferences = sys.modules[f"{package}.preferences"].BMangaPreferences

        _assert_contains(_description(balloon, "shape"), "フキダシツール", "フキダシ形状")
        _assert_contains(
            _enum_description(balloon, "line_style", "uni_flash"),
            "内端輪郭",
            "ウニフラ線種",
        )
        _assert_contains(
            _enum_description(balloon, "line_material_mapping", "tile"),
            "切れ目",
            "フキダシのマテリアル貼り方",
        )
        _assert_contains(
            _enum_description(balloon, "line_material_seam_fix", "mirror"),
            "左右反転",
            "フキダシ継ぎ目処理",
        )
        _assert_contains(_description(border, "blur_amount"), "表示更新後", "コマ枠ボカシ")
        _assert_contains(_description(effect, "base_path_enabled"), "効果線ツール", "効果線基準パス")
        _assert_contains(_description(paper, "page_number_prefix"), "p0001", "ページ番号書式")
        _assert_contains(_description(work, "coma_blend_template_path"), "プリファレンス", "コマテンプレート")
        _assert_contains(_description(preferences, "meldex_enabled"), "自動的にオフ", "Meldex受信")
        _assert_contains(_description(preferences, "key_navigate"), "Shiftで回転", "ナビゲートキー")
        _assert_contains(_description(preferences, "default_base_font_path"), "OS標準", "標準フォント")

        preset_ops = sys.modules[f"{package}.operators.detail_preset_management_op"]
        raster_ops = sys.modules[f"{package}.operators.raster_detail_action_op"]
        _assert_contains(
            preset_ops.BMANGA_OT_detail_preset_add.bl_description,
            "キャンセルでは戻りません",
            "プリセット追加",
        )
        _assert_contains(
            raster_ops.BMANGA_OT_detail_raster_save_png.bl_description,
            "即時保存",
            "ラスターPNG保存",
        )
    finally:
        addon.unregister()


def _check_render_tooltips() -> None:
    package = "bmanga_render_dev_ui_tooltip_cleanup"
    root = ROOT / "addons" / "b_manga_render"
    addon = _load_addon(package, root)
    try:
        command = sys.modules[f"{package}.core"].BMangaRenderCommand
        _assert_contains(_description(command, "command_type"), "マウスを置く", "レンダーコマンド種類")
        _assert_contains(
            _enum_description(command, "command_type", "SET_NODE_MUTE"),
            "完全一致",
            "ノードミュート",
        )
        _assert_contains(_description(command, "folder_path"), "魚眼モード時のみ", "魚眼出力フォルダ")
        _assert_contains(
            str(
                bpy.ops.bmanga_render.command_add.get_rna_type()
                .properties["command_type"]
                .description
                or ""
            ),
            "マウスを置く",
            "追加するレンダーコマンド種類",
        )
    finally:
        addon.unregister()


def _check_liner_tooltips() -> None:
    package = "bmanga_line_dev_ui_tooltip_cleanup"
    root = ROOT / "addons" / "b_manga_line"
    addon = _load_addon(package, root)
    try:
        settings = sys.modules[f"{package}.core"].BMangaLineSettings
        optimizer = sys.modules[f"{package}.mesh_optimizer"]
        quad_repair = sys.modules[f"{package}.mesh_quad_repair"]
        _assert_contains(
            _description(settings, "bump_line_enabled"),
            "ビューポートには表示されません",
            "バンプ線",
        )
        _assert_contains(
            optimizer.BMANGA_LINE_OT_optimize_purchased_mesh.bl_description,
            "ライン反映前",
            "購入素材メッシュ最適化",
        )
        _assert_contains(
            quad_repair.BMANGA_LINE_OT_auto_repair_quad_mesh.bl_description,
            "ライン反映前",
            "問題メッシュ自動修復",
        )
        _assert_contains(
            _description(bpy.types.Scene, "bmanga_line_mesh_optimize_quality"),
            "静的メッシュ",
            "メッシュ最適化品質",
        )
    finally:
        addon.unregister()


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    offenders = _info_icon_offenders()
    if offenders:
        raise AssertionError("UIにINFOアイコンが残っています: " + ", ".join(offenders))
    _check_main_tooltips()
    _check_render_tooltips()
    _check_liner_tooltips()
    print("PASS: INFO付きTipsを撤去し、説明をツールチップへ移行しました")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise
