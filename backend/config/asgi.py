import os

from django.core.asgi import get_asgi_application

from config.boot import run_pending_migrations

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()

run_pending_migrations()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.exams.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
