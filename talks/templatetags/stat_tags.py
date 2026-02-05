"""Template tags for rendering dashboard statistics components."""

from django import template


register = template.Library()


@register.inclusion_tag("talks/partials/_stat_card.html")
def stat_card(title: str, value: str | float) -> dict[str, str | float]:
    """
    Render a statistic card with title and value.

    Args:
        title: Label for the statistic
        value: The value to display

    """
    return {"title": title, "value": value}
