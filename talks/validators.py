from django.core.exceptions import ValidationError
from talks.types import StreamingProvider


def validate_video_link(video_link: str):
    is_valid_streaming_provider = any(
        provider.value in video_link for provider in StreamingProvider
    )

    if not is_valid_streaming_provider:
        providers_name = [x.name for x in StreamingProvider]
        raise ValidationError(
            f"URL must be from a valid streaming provider. Allowed streaming providers are: {', '.join(providers_name)}"
        )
