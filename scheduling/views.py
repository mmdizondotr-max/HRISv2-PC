from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import HttpResponseForbidden
from .models import Preference, Schedule, Shift, UserShopScore, ShopRequirement, ScheduleChangeLog, UserPriority
from attendance.models import Shop, ShopOperatingHours, TimeLog
from accounts.models import AccountActionLog, PasswordResetRequest
from django.db.models import Count, Q
from django.utils import timezone
from .forms import PreferenceForm, ShiftAddForm
from .utils import ensure_roving_shop_and_assignments, update_scores_for_date, calculate_assignment_score, CurrentWeekAssignments
import datetime
import math
import random
from accounts.models import User

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
            # Enforce 1 day off
            pref.preferred_days_off_count = 1
            pref.save()
            messages.success(request, "Preferences saved.")
            return redirect('scheduling:preferences')
    else:
        form = PreferenceForm(instance=pref)

    return render(request, 'scheduling/preferences.html', {'form': form})

@login_required
def my_schedule(request):
    today = timezone.localdate()
    # Week starts Monday (0)
    start_of_current_week = today - datetime.timedelta(days=today.weekday())
    start_of_next_week = start_of_current_week + datetime.timedelta(days=7)

    # Filter Shops by Area for Visibility
    # "Schedules and current status of time-ins/outs are also now separated per Area. Administrators can see all areas, Supervisors can only see theirs."
    # Regulars also only see theirs (confirmed in planning).

    shops_qs = Shop.objects.filter(is_active=True)

    user_area = None
    if request.user.tier != 'administrator' and not request.user.is_superuser:
        if request.user.area:
            user_area = request.user.area
            shops_qs = shops_qs.filter(area=user_area)
        else:
            # Unassigned users see nothing? Or maybe they see nothing.
            shops_qs = shops_qs.none()

    # Sort: Roving first
    roving_shops = list(shops_qs.filter(name='Roving'))
    other_shops = list(shops_qs.exclude(name='Roving'))
    shops = roving_shops + other_shops

    schedules_data = []

    def build_schedule_data(schedule_obj):
        if not schedule_obj:
            return None
        dates = [schedule_obj.week_start_date + datetime.timedelta(days=i) for i in range(7)]
        matrix = {}
        for d in dates:
            matrix[d] = {}
            for s in shops:
                matrix[d][s.id] = {'main': [], 'backup': []}

        shifts = schedule_obj.shifts.all().select_related('user', 'shop')

        # Filter shifts by shop (which is already filtered by Area)
        # However, for Administrators seeing "All Areas", we want to display them organized.
        # But 'shops' list handles the columns.
        # If Admin, 'shops' contains ALL shops.

        # NOTE: Roving Visibility Logic
        # "If Global: Does a Supervisor in "Area A" see "Roving" in their list? If so, do they see all staff in Roving, or only staff from "Area A" who are currently in Roving?"
        # Answer: Roving is now per Area. So Supervisor A sees Roving A.
        # So standard shop filtering works.

        # Fetch TimeLogs for the week
        week_end_date = dates[-1]
        logs = TimeLog.objects.filter(date__range=[dates[0], week_end_date]).select_related('user', 'shop')
        logs_map = {} # (date, shop_id) -> list of users who logged in
        logs_objects_map = {} # (date, shop_id) -> list of TimeLog objects

        for log in logs:
            if not log.shop: continue
            key = (log.date, log.shop.id)
            if key not in logs_map:
                logs_map[key] = []
                logs_objects_map[key] = []
            logs_map[key].append(log.user)
            logs_objects_map[key].append(log)

        for shift in shifts:
            if shift.date in matrix and shift.shop.id in matrix[shift.date]:
                # Determine actual attendance status
                status_dict = {'assigned': shift.user, 'actual': None, 'status': 'absent'}

                key = (shift.date, shift.shop.id)
                logged_users = logs_map.get(key, [])

                # Check if assigned user is present
                found = False
                for u in logged_users:
                    if u.id == shift.user.id:
                        found = True
                        break

                if found:
                    status_dict['actual'] = shift.user
                    status_dict['status'] = 'reported'
                else:
                     # Absent Logic: "Only appears if date has passed"
                     if shift.date < today:
                          status_dict['status'] = 'absent'
                     else:
                          status_dict['status'] = '' # Future/Today absent is just blank

                if shift.role == 'main':
                    matrix[shift.date][shift.shop.id]['main'].append(shift)
                else:
                    matrix[shift.date][shift.shop.id]['backup'].append(shift)

        # Re-process matrix to attach 'actual' info
        for d in dates:
            for s in shops:
                # Main
                shifts_list = matrix[d][s.id]['main']
                key = (d, s.id)
                logged_users = logs_map.get(key, [])[:] # copy

                # 1. Mark Present
                matched_logs = []
                for shift in shifts_list:
                    match = None
                    for u in logged_users:
                        if u.id == shift.user.id:
                            match = u
                            break

                    if match:
                        shift.actual_user = shift.user

                        # Find the actual log object to check times
                        log_obj = None
                        for l in logs_objects_map.get(key, []):
                            if l.user_id == shift.user.id:
                                log_obj = l
                                break

                        if log_obj:
                             if log_obj.time_in and log_obj.time_out:
                                 shift.status = 'reported'
                             elif log_obj.time_in and not log_obj.time_out:
                                 if d == today:
                                     shift.status = 'ongoing' # Display "Ongoing"
                                 elif d < today:
                                     shift.status = 'incomplete' # Display "Incomplete Log"
                                 else:
                                     shift.status = 'reported' # Should not happen for future
                             else:
                                 shift.status = 'reported' # Fallback
                        else:
                             shift.status = 'reported'

                        matched_logs.append(match)
                    else:
                        shift.actual_user = None
                        if d < today:
                            shift.status = 'absent'
                        else:
                            shift.status = ''

                # Remove matched from available logs
                for m in matched_logs:
                    if m in logged_users:
                        logged_users.remove(m)

                # Second pass: Assign remaining logs to 'absent' shifts (Substitutes)
                for shift in shifts_list:
                    if shift.status == 'absent':
                        if logged_users:
                            # Take first available
                            sub = logged_users.pop(0)
                            shift.actual_user = sub
                            shift.status = 'substituted'
                        else:
                            # Truly absent
                            pass

                # 3. Handle Supplement (Remaining logged users who weren't assigned or used as subs)
                # Create a pseudo-shift object for display
                for extra_user in logged_users:
                    # Determine status for extra user (Ongoing vs Present vs Incomplete)
                    # We need the log object
                    extra_status = 'supplement' # Default base
                    log_obj = None
                    for l in logs_objects_map.get(key, []):
                        if l.user_id == extra_user.id:
                            log_obj = l
                            break

                    # Refine status based on time_out presence?
                    # Prompt says: "show his name card and add 'Supplement'"
                    # But also: "latest status of whether reported, absent, incomplete, supplement, etc."
                    # If I use 'supplement', the template will show "SUPPLEMENT".
                    # If I want to show "Incomplete" + "Supplement", it gets complex.
                    # Let's stick to "Supplement" as the primary status indicator for these unassigned folks.
                    # Or we can check if ongoing.

                    # Create a Simple Namespace or dict-like object
                    class PseudoShift:
                        def __init__(self, user, status):
                            self.user = user
                            self.status = status
                            self.actual_user = user
                            self.role = 'supplement'

                    matrix[d][s.id]['main'].append(PseudoShift(extra_user, 'supplement'))

                # Handle Backup similarly
                # Just listing
                pass

        return {
            'schedule': schedule_obj,
            'dates': dates,
            'matrix': matrix,
            'change_logs': schedule_obj.change_logs.all().order_by('-created_at'),
            'logs_map': logs_map
        }

    # Current Week
    current_schedule = Schedule.objects.filter(week_start_date=start_of_current_week, is_published=True).first()
    data_curr = build_schedule_data(current_schedule)
    if data_curr:
        schedules_data.append(data_curr)

    # Next Week
    next_schedule = Schedule.objects.filter(week_start_date=start_of_next_week, is_published=True).first()
    data_next = build_schedule_data(next_schedule)
    if data_next:
        schedules_data.append(data_next)

    all_users = User.objects.filter(is_active=True, is_approved=True)
    return render(request, 'scheduling/my_schedule.html', {
        'schedules_data': schedules_data,
        'shops': shops,
        'today': today,
        'all_users': all_users,
    })

@login_required
def schedule_history_list(request):
    today = timezone.localdate()
    start_of_current_week = today - datetime.timedelta(days=today.weekday())

    schedules = Schedule.objects.filter(is_published=True).order_by('-week_start_date')

    if request.user.tier == 'regular':
        # Limit to past 2 weeks relative to current week
        # Allowed: Current Week, Current Week - 1, Current Week - 2
        min_date = start_of_current_week - datetime.timedelta(weeks=2)
        schedules = schedules.filter(week_start_date__gte=min_date)

    return render(request, 'scheduling/schedule_history_list.html', {'schedules': schedules})

@login_required
def schedule_history_detail(request, schedule_id):
    schedule = get_object_or_404(Schedule, id=schedule_id, is_published=True)

    if request.user.tier == 'regular':
        today = timezone.localdate()
        start_of_current_week = today - datetime.timedelta(days=today.weekday())
        min_date = start_of_current_week - datetime.timedelta(weeks=2)
        if schedule.week_start_date < min_date:
             return HttpResponseForbidden("You are not authorized to view schedules older than 2 weeks.")

    dates = [schedule.week_start_date + datetime.timedelta(days=i) for i in range(7)]

    # Filter Shops by Area
    shops_qs = Shop.objects.all()
    if request.user.tier != 'administrator' and not request.user.is_superuser:
        if request.user.area:
            shops_qs = shops_qs.filter(area=request.user.area)
        else:
            shops_qs = shops_qs.none()

    roving_shops = list(shops_qs.filter(name='Roving'))
    other_shops = list(shops_qs.exclude(name='Roving'))
    shops = roving_shops + other_shops

    matrix = {}
    for d in dates:
        matrix[d] = {}
        for s in shops:
            matrix[d][s.id] = {'main': [], 'backup': []}

    shifts = schedule.shifts.all().select_related('user', 'shop')

    # Fetch logs to determine status (Absent/Substituted)
    week_end = dates[-1]
    logs = TimeLog.objects.filter(date__range=[dates[0], week_end]).select_related('user', 'shop')
    logs_map = {}
    for log in logs:
        if log.shop:
            key = (log.date, log.shop.id)
            if key not in logs_map:
                logs_map[key] = []
            logs_map[key].append(log.user)

    for shift in shifts:
        if shift.date in matrix and shift.shop.id in matrix[shift.date]:
             # Determine Status (Simplified for History)
             # Check if present in logs
             key = (shift.date, shift.shop.id)
             shop_logs = logs_map.get(key, [])

             is_present = any(u.id == shift.user.id for u in shop_logs)

             if is_present:
                 shift.status = 'reported'
                 shift.actual_user = shift.user
             else:
                 if shift.date < timezone.localdate():
                     shift.status = 'absent'
                 else:
                     shift.status = ''
                 shift.actual_user = None

             if shift.role == 'main':
                 matrix[shift.date][shift.shop.id]['main'].append(shift)
             else:
                 matrix[shift.date][shift.shop.id]['backup'].append(shift)

    return render(request, 'scheduling/schedule_history_detail.html', {
        'schedule': schedule,
        'dates': dates,
        'shops': shops,
        'matrix': matrix,
        'change_logs': schedule.change_logs.all().order_by('-created_at')
    })

@login_required
def generator(request):
    from accounts.models import Area
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    ensure_roving_shop_and_assignments()

    # Determine Target Area
    target_area = None
    areas = Area.objects.all()

    if request.user.tier == 'supervisor' and not request.user.is_superuser:
        target_area = request.user.area
        if not target_area:
             messages.error(request, "You are not assigned to an Area.")
             return redirect('attendance:home')
    else:
        # Administrator/Superuser
        area_id = request.GET.get('area_id')
        if area_id:
            target_area = get_object_or_404(Area, id=area_id)
        else:
            # If no Area selected, show selector (or default to first?)
            # The prompt says: "Select a specific Area to generate the schedule for"
            # We can let them select via a dropdown in GET param
            pass

    today = timezone.localdate()
    days_until_monday = (0 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7

    next_week_start = today + datetime.timedelta(days=days_until_monday)

    # Handle 4 weeks
    weeks = []
    for i in range(4):
        start_date = next_week_start + datetime.timedelta(days=i*7)
        sch, _ = Schedule.objects.get_or_create(week_start_date=start_date)
        weeks.append(sch)

    current_schedule = weeks[0]

    # Filter Shops
    shops_qs = Shop.objects.filter(is_active=True)

    if target_area:
        shops_qs = shops_qs.filter(area=target_area)
    elif request.user.is_superuser or request.user.tier == 'administrator':
        # If Admin hasn't selected an area, maybe show nothing or all?
        # Prompt: "Select a specific Area to generate the schedule for"
        # It implies generation is per-area.
        # If no area selected, we probably shouldn't show the matrix or allow generate?
        # But for UI consistency, let's wait for selection.
        if not area_id:
             shops_qs = shops_qs.none()

    roving_shops = list(shops_qs.filter(name='Roving'))
    other_shops = list(shops_qs.exclude(name='Roving'))
    shops = roving_shops + other_shops

    if request.method == 'POST':
        if 'generate' in request.POST:
            if not target_area and (request.user.is_superuser or request.user.tier == 'administrator'):
                 messages.error(request, "Please select an Area to generate schedule.")
            else:
                _generate_multi_week_schedule(shops, weeks, target_area)
                messages.success(request, f"Schedule generated for 4 weeks for {target_area}.")

            # Redirect preserving GET param
            redirect_url = 'scheduling:generator'
            if target_area and (request.user.is_superuser or request.user.tier == 'administrator'):
                return redirect(f"{reverse('scheduling:generator')}?area_id={target_area.id}")
            return redirect(redirect_url)

        elif 'publish' in request.POST:
            current_schedule.is_published = True
            current_schedule.save()
            messages.success(request, "Schedule published (Week 1 only).")
            return redirect('scheduling:generator')
        elif 'clear' in request.POST:
            for sch in weeks:
                sch.shifts.all().delete()
                sch.change_logs.all().delete()
                sch.is_published = False
                sch.save()
            messages.success(request, "Generated schedule window cleared.")
            return redirect('scheduling:generator')

    # Prepare data for Template
    weeks_data = []
    for schedule in weeks:
        dates = [schedule.week_start_date + datetime.timedelta(days=i) for i in range(7)]
        matrix = {}
        for d in dates:
            matrix[d] = {}
            for s in shops:
                matrix[d][s.id] = {'main': [], 'backup': []}

        shifts = schedule.shifts.all().select_related('user', 'shop')
        duty_counts = {} # user_id -> count

        for shift in shifts:
            if shift.date in matrix and shift.shop.id in matrix[shift.date]:
                if shift.role == 'main':
                    matrix[shift.date][shift.shop.id]['main'].append(shift)
                    duty_counts[shift.user.id] = duty_counts.get(shift.user.id, 0) + 1
                else:
                    matrix[shift.date][shift.shop.id]['backup'].append(shift)

        weeks_data.append({
            'schedule': schedule,
            'dates': dates,
            'matrix': matrix,
            'duty_counts': duty_counts
        })

    from django.urls import reverse # Import needed
    return render(request, 'scheduling/generator.html', {
        'weeks_data': weeks_data,
        'current_schedule': current_schedule,
        'shops': shops,
        'change_logs': current_schedule.change_logs.all().order_by('-created_at'),
        'areas': areas,
        'selected_area': target_area,
    })

def _generate_multi_week_schedule(shops, weeks, area):
    from accounts.models import User

    # 1. Prepare Data
    # Filter users by Area
    all_users = list(User.objects.filter(is_active=True, is_approved=True, area=area).select_related('preference'))

    roving_shop = None
    for s in shops:
        if s.name == 'Roving':
            roving_shop = s
            break

    # Roving Shop must belong to the Area
    if not roving_shop:
        roving_shop = Shop.objects.filter(name='Roving', area=area).first()
        if not roving_shop:
             # Just in case
             roving_shop, _ = Shop.objects.get_or_create(name='Roving', area=area, is_active=True)

    # 2. Iterate Weeks
    for schedule in weeks:
        week_start = schedule.week_start_date

        # Clear existing
        schedule.shifts.filter(shop__in=shops).delete()
        if schedule.is_published:
            ScheduleChangeLog.objects.create(schedule=schedule, message="Regenerated.")

        # Prepare History Data for Scoring
        # a. Prev week (relative to this schedule week)
        prev_week_start = week_start - datetime.timedelta(days=7)
        prev_week_end = week_start - datetime.timedelta(days=1)

        prev_week_logs = list(TimeLog.objects.filter(date__range=[prev_week_start, prev_week_end]).select_related('shop'))
        prev_week_shifts = list(Shift.objects.filter(date__range=[prev_week_start, prev_week_end])) # We can't query shifts easily if they don't exist yet for future weeks in this loop if sequential?
        # Note: If generating multiple weeks, 'prev_week_shifts' for Week 2 are shifts from Week 1 (which we just generated).
        # We need to ensure we can access them. Since we saved them to DB in previous iteration, we can query them.

        # b. Past 3 weeks (excluding prev)
        past_3_start = prev_week_start - datetime.timedelta(weeks=3)
        past_3_end = prev_week_start - datetime.timedelta(days=1)
        past_3_weeks_logs = list(TimeLog.objects.filter(date__range=[past_3_start, past_3_end]).select_related('shop'))

        history_data = {
            'prev_week_logs': prev_week_logs,
            'prev_week_shifts': prev_week_shifts,
            'past_3_weeks_logs': past_3_weeks_logs
        }

        # Check if we should use attendance history (avoid "Absent" bonus if generating future weeks where logs don't exist yet)
        # If prev_week_end is in the future (or today), we likely don't have complete logs.
        # However, for load_test_data, we simulate logs sequentially, so checking real time might be tricky if "today" is fixed.
        # But standard use case:
        # If prev_week_end < timezone.localdate(), we assume history is valid.
        use_attendance_history = prev_week_end < timezone.localdate()

        # Simulation Logic for Future Weeks (Preview Weeks 2-4)
        # "Assume that Week 1 has 100% attendance (no substitutions)"
        if not use_attendance_history:
             # Check if prev_week_shifts exist (they should if we just generated them)
             if prev_week_shifts:
                 # Construct Simulated Logs from Shifts (Assume 100% attendance for Main shifts)
                 simulated_logs = []
                 for shift in prev_week_shifts:
                     if shift.role == 'main':
                         # Create a dummy TimeLog object (not saved to DB)
                         # We need user, shop, date.
                         # Note: shift.user is a User object.
                         t = TimeLog(
                             user=shift.user,
                             shop=shift.shop,
                             date=shift.date,
                             time_in=datetime.time(9,0),
                             time_out=datetime.time(17,0)
                         )
                         simulated_logs.append(t)

                 history_data['prev_week_logs'] = simulated_logs
                 # Enable history usage since we now have simulated logs
                 use_attendance_history = True

        current_assignments = CurrentWeekAssignments()

        # Determine Max Duty Slots required
        # Iterate through shops to find max duty needed
        max_duty_slots = 0
        for shop in shops:
            try:
                # If Roving, usually 1 supervisor? But Roving shop usually doesn't have ShopRequirement?
                # Assuming Roving has 0 requirement or specific logic.
                if shop.name == 'Roving':
                     # We can treat Supervisors as having a requirement of 1 for Roving if not defined?
                     # Existing logic looped supervisors.
                     # Let's assume Roving needs 1 slot per available supervisor?
                     # Or Roving is just treated as a shop with 1 slot?
                     # Let's check ShopRequirement for Roving.
                     req = shop.requirement.required_main_staff
                else:
                    req = shop.requirement.required_main_staff
            except ShopRequirement.DoesNotExist:
                req = 1 # Default

            if req > max_duty_slots:
                max_duty_slots = req

        # Slot Loop: Duty 1, Duty 2...
        for slot_idx in range(1, max_duty_slots + 1):

            # Day Loop
            for day_offset in range(7):
                current_date = week_start + datetime.timedelta(days=day_offset)

                # Shop Loop
                for shop in shops:
                    # Check if this shop needs this slot
                    try:
                        req_main = shop.requirement.required_main_staff
                    except ShopRequirement.DoesNotExist:
                        req_main = 1

                    if slot_idx > req_main:
                        continue

                    # Find candidates
                    candidates = []
                    # Filter applicable staff
                    # Supervisors -> Roving only? Regulars -> Non-Roving only?
                    # The prompt says: "Assignments will now be done per slots... All Duty Staff 1..."
                    # We should respect applicable_shops.

                    # Optimization: Filter users who are not already assigned Duty ON THIS DAY
                    # (One person cannot be Duty at 2 shops same day)

                    potential_users = [u for u in shop.applicable_staff.all() if u.is_active and u.is_approved]

                    # Filter candidates who are actually available (not assigned elsewhere today)
                    available_users = []
                    for user in potential_users:
                        if not current_assignments.is_assigned_on_day(user.id, current_date):
                            available_users.append(user)

                    # Calculate Min Duty among available candidates
                    min_duty = None
                    if available_users:
                        duty_counts = [current_assignments.get_duty_count(u.id) for u in available_users]
                        min_duty = min(duty_counts)

                    valid_candidates = []
                    for user in available_users:
                        score, breakdown = calculate_assignment_score(user, shop, current_date, history_data, current_assignments, min_duty_count_among_eligible=min_duty, use_attendance_history=use_attendance_history)
                        valid_candidates.append((user, score, breakdown))

                    if valid_candidates:
                        # Pick highest score
                        # Sort by score desc. Tie-break randomized?
                        # Sort is stable. Shuffle first to randomize ties?
                        random.shuffle(valid_candidates)
                        valid_candidates.sort(key=lambda x: x[1], reverse=True)

                        best_user, best_score, best_breakdown = valid_candidates[0]

                        # Check if Roving. If so, clear score/breakdown
                        if shop.name == 'Roving':
                            final_score = None
                            final_breakdown = None
                        else:
                            final_score = best_score
                            final_breakdown = best_breakdown

                        Shift.objects.create(
                            schedule=schedule,
                            user=best_user,
                            shop=shop,
                            date=current_date,
                            role='main',
                            score=final_score,
                            score_breakdown=final_breakdown
                        )
                        current_assignments.add_assignment(best_user.id, shop.id, current_date)

        # Standby Assignment Loop (Per Day)
        # "All staff not assigned as Duty Staff are automatically assigned as Standby Staff of that same day."
        # "The Standby Staff will be ranked based on who had the least Duty Staff assignment during the previous week."

        for day_offset in range(7):
            current_date = week_start + datetime.timedelta(days=day_offset)

            # Identify Duty staff for this day
            duty_users_today = set()
            for uid, sid, d in current_assignments.assignments:
                if d == current_date:
                    duty_users_today.add(uid)

            # Identify Standby Candidates (All active staff not in duty_users_today)
            standby_candidates = []
            for user in all_users:
                if user.id not in duty_users_today:
                    # Rank metric: Least Duty Staff assignment during PREVIOUS WEEK.
                    # Count prev week duty shifts
                    duty_prev_week = 0
                    for s in history_data['prev_week_shifts']:
                        if s.user_id == user.id and s.role == 'main':
                            duty_prev_week += 1

                    standby_candidates.append((user, duty_prev_week))

            # Sort by least duty prev week (asc)
            # "Highest rank will be the first to act as substitute" -> This implies we just list them.
            # But we need to assign them a Shift record.
            # We assign to Roving Shop with role='backup'.
            # We assign a score equal to negative duty_prev_week (so lower duty = higher score/rank)?
            # Or just store the rank index?
            # Let's store negative duty_prev_week as score so higher is better?
            # Or just store duty_prev_week.
            # Prompt: "Highest rank will be the first... ranked based on who had the least Duty..."
            # So User with 0 duty > User with 1 duty.
            # We should probably sort them and then maybe assign them.

            random.shuffle(standby_candidates) # Randomize ties
            standby_candidates.sort(key=lambda x: x[1]) # Ascending order of prev duty

            # Create Shifts
            # We use Roving shop for the "Universal Pool".
            for idx, (user, prev_duty) in enumerate(standby_candidates):
                Shift.objects.create(
                    schedule=schedule,
                    user=user,
                    shop=roving_shop,
                    date=current_date,
                    role='backup',
                    score=None, # No score for standby as requested
                    score_breakdown=None
                )


@login_required
def shift_delete(request, shift_id):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    shift = get_object_or_404(Shift, id=shift_id)
    schedule = shift.schedule

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
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    schedule = get_object_or_404(Schedule, id=schedule_id)
    shop = get_object_or_404(Shop, id=shop_id)
    target_date = datetime.datetime.strptime(date, "%Y-%m-%d").date()

    if request.method == 'POST':
        form = ShiftAddForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data['user']

            if Shift.objects.filter(user=user, date=target_date).exists():
                messages.error(request, f"{user} is already assigned on {target_date}")
            else:
                Shift.objects.create(
                    schedule=schedule,
                    user=user,
                    shop=shop,
                    date=target_date,
                    role=role,
                    score=0.0
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

@login_required
def regenerate_remaining_week(request, schedule_id):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    schedule = get_object_or_404(Schedule, id=schedule_id)
    today = timezone.localdate()
    start_date = today + datetime.timedelta(days=1)
    week_start = schedule.week_start_date
    week_end = week_start + datetime.timedelta(days=6)

    if start_date > week_end:
        messages.warning(request, "No remaining days in this week to regenerate.")
        return redirect('scheduling:my_schedule')

    # 1. Clear future shifts
    shifts_to_delete = Shift.objects.filter(schedule=schedule, date__gte=start_date)
    shifts_to_delete.delete()

    ScheduleChangeLog.objects.create(
        schedule=schedule,
        user=request.user,
        message=f"Regenerated schedule from {start_date} onwards."
    )

    # 2. Prepare Context for Generation
    # a. Shops
    shops_qs = Shop.objects.filter(is_active=True)
    roving = shops_qs.filter(name='Roving').first()
    others = list(shops_qs.exclude(name='Roving'))
    if roving:
        shops = [roving] + others
    else:
        shops = others
        if not roving:
            roving, _ = Shop.objects.get_or_create(name='Roving', is_active=True)

    # b. History Data (Same as generator)
    prev_week_start = week_start - datetime.timedelta(days=7)
    prev_week_end = week_start - datetime.timedelta(days=1)
    prev_week_logs = list(TimeLog.objects.filter(date__range=[prev_week_start, prev_week_end]).select_related('shop'))
    prev_week_shifts = list(Shift.objects.filter(date__range=[prev_week_start, prev_week_end]))

    past_3_start = prev_week_start - datetime.timedelta(weeks=3)
    past_3_end = prev_week_start - datetime.timedelta(days=1)
    past_3_weeks_logs = list(TimeLog.objects.filter(date__range=[past_3_start, past_3_end]).select_related('shop'))

    history_data = {
        'prev_week_logs': prev_week_logs,
        'prev_week_shifts': prev_week_shifts,
        'past_3_weeks_logs': past_3_weeks_logs
    }

    use_attendance_history = prev_week_end < timezone.localdate()
    if not use_attendance_history and prev_week_shifts:
         simulated_logs = []
         for shift in prev_week_shifts:
             if shift.role == 'main':
                 t = TimeLog(
                     user=shift.user,
                     shop=shift.shop,
                     date=shift.date,
                     time_in=datetime.time(9,0),
                     time_out=datetime.time(17,0)
                 )
                 simulated_logs.append(t)
         history_data['prev_week_logs'] = simulated_logs
         use_attendance_history = True

    # c. Initialize Current Assignments with EXISTING shifts (past days of this week)
    current_assignments = CurrentWeekAssignments()
    existing_shifts = Shift.objects.filter(schedule=schedule, date__lt=start_date)
    for s in existing_shifts:
        current_assignments.add_assignment(s.user.id, s.shop.id, s.date)

    # 3. Run Generation Logic (Partial)
    all_users = list(User.objects.filter(is_active=True, is_approved=True).select_related('preference'))

    # Calc Max Duty Slots
    max_duty_slots = 0
    for shop in shops:
        try:
            req = shop.requirement.required_main_staff
        except ShopRequirement.DoesNotExist:
            req = 1
        if req > max_duty_slots:
            max_duty_slots = req

    # Loop
    for slot_idx in range(1, max_duty_slots + 1):
        for day_offset in range(7):
            current_date = week_start + datetime.timedelta(days=day_offset)

            # Skip if before start_date
            if current_date < start_date:
                continue

            for shop in shops:
                try:
                    req_main = shop.requirement.required_main_staff
                except ShopRequirement.DoesNotExist:
                    req_main = 1

                if slot_idx > req_main:
                    continue

                potential_users = [u for u in shop.applicable_staff.all() if u.is_active and u.is_approved]
                available_users = []
                for user in potential_users:
                    if not current_assignments.is_assigned_on_day(user.id, current_date):
                        available_users.append(user)

                min_duty = None
                if available_users:
                    duty_counts = [current_assignments.get_duty_count(u.id) for u in available_users]
                    min_duty = min(duty_counts)

                valid_candidates = []
                for user in available_users:
                    score, breakdown = calculate_assignment_score(user, shop, current_date, history_data, current_assignments, min_duty_count_among_eligible=min_duty, use_attendance_history=use_attendance_history)
                    valid_candidates.append((user, score, breakdown))

                if valid_candidates:
                    random.shuffle(valid_candidates)
                    valid_candidates.sort(key=lambda x: x[1], reverse=True)
                    best_user, best_score, best_breakdown = valid_candidates[0]

                    if shop.name == 'Roving':
                        final_score = None
                        final_breakdown = None
                    else:
                        final_score = best_score
                        final_breakdown = best_breakdown

                    Shift.objects.create(
                        schedule=schedule,
                        user=best_user,
                        shop=shop,
                        date=current_date,
                        role='main',
                        score=final_score,
                        score_breakdown=final_breakdown
                    )
                    current_assignments.add_assignment(best_user.id, shop.id, current_date)

    # Standby Loop
    roving_shop = Shop.objects.filter(name='Roving').first()

    for day_offset in range(7):
        current_date = week_start + datetime.timedelta(days=day_offset)
        if current_date < start_date:
            continue

        duty_users_today = set()
        for uid, sid, d in current_assignments.assignments:
            if d == current_date:
                duty_users_today.add(uid)

        standby_candidates = []
        for user in all_users:
            if user.id not in duty_users_today:
                duty_prev_week = 0
                for s in history_data['prev_week_shifts']:
                    if s.user_id == user.id and s.role == 'main':
                        duty_prev_week += 1
                standby_candidates.append((user, duty_prev_week))

        random.shuffle(standby_candidates)
        standby_candidates.sort(key=lambda x: x[1])

        for idx, (user, prev_duty) in enumerate(standby_candidates):
            Shift.objects.create(
                schedule=schedule,
                user=user,
                shop=roving_shop,
                date=current_date,
                role='backup',
                score=None,
                score_breakdown=None
            )

    messages.success(request, f"Schedule regenerated from {start_date} to {week_end}.")
    return redirect('scheduling:my_schedule')

@login_required
def shift_update(request, shift_id):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    shift = get_object_or_404(Shift, id=shift_id)
    if request.method == 'POST':
        new_user_id = request.POST.get('user_id')

        if new_user_id == 'REMOVE':
            # Handle Removal
            old_user = shift.user
            schedule = shift.schedule
            target_date = shift.date

            # Delete the shift
            shift.delete()

            # Log Change
            ScheduleChangeLog.objects.create(
                schedule=schedule,
                user=request.user,
                message=f"Manually removed {old_user} from {shift.shop} on {shift.date}."
            )

            # Check if Supervisor -> Assign to Roving
            if old_user.tier == 'supervisor':
                roving_shop = Shop.objects.filter(name='Roving').first()
                if roving_shop:
                    # Check if already has a shift in Roving (to be safe)
                    existing = Shift.objects.filter(user=old_user, date=target_date, shop=roving_shop).exists()
                    if not existing:
                        Shift.objects.create(
                            schedule=schedule,
                            user=old_user,
                            shop=roving_shop,
                            date=target_date,
                            role='main', # Default for Supervisors in Roving
                            score=None,
                            score_breakdown={'Manual Restore to Roving': 0.0}
                        )
                        ScheduleChangeLog.objects.create(
                            schedule=schedule,
                            user=request.user,
                            message=f"Automatically restored Supervisor {old_user} to Roving on {target_date}."
                        )

            messages.success(request, f"Removed {old_user} from shift.")

        elif new_user_id:
            new_user = get_object_or_404(User, id=new_user_id)
            old_user = shift.user

            # Check if New User is Supervisor -> Remove from Roving if assigned there
            if new_user.tier == 'supervisor':
                roving_shop = Shop.objects.filter(name='Roving').first()
                if roving_shop:
                     roving_shift = Shift.objects.filter(user=new_user, date=shift.date, shop=roving_shop).first()
                     if roving_shift:
                         roving_shift.delete()
                         # Log implied by the update below, or maybe explicit?
                         # Usually standard logic is just "Moved".

            # Check if Old User is Supervisor -> Return to Roving?
            # Prompt says: "Supervisors can be assigned in any slot as needed and they will be removed to Roving automatically."
            # This refers to Case 2 (Removing from Roving when assigned elsewhere) and Case 1 (Returning to Roving when removed).
            # If we are REPLACING old_user (supervisor) with new_user:
            # Should old_user go back to Roving?
            # Prompt doesn't explicitly say "Swapping", but "Changing staff".
            # Logic: If I replace Supervisor A with Regular B. Supervisor A is now free.
            # Should Supervisor A go to Roving? Yes, per "Supervisors ... automatically assigned to Roving".

            if old_user.tier == 'supervisor':
                roving_shop = Shop.objects.filter(name='Roving').first()
                if roving_shop:
                     existing = Shift.objects.filter(user=old_user, date=shift.date, shop=roving_shop).exists()
                     if not existing:
                         Shift.objects.create(
                            schedule=shift.schedule,
                            user=old_user,
                            shop=roving_shop,
                            date=shift.date,
                            role='main',
                            score=None,
                            score_breakdown={'Manual Restore to Roving': 0.0}
                         )

            shift.user = new_user
            shift.score = 0.0
            shift.score_breakdown = {'Manual Override': 0.0}
            shift.save()

            ScheduleChangeLog.objects.create(
                schedule=shift.schedule,
                user=request.user,
                message=f"Manually replaced {old_user} with {new_user} on {shift.date} at {shift.shop}"
            )
            messages.success(request, "Shift updated.")
        else:
            messages.error(request, "No user selected.")

    return redirect('scheduling:my_schedule')

def _generate_schedule(shops, schedule):
    return _generate_multi_week_schedule(shops, [schedule])

def reset_system_data(request_user):
    """
    Deletes all data except:
    - Superusers
    - The 'Roving' Shop
    - The request_user (to prevent self-lockout)
    """
    # 1. Users: Exclude superusers and current user
    users_to_delete = User.objects.exclude(Q(is_superuser=True) | Q(id=request_user.id))
    users_to_delete.delete()

    # 2. Shops: Exclude 'Roving'
    shops_to_delete = Shop.objects.exclude(name='Roving')
    shops_to_delete.delete()

    # 3. All other operational data
    Schedule.objects.all().delete()
    TimeLog.objects.all().delete()
    AccountActionLog.objects.all().delete()
    ScheduleChangeLog.objects.all().delete()
    UserPriority.objects.all().delete()
    UserShopScore.objects.all().delete()
    Preference.objects.all().delete()
    PasswordResetRequest.objects.all().delete()

@user_passes_test(lambda u: u.is_authenticated and (u.tier == 'administrator' or u.is_superuser))
def reset_data(request):
    if request.method == 'POST':
        if 'confirm_reset' in request.POST:
            password = request.POST.get('password')
            if not password:
                messages.error(request, "Password is required to confirm reset.")
            elif not request.user.check_password(password):
                messages.error(request, "Incorrect password. Data reset cancelled.")
            else:
                reset_system_data(request.user)
                messages.success(request, "All system data has been reset.")
                return redirect('scheduling:generator')

    return render(request, 'scheduling/reset_confirm.html')

@user_passes_test(lambda u: u.is_authenticated and (u.tier == 'administrator' or u.is_superuser))
def load_test_data(request):
    from accounts.models import Area

    if request.method == 'POST':
        # 0. Reset Data first
        reset_system_data(request.user)

        # 1. Create 2 Areas
        area1, _ = Area.objects.get_or_create(name="Area 1")
        area2, _ = Area.objects.get_or_create(name="Area 2")

        # Create Shops for Area 1
        shop1_a1, _ = Shop.objects.get_or_create(name="A1 Shop 1", area=area1)
        shop2_a1, _ = Shop.objects.get_or_create(name="A1 Shop 2", area=area1)
        shop1_a1.is_active = True
        shop2_a1.is_active = True
        shop1_a1.save()
        shop2_a1.save()

        # Create Shops for Area 2
        shop1_a2, _ = Shop.objects.get_or_create(name="A2 Shop 1", area=area2)
        shop2_a2, _ = Shop.objects.get_or_create(name="A2 Shop 2", area=area2)
        shop1_a2.is_active = True
        shop2_a2.is_active = True
        shop1_a2.save()
        shop2_a2.save()

        all_shops = [shop1_a1, shop2_a1, shop1_a2, shop2_a2]

        # Operating Hours
        for shop in all_shops:
            for day in range(7):
                ShopOperatingHours.objects.get_or_create(
                    shop=shop, day=day,
                    defaults={'open_time': datetime.time(9, 0), 'close_time': datetime.time(17, 0)}
                )

        # Requirements
        # Area 1 Shops: Duty=2
        for s in [shop1_a1, shop2_a1]:
            r, _ = ShopRequirement.objects.get_or_create(shop=s)
            r.required_main_staff = 2
            r.required_reserve_staff = 1
            r.save()

        # Area 2 Shops: Duty=2
        for s in [shop1_a2, shop2_a2]:
            r, _ = ShopRequirement.objects.get_or_create(shop=s)
            r.required_main_staff = 2
            r.required_reserve_staff = 1
            r.save()

        # 2. Create Dummy Users
        # Total: 10 Regulars (5 per area), 2 Supervisors (1 per area)
        first_names = ['James', 'John', 'Robert', 'Michael', 'William', 'Mary', 'Patricia', 'Jennifer', 'Linda', 'Elizabeth',
                       'David', 'Richard', 'Joseph', 'Thomas', 'Charles']

        # Select 12 unique names
        chosen_names = random.sample(first_names, 12)
        name_idx = 0

        # Helper to create user
        def create_user(role, area, idx_offset):
            nonlocal name_idx
            fname = chosen_names[name_idx]
            name_idx += 1
            username = f"user_{role}_{area.name.replace(' ', '')}_{idx_offset}"
            u, _ = User.objects.get_or_create(username=username, defaults={
                'first_name': fname,
                'last_name': f"Dummy{area.id}",
                'email': f"{username}@example.com",
                'tier': role,
                'is_approved': True,
                'area': area
            })
            u.set_password("dummy")
            u.save()
            return u

        # Area 1 Users
        users_a1 = [create_user('regular', area1, i) for i in range(5)]
        sup_a1 = create_user('supervisor', area1, 1)

        # Area 2 Users
        users_a2 = [create_user('regular', area2, i) for i in range(5)]
        sup_a2 = create_user('supervisor', area2, 1)

        # Assign Shops (Regulars -> Shops in their area)
        for u in users_a1:
            u.applicable_shops.add(shop1_a1, shop2_a1)
        for u in users_a2:
            u.applicable_shops.add(shop1_a2, shop2_a2)

        # Ensure Roving
        ensure_roving_shop_and_assignments()

        # 3. Simulation Loop (Past 8 weeks)
        today = timezone.localdate()
        start_current_week = today - datetime.timedelta(days=today.weekday())
        start_sim = start_current_week - datetime.timedelta(weeks=7)

        # Generate Schedules for both Areas iteratively
        for w in range(8):
            week_start = start_sim + datetime.timedelta(weeks=w)
            schedule, _ = Schedule.objects.get_or_create(week_start_date=week_start)

            # Generate for Area 1
            # Get shops including Roving for Area 1
            shops_a1 = list(Shop.objects.filter(area=area1)) # Includes regular + roving
            _generate_multi_week_schedule(shops_a1, [schedule], area1)

            # Generate for Area 2
            shops_a2 = list(Shop.objects.filter(area=area2))
            _generate_multi_week_schedule(shops_a2, [schedule], area2)

            schedule.is_published = True
            schedule.save()

            # Simulate Attendance
            for d in range(7):
                sim_date = week_start + datetime.timedelta(days=d)
                if sim_date > today: break

                current_time_local = timezone.localtime(timezone.now()).time()
                if sim_date == today and current_time_local < datetime.time(17, 0):
                    time_out_val = None
                else:
                    time_out_val = datetime.time(17, 0)

                # Process all shifts for this day
                # We need to filter shifts by relevant shops to handle attendance correctly?
                # Actually we can just iterate all shifts for this day, regardless of area.

                # Duty Staff
                duty_shifts = Shift.objects.filter(schedule=schedule, date=sim_date, role='main')

                # Group by Area for Substitution Logic?
                # Substitutes must come from SAME Area.

                # Let's process per Area to ensure substitutes are correct
                for area_loop in [area1, area2]:
                    area_shops = Shop.objects.filter(area=area_loop)
                    roving = area_shops.filter(name='Roving').first()

                    duty_shifts_area = duty_shifts.filter(shop__in=area_shops)
                    absent_shops = []

                    for shift in duty_shifts_area:
                        if random.randint(1, 60) == 1:
                            absent_shops.append(shift.shop)
                        else:
                            TimeLog.objects.get_or_create(
                                user=shift.user,
                                date=sim_date,
                                defaults={'shop': shift.shop, 'time_in': datetime.time(9, 0), 'time_out': time_out_val}
                            )

                    # Standby Substitution (Same Area)
                    if roving:
                        standby_shifts = list(Shift.objects.filter(schedule=schedule, date=sim_date, role='backup', shop=roving))
                        random.shuffle(standby_shifts)

                        for absent_shop in absent_shops:
                            if standby_shifts:
                                sub_shift = standby_shifts.pop(0)
                                TimeLog.objects.get_or_create(
                                    user=sub_shift.user,
                                    date=sim_date,
                                    defaults={'shop': absent_shop, 'time_in': datetime.time(9, 0), 'time_out': time_out_val}
                                )

                        # Roving Supervisors
                        sup_shifts = Shift.objects.filter(schedule=schedule, date=sim_date, role='main', shop=roving)
                        for shift in sup_shifts:
                             if random.randint(1, 60) != 1:
                                TimeLog.objects.get_or_create(
                                    user=shift.user,
                                    date=sim_date,
                                    defaults={'shop': shift.shop, 'time_in': datetime.time(9, 0), 'time_out': time_out_val}
                                )

                # Update Scores
                update_scores_for_date(sim_date)

        messages.success(request, "Load Test Data Generated Successfully (8 Weeks, 2 Areas).")
        return redirect('scheduling:load_test_data')

    return render(request, 'scheduling/load_test_confirm.html')
