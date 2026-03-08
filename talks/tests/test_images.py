"""Tests for :class:`TalkImageGenerator` font resolution."""

# ruff: noqa: SLF001, D102

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from talks.management.commands._pretalx.images import TalkImageGenerator


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
