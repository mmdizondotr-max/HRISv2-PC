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
import math

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

    # Calculate Ideal Staff Count
    total_main_slots = 0
    total_res_slots = 0
    for shop in shops:
        try:
            req_main = shop.requirement.required_main_staff
            req_res = shop.requirement.required_reserve_staff
        except ShopRequirement.DoesNotExist:
            req_main = 1
            req_res = 0
        total_main_slots += req_main * 7
        total_res_slots += req_res * 7

    staff_needed_main = math.ceil(total_main_slots / 6)
    # Total capacity provided by these staff (assuming 6 days work + 1 day off/reserve)
    # Actually, they provide 6 Main slots and up to 1 Reserve slot (on their day off).
    # Reserve capacity = staff_needed_main * 1
    # Plus any surplus main capacity that can be used as reserve?
    # Total Slots Provided = staff_needed_main * 7.
    # Used for Main = total_main_slots.
    # Remaining capacity = (staff_needed_main * 7) - total_main_slots.
    reserve_capacity_available = (staff_needed_main * 7) - total_main_slots

    reserve_deficit = max(0, total_res_slots - reserve_capacity_available)
    # Extra staff needed purely for reserve?
    # New staff provides 7 slots of availability (0 Main, 7 Reserve potentially).
    extra_staff = math.ceil(reserve_deficit / 7)

    ideal_staff_count = staff_needed_main + extra_staff

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
        'change_logs': schedule.change_logs.all().order_by('-created_at'),
        'ideal_staff_count': ideal_staff_count
    })

def _generate_schedule(shops, schedule):
    if schedule.is_published:
        ScheduleChangeLog.objects.create(
            schedule=schedule,
            message="Schedule was completely regenerated."
        )

    schedule.shifts.all().delete()

    from accounts.models import User

    # Pre-calculate priorities for ALL active users.
    all_users = User.objects.filter(is_active=True, is_approved=True)

    user_priority_map = {}
    for u in all_users:
        p, _ = UserPriority.objects.get_or_create(user=u)
        user_priority_map[u.id] = {'user': u, 'score': p.score}

    # Helper to get candidates for a shop
    def get_sorted_candidates(shop_obj, date_obj):
        # Candidates are: Applicable + Active + Approved
        cands = shop_obj.applicable_staff.filter(is_active=True, is_approved=True)
        c_list = []
        for c in cands:
             if c.id in user_priority_map:
                 c_list.append((c, user_priority_map[c.id]['score']))
        # Sort desc by score
        c_list.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in c_list]

    for i in range(7):
        current_date = schedule.week_start_date + datetime.timedelta(days=i)
        day_of_week = current_date.weekday()

        # We need to track assigned counts for this day across phases
        shop_status = {}
        for shop in shops:
            try:
                req_main = shop.requirement.required_main_staff
                req_res = shop.requirement.required_reserve_staff
            except ShopRequirement.DoesNotExist:
                req_main = 1
                req_res = 0
            shop_status[shop.id] = {
                'shop': shop,
                'req_main': req_main,
                'req_res': req_res,
                'assigned_main': 0,
                'assigned_res': 0
            }

        # --- Phase 1: Minimum Main Coverage (1 per shop) ---
        # Round robin until all have 1 or we run out of people
        shops_needing_min = [s_id for s_id, s in shop_status.items() if s['assigned_main'] < 1 and s['req_main'] > 0]

        # We iterate in a loop until no more assignments can be made or all satisfied
        while shops_needing_min:
            made_assignment_this_round = False
            for shop_id in list(shops_needing_min): # Copy list to modify
                status = shop_status[shop_id]
                shop = status['shop']

                # Try to assign 1
                candidates = get_sorted_candidates(shop, current_date)
                assigned = False
                for user in candidates:
                    if _can_assign(user, current_date, day_of_week):
                        Shift.objects.create(schedule=schedule, user=user, shop=shop, date=current_date, role='main')
                        status['assigned_main'] += 1
                        assigned = True
                        break # Move to next shop

                if assigned:
                    made_assignment_this_round = True
                    # Check if satisfied min (1)
                    if status['assigned_main'] >= 1:
                        shops_needing_min.remove(shop_id)
                else:
                    # Cannot fill this shop? Remove it to prevent infinite loop?
                    # If we have candidates but they are all busy, then yes.
                    shops_needing_min.remove(shop_id)

            if not made_assignment_this_round:
                break # Stuck, exit phase

        # --- Phase 2: Full Main Coverage ---
        shops_needing_fill = [s_id for s_id, s in shop_status.items() if s['assigned_main'] < s['req_main']]

        while shops_needing_fill:
            made_assignment_this_round = False
            for shop_id in list(shops_needing_fill):
                status = shop_status[shop_id]
                shop = status['shop']

                candidates = get_sorted_candidates(shop, current_date)
                assigned = False
                for user in candidates:
                    if _can_assign(user, current_date, day_of_week):
                        Shift.objects.create(schedule=schedule, user=user, shop=shop, date=current_date, role='main')
                        status['assigned_main'] += 1
                        assigned = True
                        break

                if assigned:
                    made_assignment_this_round = True
                    if status['assigned_main'] >= status['req_main']:
                        shops_needing_fill.remove(shop_id)
                else:
                    shops_needing_fill.remove(shop_id)

            if not made_assignment_this_round:
                break

        # --- Phase 3: Reserve Coverage ---
        # Prioritize shops with assigned_main == 1 (if they need reserve)
        # Reserve candidates override "Preferred Day Off"

        # Helper for reserve assignment
        def assign_reserve_round_robin(target_shop_ids):
            while target_shop_ids:
                made_assignment_this_round = False
                for shop_id in list(target_shop_ids):
                    status = shop_status[shop_id]
                    shop = status['shop']

                    if status['assigned_res'] >= status['req_res']:
                        target_shop_ids.remove(shop_id)
                        continue

                    candidates = get_sorted_candidates(shop, current_date)
                    assigned = False
                    for user in candidates:
                        # Check _can_assign_reserve (ignores preference, ensures not working today)
                        if _can_assign_reserve(user, current_date):
                            Shift.objects.create(schedule=schedule, user=user, shop=shop, date=current_date, role='backup')
                            status['assigned_res'] += 1
                            assigned = True
                            break

                    if assigned:
                        made_assignment_this_round = True
                        if status['assigned_res'] >= status['req_res']:
                            target_shop_ids.remove(shop_id)
                    else:
                        target_shop_ids.remove(shop_id)

                if not made_assignment_this_round:
                    break

        # Split shops
        high_priority = []
        normal_priority = []

        for s_id, s in shop_status.items():
            if s['req_res'] > 0 and s['assigned_res'] < s['req_res']:
                if s['assigned_main'] <= 1:
                    high_priority.append(s_id)
                else:
                    normal_priority.append(s_id)

        assign_reserve_round_robin(high_priority)
        assign_reserve_round_robin(normal_priority)

def _can_assign(user, date, day_of_week):
    # For MAIN assignment
    if Shift.objects.filter(user=user, date=date).exists():
        return False
    try:
        if user.preference.top_preferred_day_off == day_of_week:
            return False
    except:
        pass
    return True

def _can_assign_reserve(user, date):
    # For RESERVE assignment
    # 1. Must not be working today (Main or Backup)
    if Shift.objects.filter(user=user, date=date).exists():
        return False
    # 2. Ignore Preferred Day Off (as per requirement)
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

            # Check if user is applicable for this shop?
            # Requirement: "In the actions that can be done in the account list... assign each account to each applicable shop... when generating the schedule, only staff applicable... will be included"
            # It doesn't explicitly forbid manual override. But usually consistency is good.
            # I will not strictly forbid it here to allow emergency overrides, as manual add is an override mechanism.
            # The "auto generator" must strictly follow it.

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
