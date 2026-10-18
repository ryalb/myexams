from django.core.management import call_command


def run_pending_migrations() -> None:
    call_command("migrate", interactive=False)
