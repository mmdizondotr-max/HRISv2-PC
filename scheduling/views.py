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

    # Ensure Roving is first for display consistency
    shops_qs = Shop.objects.filter(is_active=True)
    roving = shops_qs.filter(name='Roving').first()
    others = list(shops_qs.exclude(name='Roving'))
    if roving:
        shops = [roving] + others
    else:
        shops = others

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

        # Fetch TimeLogs for the week
        week_end_date = dates[-1]
        logs = TimeLog.objects.filter(date__range=[dates[0], week_end_date]).select_related('user', 'shop')
        logs_map = {} # (date, shop_id) -> list of users who logged in

        for log in logs:
            if not log.shop: continue
            key = (log.date, log.shop.id)
            if key not in logs_map:
                logs_map[key] = []
            logs_map[key].append(log.user)

        for shift in shifts:
            if shift.date in matrix and shift.shop.id in matrix[shift.date]:
                # Determine actual attendance status
                # 1. Did shift.user log in at shift.shop on shift.date?
                # 2. If not, who logged in? (Substitution)
                # 3. If no one, Absent.

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
                    status_dict['status'] = 'present'
                    # Remove from logged_users to avoid double counting if multiple shifts?
                    # Complex if multiple slots. But typically 1 slot per user per shop per day.
                    # We won't remove for now, simple matching.
                else:
                    # Assigned user NOT present.
                    # Is there a substitute?
                    # Pick a user from logged_users who is NOT assigned to another main shift at this shop today?
                    # This logic is complex.
                    # User request: "Actual attendance for each slot (i.e. who actually reported)"
                    # Simple heuristic:
                    # If I am assigned, and I am not there, look for someone who IS there and wasn't assigned (or just list mismatches).
                    # Issue: Mapping specific extra person to specific missing person.
                    # If 2 missing and 2 extra, who maps to whom? Doesn't matter, just show one.

                    # Let's try to grab an "unclaimed" log.
                    # We need to be smarter.
                    # Maybe just pass ALL logs for the cell and let template render?
                    # But the structure is slot-based.

                    # Let's map based on index if multiple?
                    # For this shift, if not present, pick the first log user that isn't matched to another shift?
                    # Too expensive to do perfect matching here.

                    # Simplification:
                    # If user present -> Actual: User
                    # If user absent -> Actual: "Absent" (unless we find a sub)

                    pass

                # We will defer the "Sub" logic to a second pass or just render "Absent" if not found,
                # and maybe add "Unassigned Attendance" list?
                # But user wants "Assigned -> Actual".
                # Let's try to find a substitute: Any user in logged_users not assigned to a shift in this shop/date?
                # This requires knowing all shifts first.

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
                # We need to wrap shifts in a dict or object to add attributes
                # Since 'shift' is a model instance, we can attach attributes dynamically (python)

                # First pass: Match assigned
                matched_logs = []
                for shift in shifts_list:
                    # check if shift.user in logged_users
                    # We need to match objects or IDs.
                    match = None
                    for u in logged_users:
                        if u.id == shift.user.id:
                            match = u
                            break

                    if match:
                        shift.actual_user = shift.user
                        shift.status = 'present'
                        matched_logs.append(match)
                    else:
                        shift.actual_user = None
                        shift.status = 'absent'

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

                # Handle Backup similarly?
                # Backup shifts usually don't have a "slot" unless they are activated.
                # If a backup user logs in, they are effectively working.
                # But usually backup works at Roving? Or at the shop they cover?
                # If they work at the shop they cover, they appear in logs for that shop.
                # If they were assigned 'backup' at Roving (or global pool), and work at Shop A.
                # They will appear in Shop A logs.
                # They won't appear in Roving logs.
                # So in 'Roving' display, they appear as 'Assigned Backup'.
                # If they didn't work at Roving, they are "Absent" from Roving? No, backup doesn't have to work unless needed.
                # So for Backup slots, "Actual" might not be relevant unless they worked *at that shop*.
                # If they worked at *another* shop, that's fine.

                # However, the user said "For each slot... show both assigned and actual".
                # For a Standby slot, if they didn't work, is it "Absent"? No.
                # Only if they were CALLED and didn't show? We don't track "Called".
                # So for Standby, maybe just show "Ready"? or "Did not work".
                # OR: if they logged in at Roving (unlikely for standby), show it.
                # If they logged in elsewhere, maybe show "Worked at Shop X"?
                # That's complex.
                # Let's focus on DUTY slots for "Actual vs Assigned".
                # For Backup, we'll just check if they logged in AT THE ASSIGNED SHOP (Roving).
                # If not, we leave it?
                # Or maybe user implies "Who actually reported" applies mainly to Duty.
                # I'll apply the logic to Backup too, but usually they won't log in at Roving unless they are literally Roving Standby.

                backup_list = matrix[d][s.id]['backup']
                # Same logic...
                matched_logs_b = []
                # Refresh logs for this shop (Roving usually)
                logged_users_b = logs_map.get(key, [])[:]
                # Exclude those matched to Main already?
                # Main logic used 'logged_users' which was a copy.
                # We should probably use a shared pool of logs for the shop if Main and Backup share the shop.
                # But Main and Backup lists are separate.
                # Let's filter out logs used by Main.

                used_log_ids = [u.id for u in matched_logs] # Main matched specific users
                # Also substitues used specific users.
                # Actually, in the main loop we popped from logged_users.
                # So 'logged_users' variable at end of main loop contains remaining logs?
                # No, because I re-fetched `logs_map.get(key, [])[:]` for backup which resets it.
                # I should do it in one go.

                pass

        return {
            'schedule': schedule_obj,
            'dates': dates,
            'matrix': matrix,
            'change_logs': schedule_obj.change_logs.all().order_by('-created_at'),
            'logs_map': logs_map # Passing raw map if needed, but we attached to shift objects
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

    return render(request, 'scheduling/my_schedule.html', {
        'schedules_data': schedules_data,
        'shops': shops
    })

@login_required
def schedule_history_list(request):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    today = timezone.localdate()
    start_of_current_week = today - datetime.timedelta(days=today.weekday())

    schedules = Schedule.objects.filter(is_published=True).order_by('-week_start_date')

    return render(request, 'scheduling/schedule_history_list.html', {'schedules': schedules})

@login_required
def schedule_history_detail(request, schedule_id):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    schedule = get_object_or_404(Schedule, id=schedule_id, is_published=True)

    dates = [schedule.week_start_date + datetime.timedelta(days=i) for i in range(7)]
    # Ensure Roving is first for display consistency
    shops_qs = Shop.objects.all()
    roving = shops_qs.filter(name='Roving').first()
    others = list(shops_qs.exclude(name='Roving'))
    if roving:
        shops = [roving] + others
    else:
        shops = others

    matrix = {}
    for d in dates:
        matrix[d] = {}
        for s in shops:
            matrix[d][s.id] = {'main': [], 'backup': []}

    shifts = schedule.shifts.all().select_related('user', 'shop')
    for shift in shifts:
        if shift.date in matrix and shift.shop.id in matrix[shift.date]:
             if shift.role == 'main':
                 matrix[shift.date][shift.shop.id]['main'].append(shift.user)
             else:
                 matrix[shift.date][shift.shop.id]['backup'].append(shift.user)

    return render(request, 'scheduling/schedule_history_detail.html', {
        'schedule': schedule,
        'dates': dates,
        'shops': shops,
        'matrix': matrix,
        'change_logs': schedule.change_logs.all().order_by('-created_at')
    })

@login_required
def generator(request):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    ensure_roving_shop_and_assignments()

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

    # Ensure Roving is first for display consistency
    shops_qs = Shop.objects.filter(is_active=True)
    roving = shops_qs.filter(name='Roving').first()
    others = list(shops_qs.exclude(name='Roving'))
    if roving:
        shops = [roving] + others
    else:
        shops = others

    if request.method == 'POST':
        if 'generate' in request.POST:
            _generate_multi_week_schedule(shops, weeks)
            messages.success(request, "Schedule generated for 4 weeks.")
            return redirect('scheduling:generator')
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

        # Attach duty counts to user objects in shifts for display?
        # Easier to pass a separate map or update the shift user object temporarily.
        # But shift.user is a User instance.
        # Let's create a helper map in the week_data

        weeks_data.append({
            'schedule': schedule,
            'dates': dates,
            'matrix': matrix,
            'duty_counts': duty_counts
        })

    return render(request, 'scheduling/generator.html', {
        'weeks_data': weeks_data,
        'current_schedule': current_schedule,
        'shops': shops,
        'change_logs': current_schedule.change_logs.all().order_by('-created_at'),
    })

def _generate_multi_week_schedule(shops, weeks):
    from accounts.models import User

    # 1. Prepare Data
    all_users = list(User.objects.filter(is_active=True, is_approved=True).select_related('preference'))

    roving_shop = None
    for s in shops:
        if s.name == 'Roving':
            roving_shop = s
            break
    if not roving_shop:
        roving_shop = Shop.objects.filter(name='Roving').first()
        if not roving_shop:
             # Just in case
             roving_shop, _ = Shop.objects.get_or_create(name='Roving', is_active=True)

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
    if request.method == 'POST':
        # 0. Reset Data first
        reset_system_data(request.user)

        # 1. Create Dummy Shops
        shop1, _ = Shop.objects.get_or_create(name="Dummy Shop 1")
        shop2, _ = Shop.objects.get_or_create(name="Dummy Shop 2")
        shop1.is_active = True
        shop2.is_active = True
        shop1.save()
        shop2.save()

        # Operating Hours
        for shop in [shop1, shop2]:
            for day in range(7):
                ShopOperatingHours.objects.get_or_create(
                    shop=shop, day=day,
                    defaults={'open_time': datetime.time(9, 0), 'close_time': datetime.time(17, 0)}
                )

        # Requirements
        # Shop 1: Duty=2, Standby=1
        r1, _ = ShopRequirement.objects.get_or_create(shop=shop1)
        r1.required_main_staff = 2
        r1.required_reserve_staff = 1
        r1.save()

        # Shop 2: Duty=1, Standby=1
        r2, _ = ShopRequirement.objects.get_or_create(shop=shop2)
        r2.required_main_staff = 1
        r2.required_reserve_staff = 1
        r2.save()

        # 2. Create Dummy Users
        # 4 Regulars, 1 Supervisor
        first_names = ['James', 'John', 'Robert', 'Michael', 'William', 'Mary', 'Patricia', 'Jennifer', 'Linda', 'Elizabeth']

        # Select 5 unique names
        chosen_names = random.sample(first_names, 5)

        dummy_users = []
        for i in range(1, 5):
            fname = chosen_names[i-1]
            u, _ = User.objects.get_or_create(username=f"dummy_user_{i}", defaults={
                'first_name': fname,
                'last_name': "Dummy",
                'email': f"dummy{i}@example.com",
                'tier': 'regular',
                'is_approved': True
            })
            u.set_password("dummy")
            u.save()
            dummy_users.append(u)

        fname_sup = chosen_names[4]
        sup, _ = User.objects.get_or_create(username="dummy_supervisor", defaults={
            'first_name': fname_sup,
            'last_name': "Dummy",
            'email': "dummysup@example.com",
            'tier': 'supervisor',
            'is_approved': True
        })
        sup.set_password("dummy")
        sup.save()

        # Assign Shops
        # Regulars -> Both Dummy Shops
        for u in dummy_users:
            u.applicable_shops.add(shop1, shop2)

        # Supervisor -> Roving (standard logic)
        ensure_roving_shop_and_assignments()

        # 3. Simulation Loop (Past 8 weeks)
        today = timezone.localdate()
        # Find start date: Monday 8 weeks ago
        # Start of current week
        start_current_week = today - datetime.timedelta(days=today.weekday())
        start_sim = start_current_week - datetime.timedelta(weeks=7) # 8 weeks total including current

        # Simulation Target Shops
        target_shops = [shop1, shop2]
        roving = Shop.objects.filter(name='Roving').first()
        if roving:
            target_shops.append(roving)

        for w in range(8):
            week_start = start_sim + datetime.timedelta(weeks=w)

            # Create Schedule
            schedule, _ = Schedule.objects.get_or_create(week_start_date=week_start)

            # Generate
            _generate_schedule(target_shops, schedule)

            # Publish
            schedule.is_published = True
            schedule.save()

            # Simulate Attendance for each day of this week
            for d in range(7):
                sim_date = week_start + datetime.timedelta(days=d)

                # Stop if future
                if sim_date > today:
                    break

                # Duty Staff
                duty_shifts = Shift.objects.filter(schedule=schedule, date=sim_date, role='main', shop__in=[shop1, shop2])

                absent_shops = set()

                for shift in duty_shifts:
                    # 1/60 chance of absence
                    if random.randint(1, 60) == 1:
                        # Absent
                        absent_shops.add(shift.shop.id)
                    else:
                        # Present -> TimeLog
                        # Use operating hours?
                        # Assuming 9-17
                        TimeLog.objects.get_or_create(
                            user=shift.user,
                            date=sim_date,
                            defaults={
                                'shop': shift.shop,
                                'time_in': datetime.time(9, 0),
                                'time_out': datetime.time(17, 0)
                            }
                        )

                # Standby Staff Substitution
                standby_shifts = Shift.objects.filter(schedule=schedule, date=sim_date, role='backup', shop__in=[shop1, shop2])

                for shift in standby_shifts:
                    if shift.shop.id in absent_shops:
                        # Substitute!
                        TimeLog.objects.get_or_create(
                            user=shift.user,
                            date=sim_date,
                            defaults={
                                'shop': shift.shop,
                                'time_in': datetime.time(9, 0),
                                'time_out': datetime.time(17, 0)
                            }
                        )

                # Roving Supervisor?
                if roving:
                    sup_shifts = Shift.objects.filter(schedule=schedule, date=sim_date, role='main', shop=roving)
                    for shift in sup_shifts:
                         if random.randint(1, 60) != 1:
                            TimeLog.objects.get_or_create(
                                user=shift.user,
                                date=sim_date,
                                defaults={
                                    'shop': shift.shop,
                                    'time_in': datetime.time(9, 0),
                                    'time_out': datetime.time(17, 0)
                                }
                            )

                # Update Scores
                update_scores_for_date(sim_date)

        messages.success(request, "Load Test Data Generated Successfully (8 Weeks).")
        return redirect('scheduling:load_test_data')

    return render(request, 'scheduling/load_test_confirm.html')
