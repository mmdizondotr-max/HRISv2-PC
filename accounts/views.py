from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib import messages
from .forms import UserRegistrationForm, AccountSettingsForm, UserPromotionForm, ForgotPasswordForm
from django.contrib.auth.decorators import login_required
from .models import User, PasswordResetRequest, AccountActionLog
from django.http import HttpResponseForbidden
from django.contrib.auth.hashers import make_password

def register(request):
    if request.user.is_authenticated:
        return redirect('attendance:home')

    if request.method == 'POST':
        form = UserRegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False # Require approval
            user.save()

            # Log Creation
            AccountActionLog.objects.create(
                user=user,
                action_type='creation',
                details='Account created via registration form.',
                performed_by=None
            )

            messages.success(request, 'Registration successful! Please wait for Supervisor approval.')
            return redirect('login')
    else:
        form = UserRegistrationForm()

    return render(request, 'accounts/register.html', {'form': form})

def forgot_password(request):
    if request.user.is_authenticated:
        return redirect('attendance:home')

    if request.method == 'POST':
        form = ForgotPasswordForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data['user_cache']
            new_username = form.cleaned_data['new_username']
            new_password = form.cleaned_data['new_password']

            # Create Request
            PasswordResetRequest.objects.create(
                user=user,
                new_username=new_username,
                new_password=make_password(new_password)
            )
            messages.success(request, "Password reset request submitted for approval.")
            return redirect('login')
    else:
        form = ForgotPasswordForm()

    return render(request, 'accounts/forgot_password.html', {'form': form})

@login_required
def approvals(request):
    if not request.user.is_superuser and request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden("You are not authorized to view this page.")

    pending_users = User.objects.filter(is_active=False, is_approved=False).order_by('-date_joined')
    pending_resets = PasswordResetRequest.objects.all().order_by('-created_at')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action in ['approve', 'reject']:
            user_id = request.POST.get('user_id')
            try:
                target_user = User.objects.get(id=user_id)
                if action == 'approve':
                    target_user.is_active = True
                    target_user.is_approved = True
                    target_user.save()

                    # Log Activation/Approval
                    AccountActionLog.objects.create(
                        user=target_user,
                        action_type='suspension',
                        details='Account approved and activated.',
                        performed_by=request.user
                    )

                    messages.success(request, f'User {target_user.username} approved.')
                elif action == 'reject':
                    username = target_user.username
                    target_user.delete() # Deletion log? Maybe too late.
                    messages.warning(request, f'User {username} rejected/deleted.')
            except User.DoesNotExist:
                messages.error(request, 'User not found.')

        elif action in ['approve_reset', 'reject_reset']:
            request_id = request.POST.get('request_id')
            try:
                reset_req = PasswordResetRequest.objects.get(id=request_id)
                if action == 'approve_reset':
                    user = reset_req.user
                    user.username = reset_req.new_username
                    user.password = reset_req.new_password
                    user.save()

                    AccountActionLog.objects.create(
                        user=user,
                        action_type='password_reset',
                        details='Password reset approved.',
                        performed_by=request.user
                    )

                    reset_req.delete()
                    messages.success(request, f"Password reset for {user.first_name} approved.")
                elif action == 'reject_reset':
                    reset_req.delete()
                    messages.warning(request, "Password reset request rejected.")
            except PasswordResetRequest.DoesNotExist:
                messages.error(request, "Request not found.")

        return redirect('accounts:approvals')

    return render(request, 'accounts/approvals.html', {'pending_users': pending_users, 'pending_resets': pending_resets})

@login_required
def account_settings(request):
    if request.method == 'POST':
        form = AccountSettingsForm(request.POST, instance=request.user)
        if form.is_valid():
            if form.has_changed():
                form.save()
                AccountActionLog.objects.create(
                    user=request.user,
                    action_type='update',
                    details=f"Settings updated: {', '.join(form.changed_data)}",
                    performed_by=request.user
                )
            messages.success(request, 'Account settings updated.')
            return redirect('accounts:account_settings')
    else:
        form = AccountSettingsForm(instance=request.user)

    logs = request.user.action_logs.all()
    return render(request, 'accounts/account_settings.html', {'form': form, 'logs': logs})

@login_required
def account_list(request):
    if not request.user.is_superuser and request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden("You are not authorized to view this page.")

    users = User.objects.all().order_by('last_name')
    return render(request, 'accounts/account_list.html', {'users': users})

@login_required
def account_promote(request, user_id):
    if not request.user.is_superuser and request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden("You are not authorized to view this page.")

    target_user = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        action = request.POST.get('action')

        # Handle Delete
        if action == 'delete':
            # Permission Check: Delete
            if not request.user.is_superuser:
                if request.user.tier == 'supervisor':
                    if target_user.tier in ['administrator', 'supervisor']:
                        messages.error(request, "Supervisors cannot delete Supervisors or Administrators.")
                        return redirect('accounts:account_promote', user_id=target_user.id)

                if request.user.tier == 'administrator':
                    if target_user.tier == 'administrator':
                        messages.error(request, "Administrators cannot delete other Administrators.")
                        return redirect('accounts:account_promote', user_id=target_user.id)

            username = target_user.username
            target_user.delete()
            messages.success(request, f"User {username} deleted permanently.")
            return redirect('accounts:account_list')

        # Check permissions strictly before binding form or saving
        if not request.user.is_superuser:
            if request.user.tier == 'administrator':
                if target_user.tier == 'administrator':
                     messages.error(request, "Administrators cannot modify other Administrators.")
                     return redirect('accounts:account_list')

            if request.user.tier == 'supervisor':
                if target_user.tier in ['administrator', 'supervisor']:
                     messages.error(request, "Supervisors cannot modify Supervisors or Administrators.")
                     return redirect('accounts:account_list')

        # Capture State Before Save
        old_tier = target_user.tier
        old_active = target_user.is_active
        old_shops = set(target_user.applicable_shops.all())

        form = UserPromotionForm(request.POST, instance=target_user, current_user=request.user)
        if form.is_valid():
            user = form.save(commit=False)
            user.save()
            form.save_m2m() # Save assignment changes

            # Capture State After Save
            new_tier = user.tier
            new_shops = set(user.applicable_shops.all())

            # Handle suspension logic from form
            is_suspended = form.cleaned_data.get('suspend_user')

            # Permission Check: Suspend
            if is_suspended:
                can_suspend = True
                if not request.user.is_superuser:
                    if request.user.tier == 'supervisor' and target_user.tier in ['administrator', 'supervisor']:
                        can_suspend = False
                    elif request.user.tier == 'administrator' and target_user.tier == 'administrator':
                        can_suspend = False

                if not can_suspend:
                     messages.error(request, "You do not have permission to suspend this user.")
                     user.is_active = True # Revert
                     user.save()
                else:
                    user.is_active = False
                    user.applicable_shops.clear() # Force clear assignments
                    user.save()
                    new_shops = set() # Shops cleared
            else:
                user.is_active = True
                user.save()

            new_active = user.is_active

            # Logging Logic

            # 1. Tier Change
            if old_tier != new_tier:
                AccountActionLog.objects.create(
                    user=user,
                    action_type='promotion',
                    details=f"Tier changed from {old_tier} to {new_tier}",
                    performed_by=request.user
                )

            # 2. Suspension/Activation
            if old_active != new_active:
                status = "Suspended" if not new_active else "Activated"
                AccountActionLog.objects.create(
                    user=user,
                    action_type='suspension',
                    details=f"User account {status}",
                    performed_by=request.user
                )

            # 3. Shop Assignment
            if old_shops != new_shops:
                old_shop_names = ", ".join([s.name for s in old_shops])
                new_shop_names = ", ".join([s.name for s in new_shops])
                AccountActionLog.objects.create(
                    user=user,
                    action_type='assignment',
                    details=f"Shops changed from [{old_shop_names}] to [{new_shop_names}]",
                    performed_by=request.user
                )

            messages.success(request, f"User {target_user.username} updated.")
            return redirect('accounts:account_list')
    else:
        form = UserPromotionForm(instance=target_user, current_user=request.user)

    logs = target_user.action_logs.all()
    return render(request, 'accounts/account_promote.html', {'form': form, 'target_user': target_user, 'logs': logs})
