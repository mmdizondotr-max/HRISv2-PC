from django.core.management.base import BaseCommand
from accounts.models import User

class Command(BaseCommand):
    help = 'Approve a user via CLI'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str)

    def handle(self, *args, **options):
        username = options['username']
        try:
            user = User.objects.get(username=username)
            if user.is_approved:
                self.stdout.write(self.style.WARNING(f"User {username} is already approved."))
            else:
                user.is_active = True
                user.is_approved = True
                user.save()
                self.stdout.write(self.style.SUCCESS(f"User {username} successfully approved."))
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User {username} not found."))
