from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.refactor_certification.image_metrics import compare_images
from tools.refactor_certification.phase0_performance_probe import _percentile


def _save(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (32, 32), color).save(path)


def test_identical_images_have_exact_metrics(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _save(first, (40, 80, 120))
    _save(second, (40, 80, 120))
    result = compare_images(first, second)
    assert result["ssim"] == 1.0
    assert result["psnr_db"] == 999.0
    assert result["max_color_delta"] == 0
    assert result["mean_absolute_error"] == 0.0


def test_changed_image_reports_nonzero_delta(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _save(first, (40, 80, 120))
    _save(second, (41, 80, 120))
    result = compare_images(first, second)
    assert 0.0 < result["ssim"] < 1.0
    assert result["psnr_db"] < 999.0
    assert result["max_color_delta"] == 1
    assert result["mean_absolute_error"] > 0.0


def test_nearest_rank_p95_keeps_maximum_for_nine_samples() -> None:
    values = [float(value) for value in range(1, 10)]
    assert _percentile(values, 0.95) == 9.0
