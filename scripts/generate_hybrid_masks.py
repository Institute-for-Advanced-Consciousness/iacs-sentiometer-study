"""Generate hybrid (Mondrian + scrambled-face composite) masks for Task 03.

Diagnostic run T03_DIAG_20260417 showed the original Mondrian-only masks
are weak against face targets because Mondrians live in the low / high-SF
bands while face identity lives in the mid-SF band (8-16 cycles/face) —
faces "leak through" as an afterimage. This script composites a Mondrian
(low-SF coverage + strong luminance edges) with a block-scrambled KDEF
face (mid-SF face-band coverage, scrambled to destroy identity) to mask
both bands simultaneously.

Output: 100 unique 256x256 PNGs in
``src/tasks/03_backward_masking/stimuli/masks_hybrid/``. Does NOT touch
the original Mondrian bank at ``stimuli/masks/`` — both banks live side
by side and the active one is selected by ``task03_backward_masking.
mask_type`` in ``session_defaults.yaml``.

Run::

    uv run python scripts/generate_hybrid_masks.py

Deterministic: seeded by mask index, so regenerating is byte-identical.
"""

from __future__ import annotations

import random
from pathlib import Path

from generate_mondrians import generate_mondrian  # reuse Mondrian generator
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
FACES_DIR = REPO_ROOT / "src" / "tasks" / "03_backward_masking" / "stimuli" / "faces"
OUTPUT_DIR = REPO_ROOT / "src" / "tasks" / "03_backward_masking" / "stimuli" / "masks_hybrid"

N_MASKS = 100
MASK_SIZE_PX = 256
# 8x8 scrambling grid = 64 tiles. Tiles are ~32px on a 256px mask —
# fine-grained enough to destroy face identity while preserving local
# mid-SF contour content (the diagnostic piece that competes with
# face-processing channels).
SCRAMBLE_GRID = 8
# Alpha of the scrambled-face layer over the Mondrian base. 0.55 keeps
# the Mondrian dominant (preserving its strong luminance-edge energy)
# while still laying a clearly visible face-band texture on top. A pilot
# participant would tell us if this should shift — easy to tune later.
FACE_LAYER_ALPHA = 0.55


def _load_and_square(path: Path, size: int) -> Image.Image:
    """Load a KDEF face, crop to square, resize to *size*×*size*, grayscale."""
    img = Image.open(path).convert("L")
    w, h = img.size
    short = min(w, h)
    left = (w - short) // 2
    top = (h - short) // 2
    img = img.crop((left, top, left + short, top + short))
    return img.resize((size, size), Image.LANCZOS)


def scramble_face(face_img: Image.Image, grid: int, rng: random.Random) -> Image.Image:
    """Cut *face_img* into grid×grid tiles and shuffle their positions.

    Returns an RGB image of the same size. The tile size is
    ``face_img.size[0] // grid`` — any remainder at the right/bottom
    edge is dropped, which is fine since masks display centred on the
    fixation cross.
    """
    size = face_img.size[0]
    tile = size // grid
    tiles: list[Image.Image] = []
    for r in range(grid):
        for c in range(grid):
            box = (c * tile, r * tile, (c + 1) * tile, (r + 1) * tile)
            tiles.append(face_img.crop(box))
    rng.shuffle(tiles)
    scrambled = Image.new("L", (tile * grid, tile * grid))
    for idx, t in enumerate(tiles):
        r, c = divmod(idx, grid)
        scrambled.paste(t, (c * tile, r * tile))
    # Resize back up to the full mask size in case grid doesn't divide evenly
    return scrambled.resize((size, size), Image.LANCZOS).convert("RGB")


def generate_hybrid(index: int, face_paths: list[Path]) -> Image.Image:
    """Compose one hybrid mask: Mondrian base + scrambled-face overlay."""
    rng = random.Random(index)
    mondrian = generate_mondrian(index, size=MASK_SIZE_PX).convert("RGB")
    # Pick a face by index (deterministic) but scramble with the same rng
    # so two masks using the same source face look different.
    face_path = face_paths[index % len(face_paths)]
    face = _load_and_square(face_path, MASK_SIZE_PX)
    scrambled = scramble_face(face, SCRAMBLE_GRID, rng)
    return Image.blend(mondrian, scrambled, FACE_LAYER_ALPHA)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    face_paths = sorted(FACES_DIR.glob("*.png"))
    if not face_paths:
        raise SystemExit(
            f"No source faces found in {FACES_DIR}. "
            f"Ensure KDEF faces are committed."
        )
    print(
        f"Generating {N_MASKS} hybrid masks "
        f"(Mondrian + {SCRAMBLE_GRID}×{SCRAMBLE_GRID} scrambled KDEF "
        f"@ alpha={FACE_LAYER_ALPHA}) -> {OUTPUT_DIR}"
    )
    print(f"Source faces: {len(face_paths)} identities")
    for i in range(N_MASKS):
        img = generate_hybrid(i, face_paths)
        img.save(OUTPUT_DIR / f"hybrid_{i:03d}.png", "PNG")
    print(f"Done. {N_MASKS} masks written.")


if __name__ == "__main__":
    # Allow `import generate_mondrians` to work when invoked from anywhere.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
