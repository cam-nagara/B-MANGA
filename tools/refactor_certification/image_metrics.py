"""Visual golden認定で使う画像差分指標。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter


def _rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float64)


def _ssim_channel(first: np.ndarray, second: np.ndarray) -> float:
    first_mean = gaussian_filter(first, sigma=1.5)
    second_mean = gaussian_filter(second, sigma=1.5)
    first_var = gaussian_filter(first * first, sigma=1.5) - first_mean**2
    second_var = gaussian_filter(second * second, sigma=1.5) - second_mean**2
    covariance = gaussian_filter(first * second, sigma=1.5) - first_mean * second_mean
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    numerator = (2.0 * first_mean * second_mean + c1) * (2.0 * covariance + c2)
    denominator = (first_mean**2 + second_mean**2 + c1) * (
        first_var + second_var + c2
    )
    return float(np.mean(numerator / denominator))


def compare_images(first_path: Path, second_path: Path) -> dict[str, Any]:
    first = _rgb(first_path)
    second = _rgb(second_path)
    if first.shape != second.shape:
        raise ValueError(f"image size mismatch: {first.shape} != {second.shape}")
    delta = np.abs(first - second)
    mse = float(np.mean((first - second) ** 2))
    psnr = 999.0 if mse == 0.0 else 20.0 * math.log10(255.0 / math.sqrt(mse))
    return {
        "ssim": round(
            sum(
                _ssim_channel(first[:, :, channel], second[:, :, channel])
                for channel in range(3)
            )
            / 3.0,
            8,
        ),
        "psnr_db": round(psnr, 4),
        "max_color_delta": int(delta.max()),
        "mean_absolute_error": round(float(delta.mean()), 6),
    }
