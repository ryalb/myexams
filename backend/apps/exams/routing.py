from django.urls import re_path

from .consumers import DocumentConsumer

websocket_urlpatterns = [
    re_path(r"ws/documents/$", DocumentConsumer.as_asgi()),
]
