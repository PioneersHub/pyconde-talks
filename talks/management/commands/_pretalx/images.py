"""
Social-card image generation for talks (Pillow / Pilmoji).

Each social card is assembled from a per-event template image, the talk
title, speaker avatar(s), and speaker names.
"""

# ruff: noqa: BLE001

import random
import re
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from django.conf import settings
from django.core.files.uploadedfile import InMemoryUploadedFile
from matplotlib import font_manager
from PIL import Image, ImageDraw, ImageFont, ImageOps, features
from pilmoji import Pilmoji  # type: ignore[attr-defined]

from talks.management.commands._pretalx.avatars import (
    download_avatar_bytes_sync,
    get_avatar_cache_dir,
    get_cached_avatar_bytes,
    save_avatar_bytes,
)
from talks.management.commands._pretalx.types import VerbosityLevel


if TYPE_CHECKING:
    from talks.management.commands._pretalx.context import ImportContext
    from talks.models import Speaker, Talk


# Cap the pixel count Pillow will decode for a (semi-trusted) speaker avatar, so a crafted image
# that decompresses to an enormous bitmap cannot exhaust memory during decode. ~50 MP is far more
# than any headshot rendered at avatar sizes; beyond it Pillow raises DecompressionBombError,
# which the per-talk import error handling already absorbs. Pairs with the byte cap in avatars.py.
Image.MAX_IMAGE_PIXELS = 50_000_000


#: Supported output formats for generated talk images.
type _ImageFormat = Literal["webp", "jpeg"]

# ------------------------------------------------------------------
# Layout constants - defined at a "design" resolution of 1920 x 1080.
# When the template is larger the generator scales every value
# proportionally so the final card looks identical at any size.
# ------------------------------------------------------------------

_DESIGN_WIDTH: int = 1920
"""Baseline width the pixel constants below are authored for."""

_MARGIN_X: int = 60
"""Left margin for text elements (title, speaker names)."""

_TEXT_H_PADDING: int = 120
"""Total horizontal padding subtracted from the card width for text wrapping."""

_SPEAKER_MARGIN_X: int = 40
"""Horizontal offset for the first speaker avatar."""

_SPEAKER_MARGIN_Y: int = 50
"""Vertical offset for the first speaker avatar row."""

_AVATAR_SPACING: int = 20
"""Gap between avatars in a multi-avatar grid."""

_TITLE_BOTTOM_Y: int = 900
"""Y-coordinate that anchors the bottom of the title block."""

_TITLE_LINE_HEIGHT: int = 80
"""Vertical distance between successive title lines."""

_SPEAKER_NAME_BOTTOM_OFFSET: int = 110
"""Distance from the bottom edge to the speaker-name baseline."""

_CODE_BOTTOM_OFFSET: int = 45
"""Distance from the bottom edge to the pretalx-code baseline."""

_MAX_TITLE_LINES: int = 5
"""Maximum number of wrapped title lines rendered on the card."""

_MAX_SPEAKER_AVATARS: int = 4
"""Maximum number of speaker avatars displayed."""

_FONT_SIZE_TITLE: int = 70
_FONT_SIZE_SUBTITLE: int = 40
_FONT_SIZE_SMALL: int = 24
_FONT_SIZE_EVENT: int = 50

_AVATAR_SS_FACTOR: int = 4
"""Supersample factor for the circular avatar mask (anti-aliasing)."""

_OUTPUT_WIDTH: int = 1920
_OUTPUT_HEIGHT: int = 1080
"""Final card dimensions after downscaling."""

#: RGB color tuples used across the color schemes.
_BLUE: tuple[int, int, int] = (0, 200, 225)
_DARKBLUE: tuple[int, int, int] = (55, 120, 190)
_GREEN: tuple[int, int, int] = (150, 220, 0)
_DARKGREEN: tuple[int, int, int] = (0, 170, 65)
_ORANGE: tuple[int, int, int] = (255, 155, 0)
_YELLOW: tuple[int, int, int] = (250, 200, 0)
_WHITE: tuple[int, int, int] = (255, 255, 255)

type _RGB = tuple[int, int, int]

type _CardColors = dict[str, _RGB]
"""Mapping of element name -> RGB color for a single card variant."""

_CARD_COLORS: dict[str, _CardColors] = {
    "blue": {"title": _DARKBLUE, "speaker": _DARKGREEN, "code": _WHITE},
    "darkblue": {"title": _ORANGE, "speaker": _GREEN, "code": _WHITE},
    "green": {"title": _DARKBLUE, "speaker": _DARKGREEN, "code": _WHITE},
    "darkgreen": {"title": _YELLOW, "speaker": _BLUE, "code": _WHITE},
    "orange": {"title": _DARKBLUE, "speaker": _WHITE, "code": _WHITE},
    "yellow": {"title": _DARKBLUE, "speaker": _DARKGREEN, "code": _WHITE},
    "grey": {"title": _DARKBLUE, "speaker": _WHITE, "code": _WHITE},
}

_DEFAULT_CARD_COLORS: _CardColors = {"title": _WHITE, "speaker": _WHITE, "code": _WHITE}


# ------------------------------------------------------------------
# Template / image freshness helpers
# ------------------------------------------------------------------


def _template_dir_for_event_slug(event_slug: str) -> Path:
    """Return the directory holding social-card template PNGs for *event_slug*."""
    return Path(settings.MEDIA_ROOT) / "social_card_templates" / event_slug


def latest_template_mtime(ctx: ImportContext) -> float | None:
    """
    Return the most recent mtime across the event's social-card templates.

    ``None`` means there is nothing to compare against - either no event is bound
    to the context yet or the template directory has no PNGs. Used by the importer
    to decide when previously-generated talk images are stale.
    """
    if ctx.event_obj is None:
        return None
    template_dir = _template_dir_for_event_slug(ctx.event_obj.slug)
    if not template_dir.is_dir():
        return None
    mtimes = [p.stat().st_mtime for p in template_dir.glob("*.png")]
    return max(mtimes) if mtimes else None


def image_is_older_than(talk: Talk, threshold_mtime: float) -> bool:
    """
    Return ``True`` if *talk*'s saved image is missing or older than *threshold_mtime*.

    Treats a missing or unreadable file as stale (caller will regenerate).
    """
    if not talk.image:
        return True
    try:
        return Path(talk.image.path).stat().st_mtime < threshold_mtime
    except OSError, ValueError, NotImplementedError:
        # OSError: missing file. ValueError/NotImplementedError: storages that do not
        # expose a filesystem ``path`` (e.g. S3). Treat both as "regenerate it."
        return True


class TalkImageGenerator:
    """
    Generate social-card images for talks.

    Usage::

        generator = TalkImageGenerator()
        img = generator.generate(talk, ctx)
    """

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    def generate(
        self,
        talk: Talk,
        ctx: ImportContext,
    ) -> Image.Image:
        """Generate a social card for *talk*, save it, and return the :class:`Image`."""
        image_format = self._resolve_image_format(ctx)

        template_dir = _template_dir_for_event_slug(talk.event.slug if talk.event else "")
        templates = list(template_dir.glob("*.png"))
        if not templates:
            msg = f"No template PNGs found in {template_dir}"
            raise FileNotFoundError(msg)
        template_path = random.choice(templates)  # noqa: S311  # nosec: B311
        colors = self._colors_for_template(template_path)
        img = Image.open(template_path).copy().convert("RGBA")
        width, height = img.size
        scale = width / _DESIGN_WIDTH

        final_img = Image.new("RGB", (width, height), (255, 255, 255))
        final_img.paste(img, (0, 0), img)

        draw = ImageDraw.Draw(final_img)

        # Speaker avatars
        if not ctx.no_avatars:
            self._paste_speaker_avatars(final_img, talk, height, scale)

        # Title
        fonts = self._load_fonts(scale)
        margin_x = int(_MARGIN_X * scale)
        full_width = width - int(_TEXT_H_PADDING * scale)
        self._draw_title_block(
            canvas=final_img,
            title=talk.title,
            fonts=fonts,
            full_width=full_width,
            scale=scale,
            fill=colors["title"],
        )

        # Speaker names
        speakers_text = talk.speaker_names
        if speakers_text:
            speaker_y = height - int(_SPEAKER_NAME_BOTTOM_OFFSET * scale)
            draw.text(
                (margin_x, speaker_y),
                speakers_text,
                font=fonts["subtitle"],
                fill=colors["speaker"],
            )

        # Pretalx code
        pretalx_code = talk.pretalx_code
        if pretalx_code:
            code_y = height - int(_CODE_BOTTOM_OFFSET * scale)
            draw.text(
                (margin_x, code_y),
                pretalx_code,
                font=fonts["small"],
                fill=colors["code"],
            )

        # Downscale to output resolution when the template is larger.
        if width != _OUTPUT_WIDTH or height != _OUTPUT_HEIGHT:
            final_img = final_img.resize(
                (_OUTPUT_WIDTH, _OUTPUT_HEIGHT),
                Image.Resampling.LANCZOS,
            )

        # Save to model
        self._save_image_to_talk(talk, final_img, image_format, ctx)
        return final_img

    # ------------------------------------------------------------------
    # Image format
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_image_format(ctx: ImportContext) -> _ImageFormat:
        """
        Normalize the user-supplied format string to a supported value.

        Falls back to ``"webp"`` when the format is unrecognized.
        """
        fmt = ctx.image_format.lower()
        if fmt == "jpg":
            fmt = "jpeg"
        if fmt not in {"webp", "jpeg"}:
            ctx.log(
                f"Unsupported image format '{ctx.image_format}', defaulting to 'webp'",
                VerbosityLevel.DETAILED,
                "WARNING",
            )
            return "webp"
        # At this point fmt is guaranteed to be "webp" or "jpeg".
        return cast("_ImageFormat", fmt)

    # ------------------------------------------------------------------
    # Speaker avatars
    # ------------------------------------------------------------------

    def _paste_speaker_avatars(
        self,
        canvas: Image.Image,
        talk: Talk,
        height: int,
        scale: float,
    ) -> None:
        """
        Download, crop, and paste speaker avatar photos onto *canvas*.

        Arranges up to four circular avatars in a grid in the upper-left region of the card.
        """
        margin_x = int(_SPEAKER_MARGIN_X * scale)
        margin_y = int(_SPEAKER_MARGIN_Y * scale)
        spacing = int(_AVATAR_SPACING * scale)

        raw_photos = self._download_speaker_photos(
            list(talk.speakers.all()),
            limit=_MAX_SPEAKER_AVATARS,
        )
        avatar_count = len(raw_photos)
        area_side = int(height * 0.5)

        if avatar_count <= 1:
            speaker_size = area_side
            grid_cols = 1
        elif avatar_count == 2:  # noqa: PLR2004
            area_width = int(height * 0.7)
            speaker_size = (area_width - spacing) // 2
            grid_cols = 2
        else:
            speaker_size = (area_side - spacing) // 2
            grid_cols = 2

        processed = [
            self._process_speaker_photo(p, size=speaker_size)
            for p, _ in raw_photos[:_MAX_SPEAKER_AVATARS]
        ]

        for idx, photo in enumerate(processed):
            row = idx // grid_cols
            col = idx % grid_cols
            x = margin_x + col * (speaker_size + spacing)
            y = margin_y + row * (speaker_size + spacing)
            canvas.paste(photo, (x, y), photo)

    # ------------------------------------------------------------------
    # Speaker photo download / processing
    # ------------------------------------------------------------------

    def _download_speaker_photos(
        self,
        speakers: list[Speaker],
        limit: int = 4,
    ) -> list[tuple[Image.Image, str]]:
        """
        Download speaker photos (unprocessed).

        Return up to *limit* ``(image, pretalx_id)`` pairs.
        """
        photos: list[tuple[Image.Image, str]] = []
        for speaker in speakers:
            photo = self._download_speaker_photo(speaker)
            if photo:
                photos.append((photo, speaker.pretalx_id))
                if len(photos) >= limit:
                    break
        return photos

    @staticmethod
    def _download_speaker_photo(speaker: Speaker) -> Image.Image | None:
        """Fetch a speaker's avatar from cache or network; return ``None`` on failure."""
        url = speaker.avatar
        if not url:
            return None
        cache_dir = get_avatar_cache_dir()
        data = get_cached_avatar_bytes(cache_dir, url)
        if data is None:
            data = download_avatar_bytes_sync(url)
            if data is None:
                return None
            save_avatar_bytes(cache_dir, url, data)
        try:
            return Image.open(BytesIO(data))
        except Exception:
            return None

    @staticmethod
    def _process_speaker_photo(photo: Image.Image, size: int = 200) -> Image.Image:
        """Crop to square, resize, and apply a supersampled circular alpha mask."""
        img = ImageOps.fit(photo, (size, size), Image.Resampling.LANCZOS, centering=(0.5, 0.5))

        # Flatten transparency onto a white background so that avatars with
        # alpha channels don't end up with black fill or edge artifacts.
        background = Image.new("RGB", (size, size), (255, 255, 255))
        if img.mode == "RGBA":
            background.paste(img, mask=img.split()[3])
        else:
            background.paste(img.convert("RGB"))

        # Build the circle mask at a higher resolution, then downscale so
        # the edge pixels get proper anti-aliasing (no jagged/serrated border).
        ss_size = size * _AVATAR_SS_FACTOR
        mask = Image.new("L", (ss_size, ss_size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, ss_size, ss_size), fill=255)
        mask = mask.resize((size, size), Image.Resampling.LANCZOS)

        output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        output.paste(background, (0, 0))
        output.putalpha(mask)
        return output

    # ------------------------------------------------------------------
    # Fonts
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_font_path() -> str:
        """
        Return the font file path from settings or by searching system fonts.

        Resolution order:

        1. ``settings.TALK_CARD_FONT`` - explicit path to a ``.ttf`` / ``.otf`` file.
        2. ``settings.TALK_CARD_FONT_NAME`` (default ``"Noto Sans"``) - looked
           up via :func:`matplotlib.font_manager.findfont`.

        Raises
        ------
        FileNotFoundError
            If neither approach yields a usable font file.

        """
        font_path = getattr(settings, "TALK_CARD_FONT", None)
        if font_path and Path(font_path).exists():
            return str(font_path)

        font_name: str = getattr(settings, "TALK_CARD_FONT_NAME", "Noto Sans")
        try:
            resolved = font_manager.findfont(
                font_manager.FontProperties(family=font_name),
                fallback_to_default=False,
            )
        except ValueError:
            resolved = None

        if resolved and Path(resolved).exists():
            return resolved

        msg = (
            f"Font '{font_name}' not found. "
            "Install the font or set TALK_CARD_FONT to an explicit path."
        )
        raise FileNotFoundError(msg)

    @staticmethod
    def _load_fonts(scale: float = 1.0) -> dict[str, ImageFont.FreeTypeFont]:
        """
        Load the font family used for social-card rendering.

        Font sizes are multiplied by *scale* so text is proportional to the
        working resolution of the template.

        Returns a dict with ``"title"``, ``"subtitle"``, ``"small"``, and
        ``"event_info"`` keys mapped to pre-sized font instances.
        """
        font_path = TalkImageGenerator._resolve_font_path()
        layout = ImageFont.Layout.RAQM if features.check_feature("raqm") else ImageFont.Layout.BASIC

        def _font(size_1x: int) -> ImageFont.FreeTypeFont:
            return ImageFont.truetype(
                font_path,
                int(size_1x * scale),
                layout_engine=layout,
            )

        return {
            "title": _font(_FONT_SIZE_TITLE),
            "subtitle": _font(_FONT_SIZE_SUBTITLE),
            "small": _font(_FONT_SIZE_SMALL),
            "event_info": _font(_FONT_SIZE_EVENT),
        }

    # ------------------------------------------------------------------
    # Text rendering
    # ------------------------------------------------------------------

    def _draw_title_block(  # noqa: PLR0913
        self,
        canvas: Image.Image,
        title: str,
        fonts: dict[str, ImageFont.FreeTypeFont],
        full_width: int,
        scale: float = 1.0,
        fill: _RGB = _WHITE,
    ) -> None:
        """Draw wrapped title text aligned to bottom of the safe area."""
        title_lines = self._wrap_text(title, fonts, full_width)
        line_height = int(_TITLE_LINE_HEIGHT * scale)
        margin_x = int(_MARGIN_X * scale)
        title_block_height = len(title_lines[:_MAX_TITLE_LINES]) * line_height
        title_y = int(_TITLE_BOTTOM_Y * scale) - title_block_height
        with Pilmoji(canvas) as pilmoji:
            for line in title_lines[:_MAX_TITLE_LINES]:
                pilmoji.text((margin_x, title_y), line, fill, fonts["title"])
                title_y += line_height

    def _wrap_text(
        self,
        text: str,
        fonts: dict[str, ImageFont.FreeTypeFont],
        max_width: int,
    ) -> list[str]:
        """Greedy word-wrap within *max_width* using Pilmoji-based measurement."""
        words = text.split()
        lines: list[str] = []
        current: list[str] = []

        for word in words:
            trial = " ".join([*current, word])
            width = self._pilmoji_text_width(trial, fonts["title"], max_width)
            if width <= max_width:
                current.append(word)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]

        if current:
            lines.append(" ".join(current))
        return lines

    @staticmethod
    def _pilmoji_text_width(
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
    ) -> int:
        """Measure rendered width by drawing with Pilmoji on a temporary image."""
        canvas_w = max(64, max_width * 2)
        canvas_h = max(64, int(font.size * 2))
        img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        with Pilmoji(img) as pilmoji:
            pilmoji.text((0, 0), text, (255, 255, 255), font)
        bbox = img.getbbox()
        return 0 if bbox is None else bbox[2]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _colors_for_template(template_path: Path) -> _CardColors:
        """Extract the color key from the template filename and return the palette."""
        stem = template_path.stem  # e.g. "social-card-darkblue"
        match = re.search(r"social-card-(.+)", stem)
        if match:
            key = match.group(1).lower()
            if key in _CARD_COLORS:
                return _CARD_COLORS[key]
        return _DEFAULT_CARD_COLORS

    @staticmethod
    def _save_image_to_talk(
        talk: Talk,
        final_img: Image.Image,
        image_format: _ImageFormat,
        ctx: ImportContext,
    ) -> None:
        """Encode *final_img* and save it to ``talk.image`` via an in-memory upload."""
        buf = BytesIO()
        if image_format == "webp":
            final_img.save(buf, format="WEBP", quality=82, method=6)
            content_type, ext = "image/webp", "webp"
        else:
            final_img.save(buf, format="JPEG", quality=88, optimize=True, progressive=True)
            content_type, ext = "image/jpeg", "jpeg"
        buf.seek(0)

        talk.image = InMemoryUploadedFile(
            buf,
            None,
            f"talk_{talk.pk}.{ext}",
            content_type,
            buf.getbuffer().nbytes,
            None,
        )
        talk.save()

        ctx.log(
            f"Generated talk image for: {talk.title}",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
