"""
Blur detection: variance of the Laplacian on the grayscale image.
Sharp text gives high variance; a blurred scan gives low variance.
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def laplacian_variance(image_path: str) -> float:
    gray = np.asarray(Image.open(image_path).convert("L"), dtype=float)
    lap = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1]
           + gray[1:-1, :-2] + gray[1:-1, 2:])
    return float(lap.var())
