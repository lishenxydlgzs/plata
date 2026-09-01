"""Timer action helpers."""

from .models import Action, ConversationMode, ConversationResponse

MAX_TIMER_SECONDS = 24 * 60 * 60
def _format_duration(seconds: int) -> str:
    for unit, multiplier in (("hour", 3600), ("minute", 60), ("second", 1)):
        if seconds % multiplier == 0:
            amount = seconds // multiplier
            return f"{amount} {unit}{'' if amount == 1 else 's'}"
    return f"{seconds} seconds"


def timer_response(seconds: int) -> ConversationResponse:
    """Build the spoken acknowledgement and HA scheduling action."""
    duration = _format_duration(seconds)
    return ConversationResponse(
        reply_text=f"Okay! I'll let you know in {duration}.",
        mode=ConversationMode.CHAT,
        continue_conversation=False,
        actions=[
            Action(
                type="ha_service",
                data={
                    "domain": "kids_robot",
                    "service": "start_timer",
                    "service_data": {"duration_seconds": seconds},
                },
            )
        ],
    )
