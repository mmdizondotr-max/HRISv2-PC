
from django.test import TestCase, Client
from accounts.models import User
from attendance.models import Shop, ShopOperatingHours
import datetime

class ShopManageTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(username='admin', first_name='Admin', last_name='User', password='password', tier='administrator')
        self.supervisor = User.objects.create_user(username='supervisor', first_name='Super', last_name='Visor', password='password', tier='supervisor')
        self.regular = User.objects.create_user(username='regular', first_name='Regular', last_name='User', password='password', tier='regular')
        self.shop = Shop.objects.create(name='Test Shop', is_active=True)

    def test_supervisor_cannot_change_tier(self):
        self.client.login(username='supervisor', password='password')
        target_user = self.regular

        # POST data attempting to promote regular to supervisor
        response = self.client.post(f'/accounts/promote/{target_user.id}/', {
            'tier': 'supervisor',
            'applicable_shops': [self.shop.id]
        })

        target_user.refresh_from_db()
        self.assertEqual(target_user.tier, 'regular', "Supervisor should not be able to change tier")

    def test_admin_cannot_change_admin_tier(self):
        self.client.login(username='admin', password='password')
        target_admin = User.objects.create_user(username='target_admin', first_name='Target', last_name='Admin', password='password', tier='administrator')

        # POST data attempting to demote admin
        response = self.client.post(f'/accounts/promote/{target_admin.id}/', {
            'tier': 'regular',
            'applicable_shops': [self.shop.id]
        })

        target_admin.refresh_from_db()
        self.assertEqual(target_admin.tier, 'administrator', "Admin should not be able to modify another admin")

    def test_shop_manage_closed_logic(self):
        self.client.login(username='admin', password='password')

        # Create a shop
        response = self.client.get('/attendance/shops/create/')
        self.assertEqual(response.status_code, 200)

        # Verify context has 7 ordered forms
        self.assertIn('ordered_forms', response.context)
        self.assertEqual(len(response.context['ordered_forms']), 7)

        # Submit form with Monday (0) open, Tuesday (1) closed (DELETE checked)
        # We need to simulate the formset management form and data
        data = {
            'name': 'New Shop',
            'is_active': 'on',
            'required_main_staff': 1,
            'required_reserve_staff': 1,
            'operating_hours-TOTAL_FORMS': 7,
            'operating_hours-INITIAL_FORMS': 0,
            'operating_hours-MIN_NUM_FORMS': 0,
            'operating_hours-MAX_NUM_FORMS': 7,
        }

        # Monday Open
        data['operating_hours-0-day'] = 0
        data['operating_hours-0-open_time'] = '09:00'
        data['operating_hours-0-close_time'] = '17:00'

        # Tuesday Closed (Checked DELETE, inputs disabled/empty)
        data['operating_hours-1-day'] = 1
        data['operating_hours-1-open_time'] = ''
        data['operating_hours-1-close_time'] = ''
        data['operating_hours-1-DELETE'] = 'on'

        # Others empty (implicitly DELETE or ignore)
        for i in range(2, 7):
            data[f'operating_hours-{i}-day'] = i
            data[f'operating_hours-{i}-open_time'] = ''
            data[f'operating_hours-{i}-close_time'] = ''
            data[f'operating_hours-{i}-DELETE'] = 'on'

        response = self.client.post('/attendance/shops/create/', data)

        if response.status_code != 302:
            print(response.context['form'].errors)
            print(response.context['hours_formset'].errors)

        self.assertEqual(response.status_code, 302)

        shop = Shop.objects.get(name='New Shop')
        self.assertEqual(shop.operating_hours.count(), 1)
        self.assertEqual(shop.operating_hours.first().day, 0)
