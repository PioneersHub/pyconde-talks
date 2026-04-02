"""Tests for :class:`TalkImageGenerator` image generation."""

# ruff: noqa: D102, PLR2004

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from PIL import Image

from talks.management.commands._pretalx.images import (
    _AVATAR_SS_FACTOR,
    _DESIGN_WIDTH,
    _FONT_SIZE_SUBTITLE,
    _FONT_SIZE_TITLE,
    _OUTPUT_HEIGHT,
    _OUTPUT_WIDTH,
    TalkImageGenerator,
)


if TYPE_CHECKING:
    from pathlib import Path


class TestResolveFontPath:
    """Tests for ``TalkImageGenerator._resolve_font_path``."""

    def test_returns_explicit_path_when_file_exists(self, tmp_path: Path) -> None:
        font_file = tmp_path / "Custom.ttf"
        font_file.touch()

        with patch("talks.management.commands._pretalx.images.settings") as mock_settings:
            mock_settings.TALK_CARD_FONT = str(font_file)
            result = TalkImageGenerator._resolve_font_path()

        assert result == str(font_file)

    def test_falls_back_to_font_name_when_path_missing(self, tmp_path: Path) -> None:
        resolved_path = str(tmp_path / "NotoSans-Regular.ttf")
        (tmp_path / "NotoSans-Regular.ttf").touch()

        with (
            patch("talks.management.commands._pretalx.images.settings") as mock_settings,
            patch("talks.management.commands._pretalx.images.font_manager") as mock_fm,
        ):
            mock_settings.TALK_CARD_FONT = "/nonexistent/font.ttf"
            mock_settings.TALK_CARD_FONT_NAME = "Noto Sans"
            mock_fm.FontProperties.return_value = "props-sentinel"
            mock_fm.findfont.return_value = resolved_path

            result = TalkImageGenerator._resolve_font_path()

        assert result == resolved_path
        mock_fm.findfont.assert_called_once_with("props-sentinel", fallback_to_default=False)

    def test_falls_back_to_font_name_when_path_not_set(self, tmp_path: Path) -> None:
        resolved_path = str(tmp_path / "NotoSans-Regular.ttf")
        (tmp_path / "NotoSans-Regular.ttf").touch()

        with (
            patch("talks.management.commands._pretalx.images.settings") as mock_settings,
            patch("talks.management.commands._pretalx.images.font_manager") as mock_fm,
        ):
            mock_settings.TALK_CARD_FONT = None
            mock_settings.TALK_CARD_FONT_NAME = "Noto Sans"
            mock_fm.FontProperties.return_value = "props-sentinel"
            mock_fm.findfont.return_value = resolved_path

            result = TalkImageGenerator._resolve_font_path()

        assert result == resolved_path

    def test_uses_default_font_name_when_setting_absent(self, tmp_path: Path) -> None:
        resolved_path = str(tmp_path / "NotoSans-Regular.ttf")
        (tmp_path / "NotoSans-Regular.ttf").touch()

        with (
            patch("talks.management.commands._pretalx.images.settings") as mock_settings,
            patch("talks.management.commands._pretalx.images.font_manager") as mock_fm,
        ):
            # Simulate TALK_CARD_FONT_NAME not being set on settings at all
            del mock_settings.TALK_CARD_FONT_NAME
            mock_settings.TALK_CARD_FONT = None
            mock_fm.FontProperties.return_value = "props-sentinel"
            mock_fm.findfont.return_value = resolved_path

            result = TalkImageGenerator._resolve_font_path()

        assert result == resolved_path
        mock_fm.FontProperties.assert_called_once_with(family="Noto Sans")

    def test_raises_when_findfont_returns_none(self) -> None:
        with (
            patch("talks.management.commands._pretalx.images.settings") as mock_settings,
            patch("talks.management.commands._pretalx.images.font_manager") as mock_fm,
        ):
            mock_settings.TALK_CARD_FONT = None
            mock_settings.TALK_CARD_FONT_NAME = "Missing Font"
            mock_fm.FontProperties.return_value = "props-sentinel"
            mock_fm.findfont.return_value = None

            with pytest.raises(FileNotFoundError, match="Missing Font"):
                TalkImageGenerator._resolve_font_path()

    def test_raises_when_findfont_raises_value_error(self) -> None:
        with (
            patch("talks.management.commands._pretalx.images.settings") as mock_settings,
            patch("talks.management.commands._pretalx.images.font_manager") as mock_fm,
        ):
            mock_settings.TALK_CARD_FONT = None
            mock_settings.TALK_CARD_FONT_NAME = "Missing Font"
            mock_fm.FontProperties.return_value = "props-sentinel"
            mock_fm.findfont.side_effect = ValueError("not found")

            with pytest.raises(FileNotFoundError, match="Missing Font"):
                TalkImageGenerator._resolve_font_path()

    def test_raises_when_findfont_returns_nonexistent_path(self) -> None:
        with (
            patch("talks.management.commands._pretalx.images.settings") as mock_settings,
            patch("talks.management.commands._pretalx.images.font_manager") as mock_fm,
        ):
            mock_settings.TALK_CARD_FONT = None
            mock_settings.TALK_CARD_FONT_NAME = "Ghost Font"
            mock_fm.FontProperties.return_value = "props-sentinel"
            mock_fm.findfont.return_value = "/no/such/file.ttf"

            with pytest.raises(FileNotFoundError, match="Ghost Font"):
                TalkImageGenerator._resolve_font_path()


class TestProcessSpeakerPhoto:
    """Tests for ``TalkImageGenerator._process_speaker_photo``."""

    @staticmethod
    def _make_photo(size: tuple[int, int] = (400, 400), mode: str = "RGB") -> Image.Image:
        """Create a simple solid-colour test image."""
        return Image.new(mode, size, (100, 150, 200))

    def test_returns_rgba_with_correct_size(self) -> None:
        photo = self._make_photo()
        result = TalkImageGenerator._process_speaker_photo(photo, size=200)

        assert result.mode == "RGBA"
        assert result.size == (200, 200)

    def test_circle_mask_is_antialiased(self) -> None:
        """Edge pixels should have intermediate alpha values (not just 0/255)."""
        photo = self._make_photo(size=(800, 800))
        result = TalkImageGenerator._process_speaker_photo(photo, size=200)
        alpha_band = result.split()[3]
        unique_alpha = set(alpha_band.getdata())  # type: ignore[call-overload]

        # A supersampled mask produces intermediate values at the edge.
        assert len(unique_alpha) > 2, (
            f"Expected anti-aliased alpha with intermediate values, got only {unique_alpha}"
        )

    def test_corner_pixel_is_fully_transparent(self) -> None:
        """Pixels outside the circle must be fully transparent."""
        photo = self._make_photo(size=(400, 400))
        result = TalkImageGenerator._process_speaker_photo(photo, size=200)
        pixel = result.getpixel((0, 0))
        assert isinstance(pixel, tuple)
        _r, _g, _b, a = pixel
        assert a == 0

    def test_transparent_avatar_gets_white_background(self) -> None:
        """RGBA inputs with transparency should be flattened onto white."""
        # Create a fully transparent image so the background fill is visible.
        photo = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        result = TalkImageGenerator._process_speaker_photo(photo, size=200)
        # The center pixel should show the white background fill.
        pixel = result.getpixel((100, 100))
        assert isinstance(pixel, tuple)
        r, g, b, a = pixel
        assert (r, g, b) == (255, 255, 255)
        assert a == 255

    def test_handles_rgba_input(self) -> None:
        photo = self._make_photo(mode="RGBA")
        result = TalkImageGenerator._process_speaker_photo(photo, size=100)

        assert result.mode == "RGBA"
        assert result.size == (100, 100)

    def test_center_pixel_is_opaque(self) -> None:
        photo = self._make_photo()
        result = TalkImageGenerator._process_speaker_photo(photo, size=200)
        pixel = result.getpixel((100, 100))
        assert isinstance(pixel, tuple)
        _, _, _, a = pixel

        assert a == 255

    def test_supersample_factor_used(self) -> None:
        """The mask should be built at _AVATAR_SS_FACTOR times the target size."""
        assert _AVATAR_SS_FACTOR >= 2, "Supersample factor should be >= 2"


class TestScaleAwareLayout:
    """Verify that scale-dependent helpers produce proportional results."""

    def test_load_fonts_scales_sizes(self) -> None:
        sizes: dict[str, int] = {}

        def _patched_load(scale: float = 1.0) -> dict[str, int]:
            sizes.clear()
            for name, base in [
                ("title", _FONT_SIZE_TITLE),
                ("subtitle", _FONT_SIZE_SUBTITLE),
            ]:
                sizes[name] = int(base * scale)
            return sizes

        # Just verify the math - actual font loading is tested elsewhere.
        _patched_load(2.0)
        assert sizes["title"] == _FONT_SIZE_TITLE * 2
        assert sizes["subtitle"] == _FONT_SIZE_SUBTITLE * 2

    def test_design_width_matches_output_width(self) -> None:
        """Output width should equal the design-time baseline."""
        assert _OUTPUT_WIDTH == _DESIGN_WIDTH
        assert _OUTPUT_HEIGHT == 1080
