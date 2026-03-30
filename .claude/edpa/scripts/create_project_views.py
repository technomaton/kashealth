#!/usr/bin/env python3
"""
EDPA GitHub Project Views — Automated setup via Playwright.

Creates 6 views: All Items, Board, Epics, Features, Stories, WSJF Ranking.
Uses persistent browser profile — log in once, then it remembers.

Usage:
    python scripts/create_project_views.py

First run: browser opens → log in to GitHub → views created automatically.
Next runs: uses saved session, no login needed.
"""
import asyncio
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Install: pip install playwright && playwright install chromium")
    sys.exit(1)

PROFILE = Path.home() / ".edpa" / "playwright-profile"


def get_project_url():
    """Build project URL from .edpa/config/edpa.yaml."""
    config_path = Path(".edpa/config/edpa.yaml")
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
            sync = config.get("sync", {})
            org = sync.get("github_org", "")
            num = sync.get("github_project_number", "")
            if org and num:
                return f"https://github.com/orgs/{org}/projects/{num}"
        except Exception:
            pass
    return None


async def wait_for_login(page, timeout=300):
    """Wait until user is logged in (max timeout seconds)."""
    for _ in range(timeout):
        try:
            # page might navigate during login — catch context destruction
            await page.wait_for_load_state("domcontentloaded", timeout=2000)
            url = page.url
            # After login, GitHub redirects to dashboard or previous page
            if "github.com/login" not in url and "github.com/session" not in url:
                # Check for any sign of being logged in
                try:
                    count = await page.locator('img.avatar-user, [data-login], .Header-link--profile').count()
                    if count > 0:
                        return True
                except:
                    pass
                # If we're on github.com but not login page, probably logged in
                if "github.com" in url and "/login" not in url:
                    await page.wait_for_timeout(2000)
                    return True
        except:
            pass
        await page.wait_for_timeout(1000)
    return False


async def save_view(page):
    """Try to save the current view."""
    save = page.locator('button:has-text("Save")')
    for i in range(await save.count()):
        btn = save.nth(i)
        if await btn.is_visible():
            await btn.click()
            await page.wait_for_timeout(1500)
            return
    await page.keyboard.press("Meta+s")
    await page.wait_for_timeout(1500)


async def rename_tab(page, index, new_name):
    """Double-click tab at index to rename it."""
    tabs = page.locator('[role="tab"]')
    if await tabs.count() <= index:
        return False
    tab = tabs.nth(index)
    await tab.dblclick()
    await page.wait_for_timeout(800)
    inp = page.locator('input[aria-label="View name"]')
    if await inp.count() == 0:
        inp = page.locator('[role="tab"] input')
    if await inp.count() > 0:
        await inp.first.clear()
        await inp.first.fill(new_name)
        await inp.first.press("Enter")
        await page.wait_for_timeout(1000)
        await save_view(page)
        return True
    return False


async def create_view(page, name, filter_text=""):
    """Create a new table view with optional filter."""
    # Click + New view
    btn = page.locator('button:has-text("New view")')
    if await btn.count() == 0:
        print(f"    ✗ Cannot find 'New view' button")
        return False
    await btn.first.click()
    await page.wait_for_timeout(2000)

    # If layout picker appears, click Table
    tbl = page.locator('button:has-text("Table")')
    if await tbl.count() > 0 and await tbl.first.is_visible():
        await tbl.first.click()
        await page.wait_for_timeout(1500)

    # Rename the new tab (it's the last one)
    tabs = page.locator('[role="tab"]')
    last_index = await tabs.count() - 1
    ok = await rename_tab(page, last_index, name)
    if not ok:
        print(f"    ⚠ Could not rename tab to '{name}'")

    # Apply filter
    if filter_text:
        fi = page.locator('input[placeholder*="Filter"], input[placeholder*="filter"]')
        if await fi.count() > 0 and await fi.first.is_visible():
            await fi.first.click()
            await fi.first.fill(filter_text)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1500)
            await save_view(page)

    return True


async def main(project_url: str):
    PROFILE.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE),
            headless=False,
            slow_mo=300,
            viewport={"width": 1400, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Check login state
        await page.goto("https://github.com")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)

        if await page.locator('img.avatar-user, [data-login]').count() == 0:
            print("\n  Not logged in. Please log in in the browser window.")
            print("  Waiting up to 5 minutes...\n")
            await page.goto("https://github.com/login")
            ok = await wait_for_login(page, 300)
            if not ok:
                print("  ✗ Login timeout. Run again after logging in.")
                await ctx.close()
                return

        print("  ✓ Logged in")
        print(f"  Loading project: {project_url}")
        await page.goto(project_url)
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)

        # Verify we see the project
        if "projects" not in page.url:
            print("  ✗ Could not load project")
            await ctx.close()
            return
        print("  ✓ Project loaded\n")

        # Step 1: Rename existing views
        print("  [1/6] Renaming 'View 1' → 'All Items'")
        if await rename_tab(page, 0, "All Items"):
            print("    ✓ Done")
        else:
            print("    ⚠ Skipped")

        tabs = page.locator('[role="tab"]')
        if await tabs.count() >= 2:
            print("  [2/6] Renaming 'View 2' → 'Board'")
            await tabs.nth(1).click()
            await page.wait_for_timeout(1000)
            if await rename_tab(page, 1, "Board"):
                print("    ✓ Done")
            else:
                print("    ⚠ Skipped")

        # Step 2: Create new views
        views = [
            ("Epics", "type:Epic"),
            ("Features", "type:Feature"),
            ("Stories", "type:Story"),
            ("WSJF Ranking", ""),
        ]
        for i, (name, filt) in enumerate(views, 3):
            print(f"  [{i}/6] Creating '{name}'" + (f" (filter: {filt})" if filt else ""))
            ok = await create_view(page, name, filt)
            print(f"    {'✓ Done' if ok else '✗ Failed'}")

        print(f"\n  {'═' * 50}")
        print(f"  ✓ Views setup complete!")
        print(f"  Keep the browser open to verify, then close it.")
        print(f"  {'═' * 50}\n")

        # Keep open for verification
        try:
            await page.wait_for_timeout(120000)
        except:
            pass
        await ctx.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="EDPA Project Views — Playwright automation")
    parser.add_argument("--url", default=None, help="Project URL (default: from .edpa/config/edpa.yaml)")
    args = parser.parse_args()

    url = args.url or get_project_url()
    if not url:
        print("  Error: No project URL. Pass --url or configure .edpa/config/edpa.yaml")
        print("  Example: python scripts/create_project_views.py --url https://github.com/orgs/ORG/projects/N")
        sys.exit(1)

    asyncio.run(main(url))
