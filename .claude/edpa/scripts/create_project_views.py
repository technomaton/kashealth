#!/usr/bin/env python3
"""
EDPA GitHub Project Views — Automated setup via Playwright.

Creates views: Epics, Features, Stories, WSJF Ranking.
Uses persistent browser profile — log in once, then it remembers.

Usage:
    python scripts/create_project_views.py --url URL
    python scripts/create_project_views.py  # reads from .edpa/config/edpa.yaml

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
            await page.wait_for_load_state("domcontentloaded", timeout=2000)
            url = page.url
            if "github.com/login" not in url and "github.com/session" not in url:
                try:
                    count = await page.locator('img.avatar-user, [data-login], .Header-link--profile').count()
                    if count > 0:
                        return True
                except Exception:
                    pass
                if "github.com" in url and "/login" not in url:
                    await page.wait_for_timeout(2000)
                    return True
        except Exception:
            pass
        await page.wait_for_timeout(1000)
    return False


async def dismiss_modal(page):
    """Confirm any 'Save filters?' modal dialog."""
    dialog = page.locator('[role="dialog"], [class*="Dialog-Backdrop"]')
    if await dialog.count() > 0:
        save_btn = dialog.locator('button:has-text("Save")')
        if await save_btn.count() > 0 and await save_btn.first.is_visible():
            await save_btn.first.click()
            await page.wait_for_timeout(1500)
            return True
        cancel_btn = dialog.locator('button:has-text("Cancel")')
        if await cancel_btn.count() > 0 and await cancel_btn.first.is_visible():
            await cancel_btn.first.click()
            await page.wait_for_timeout(1500)
            return True
    return False


async def save_view(page):
    """Click the green Save button, then confirm any modal."""
    save = page.locator('button:has-text("Save"):not([role="tab"])')
    for i in range(await save.count()):
        btn = save.nth(i)
        if await btn.is_visible():
            await btn.click()
            await page.wait_for_timeout(2000)
            await dismiss_modal(page)
            return
    # Fallback: Cmd+S
    await page.keyboard.press("Meta+s")
    await page.wait_for_timeout(2000)
    await dismiss_modal(page)


async def rename_tab(page, index, new_name):
    """Double-click tab at index to rename it."""
    tabs = page.locator('[role="tab"]')
    if await tabs.count() <= index:
        return False
    tab = tabs.nth(index)
    await tab.click()
    await page.wait_for_timeout(500)
    await tab.dblclick()
    await page.wait_for_timeout(1500)
    # GitHub uses aria-label="Change view name" for the rename input
    inp = page.locator('input[aria-label="Change view name"]')
    if await inp.count() == 0:
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
    await dismiss_modal(page)
    await page.wait_for_timeout(500)

    # Click "+ New view" (it's the last tab)
    new_view_tab = page.locator('[role="tab"]:has-text("New view")')
    if await new_view_tab.count() == 0:
        print(f"    ✗ Cannot find 'New view' tab")
        return False
    try:
        await new_view_tab.first.click(timeout=10000)
    except Exception:
        await dismiss_modal(page)
        await page.wait_for_timeout(1000)
        await new_view_tab.first.click(timeout=10000)
    await page.wait_for_timeout(2000)

    # Layout picker may appear — click Table
    tbl = page.locator('button:has-text("Table"), [data-testid="table-layout"]')
    if await tbl.count() > 0 and await tbl.first.is_visible():
        await tbl.first.click()
        await page.wait_for_timeout(2000)

    # The new tab is second-to-last (last is always "+ New view")
    tabs = page.locator('[role="tab"]')
    new_tab_index = await tabs.count() - 2
    if new_tab_index < 0:
        new_tab_index = 0

    # Rename
    ok = await rename_tab(page, new_tab_index, name)
    if not ok:
        print(f"    ⚠ Could not rename tab to '{name}'")

    # Apply filter
    if filter_text:
        fi = page.locator('input[placeholder*="Filter"], input[name*="filter"]')
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
        try:
            ctx = await p.chromium.launch_persistent_context(
                str(PROFILE),
                headless=False,
                slow_mo=300,
                viewport={"width": 1400, "height": 900},
            )
        except Exception as e:
            print(f"\n  ✗ Cannot launch browser: {e}")
            print("  Install: pip install playwright && playwright install chromium")
            print(f"\n  Alternative: open the project in browser and create views manually:")
            print(f"  {project_url}")
            return
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Check login state
        await page.goto("https://github.com")
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
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
        try:
            await page.goto(project_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            await page.wait_for_timeout(3000)
        await page.wait_for_timeout(3000)

        if "projects" not in page.url:
            try:
                await page.goto(project_url, wait_until="commit", timeout=15000)
                await page.wait_for_timeout(3000)
            except Exception:
                pass
        if "projects" not in page.url:
            print("  ✗ Could not load project")
            await ctx.close()
            return
        print("  ✓ Project loaded\n")

        # Rename default view
        print("  [1] Renaming default view → 'All Items'")
        if await rename_tab(page, 0, "All Items"):
            print("    ✓ Done")
        else:
            print("    ⚠ Skipped (may already be named)")

        # Create filtered views
        views = [
            ("Epics", "type:Epic"),
            ("Features", "type:Feature"),
            ("Stories", "type:Story"),
            ("WSJF Ranking", ""),
        ]
        for i, (name, filt) in enumerate(views, 2):
            desc = f" (filter: {filt})" if filt else ""
            print(f"  [{i}] Creating '{name}'{desc}")
            ok = await create_view(page, name, filt)
            print(f"    {'✓ Done' if ok else '✗ Failed'}")

        print(f"\n  {'═' * 50}")
        print(f"  ✓ Views setup complete!")
        print(f"  Keep the browser open to verify, then close it.")
        print(f"  {'═' * 50}\n")

        try:
            await page.wait_for_timeout(120000)
        except Exception:
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
        print("  Example: python create_project_views.py --url https://github.com/orgs/ORG/projects/N")
        sys.exit(1)

    asyncio.run(main(url))
