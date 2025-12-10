from playwright.sync_api import sync_playwright, expect
import time

def verify_schedule_generator(page):
    page.goto("http://127.0.0.1:8000/")

    if page.locator("input[name='username']").is_visible():
        page.fill("input[name='username']", "admin")
        page.fill("input[name='password']", "password")
        page.click("button[type='submit']")

    # Use explicit role locator
    expect(page.get_by_role("button", name="Management")).to_be_visible()
    page.get_by_role("button", name="Management").click()

    # Correct Link Name
    page.get_by_role("link", name="Schedule Generation").click()

    expect(page.get_by_role("heading", name="Schedule Generation & Management")).to_be_visible()

    # Generate
    if page.get_by_role("button", name="Regenerate 4-Week Schedule").is_visible():
         page.on("dialog", lambda dialog: dialog.accept())
         page.get_by_role("button", name="Regenerate 4-Week Schedule").click()
    elif page.get_by_role("button", name="Generate 4-Week Schedule").is_visible():
         page.get_by_role("button", name="Generate 4-Week Schedule").click()

    expect(page.get_by_role("button", name="Publish Week 1")).to_be_visible()

    expect(page.get_by_text("Requirements Verification Checklist")).to_be_visible()
    expect(page.get_by_text("Ideal number of staff required:")).to_be_visible()
    expect(page.get_by_role("button", name="Clear Generated Schedule")).to_be_visible()

    page.screenshot(path="/home/jules/verification/schedule_generator_ui.png", full_page=True)

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            verify_schedule_generator(page)
        finally:
            browser.close()
