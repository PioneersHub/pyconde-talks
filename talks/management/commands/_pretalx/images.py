"""
Social-card image generation for talks (Pillow / Pilmoji).

Each social card is assembled from a per-event template image, the talk
title, speaker avatar(s), and speaker names.
"""

# ruff: noqa: BLE001

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from django.conf import settings
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageDraw, ImageFont, ImageOps, features
from pilmoji import Pilmoji

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


#: Supported output formats for generated talk images.
type _ImageFormat = Literal["webp", "jpeg"]


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
        card_width: int = 1920,
    ) -> Image.Image:
        """Generate a social card for *talk*, save it, and return the :class:`Image`."""
        image_format = self._resolve_image_format(ctx)

        template_path = (
            settings.BASE_DIR
            / "assets"
            / "img"
            / (talk.event.slug if talk.event else "")
            / "talk_template.png"
        )
        img = Image.open(template_path).copy().convert("RGBA")
        width, height = img.size

        final_img = Image.new("RGB", (width, height), (255, 255, 255))
        final_img.paste(img, (0, 0), img)

        draw = ImageDraw.Draw(final_img)

        # Speaker avatars
        self._paste_speaker_avatars(final_img, talk, height)

        # Title
        fonts = self._load_fonts()
        full_width = card_width - 80
        self._draw_title_block(
            canvas=final_img,
            title=talk.title,
            fonts=fonts,
            full_width=full_width,
        )

        # Speaker names
        speakers_text = talk.speaker_names
        if speakers_text:
            speaker_y = height - 80
            draw.text((60, speaker_y), speakers_text, font=fonts["subtitle"], fill=(255, 255, 255))

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
    ) -> None:
        """
        Download, crop, and paste speaker avatar photos onto *canvas*.

        Arranges up to four circular avatars in a grid in the upper-left region of the card.
        """
        speaker_margin_x = 40
        speaker_margin_y = 50
        limit = 4
        speaker_photos = self._download_and_process_speaker_photos(
            list(talk.speakers.all()),
            limit=limit,
        )
        avatar_count = len(speaker_photos)
        spacing = 20
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
            self._process_speaker_photo(p, size=speaker_size) for p, _ in speaker_photos[:limit]
        ]

        for idx, photo in enumerate(processed):
            row = idx // grid_cols
            col = idx % grid_cols
            x = speaker_margin_x + col * (speaker_size + spacing)
            y = speaker_margin_y + row * (speaker_size + spacing)
            canvas.paste(photo, (x, y), photo)

    # ------------------------------------------------------------------
    # Speaker photo download / processing
    # ------------------------------------------------------------------

    def _download_and_process_speaker_photos(
        self,
        speakers: list[Speaker],
        limit: int = 2,
    ) -> list[tuple[Image.Image, str]]:
        """
        Download and crop speaker photos.

        Return up to *limit* ``(image, pretalx_id)`` pairs.
        """
        photos: list[tuple[Image.Image, str]] = []
        for speaker in speakers:
            photo = self._download_speaker_photo(speaker)
            if photo:
                photo = self._process_speaker_photo(photo)
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
        """Crop to square, resize, and apply a circular alpha mask."""
        img = ImageOps.fit(photo, (size, size), Image.Resampling.LANCZOS, centering=(0.5, 0.5))

        # Flatten transparency onto a white background to avoid border artifacts.
        background = Image.new("RGB", (size, size), (255, 255, 255))
        if img.mode == "RGBA":
            background.paste(img, mask=img.split()[3])
        else:
            background.paste(img.convert("RGB"))

        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
        output = Image.new("RGBA", (size, size))
        output.paste(background, (0, 0))
        output.putalpha(mask)
        return output

    # ------------------------------------------------------------------
    # Fonts
    # ------------------------------------------------------------------

    @staticmethod
    def _load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
        """
        Load the font family configured in ``settings.TALK_CARD_FONT``.

        Returns a dict with ``"title"``, ``"subtitle"``, ``"small"``, and
        ``"event_info"`` keys mapped to pre-sized font instances.

        Raises
        ------
        FileNotFoundError
            If ``TALK_CARD_FONT`` is unset or points to a missing file.

        """
        font_path = getattr(settings, "TALK_CARD_FONT", None)
        if not font_path or not Path(font_path).exists():
            msg = "TALK_CARD_FONT must be configured and point to an existing font file"
            raise FileNotFoundError(msg)

        layout = ImageFont.Layout.RAQM if features.check_feature("raqm") else ImageFont.Layout.BASIC
        return {
            "title": ImageFont.truetype(font_path, 46, layout_engine=layout),
            "subtitle": ImageFont.truetype(font_path, 28, layout_engine=layout),
            "small": ImageFont.truetype(font_path, 24, layout_engine=layout),
            "event_info": ImageFont.truetype(font_path, 42, layout_engine=layout),
        }

    # ------------------------------------------------------------------
    # Text rendering
    # ------------------------------------------------------------------

    def _draw_title_block(
        self,
        canvas: Image.Image,
        title: str,
        fonts: dict[str, ImageFont.FreeTypeFont],
        full_width: int,
    ) -> None:
        """Draw wrapped title text aligned to bottom of the safe area."""
        title_lines = self._wrap_text(title, fonts, full_width)
        line_height = 80
        title_block_height = len(title_lines[:5]) * line_height
        title_y = 900 - title_block_height
        with Pilmoji(canvas) as pilmoji:
            for line in title_lines[:5]:
                pilmoji.text((60, title_y), line, (255, 255, 255), fonts["title"])
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
