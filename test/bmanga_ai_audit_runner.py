"""B-MANGA / B-MANGA Render のAI監査用証拠パックを生成するランナー.

通常の単体テストだけでは拾いにくい「画面ではおかしい」問題をAIに渡せるよう、
コード棚卸し、Blender実機テスト、目視用画像、レビュー用プロンプトを1か所に集約する。
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLENDER = Path(
    os.environ.get("BMANGA_BLENDER_EXE", r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
)
DEFAULT_C00 = Path(os.environ.get("BMANGA_C00_BLEND", r"D:\TM Dropbox\Share\B-MANGA\c_file\c00.blend"))
DEFAULT_EEVR = Path(
    os.environ.get("BMANGA_EEVR_ZIP", r"D:\Develop\Blender\暫定安定版_編集禁止\eeVR-master_ミウラ修正 (19).zip")
)
SKIP_DIR_NAMES = {
    ".git",
    ".codex",
    ".claude",
    ".gemini",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "wheels",
}


@dataclass(frozen=True)
class AuditCase:
    key: str
    title: str
    script: str
    target: str
    tags: tuple[str, ...]
    out_env: str = ""
    background: bool = True
    requires_ui: bool = False
    requires_c00: bool = False
    requires_eevr: bool = False
    slow: bool = False


def _audit_cases() -> list[AuditCase]:
    return [
        AuditCase("bmanga_ui_inventory", "B-MANGA UI項目棚卸し", "test/blender_bmanga_ui_inventory_visual_audit.py", "B-MANGA", ("standard", "visual"), "BMANGA_UI_INVENTORY_OUT"),
        AuditCase("bmanga_ui_micro_behavior", "B-MANGA UI微細挙動マトリクス", "test/blender_ui_micro_behavior_matrix_check.py", "B-MANGA", ("standard", "regression"), "BMANGA_UI_MICRO_OUT"),
        AuditCase("bmanga_detail_matrix", "B-MANGA 詳細設定マトリクス", "test/blender_bmanga_full_visual_audit.py", "B-MANGA", ("standard", "visual"), "BMANGA_FULL_VISUAL_OUT"),
        AuditCase("bmanga_partial_completion", "B-MANGA 主要操作状態監査", "test/blender_bmanga_partial_completion_check.py", "B-MANGA", ("standard", "visual"), "BMANGA_PARTIAL_VISUAL_OUT"),
        AuditCase("bmanga_mask_matrix", "B-MANGA ページ/コママスク目視監査", "test/blender_mask_visual_matrix_check.py", "B-MANGA", ("standard", "visual"), "BMANGA_MASK_VISUAL_OUT"),
        AuditCase("bmanga_effect_visibility", "B-MANGA 効果線/コマ表示回帰監査", "test/blender_effect_line_mask_visibility_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_geometry_nodes_bridge", "B-MANGA ジオメトリノード入力監査", "test/blender_geometry_nodes_bridge_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_geometry_nodes_functional", "B-MANGA ジオメトリノード機能反映監査", "test/blender_geometry_nodes_functional_settings_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_balloon_curve_render_visual", "B-MANGA フキダシ塗り/輪郭表示監査", "test/blender_balloon_curve_render_visual_check.py", "B-MANGA", ("standard", "visual"), "BMANGA_BALLOON_CURVE_RENDER_VISUAL_OUT"),
        AuditCase("bmanga_effect_end_fill", "B-MANGA 効果線下地塗り監査", "test/blender_effect_line_end_fill_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_effect_frame_spacing", "B-MANGA 効果線間隔監査", "test/blender_effect_line_frame_spacing_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_balloon_uni_flash", "B-MANGA フキダシ形状監査", "test/blender_balloon_uni_flash_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_effect_detail_graph", "B-MANGA 効果線詳細/線幅グラフ監査", "test/blender_effect_line_detail_graph_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_balloon_path_line", "B-MANGA フキダシパス線監査", "test/blender_balloon_path_line_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_coma_edge_highlight", "B-MANGA コマ枠辺ハイライト監査", "test/blender_coma_edge_highlight_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_coma_id_duplicate", "B-MANGA コマID重複の予防・治癒監査", "test/blender_coma_id_duplicate_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_layer_detail_mask", "B-MANGA 詳細設定/マスク契約監査", "test/blender_layer_detail_and_mask_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_layer_lock", "B-MANGA レイヤーロック監査", "test/blender_layer_lock_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_detail_dialog_unification", "B-MANGA 詳細設定共通契約監査", "test/blender_detail_dialog_unification_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_detail_content_conversion", "B-MANGA 個別レイヤー変換監査", "test/blender_detail_dialog_content_conversion_check.py", "B-MANGA", ("standard", "persistence")),
        AuditCase("bmanga_detail_migration_ownership", "B-MANGA 旧作品変換の所有権監査", "test/blender_detail_migration_worker_ownership_check.py", "B-MANGA", ("standard", "persistence")),
        AuditCase("bmanga_native_stale_save", "B-MANGA 複数画面保存保護監査", "test/blender_native_stale_save_guard_check.py", "B-MANGA", ("standard", "persistence")),
        AuditCase("bmanga_detail_data_migration", "B-MANGA 80ページ全件変換・復旧監査", "test/blender_detail_dialog_data_migration_check.py", "B-MANGA", ("full", "persistence", "slow"), slow=True),
        AuditCase("bmanga_detail_dialog_width_visual", "B-MANGA 詳細設定の固定幅目視監査", "test/blender_detail_dialog_width_visual_check.py", "B-MANGA", ("full", "visual"), "BMANGA_DETAIL_DIALOG_WIDTH_VISUAL_OUT", background=False, requires_ui=True),
        AuditCase("bmanga_text_ime_runtime", "B-MANGA テキスト入力/IME/キャレット監査", "test/blender_text_ime_runtime_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_text_autofit", "B-MANGA テキスト初期枠/自動フィット監査", "test/blender_text_autofit_perf_check.py", "B-MANGA", ("standard", "regression")),
        AuditCase("bmanga_text_request_visual", "B-MANGA テキスト入力仕様の目視監査", "test/blender_text_request_visual_audit_check.py", "B-MANGA", ("standard", "visual"), "BMANGA_TEXT_REQUEST_VISUAL_AUDIT_OUT"),
        AuditCase("bmanga_real_object_safety", "B-MANGA 実オブジェクト安全監査", "test/blender_real_object_safety_check.py", "B-MANGA", ("standard", "persistence")),
        AuditCase("bmanga_tool_behavior", "B-MANGA ツール操作AI目視監査", "test/blender_tool_behavior_visual_audit.py", "B-MANGA", ("full", "visual"), "BMANGA_TOOL_VISUAL_OUT", background=False, requires_ui=True),
        AuditCase("render_split", "B-MANGA Render 分離/登録監査", "test/blender_b_manga_render_split_check.py", "B-MANGA Render", ("standard", "regression")),
        AuditCase("render_ui", "B-MANGA Render UI/カード設定監査", "test/blender_b_manga_render_ui_audit.py", "B-MANGA Render", ("standard", "visual"), "BMANGA_RENDER_UI_AUDIT_OUT"),
        AuditCase("render_c00_audit", "B-MANGA Render c00連動構造監査", "test/blender_b_manga_render_c00_audit.py", "B-MANGA Render", ("full", "c00"), requires_c00=True),
        AuditCase("render_c00_execution", "B-MANGA Render c00全プリセット実行準備監査", "test/blender_b_manga_render_c00_execution_check.py", "B-MANGA Render", ("full", "c00"), requires_c00=True, requires_eevr=True),
        AuditCase("render_c00_full_flow", "B-MANGA Render c00完全連動監査", "test/blender_b_manga_render_c00_full_flow_check.py", "B-MANGA Render", ("full", "c00"), "BMANGA_RENDER_FULL_FLOW_OUT", requires_c00=True),
        AuditCase("render_c00_output_range", "B-MANGA Render c00出力範囲往復監査", "test/blender_b_manga_render_c00_output_range_roundtrip_check.py", "B-MANGA Render", ("full", "c00"), requires_c00=True),
        AuditCase("render_visual_presets", "B-MANGA Render 全プリセット実レンダー目視監査", "test/blender_b_manga_render_visual_presets.py", "B-MANGA Render", ("full", "visual", "slow"), "BMANGA_RENDER_VISUAL_OUT", requires_c00=True, slow=True),
    ]


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _is_script_file(path: Path) -> bool:
    if path.suffix != ".py":
        return False
    return not any(part in SKIP_DIR_NAMES for part in path.parts)


def _script_files() -> list[Path]:
    return sorted(path for path in ROOT.rglob("*.py") if _is_script_file(path))


def _literal_assignments(node: ast.ClassDef) -> dict[str, str]:
    values: dict[str, str] = {}
    for child in node.body:
        if not isinstance(child, ast.Assign):
            continue
        for target in child.targets:
            if isinstance(target, ast.Name) and isinstance(child.value, ast.Constant):
                values[target.id] = str(child.value.value)
    return values


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _analyze_ast(path: Path, text: str) -> dict[str, Any]:
    tree = ast.parse(text, filename=str(path))
    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = int(getattr(node, "end_lineno", node.lineno) - node.lineno + 1)
            functions.append({"name": node.name, "line": node.lineno, "lines": length})
        elif isinstance(node, ast.ClassDef):
            assigns = _literal_assignments(node)
            classes.append(
                {
                    "name": node.name,
                    "line": node.lineno,
                    "bases": [_call_name(base) for base in node.bases],
                    "bl_idname": assigns.get("bl_idname", ""),
                    "bl_label": assigns.get("bl_label", ""),
                }
            )
        elif isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return {"functions": functions, "classes": classes, "imports": sorted(imports)}


def _inventory_one(path: Path) -> dict[str, Any]:
    rel = _relative(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    item: dict[str, Any] = {
        "path": rel,
        "package": "B-MANGA Render" if rel.startswith("addons/b_manga_render/") else "B-MANGA",
        "line_count": len(lines),
        "risk_flags": [],
    }
    try:
        item.update(_analyze_ast(path, text))
    except SyntaxError as exc:
        item["syntax_error"] = f"{exc.msg} at line {exc.lineno}"
    if item["line_count"] > 1000:
        item["risk_flags"].append("1000行超")
    long_functions = [fn for fn in item.get("functions", []) if int(fn["lines"]) > 50]
    if long_functions:
        item["risk_flags"].append("50行超の関数あり")
        item["long_functions"] = long_functions
    return item


def _write_inventory(out_dir: Path) -> dict[str, Any]:
    scripts = [_inventory_one(path) for path in _script_files()]
    by_package = {"B-MANGA": 0, "B-MANGA Render": 0}
    for item in scripts:
        by_package[item["package"]] = by_package.get(item["package"], 0) + 1
    payload = {"script_count": len(scripts), "by_package": by_package, "scripts": scripts}
    inv_dir = out_dir / "inventory"
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "script_inventory.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_inventory_markdown(inv_dir / "script_inventory.md", payload)
    _write_code_review_batches(inv_dir / "code_review_batches.md", scripts)
    return payload


def _write_inventory_markdown(path: Path, payload: dict[str, Any]) -> None:
    rows = ["# B-MANGA / B-MANGA Render スクリプト棚卸し", ""]
    rows.append(f"- スクリプト数: {payload['script_count']}")
    rows.append(f"- B-MANGA: {payload['by_package'].get('B-MANGA', 0)}")
    rows.append(f"- B-MANGA Render: {payload['by_package'].get('B-MANGA Render', 0)}")
    rows.append("")
    rows.append("| 対象 | 行数 | クラス | 関数 | 注意 |")
    rows.append("|---|---:|---:|---:|---|")
    for item in payload["scripts"]:
        flags = ", ".join(item.get("risk_flags", []))
        rows.append(
            f"| `{item['path']}` | {item['line_count']} | "
            f"{len(item.get('classes', []))} | {len(item.get('functions', []))} | {flags} |"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_code_review_batches(path: Path, scripts: list[dict[str, Any]]) -> None:
    rows = ["# AIコード監査バッチ", ""]
    rows.append("各バッチを別AIに渡し、実際に全行を読んでユーザー操作に影響する問題だけを報告させる。")
    for index in range(0, len(scripts), 4):
        rows.append("")
        rows.append(f"## Batch {index // 4 + 1:03d}")
        rows.append("指示: 次のファイルの全行を読み、バグ・不整合・未処理エラー・エッジケース・データ不整合だけを報告してください。")
        for item in scripts[index:index + 4]:
            rows.append(f"- `{item['path']}`")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _syntax_check(scripts: list[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    for item in scripts:
        path = ROOT / item["path"]
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            errors.append({"path": item["path"], "error": str(exc)})
    payload = {"ok": not errors, "checked": len(scripts), "errors": errors}
    (out_dir / "syntax_check.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _select_cases(profile: str, only: set[str], include_slow: bool) -> list[AuditCase]:
    if only:
        return [case for case in _audit_cases() if case.key in only]
    selected: list[AuditCase] = []
    for case in _audit_cases():
        if profile == "inventory":
            continue
        if profile == "render" and case.target != "B-MANGA Render":
            continue
        elif profile == "visual" and "visual" not in case.tags:
            continue
        elif profile == "standard" and "standard" not in case.tags:
            continue
        if case.slow and not include_slow:
            continue
        selected.append(case)
    return selected


def _skip_reason(case: AuditCase, args: argparse.Namespace) -> str:
    if case.requires_ui and not args.allow_ui:
        return "UI画面が必要なため --allow-ui 未指定ではスキップ"
    if case.requires_c00 and not Path(args.c00_blend).exists():
        return f"c00.blend が見つからない: {args.c00_blend}"
    if case.requires_eevr and not Path(args.eevr_zip).exists():
        return f"eeVR zip が見つからない: {args.eevr_zip}"
    if not Path(args.blender).exists():
        return f"Blender が見つからない: {args.blender}"
    return ""


def _output_failure_reason(stdout: str, stderr: str) -> str:
    combined = f"{stdout}\n{stderr}"
    failure_markers = (
        "Traceback (most recent call last):",
        "AssertionError:",
        "RuntimeError:",
        "Error: Python:",
    )
    for marker in failure_markers:
        if marker in combined:
            return f"Blender exited without a failing return code but output contains {marker}"
    return ""


def _run_case(case: AuditCase, args: argparse.Namespace, out_dir: Path) -> dict[str, Any]:
    case_dir = out_dir / "cases" / case.key
    case_dir.mkdir(parents=True, exist_ok=True)
    reason = _skip_reason(case, args)
    if reason:
        return {"key": case.key, "title": case.title, "status": "skipped", "reason": reason}

    env = os.environ.copy()
    env["BMANGA_C00_BLEND"] = str(args.c00_blend)
    env["BMANGA_EEVR_ZIP"] = str(args.eevr_zip)
    env["BMANGA_AI_AUDIT_CASE_OUT"] = str(case_dir)
    if case.out_env:
        env[case.out_env] = str(case_dir / "evidence")
    cmd = [str(args.blender), "--factory-startup"]
    if case.background:
        cmd.append("--background")
    cmd.extend(["--python", str(ROOT / case.script)])
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.perf_counter() - started, 3)
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        (case_dir / "stdout.txt").write_text(str(stdout), encoding="utf-8", errors="replace")
        (case_dir / "stderr.txt").write_text(str(stderr), encoding="utf-8", errors="replace")
        return {"key": case.key, "title": case.title, "status": "failed", "reason": "timeout", "seconds": elapsed}
    elapsed = round(time.perf_counter() - started, 3)
    (case_dir / "stdout.txt").write_text(completed.stdout or "", encoding="utf-8", errors="replace")
    (case_dir / "stderr.txt").write_text(completed.stderr or "", encoding="utf-8", errors="replace")
    output_failure = _output_failure_reason(completed.stdout or "", completed.stderr or "")
    status = "passed" if completed.returncode == 0 and not output_failure else "failed"
    result = {
        "key": case.key,
        "title": case.title,
        "target": case.target,
        "status": status,
        "returncode": completed.returncode,
        "seconds": elapsed,
        "command": cmd,
        "case_dir": _relative(case_dir),
    }
    if output_failure:
        result["reason"] = output_failure
    return result


def _collect_artifacts(out_dir: Path) -> list[str]:
    suffixes = {".json", ".md", ".png", ".jpg", ".jpeg", ".svg", ".txt"}
    paths = [path for path in out_dir.rglob("*") if path.is_file() and path.suffix.lower() in suffixes]
    return [_relative(path) for path in sorted(paths)]


def _image_font(size: int):
    from PIL import ImageFont

    for font_path in (
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ):
        try:
            if Path(font_path).is_file():
                return ImageFont.truetype(font_path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _postprocess_render_visual(out_dir: Path) -> list[str]:
    json_path = out_dir / "cases" / "render_visual_presets" / "evidence" / "b_manga_render_visual_presets.json"
    if not json_path.exists():
        return []
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return []
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    presets = list(payload.get("presets", []))
    width = 1640
    row_h = 124
    height = 110 + row_h * max(1, len(presets))
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    font = _image_font(12)
    title_font = _image_font(18)
    draw.text((24, 18), "B-MANGA Render 全プリセット 実レンダー AI目視シート", fill=(0, 0, 0), font=title_font)
    draw.text((24, 50), f"プリセット: {len(presets)} / JSON: {json_path.name}", fill=(0, 0, 0), font=font)
    _draw_render_rows(draw, sheet, presets, json_path.parent, row_h, font)
    image_path = json_path.with_name("b_manga_render_visual_presets_contact.png")
    sheet.save(image_path)
    return [_relative(image_path)]


def _draw_render_rows(draw, sheet, presets: list[dict[str, Any]], base_dir: Path, row_h: int, font) -> None:
    thumb_w = 96
    thumb_h = 96
    y = 86
    for index, item in enumerate(presets):
        has_error = bool(item.get("errors"))
        fill = (236, 249, 236) if not has_error else (255, 238, 224)
        draw.rectangle((20, y, sheet.width - 20, y + row_h - 8), fill=fill, outline=(180, 190, 180))
        draw.text((34, y + 10), f"{index + 1:02d}. {item.get('preset', '')}", fill=(0, 0, 0), font=font)
        draw.text((34, y + 32), f"レンダー {len(item.get('renders', []))}", fill=(0, 0, 0), font=font)
        if has_error:
            draw.text((34, y + 54), " / ".join(item.get("errors", []))[:130], fill=(150, 50, 0), font=font)
        for thumb_index, render in enumerate(item.get("renders", [])[:10]):
            _paste_thumb(draw, sheet, base_dir, render, 360 + thumb_index * (thumb_w + 18), y + 10, font)
        y += row_h


def _paste_thumb(draw, sheet, base_dir: Path, render: dict[str, Any], x: int, y: int, font) -> None:
    from PIL import Image

    thumb_w = 96
    thumb_h = 96
    raw_path = Path(str(render.get("path", "")))
    if raw_path.is_absolute() or raw_path.exists():
        image_path = raw_path
    else:
        image_path = base_dir / raw_path
    try:
        with Image.open(image_path) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((thumb_w, thumb_h))
            sheet.paste(thumb, (x, y))
    except Exception:
        draw.rectangle((x, y, x + thumb_w, y + thumb_h), outline=(180, 0, 0))
        draw.text((x + 5, y + 42), "画像不可", fill=(180, 0, 0), font=font)
    label = f"{render.get('kind', '')}:{render.get('engine', '')}"
    if render.get("blank"):
        label += " blank"
    draw.text((x, y + thumb_h), label[:18], fill=(0, 0, 0), font=font)


def _write_manifest(out_dir: Path, cases: list[AuditCase], results: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "coverage_axes": [
            "仕様/設計文書",
            "全スクリプト棚卸し",
            "UI項目棚卸し",
            "ページ/コマ/レイヤー表示",
            "保存/再読み込み/修復",
            "B-MANGA Render UI",
            "B-MANGA Render プリセット/出力",
            "AI目視用画像",
        ],
        "cases": [case.__dict__ for case in cases],
        "results": results,
        "artifacts": _collect_artifacts(out_dir),
    }
    (out_dir / "audit_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _write_ai_prompt(out_dir: Path, manifest: dict[str, Any]) -> Path:
    rows = [
        "# AI監査依頼プロンプト",
        "",
        "あなたはB-MANGA / B-MANGA Renderの監査担当です。次の証拠パックを読み、ユーザー操作で起きる実害だけを報告してください。",
        "",
        "## 必ず見るファイル",
        "- `audit_manifest.json`",
        "- `summary.json`",
        "- `inventory/script_inventory.md`",
        "- `inventory/code_review_batches.md`",
        "",
        "## 目視確認の観点",
        "- ページ、コマ、コマ枠、テキスト、フキダシ、効果線、ラスター、画像が消えていないか",
        "- 表示順、マスク、透明度、選択状態、詳細設定の切り替えが破綻していないか",
        "- B-MANGA本体とB-MANGA Renderの責務が混ざっていないか",
        "- B-MANGA Renderのプリセット、カード、出力画像が欠けたり空画像になっていないか",
        "",
        "## 報告形式",
        "重要度: 高/中/低",
        "対象: B-MANGA または B-MANGA Render",
        "問題: 具体的な症状",
        "根拠: 確認した画像/JSON/ログ",
        "影響: ユーザーにどう見えるか",
        "修正方針: 最小限の方針",
        "",
        "## 画像/証拠一覧",
    ]
    for artifact in manifest.get("artifacts", []):
        if artifact.lower().endswith((".png", ".jpg", ".jpeg", ".svg", ".json")):
            rows.append(f"- `{artifact}`")
    path = out_dir / "AI_REVIEW_PROMPT.md"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="B-MANGA / B-MANGA Render AI監査証拠パック生成")
    parser.add_argument("--profile", choices=("standard", "full", "visual", "render", "inventory"), default="standard")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--blender", default=str(DEFAULT_BLENDER))
    parser.add_argument("--c00-blend", default=str(DEFAULT_C00))
    parser.add_argument("--eevr-zip", default=str(DEFAULT_EEVR))
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--include-slow", action="store_true")
    parser.add_argument("--allow-ui", action="store_true")
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--only", default="", help="カンマ区切りのcase key")
    parser.add_argument("--list", action="store_true")
    return parser.parse_args()


def _out_dir(args: argparse.Namespace) -> Path:
    if args.out_dir:
        return Path(args.out_dir)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / ".codex" / "ai_audit" / stamp


def main() -> int:
    args = _parse_args()
    only = {item.strip() for item in args.only.split(",") if item.strip()}
    cases = _select_cases(args.profile, only, args.include_slow)
    if args.list:
        for case in _audit_cases():
            print(f"{case.key}\t{case.target}\t{','.join(case.tags)}\t{case.title}")
        return 0

    out_dir = _out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory = _write_inventory(out_dir)
    syntax = _syntax_check(inventory["scripts"], out_dir)
    results: list[dict[str, Any]] = []
    for case in cases:
        result = _run_case(case, args, out_dir)
        results.append(result)
        print(f"{result['status'].upper()}: {case.key} {result.get('seconds', '')}")
        if result["status"] == "failed" and not args.keep_going:
            break
    postprocessed = _postprocess_render_visual(out_dir)

    summary = {
        "profile": args.profile,
        "out_dir": str(out_dir),
        "syntax": syntax,
        "results": results,
        "postprocessed": postprocessed,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = _write_manifest(out_dir, cases, results)
    prompt_path = _write_ai_prompt(out_dir, manifest)
    failed = [result for result in results if result["status"] == "failed"]
    print(f"BMANGA_AI_AUDIT_READY out={out_dir} prompt={prompt_path} failed={len(failed)}")
    return 1 if failed or not syntax["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
