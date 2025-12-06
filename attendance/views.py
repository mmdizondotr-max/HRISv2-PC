from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .models import Shop, TimeLog
import ntplib
from time import ctime
import datetime
import requests

def get_ntp_time():
    """
    Fetches time from ntp.pagasa.dost.gov.ph or fallbacks.
    """
    try:
        client = ntplib.NTPClient()
        response = client.request('ntp.pagasa.dost.gov.ph', version=3)
        return datetime.datetime.fromtimestamp(response.tx_time, tz=timezone.get_current_timezone())
    except Exception as e:
        # Fallback to WorldTimeAPI or System Time if NTP fails
        try:
            r = requests.get('http://worldtimeapi.org/api/timezone/Asia/Manila', timeout=2)
            if r.status_code == 200:
                data = r.json()
                # Parse ISO format
                return datetime.datetime.fromisoformat(data['datetime'])
        except:
            pass
        return timezone.now()

@login_required
def home(request):
    current_time = get_ntp_time()
    today = timezone.localdate()

    # Check if user already timed in today
    try:
        todays_log = TimeLog.objects.get(user=request.user, date=today)
    except TimeLog.DoesNotExist:
        todays_log = None

    shops = Shop.objects.filter(is_active=True)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'time_in':
            if todays_log:
                messages.warning(request, "You have already timed in today.")
            else:
                shop_id = request.POST.get('shop_id')
                if not shop_id:
                    messages.error(request, "Please select a shop.")
                else:
                    shop = Shop.objects.get(id=shop_id)
                    TimeLog.objects.create(
                        user=request.user,
                        shop=shop,
                        date=today,
                        time_in=current_time.time()
                    )
                    messages.success(request, f"Timed in at {current_time.strftime('%I:%M %p')} for {shop.name}.")
                    return redirect('attendance:home')

        elif action == 'time_out':
            if not todays_log:
                messages.error(request, "You haven't timed in yet.")
            else:
                todays_log.time_out = current_time.time()
                todays_log.save()
                messages.success(request, f"Timed out at {current_time.strftime('%I:%M %p')}.")
                return redirect('attendance:home')

    return render(request, 'attendance/home.html', {
        'current_time': current_time,
        'shops': shops,
        'todays_log': todays_log,
    })
