"""Social-card image generation for talks (Pillow / Pilmoji)."""

# ruff: noqa: BLE001

import sys
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

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
    from talks.models import Speaker, Talk


class TalkImageGenerator:
    """Generate social-card images for talks."""

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    def generate(
        self,
        talk: Talk,
        options: dict[str, Any],
        card_width: int = 1920,
    ) -> Image.Image:
        """Generate a social card for *talk*, save it, and return the :class:`Image`."""
        verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))
        image_format = self._resolve_image_format(options, verbosity)

        template_path = (
            settings.BASE_DIR
            / "assets"
            / "img"
            / settings.BRAND_ASSETS_SUBDIR
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
        self._save_image_to_talk(talk, final_img, image_format, verbosity)
        return final_img

    # ------------------------------------------------------------------
    # Image format
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_image_format(options: dict[str, Any], verbosity: VerbosityLevel) -> str:
        raw = cast("str | None", options.get("image_format", "webp")) or "webp"
        fmt = raw.lower()
        if fmt == "jpg":
            fmt = "jpeg"
        if fmt not in {"webp", "jpeg"}:
            if verbosity.value >= VerbosityLevel.DETAILED.value:
                sys.stderr.write(f"Unsupported image format '{raw}', defaulting to 'webp'\n")
            fmt = "webp"
        return fmt

    # ------------------------------------------------------------------
    # Speaker avatars
    # ------------------------------------------------------------------

    def _paste_speaker_avatars(
        self,
        canvas: Image.Image,
        talk: Talk,
        height: int,
    ) -> None:
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
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
        output = Image.new("RGBA", (size, size))
        output.paste(img.convert("RGB"), (0, 0))
        output.putalpha(mask)
        return output

    # ------------------------------------------------------------------
    # Fonts
    # ------------------------------------------------------------------

    @staticmethod
    def _load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
        """Load the configured font for the social card."""
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
        image_format: str,
        verbosity: VerbosityLevel,
    ) -> None:
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

        if verbosity.value >= VerbosityLevel.DETAILED.value:
            sys.stdout.write(f"Generated talk image for: {talk.title}\n")
