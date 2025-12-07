from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from .models import Preference, Schedule, Shift, UserPriority, ShopRequirement, ScheduleChangeLog
from attendance.models import Shop
from django.db.models import Count, Q
from django.utils import timezone
from .forms import PreferenceForm, ShiftAddForm
import datetime

@login_required
def preferences(request):
    try:
        pref = request.user.preference
    except Preference.DoesNotExist:
        pref = Preference(user=request.user)

    if request.method == 'POST':
        form = PreferenceForm(request.POST, instance=pref)
        if form.is_valid():
            pref = form.save(commit=False)
            pref.user = request.user
            pref.save()
            messages.success(request, "Preferences saved.")
            return redirect('scheduling:preferences')
    else:
        form = PreferenceForm(instance=pref)

    return render(request, 'scheduling/preferences.html', {'form': form})

@login_required
def my_schedule(request):
    today = timezone.localdate()

    # 1. Schedule starting this coming Sunday (if published)
    # 2. Schedule starting last Sunday (current week)

    start_of_current_week = today - datetime.timedelta(days=(today.weekday() + 1) % 7)
    start_of_next_week = start_of_current_week + datetime.timedelta(days=7)

    schedule = Schedule.objects.filter(week_start_date=start_of_next_week, is_published=True).first()

    if not schedule:
        schedule = Schedule.objects.filter(week_start_date=start_of_current_week, is_published=True).first()

    if not schedule:
        return render(request, 'scheduling/my_schedule.html', {'schedule': None})

    dates = [schedule.week_start_date + datetime.timedelta(days=i) for i in range(7)]
    shops = Shop.objects.filter(is_active=True)

    # Nested Dict for Template: matrix[date][shop_id]
    matrix = {}
    for d in dates:
        matrix[d] = {}
        for s in shops:
            matrix[d][s.id] = {'main': [], 'backup': []}

    shifts = schedule.shifts.all().select_related('user', 'shop')
    for shift in shifts:
        # Note: shift.date is Date, matrix keys are Dates. Should match if types are consistent.
        if shift.date in matrix and shift.shop.id in matrix[shift.date]:
             if shift.role == 'main':
                 matrix[shift.date][shift.shop.id]['main'].append(shift.user)
             else:
                 matrix[shift.date][shift.shop.id]['backup'].append(shift.user)

    return render(request, 'scheduling/my_schedule.html', {
        'schedule': schedule,
        'dates': dates,
        'shops': shops,
        'matrix': matrix,
        'change_logs': schedule.change_logs.all().order_by('-created_at')
    })

@login_required
def generator(request):
    if request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden()

    today = timezone.localdate()

    # Target Next Week (Sunday)
    days_until_sunday = (6 - today.weekday()) % 7
    if days_until_sunday == 0:
        target_start = today + datetime.timedelta(days=7)
    else:
        target_start = today + datetime.timedelta(days=days_until_sunday)
        if today.weekday() == 6:
             target_start = today

    schedule, _ = Schedule.objects.get_or_create(week_start_date=target_start)
    shops = Shop.objects.filter(is_active=True)

    if request.method == 'POST':
        if 'generate' in request.POST:
            _generate_schedule(shops, schedule)
            messages.success(request, "Schedule generated.")
            return redirect('scheduling:generator')
        elif 'publish' in request.POST:
            schedule.is_published = True
            schedule.save()
            messages.success(request, "Schedule published.")
            return redirect('scheduling:generator')

    dates = [schedule.week_start_date + datetime.timedelta(days=i) for i in range(7)]

    # Nested Dict for Template: matrix[date][shop_id]
    matrix = {}
    for d in dates:
        matrix[d] = {}
        for s in shops:
            matrix[d][s.id] = {'main': [], 'backup': []}

    shifts = schedule.shifts.all().select_related('user', 'shop')
    for shift in shifts:
        if shift.date in matrix and shift.shop.id in matrix[shift.date]:
             if shift.role == 'main':
                 matrix[shift.date][shift.shop.id]['main'].append(shift)
             else:
                 matrix[shift.date][shift.shop.id]['backup'].append(shift)

    return render(request, 'scheduling/generator.html', {
        'schedule': schedule,
        'dates': dates,
        'shops': shops,
        'matrix': matrix,
        'change_logs': schedule.change_logs.all().order_by('-created_at')
    })

def _generate_schedule(shops, schedule):
    if schedule.is_published:
        ScheduleChangeLog.objects.create(
            schedule=schedule,
            message="Schedule was completely regenerated."
        )

    schedule.shifts.all().delete()

    from accounts.models import User
    users = User.objects.filter(is_active=True, is_approved=True)

    user_priorities = []
    for u in users:
        p, _ = UserPriority.objects.get_or_create(user=u)
        user_priorities.append((u, p.score))

    user_priorities.sort(key=lambda x: x[1], reverse=True)

    for i in range(7):
        current_date = schedule.week_start_date + datetime.timedelta(days=i)
        day_of_week = current_date.weekday()

        for shop in shops:
            try:
                req_main = shop.requirement.required_main_staff
                req_res = shop.requirement.required_reserve_staff
            except ShopRequirement.DoesNotExist:
                req_main = 1
                req_res = 0

            assigned_main = 0
            for user, score in user_priorities:
                if assigned_main >= req_main:
                    break
                if _can_assign(user, current_date, day_of_week):
                     Shift.objects.create(schedule=schedule, user=user, shop=shop, date=current_date, role='main')
                     assigned_main += 1

            assigned_res = 0
            for user, score in user_priorities:
                if assigned_res >= req_res:
                    break
                if _can_assign(user, current_date, day_of_week):
                    Shift.objects.create(schedule=schedule, user=user, shop=shop, date=current_date, role='backup')
                    assigned_res += 1

def _can_assign(user, date, day_of_week):
    if Shift.objects.filter(user=user, date=date).exists():
        return False
    try:
        if user.preference.top_preferred_day_off == day_of_week:
            return False
    except:
        pass
    return True

@login_required
def shift_delete(request, shift_id):
    if request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden()

    shift = get_object_or_404(Shift, id=shift_id)
    schedule = shift.schedule

    # Log Change
    ScheduleChangeLog.objects.create(
        schedule=schedule,
        user=request.user,
        message=f"Removed {shift.user} from {shift.shop} on {shift.date} ({shift.get_role_display()})"
    )

    shift.delete()
    messages.success(request, "Shift removed.")
    return redirect('scheduling:generator')

@login_required
def shift_add(request, schedule_id, date, shop_id, role):
    if request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden()

    schedule = get_object_or_404(Schedule, id=schedule_id)
    shop = get_object_or_404(Shop, id=shop_id)
    target_date = datetime.datetime.strptime(date, "%Y-%m-%d").date()

    if request.method == 'POST':
        form = ShiftAddForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data['user']

            # Basic validation
            if Shift.objects.filter(user=user, date=target_date).exists():
                messages.error(request, f"{user} is already assigned on {target_date}")
            else:
                Shift.objects.create(
                    schedule=schedule,
                    user=user,
                    shop=shop,
                    date=target_date,
                    role=role
                )
                ScheduleChangeLog.objects.create(
                    schedule=schedule,
                    user=request.user,
                    message=f"Added {user} to {shop} on {target_date} ({role})"
                )
                messages.success(request, "Shift added.")
                return redirect('scheduling:generator')
    else:
        form = ShiftAddForm()

    return render(request, 'scheduling/shift_add.html', {
        'form': form, 'date': target_date, 'shop': shop, 'role': role
    })
