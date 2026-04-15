"""Generate Mondrian mask images for the backward masking task (Task 03).

Produces 100 unique 256x256 PNG Mondrian masks in
``src/tasks/03_backward_masking/stimuli/masks/``. Each mask is filled with
100-200 randomly positioned and sized colored rectangles on a medium-gray
background, providing high spatial-frequency masking for face stimuli.

Run with:

    uv run python scripts/generate_mondrians.py

Deterministic: seeded with the mask index so regenerating produces
byte-identical output.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "src" / "tasks" / "03_backward_masking" / "stimuli" / "masks"

N_MASKS = 100
MASK_SIZE_PX = 256
MIN_RECTS = 100
MAX_RECTS = 200
# Rectangle size range as a fraction of the canvas
MIN_RECT_FRAC = 0.05
MAX_RECT_FRAC = 0.30
BG_COLOR = (128, 128, 128)


def _random_color(rng: random.Random) -> tuple[int, int, int]:
    """Return a saturated random RGB color."""
    return (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))


def generate_mondrian(index: int, size: int = MASK_SIZE_PX) -> Image.Image:
    """Generate one Mondrian mask as a PIL Image, seeded by *index*."""
    rng = random.Random(index)
    img = Image.new("RGB", (size, size), BG_COLOR)
    draw = ImageDraw.Draw(img)

    n_rects = rng.randint(MIN_RECTS, MAX_RECTS)
    for _ in range(n_rects):
        w = rng.randint(int(size * MIN_RECT_FRAC), int(size * MAX_RECT_FRAC))
        h = rng.randint(int(size * MIN_RECT_FRAC), int(size * MAX_RECT_FRAC))
        x = rng.randint(-w // 4, size - (3 * w) // 4)
        y = rng.randint(-h // 4, size - (3 * h) // 4)
        color = _random_color(rng)
        draw.rectangle([x, y, x + w, y + h], fill=color)

    return img


def main() -> None:
    """Generate and write all masks."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Generating {N_MASKS} Mondrian masks -> {OUTPUT_DIR}")
    for i in range(N_MASKS):
        img = generate_mondrian(i)
        out_path = OUTPUT_DIR / f"mondrian_{i:03d}.png"
        img.save(out_path, "PNG")
    print(f"Done. {N_MASKS} masks written.")


if __name__ == "__main__":
    main()
