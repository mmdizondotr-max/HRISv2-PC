from django.test import TestCase, Client
from accounts.models import User
from django.urls import reverse

class SuperUserTierTest(TestCase):
    def test_superuser_creation(self):
        """
        This test checks the logic for the migration task.
        """
        user = User.objects.create_superuser(username='testadmin', password='password', email='test@test.com')
        # By default, createsuperuser doesn't set tier to administrator unless specified in the manager?
        self.assertEqual(user.tier, 'regular') # Assuming default is regular

        # Now apply the logic from the migration
        User.objects.filter(is_superuser=True).update(tier='administrator')

        user.refresh_from_db()
        self.assertEqual(user.tier, 'administrator')

class SuperUserLoginSignalTest(TestCase):
    def test_superuser_promoted_on_login(self):
        """
        Test that a superuser with 'regular' tier is promoted to 'administrator' upon login.
        """
        # Create a superuser with 'regular' tier
        user = User.objects.create_superuser(username='superuser_login', password='password', email='super@test.com')
        user.tier = 'regular'
        user.save()

        self.assertEqual(user.tier, 'regular')
        self.assertTrue(user.is_superuser)

        # Log the user in
        client = Client()
        login_success = client.login(username='superuser_login', password='password')
        self.assertTrue(login_success)

        # Check if tier is updated
        user.refresh_from_db()
        self.assertEqual(user.tier, 'administrator')

    def test_regular_user_not_promoted(self):
        """
        Test that a regular user is NOT promoted to 'administrator' upon login.
        """
        user = User.objects.create_user(username='regular_login', password='password', email='regular@test.com')
        user.tier = 'regular'
        user.is_superuser = False
        user.save()

        client = Client()
        login_success = client.login(username='regular_login', password='password')
        self.assertTrue(login_success)

        user.refresh_from_db()
        self.assertEqual(user.tier, 'regular')
