from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .models import Shop, TimeLog, ShopOperatingHours
from scheduling.models import ShopRequirement
from .forms import ShopForm, ShopRequirementForm, ShopOperatingHoursForm
from django.forms import inlineformset_factory
from django.http import HttpResponseForbidden
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

@login_required
def shop_list(request):
    if request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden("Unauthorized")

    shops = Shop.objects.all()
    return render(request, 'attendance/shop_list.html', {'shops': shops})

@login_required
def shop_manage(request, shop_id=None):
    if request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden("Unauthorized")

    if shop_id:
        shop = get_object_or_404(Shop, id=shop_id)
        # Ensure requirement exists
        if not hasattr(shop, 'requirement'):
            ShopRequirement.objects.create(shop=shop)
    else:
        shop = Shop()

    # Formsets
    HoursFormSet = inlineformset_factory(Shop, ShopOperatingHours, form=ShopOperatingHoursForm, extra=7, max_num=7, can_delete=True)

    # Roving Logic: Clean up existing hours if any
    if shop.name == 'Roving':
        if shop.operating_hours.exists():
            shop.operating_hours.all().delete()

    if request.method == 'POST':
        form = ShopForm(request.POST, instance=shop)

        if form.is_valid():
            created_shop = form.save()

            # Retrieve existing requirement reliably or create new
            req_instance, _ = ShopRequirement.objects.get_or_create(shop=created_shop)
            req_form = ShopRequirementForm(request.POST, instance=req_instance)

            if shop.name == 'Roving':
                # Initialize hours_formset to None for safety in case of fallback
                hours_formset = None
                if req_form.is_valid():
                    req_form.save()
                    messages.success(request, "Shop saved successfully.")
                    return redirect('attendance:shop_list')
            else:
                hours_formset = HoursFormSet(request.POST, instance=created_shop)

                if req_form.is_valid() and hours_formset.is_valid():
                    req_form.save()
                    hours_formset.save()
                    messages.success(request, "Shop saved successfully.")
                    return redirect('attendance:shop_list')
    else:
        form = ShopForm(instance=shop)
        if shop_id:
            req_instance = shop.requirement
            req_form = ShopRequirementForm(instance=req_instance)
        else:
            req_form = ShopRequirementForm()

        if shop.name != 'Roving':
            hours_formset = HoursFormSet(instance=shop)
        else:
            hours_formset = None

    # Prepare ordered forms for Mon-Sun (0-6)
    ordered_forms = []
    if shop.name != 'Roving':
        # This logic matches forms to days for the template to render fixed rows
        days = range(7)
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

        # Map existing forms by day
        existing_map = {}
        extra_forms = []

        for f in hours_formset:
            if f.instance.pk and f.instance.day is not None:
                 existing_map[f.instance.day] = f
            else:
                 extra_forms.append(f)

        extra_iter = iter(extra_forms)

        for day_code in days:
            if day_code in existing_map:
                form_to_use = existing_map[day_code]
            else:
                # Grab next extra form
                try:
                    form_to_use = next(extra_iter)
                    # Set the initial day for this form so it saves correctly
                    form_to_use.initial['day'] = day_code
                except StopIteration:
                    # Should not happen if extra=7 and we don't have >7 forms
                    form_to_use = None

            ordered_forms.append({
                'day_name': day_names[day_code],
                'day_code': day_code,
                'form': form_to_use
            })

    return render(request, 'attendance/shop_manage.html', {
        'form': form,
        'req_form': req_form,
        'hours_formset': hours_formset,
        'shop': shop,
        'ordered_forms': ordered_forms,
    })

@login_required
def shop_delete(request, shop_id):
    if request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden("Unauthorized")

    shop = get_object_or_404(Shop, id=shop_id)

    if shop.name == 'Roving':
        messages.error(request, "The 'Roving' shop cannot be deleted.")
        return redirect('attendance:shop_list')

    if request.method == 'POST':
        shop.delete()
        messages.success(request, "Shop deleted.")
        return redirect('attendance:shop_list')

    return render(request, 'attendance/shop_delete.html', {'shop': shop})

@login_required
def daily_time_record(request, user_id=None):
    User = get_user_model()

    if user_id:
        if request.user.tier not in ['supervisor', 'administrator']:
            return HttpResponseForbidden("Unauthorized to view other users' DTR.")
        target_user = get_object_or_404(User, id=user_id)
    else:
        target_user = request.user

    # Date Filtering
    today = timezone.localdate()
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if start_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = today.replace(day=1)
    else:
        # Default to first day of current month
        start_date = today.replace(day=1)

    if end_date_str:
        try:
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = today
    else:
        end_date = today

    logs = TimeLog.objects.filter(
        user=target_user,
        date__range=[start_date, end_date]
    ).order_by('-date')

    return render(request, 'attendance/dtr.html', {
        'target_user': target_user,
        'logs': logs,
        'start_date': start_date,
        'end_date': end_date,
    })
