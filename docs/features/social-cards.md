---
icon: lucide/image
---

# Social cards

Every talk gets an auto-generated social card: a 1920x1080 image built from a per-event template,
the talk title, speaker avatars, the speaker names, and the Pretalx code. The card is what shows up
as the talk's image on the list, schedule, and detail pages, and as the preview when a talk link is
shared.

Source:
[`talks/management/commands/_pretalx/images.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/management/commands/_pretalx/images.py)
(rendering) and
[`talks/management/commands/_pretalx/avatars.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/management/commands/_pretalx/avatars.py)
(avatar download and cache). Cards are produced as part of the Pretalx sync; see
[Pretalx sync](../reference/pretalx-sync.md).

## What a card contains

The generator (`TalkImageGenerator`) assembles each card on top of a randomly chosen template PNG
for the event:

- **Speaker avatars**: up to four circular avatars laid out in the upper-left. One avatar fills a
    large square; two sit side by side; three or four form a 2x2 grid. Each photo is cropped square,
    flattened onto white (so avatars with transparency do not get black edges), and masked into a
    circle with supersampled anti-aliasing for a clean edge.
- **Title**: word-wrapped to fit the card width, up to five lines, anchored to the bottom of the
    safe area. Rendering goes through Pilmoji, so emoji in a title are drawn correctly.
- **Speaker names**: the formatted `speaker_names` string near the bottom.
- **Pretalx code**: the submission code in small text at the very bottom.

Layout constants are authored at a 1920-wide design resolution and scaled proportionally, so a
larger template renders an identical-looking card and is downscaled to 1920x1080 with Lanczos
resampling at the end.

## Templates and color schemes

Card templates are PNG files stored per event under `media/social_card_templates/<event-slug>/`. The
generator picks one at random per talk, so a set of talks gets visual variety.

The template filename encodes a color scheme. A file named `social-card-<key>.png` maps to a palette
that sets the title, speaker-name, and code text colors. The recognized keys are `blue`, `darkblue`,
`green`, `darkgreen`, `orange`, `yellow`, and `grey`; an unrecognized name falls back to all-white
text.

The shipped templates for the default event live in
`media/social_card_templates/pyconde-pydata-2026/` and cover all seven color keys. An event with a
template directory but no matching `social-card-*` name (for example a single `talk_template.png`)
still renders, using the default white palette.

!!! warning "An event needs at least one template PNG"

    Card generation raises `FileNotFoundError` when the event's template directory has no `.png` files.
    The Pretalx importer absorbs per-talk errors, so a missing template directory means cards are simply
    not produced for that event rather than the import failing.

## Fonts

Text is drawn with Noto Sans, resolved by `_resolve_font_path` in this order:

1. `settings.TALK_CARD_FONT`, an explicit path to a `.ttf` or `.otf` file, if it exists.
2. Otherwise `settings.TALK_CARD_FONT_NAME` (default `Noto Sans`), looked up through matplotlib's
    font manager.

If neither resolves, generation raises a `FileNotFoundError` telling the operator to install the
font or set `TALK_CARD_FONT`.

For local development the setup script downloads Noto Sans automatically. When `DOWNLOAD_FONT` is
`true` (the default),
[`scripts/dev-setup.sh`](https://github.com/PioneersHub/pyconde-talks/blob/main/scripts/dev-setup.sh)
fetches the variable Noto Sans TTF into `assets/fonts/NotoSans.ttf`. Noto Sans is chosen for its
broad script and emoji coverage, so speaker names and titles in many languages render without
missing glyphs.

## Avatars: download, cap, and cache

Speaker avatar URLs come from Pretalx profile uploads, so the download is treated as semi-trusted
and bounded on two axes:

- **Byte cap**: each download is streamed and aborted past 10 MiB (`_MAX_AVATAR_BYTES`), and a
    response that advertises a larger `Content-Length` is rejected before the body is read. A failed
    or oversized download simply yields no avatar.
- **Decode cap**: Pillow's `MAX_IMAGE_PIXELS` is set to 50 megapixels, so a small file that
    decompresses to a huge bitmap raises rather than exhausting memory. That error is absorbed by
    the per-talk import error handling.

Avatars are cached in two tiers to avoid re-downloading the same photo for every card: an in-memory
dict keyed by URL, and on-disk files under `media/avatars/` named by the SHA-256 of the URL. Before
generation begins, the importer prefetches every unique avatar URL for accepted and confirmed
submissions concurrently (bounded to eight connections) to warm both caches.

The `--no-avatars` flag on the import command skips avatar rendering entirely, producing cards with
just the title, speaker names, and code.

## Output formats

Cards are saved to the talk's `image` field. The format is chosen by the importer's `--image-format`
option, normalized by `_resolve_image_format`:

- **`webp`** (the default): saved at quality 82 with maximum compression effort.
- **`jpeg`** (also accepts `jpg`): saved at quality 88, optimized and progressive.

Any other value logs a warning and falls back to WebP. The saved file is named `talk_<id>.<ext>`.

## When cards are generated and regenerated

Cards are produced during the Pretalx sync. A new talk always gets a card (unless `--skip-images` is
set). For an existing talk, the card is regenerated when any of these holds:

- The talk's title changed.
- `--force-images` was passed.
- A still-attached speaker's name or avatar changed earlier in the same run.
- The current image file is missing, or a template PNG for the event was touched on disk after the
    image's modification time (handy when you swap a template and want every card re-rendered).

The full set of triggers and the relevant import flags are documented in
[Pretalx sync](../reference/pretalx-sync.md).
