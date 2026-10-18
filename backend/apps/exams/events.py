from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def broadcast_document_event(user_id: int, document_id: int, status: str) -> None:
    """Notifica o frontend. Só código de status e id: nunca valores de exame."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        f"user_{user_id}",
        {"type": "document_event", "status": status, "document_id": document_id},
    )
