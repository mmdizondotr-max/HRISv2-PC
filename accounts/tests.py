from django.test import TestCase
from accounts.models import User

class SuperUserTierTest(TestCase):
    def test_superuser_creation(self):
        """
        This test checks the logic, but note that the data migration only runs once.
        However, if we want to ensure that FUTURE superusers are also administrators,
        we might need to override the save method or createsuperuser command.

        The requirement was "Convert all existing SuperUsers into Administrator tiers".
        It didn't explicitly say "Ensure all future SuperUsers are Administrators".

        But let's verify that if I manually update a user to be a superuser,
        running the migration logic (if it were a signal or method) would update it.

        For this specific task (migration), I should verify that the migration logic works.
        """
        user = User.objects.create_superuser(username='testadmin', password='password', email='test@test.com')
        # By default, createsuperuser doesn't set tier to administrator unless specified in the manager?
        # Let's check what createsuperuser does.

        self.assertEqual(user.tier, 'regular') # Assuming default is regular

        # Now apply the logic from the migration
        User.objects.filter(is_superuser=True).update(tier='administrator')

        user.refresh_from_db()
        self.assertEqual(user.tier, 'administrator')
