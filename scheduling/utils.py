from attendance.models import Shop
from accounts.models import User

def ensure_roving_shop_and_assignments():
    """
    Ensures 'Roving' shop exists.
    Updates assignments:
    - Supervisors -> Only Roving
    - Regulars -> All Shops (excluding Roving? or including?
      Prompt: "all Regulars should be assigned to all Shops".
      Prompt: "Supervisors are only assigned under Roving".
      Interpretation: Shops = {S1, S2, Roving}.
      Supervisors = {Roving}
      Regulars = {S1, S2} (Assuming Regulars don't 'Rove').
    """
    roving_shop, created = Shop.objects.get_or_create(name="Roving")
    if created or not roving_shop.is_active:
        roving_shop.is_active = True
        roving_shop.save()

    # Get all active shops excluding Roving
    regular_shops = Shop.objects.filter(is_active=True).exclude(id=roving_shop.id)

    all_users = User.objects.filter(is_active=True)

    for user in all_users:
        current_applicable = set(user.applicable_shops.all())
        target_applicable = set()

        if user.tier == 'supervisor':
            target_applicable.add(roving_shop)
        else:
            # Regular (and Administrator? assuming Admins act as Regulars or Supervisors?
            # Prompt: "all Regulars should be assigned to all Shops".
            # Usually Admins are not scheduled, or scheduled as Supervisors.
            # But the tier choices are regular, supervisor, administrator.
            # I will treat Administrator as Supervisor for scheduling purposes?
            # Or Regular?
            # Existing code: generator access allowed for 'supervisor', 'administrator'.
            # I will assume 'Regular' tier specifically.
            if user.tier == 'regular':
                for s in regular_shops:
                    target_applicable.add(s)
            else:
                 # Administrator?
                 # Let's assume they are like Supervisors for Roving?
                 # Or just leave them alone?
                 # Prompt only specifies "Supervisors" and "Regulars".
                 # I'll stick to strictly those tiers.
                 pass

        # Apply changes if needed
        # Note: If an Administrator is handling scheduling, we might not want to mess with their shops.
        # But if the prompt implies a rule...
        # "all Supervisors are only assigned under Roving"

        if user.tier == 'supervisor':
            # Force set
            if set(current_applicable) != target_applicable:
                user.applicable_shops.set(target_applicable)
        elif user.tier == 'regular':
             # Force set
             # Note: This overwrites manual assignments. The prompt implies this is a rule.
             if set(current_applicable) != target_applicable:
                user.applicable_shops.set(target_applicable)
