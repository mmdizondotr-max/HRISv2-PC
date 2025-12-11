from playwright.sync_api import sync_playwright, expect
import time

def verify_frontend_changes(page):
    # 1. Login as Admin
    page.goto("http://127.0.0.1:8000/")

    # If redirected to login, perform login
    if page.locator("input[name='username']").is_visible():
        page.fill("input[name='username']", "admin")
        page.fill("input[name='password']", "password")
        page.click("button[type='submit']")

    # 2. Check Shop List for Roving Color
    page.get_by_role("button", name="Management").click()
    page.get_by_role("link", name="Shop Management").click()

    # Screenshot Shop List
    page.screenshot(path="/home/jules/verification/shop_list.png", full_page=True)

    # 3. Check Shop Manage for Roving (Operating Hours hidden)
    # Find Roving edit button
    # Assuming Roving exists or we need to ensure it.
    # If generator was run, it might exist. But we just migrated. It might not exist.
    # We might need to create it manually or run generator.
    # Let's run generator page first which creates it.

    page.get_by_role("button", name="Management").click()
    page.get_by_role("link", name="Schedule Generation").click()

    # Now go back to Shop Management
    page.get_by_role("button", name="Management").click()
    page.get_by_role("link", name="Shop Management").click()

    # Find row with Roving
    roving_row = page.locator("tr", has_text="Roving")
    roving_row.get_by_role("link", name="Edit").click()

    # Verify "Operating Hours" is NOT visible
    expect(page.get_by_text("Operating Hours")).not_to_be_visible()

    page.screenshot(path="/home/jules/verification/roving_manage.png", full_page=True)

    # 4. Check Generator UI (No Standby Column for Roving)
    page.get_by_role("button", name="Management").click()
    page.get_by_role("link", name="Schedule Generation").click()

    # Generate Schedule if needed
    if page.get_by_role("button", name="Generate 4-Week Schedule").is_visible():
        page.get_by_role("button", name="Generate 4-Week Schedule").click()
    elif page.get_by_role("button", name="Regenerate 4-Week Schedule").is_visible():
        page.on("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Regenerate 4-Week Schedule").click()

    # Look at the table
    # Check that "Roving" header exists
    expect(page.get_by_role("cell", name="Roving", exact=True)).to_be_visible()

    # Check that under Roving, there is no "Standby" column
    # This is tricky to check with locators generically.
    # We can check that the colspan for Roving is 1 (if we changed it to 1, or default).
    # In my code: {% if shop.name == 'Roving' %}<th>{{ shop.name }}</th> (colspan default 1)
    # else colspan=2.

    # Verify visually via screenshot
    page.screenshot(path="/home/jules/verification/generator_ui.png", full_page=True)

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Create admin user if not exists (via shell or just assume from previous runs?)
        # Since I ran migrate, DB is fresh. I need to create user.
        # I'll rely on a bash command to create superuser before running this script.

        try:
            verify_frontend_changes(page)
        finally:
            browser.close()
