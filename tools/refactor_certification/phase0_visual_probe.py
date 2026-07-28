"""Phase 0のGPU screenshotとJPEG比較閾値を実測固定する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from .image_metrics import compare_images


DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=180)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture_once(args: argparse.Namespace, root: Path, run: int) -> Path:
    script = root / "test" / "blender_page_file_preview_visual_check.py"
    command = [
        str(args.blender),
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(script),
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=args.timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    log_prefix = args.out / "gpu_logs" / f"run_{run:02d}"
    log_prefix.parent.mkdir(parents=True, exist_ok=True)
    log_prefix.with_suffix(".stdout.txt").write_text(
        completed.stdout,
        encoding="utf-8",
    )
    log_prefix.with_suffix(".stderr.txt").write_text(
        completed.stderr,
        encoding="utf-8",
    )
    source = root / ".codex" / "visual" / "page_file_preview_visual_check"
    source /= "page_preview_screen.png"
    if (
        completed.returncode != 0
        or "BMANGA_PAGE_FILE_PREVIEW_VISUAL_OK" not in completed.stdout
        or not source.is_file()
    ):
        raise RuntimeError(f"GPU screenshot run {run} failed")
    target = args.out / "gpu" / f"page_preview_{run:02d}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def _thresholds(comparisons: list[dict[str, object]]) -> dict[str, object]:
    min_ssim = min(float(item["ssim"]) for item in comparisons)
    max_delta = max(int(item["max_color_delta"]) for item in comparisons)
    return {
        "minimum_ssim": round(max(0.99, math.floor(min_ssim * 10000) / 10000 - 0.0001), 4),
        "maximum_color_delta": min(255, max_delta + 2),
    }


def _gpu_results(args: argparse.Namespace, root: Path) -> dict[str, object]:
    captures = [_capture_once(args, root, run) for run in range(args.runs)]
    golden = captures[0]
    comparisons = [
        {"run": run, **compare_images(golden, path)}
        for run, path in enumerate(captures[1:], start=1)
    ]
    return {
        "golden_sha256": _sha256(golden),
        "capture_count": len(captures),
        "comparisons": comparisons,
        "thresholds": _thresholds(comparisons),
    }


def _jpeg_results(args: argparse.Namespace) -> dict[str, object]:
    product_dir = args.out / "product_jpeg"
    metadata_path = product_dir / "product_jpeg_metadata.json"
    if not metadata_path.is_file():
        raise RuntimeError("製品JPEG基準がありません。Blender製品経路検査を先に実行してください")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    source = product_dir / "product_source.png"
    paths = sorted(product_dir.glob("product_page_q95_*.jpg"))
    if len(paths) != args.runs:
        raise RuntimeError(f"製品JPEG数が不正です: {len(paths)} != {args.runs}")
    golden = paths[0]
    comparisons = [
        {"run": run, **compare_images(golden, path)}
        for run, path in enumerate(paths[1:], start=1)
    ]
    source_quality = compare_images(source, golden)
    min_ssim = min(float(item["ssim"]) for item in comparisons)
    min_psnr = min(float(item["psnr_db"]) for item in comparisons)
    return {
        "producer": metadata["producer"],
        "quality": 95,
        "requested_dpi": metadata["requested_dpi"],
        "independent_reader": metadata,
        "golden_sha256": _sha256(golden),
        "source_quality": source_quality,
        "comparisons": comparisons,
        "thresholds": {
            "minimum_ssim_to_same_encoder_golden": round(
                max(0.99, min_ssim - 0.00001),
                5,
            ),
            "minimum_psnr_db_to_same_encoder_golden": round(
                max(40.0, min_psnr - 1.0),
                2,
            ),
        },
    }


def main() -> int:
    args = _arguments()
    root = args.root.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    gpu = _gpu_results(args, root)
    payload = {
        "schema_version": 1,
        "reference_status": "Phase 0 frozen-current reference; user visual approval pending",
        "gpu": gpu,
        "jpeg": _jpeg_results(args),
    }
    output = args.out / "visual_thresholds.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"PHASE0_VISUAL_PROBE_OK {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
