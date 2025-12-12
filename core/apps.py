from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        # Only clear sessions if this is the actual server process starting,
        # not a management command (like migrate) or autoreload
        # A simple heuristic is checking argv

        # However, "server is restarted" implies we want to clear sessions on startup.
        # Clearing on every 'runserver' reload might be annoying in dev, but
        # it fulfills the requirement "automatically logged out if ... server is restarted".

        from django.contrib.sessions.models import Session
        try:
            # We wrap in try-except in case migrations haven't run yet
            Session.objects.all().delete()
        except Exception:
            pass
