import os

from django.core.wsgi import get_wsgi_application

from config.boot import run_pending_migrations

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

run_pending_migrations()
