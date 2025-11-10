
# mcarthurglen_offers_playwright.py (fixed)
#
# Usage:
#   pip install playwright
#   python -m playwright install
#   python mcarthurglen_offers_playwright.py [--no-headless] [--timeout 30000]
#
import argparse
import asyncio
import csv
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

OFFERS_URL = "https://www.mcarthurglen.com/en/outlets/ca/designer-outlet-vancouver/offers/"
STATE_JSON = Path("offers_latest.json")
STATE_PREV = Path("offers_latest.prev.json")
STATE_CSV = Path("offers_latest.csv")

PCT_RE = re.compile(r"(\d{1,3})\s*%")

def abs_url(base: str, href: str) -> str:
    from urllib.parse import urljoin
    try:
        return urljoin(base, href or "")
    except Exception:
        return href or ""

async def auto_scroll(page, idle_rounds: int = 4, delay_ms: int = 900):
    last_h, idle = 0, 0
    while idle < idle_rounds:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(delay_ms)
        h = await page.evaluate("document.body.scrollHeight")
        if h <= last_h:
            idle += 1
        else:
            idle = 0
        last_h = h
    await page.evaluate("window.scrollTo(0, 0)")

async def safe_inner_text(locator) -> str:
    try:
        return (await locator.inner_text()).strip()
    except Exception:
        return ""

async def safe_attr(locator, name: str) -> str:
    try:
        v = await locator.get_attribute(name)
        return v or ""
    except Exception:
        return ""

async def extract_cards(page) -> List[Dict[str, Any]]:
    cards = await page.locator('[data-testid="offer-card-molecule"]').all()
    results: List[Dict[str, Any]] = []
    for card in cards:
        brand = await safe_inner_text(card.locator('[data-testid="offer-card-brand"]'))
        title = await safe_inner_text(card.locator('[data-testid="offer-card-title"]'))
        cats  = await safe_inner_text(card.locator('[data-testid="offer-card-categories"]'))

        # Prefer link in media, else any link
        link_el = card.locator('[data-testid="offer-card-media"] a[href]').first
        if not await link_el.count():
            link_el = card.locator("a[href]").first
        href = await safe_attr(link_el, "href")
        link = abs_url(OFFERS_URL, href)

        m = PCT_RE.search(title or "")
        discount_percent = int(m.group(1)) if m else None

        results.append({
            "brand": brand or "Unknown",
            "discount_percent": discount_percent,
            "title": title,
            "categories": cats,
            "link": link,
        })

    # Dedup by (brand, title, link)
    uniq = {}
    for r in results:
        key = (r["brand"], r["title"], r["link"])
        if key not in uniq:
            uniq[key] = r

    out = list(uniq.values())
    out.sort(key=lambda r: (-(r["discount_percent"] if r["discount_percent"] is not None else -1), r["brand"]))
    return out

def save_json(rows: List[Dict[str, Any]], path: Path):
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

def save_csv(rows: List[Dict[str, Any]], path: Path):
    cols = ["brand", "discount_percent", "title", "categories", "link"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

def load_json(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

def diff(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ok = {(o.get("brand"), o.get("title")) for o in old}
    nk = {(o.get("brand"), o.get("title")) for o in new}
    added = [o for o in new if (o.get("brand"), o.get("title")) in (nk - ok)]
    removed = [o for o in old if (o.get("brand"), o.get("title")) in (ok - nk)]
    return added, removed

def print_table(rows: List[Dict[str, Any]]):
    if not rows:
        print("No offers found.")
        return
    brand_w = max(6, min(28, max(len(r["brand"]) for r in rows)))
    print(f"{'Brand'.ljust(brand_w)}  Discount  Title")
    print("-" * (brand_w + 2 + 8 + 2 + 60))
    for r in rows:
        pct = "-" if r["discount_percent"] is None else f"{r['discount_percent']}%"
        title = r["title"]
        if len(title) > 60:
            title = title[:57] + "..."
        print(f"{r['brand'][:brand_w].ljust(brand_w)}  {pct.ljust(8)}  {title}")

async def run(headless: bool, timeout_ms: int):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(timeout_ms)

        await page.goto(OFFERS_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)

        await auto_scroll(page, idle_rounds=4, delay_ms=900)

        rows = await extract_cards(page)
        print_table(rows)

        # Load previous snapshot BEFORE overwriting
        prev = load_json(STATE_PREV)

        save_json(rows, STATE_JSON)
        save_csv(rows, STATE_CSV)
        print(f"\nSaved {len(rows)} offers to: {STATE_JSON} and {STATE_CSV}")

        added, removed = diff(prev, rows)
        if added or removed:
            print("\nChanges since last run:")
            for a in added:
                print(f"  + {a['brand']} — {a['title']}")
            for r in removed:
                print(f"  - {r['brand']} — {r['title']}")
        else:
            print("\nNo changes since last run.")

        # Rotate: current -> prev for next time
        save_json(rows, STATE_PREV)

        await context.close()
        await browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape McArthurGlen Vancouver offers")
    parser.add_argument("--no-headless", action="store_true", help="Show browser window")
    parser.add_argument("--timeout", type=int, default=30000, help="Default timeout in ms")
    args = parser.parse_args()
    asyncio.run(run(headless=not args.no_headless, timeout_ms=args.timeout))
