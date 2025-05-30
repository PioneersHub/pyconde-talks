from django.core.exceptions import ValidationError
from talks.types import VideoProvider


def validate_video_link(video_link: str):
    is_valid_video_provider = any(provider in video_link for provider in VideoProvider)

    if not is_valid_video_provider:
        video_provider_names = [x.name for x in VideoProvider]
        raise ValidationError(
            f"URL must be from a valid video provider. Allowed video providers are: {', '.join(video_provider_names)}"
        )
