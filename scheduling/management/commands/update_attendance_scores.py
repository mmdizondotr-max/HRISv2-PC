from django.core.management.base import BaseCommand
from django.utils import timezone
import datetime
from scheduling.utils import update_scores_for_date

class Command(BaseCommand):
    help = 'Updates user scores based on yesterday\'s attendance'

    def handle(self, *args, **options):
        # We look at yesterday
        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)

        self.stdout.write(f"Running score update for {yesterday}")
        update_scores_for_date(yesterday)
        self.stdout.write(self.style.SUCCESS("Score update complete."))
