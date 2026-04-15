# Task 03 Stimuli

This directory holds the visual stimuli used by the backward masking task.

## `faces/` — KDEF-cropped neutral faces

Neutral-expression face images from the **KDEF-cropped** set (Karolinska
Directed Emotional Faces, cropped to an oval mask over a uniform background).

- **Source / citation**: Dawel, A., Wright, L., Irons, J., Dumbleton, R.,
  Palermo, R., O'Kearney, R., & McKone, E. (2017). *Perceived emotion
  genuineness: Normative ratings for popular facial expression stimuli and the
  development of perceived-as-genuine and perceived-as-fake sets.* Behavior
  Research Methods, 49(4), 1539–1562. (Introduces KDEF-cropped.) Original
  KDEF: Lundqvist, D., Flykt, A., & Öhman, A. (1998). *The Karolinska
  Directed Emotional Faces – KDEF.* Department of Clinical Neuroscience,
  Psychology Section, Karolinska Institutet.
- **Filter criterion at runtime**: the task scans this directory for PNG
  files containing the substring `NE` in the filename (KDEF expression code
  for Neutral). All matched files are used.
- **Expected count**: 28 neutral identities (20 female, 8 male) per the
  KDEF-cropped neutral subset used in this study.
- **Minimum**: the task raises at startup if fewer than 10 neutral faces are
  found, so the stimulus set stays large enough to avoid per-identity
  learning effects.
- **Image format**: PNG. Images are already oval-cropped against a uniform
  background. The task resizes each to 256×256 at display time for
  consistency.
- **Use in this task**: Task 03 (Backward Masking / Face Detection) uses
  these faces as the target stimulus in a QUEST-controlled adaptive
  staircase targeting the ~50% detection threshold, masked by Mondrian
  patterns (see `masks/`).

These images are committed directly to the repository. This repository is
**private** until publication; redistribution is subject to the KDEF license
terms.

## `masks/` — Mondrian masks (procedurally generated)

100 unique 256×256 PNG Mondrian masks used as backward masks. Each mask
contains 100–200 randomly positioned, sized, and colored rectangles on a
medium-gray background, providing dense high-spatial-frequency structure to
interrupt face-processing.

Regenerate with:

```
uv run python scripts/generate_mondrians.py
```

The generator is deterministic (seeded per-mask), so rerunning it produces
byte-identical output. The masks are committed to the repo; you only need to
regenerate if you change the generator parameters.
