import logging
import urllib.parse

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

logger = logging.getLogger(__name__)


class DocumentConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = None
        self.group_name = None

        query_string = self.scope.get("query_string", b"").decode("utf-8")
        token_value = urllib.parse.parse_qs(query_string).get("token", [None])[0]

        if token_value:
            try:
                jwt_auth = JWTAuthentication()
                validated_token = jwt_auth.get_validated_token(token_value.encode())
                self.user = await database_sync_to_async(jwt_auth.get_user)(validated_token)
            except (InvalidToken, Exception):
                logger.warning("WebSocket authentication failed")

        if self.user is None:
            # O Daphne só entrega close code customizado se o handshake foi aceito:
            # fechar antes do accept() vira 403 genérico e o front vê 1006.
            await self.accept()
            await self.close(code=4001)
            return

        self.group_name = f"user_{self.user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def document_event(self, event):
        await self.send_json(
            {
                "type": event["type"],
                "status": event["status"],
                "document_id": event["document_id"],
            }
        )
