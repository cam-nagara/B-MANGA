from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_DIR = ROOT / "_verify" / "2026-07-06_bml_width_all_shapes_strict"

LINE_TYPE_FILES = {
    "outline": "outline_only.png",
    "inner": "inner_only.png",
    "intersection": "intersection_only.png",
    "selection": "selection_only.png",
}
LINE_TYPE_LABELS = {
    "outline": "アウトライン",
    "inner": "稜谷線",
    "intersection": "交差線",
    "selection": "選択線",
}

LINE_WIDTH_MM = 0.3
DPI = 600
EXPECTED_PX = LINE_WIDTH_MM * DPI / 25.4
EXPECTED_ROUNDED_PX = round(EXPECTED_PX)
MIN_OK_PX = EXPECTED_ROUNDED_PX - 2
MAX_OK_PX = EXPECTED_ROUNDED_PX + 2


def _mask_for_line_type(rgb: np.ndarray, line_type: str) -> np.ndarray:
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    if line_type == "outline":
        return (r < 128) & (g < 128) & (b < 128)
    if line_type == "inner":
        return (b > 120) & ((b - r) > 45) & ((b - g) > 20)
    if line_type == "intersection":
        return (g > 80) & ((g - r) > 10) & ((g - b) > 10)
    if line_type == "selection":
        return (r > 120) & (b > 120) & (g < 170) & ((r - g) > 20) & ((b - g) > 20)
    raise ValueError(line_type)


def _contiguous_run_lengths(rows) -> list[int]:
    lengths: list[int] = []
    for row in rows:
        idx = np.flatnonzero(row)
        if idx.size == 0:
            continue
        gaps = np.where(np.diff(idx) > 1)[0]
        starts = np.r_[idx[0], idx[gaps + 1]]
        ends = np.r_[idx[gaps], idx[-1]]
        for start, end in zip(starts, ends):
            length = int(end - start + 1)
            if 2 <= length <= 20:
                lengths.append(length)
    return lengths


def _directional_run_lengths(mask: np.ndarray) -> dict[str, list[int]]:
    diagonal_rows = [
        np.diagonal(mask, offset=offset)
        for offset in range(-mask.shape[0] + 1, mask.shape[1])
    ]
    anti_diagonal_source = np.fliplr(mask)
    anti_diagonal_rows = [
        np.diagonal(anti_diagonal_source, offset=offset)
        for offset in range(-anti_diagonal_source.shape[0] + 1, anti_diagonal_source.shape[1])
    ]
    return {
        "horizontal": _contiguous_run_lengths(mask),
        "vertical": _contiguous_run_lengths(mask.T),
        "diagonal": _contiguous_run_lengths(diagonal_rows),
        "anti_diagonal": _contiguous_run_lengths(anti_diagonal_rows),
    }


def _edt_1d(values: np.ndarray) -> np.ndarray:
    count = values.shape[0]
    distance = np.empty(count, dtype=np.float64)
    centers = np.zeros(count, dtype=np.int32)
    bounds = np.empty(count + 1, dtype=np.float64)
    segment = 0
    centers[0] = 0
    bounds[0] = -np.inf
    bounds[1] = np.inf

    for q in range(1, count):
        while True:
            p = centers[segment]
            s = ((values[q] + q * q) - (values[p] + p * p)) / (2 * q - 2 * p)
            if s > bounds[segment]:
                break
            segment -= 1
        segment += 1
        centers[segment] = q
        bounds[segment] = s
        bounds[segment + 1] = np.inf

    segment = 0
    for q in range(count):
        while bounds[segment + 1] < q:
            segment += 1
        p = centers[segment]
        distance[q] = (q - p) * (q - p) + values[p]
    return distance


def _distance_to_background(mask: np.ndarray) -> np.ndarray:
    inf = 1.0e9
    source = np.where(mask, inf, 0.0).astype(np.float64)
    tmp = np.empty_like(source)
    for x in range(source.shape[1]):
        tmp[:, x] = _edt_1d(source[:, x])
    dist2 = np.empty_like(source)
    for y in range(source.shape[0]):
        dist2[y, :] = _edt_1d(tmp[y, :])
    dist = np.sqrt(dist2)
    dist[~mask] = 0.0
    return dist


def _ridge_widths(mask: np.ndarray) -> list[float]:
    if not mask.any():
        return []
    dist = _distance_to_background(mask)
    ridge = dist > 0.0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            shifted = np.zeros_like(dist)
            src_y0 = max(0, -dy)
            src_y1 = dist.shape[0] - max(0, dy)
            src_x0 = max(0, -dx)
            src_x1 = dist.shape[1] - max(0, dx)
            dst_y0 = max(0, dy)
            dst_y1 = dist.shape[0] - max(0, -dy)
            dst_x0 = max(0, dx)
            dst_x1 = dist.shape[1] - max(0, -dx)
            shifted[dst_y0:dst_y1, dst_x0:dst_x1] = dist[src_y0:src_y1, src_x0:src_x1]
            ridge &= dist >= shifted
    widths = 2.0 * dist[ridge]
    widths = widths[(widths >= 2.0) & (widths <= 20.0)]
    return widths.tolist()


def _representative_width(widths: list[float]) -> dict[str, object]:
    if not widths:
        return {
            "sample_count": 0,
            "mode_px": None,
            "median_px": None,
            "p10_px": None,
            "p90_px": None,
            "status": "no_sample",
        }
    values = np.array(widths, dtype=np.float64)
    focused = values[(values >= 4) & (values <= 12)]
    if focused.size:
        values = focused
    rounded = np.rint(values).astype(np.int32)
    counts = np.bincount(rounded)
    mode_px = int(np.argmax(counts))
    median_px = float(np.median(values))
    representative_px = int(round(median_px))
    status = "pass" if MIN_OK_PX <= representative_px <= MAX_OK_PX else "fail"
    return {
        "sample_count": int(values.size),
        "mode_px": mode_px,
        "median_px": median_px,
        "representative_px": representative_px,
        "p10_px": float(np.percentile(values, 10)),
        "p90_px": float(np.percentile(values, 90)),
        "status": status,
    }


def _analyze_crop(image: Image.Image, line_type: str, crop_px: list[int]) -> dict[str, object]:
    x0, y0, x1, y1 = crop_px
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    rgb = np.array(crop)
    mask = _mask_for_line_type(rgb, line_type)
    direction_lengths = _directional_run_lengths(mask)
    direction_results = {
        direction: _representative_width(lengths)
        for direction, lengths in direction_lengths.items()
    }
    candidates = [
        (direction, result)
        for direction, result in direction_results.items()
        if result["sample_count"] >= 20 and result["median_px"] is not None
    ]
    if candidates:
        direction, result = min(
            candidates,
            key=lambda item: abs(float(item[1]["median_px"]) - EXPECTED_PX),
        )
        result = dict(result)
        result["selected_direction"] = direction
    else:
        result = _representative_width([])
        result["selected_direction"] = None
    result["method"] = "directional_runs"
    result["direction_medians"] = {
        direction: value["median_px"]
        for direction, value in direction_results.items()
    }
    result["direction_samples"] = {
        direction: value["sample_count"]
        for direction, value in direction_results.items()
    }
    result["mask_pixels"] = int(mask.sum())
    result["crop_px"] = crop_px
    return result


def analyze(audit_dir: Path) -> dict[str, object]:
    metrics_path = audit_dir / "all_shapes_width_audit.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    images = {
        line_type: Image.open(audit_dir / filename)
        for line_type, filename in LINE_TYPE_FILES.items()
    }

    rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for pair in metrics["pair_results"]:
        for line_type, image in images.items():
            result = _analyze_crop(image, line_type, pair["crop_px"])
            row = {
                "index": pair["index"],
                "shapes": " x ".join(pair["shapes"]),
                "line_type": line_type,
                "line_type_label": LINE_TYPE_LABELS[line_type],
                "expected_px": round(EXPECTED_PX, 3),
                **result,
            }
            rows.append(row)
            if result["status"] != "pass":
                failures.append(row)

    return {
        "expected_px": round(EXPECTED_PX, 3),
        "expected_rounded_px": EXPECTED_ROUNDED_PX,
        "accepted_px_range": [MIN_OK_PX, MAX_OK_PX],
        "row_count": len(rows),
        "failures": failures,
        "rows": rows,
    }


def write_outputs(audit_dir: Path, result: dict[str, object]) -> None:
    json_path = audit_dir / "pixel_width_audit.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = audit_dir / "pixel_width_audit.csv"
    fieldnames = [
        "index",
        "shapes",
        "line_type_label",
        "expected_px",
        "mode_px",
        "median_px",
        "representative_px",
        "p10_px",
        "p90_px",
        "sample_count",
        "mask_pixels",
        "method",
        "selected_direction",
        "status",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in result["rows"]:
            writer.writerow({field: row.get(field) for field in fieldnames})


def main(argv: list[str]) -> int:
    audit_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_AUDIT_DIR
    result = analyze(audit_dir)
    write_outputs(audit_dir, result)
    failures = result["failures"]
    print(
        f"expected={result['expected_px']}px accepted={result['accepted_px_range']} "
        f"rows={result['row_count']} failures={len(failures)}"
    )
    for failure in failures[:20]:
        print(
            f"FAIL {failure['index']:02d} {failure['shapes']} "
            f"{failure['line_type_label']} representative={failure['representative_px']} "
            f"mode={failure['mode_px']} median={failure['median_px']} "
            f"samples={failure['sample_count']}"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
