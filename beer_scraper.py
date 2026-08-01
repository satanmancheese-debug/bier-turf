"""
Beer price-per-liter scraper for Dutch supermarkets (v2 - Playwright based).

WHY v2: the first version used `requests` + BeautifulSoup, which only ever
sees the raw server HTML. All of these sites load their product listings
via JavaScript after the page loads, so `requests` got back an empty shell
(0 results) or, for Albert Heijn, an outright 403 from their bot protection.

This version instead:
  - Uses Playwright to actually render pages in a real (headless) browser,
    so we see the same DOM you'd see in your own browser.
  - For Albert Heijn, calls their public product-search JSON endpoint
    directly (used by their own website search bar) instead of rendering
    the page -- lighter weight and avoids their anti-bot page protection.

SETUP
-----
    pip install playwright requests --break-system-packages
    playwright install chromium

USAGE
-----
    python beer_scraper_v2.py
    python beer_scraper_v2.py --csv beers.csv
    python beer_scraper_v2.py --only Dirk "Albert Heijn"
    python beer_scraper_v2.py --headed      # watch the browser work, for debugging

IMPORTANT
---------
I can't reach any of these sites from my own environment to test this end
to end, so treat this as a solid starting point rather than a finished,
verified product. Run it and send me whatever prints out (including any
"found 0" lines or tracebacks) and I'll help adjust the specific site
that's giving trouble -- these things need a feedback loop with someone
who can actually load the pages.
"""

import re
import csv
import sys
import time
import argparse
from dataclasses import dataclass, asdict
from typing import List, Optional

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class Beer:
    supermarket: str
    name: str
    price: float
    volume_liters: float
    price_per_liter: float
    url: Optional[str] = None


# --------------------------------------------------------------------------
# Shared parsing helpers
# --------------------------------------------------------------------------

def to_float(num_str: str) -> Optional[float]:
    if not num_str:
        return None
    try:
        return float(num_str.strip().replace(",", "."))
    except ValueError:
        return None


def parse_volume_to_liters(text: str) -> Optional[float]:
    """
    "500 ml" -> 0.5 | "1,8 liter" -> 1.8 | "6 x 0,33 l" -> 1.98
    "6x33cl" -> 1.98 | "24 x 300 ml" -> 7.2
    """
    if not text:
        return None
    text = text.lower().replace("×", "x")

    m = re.search(r"(\d+)\s*x\s*([\d.,]+)\s*(ml|cl|l|liter)\b", text)
    if m:
        count = int(m.group(1))
        amount = to_float(m.group(2))
        if amount is None:
            return None
        return round(count * _unit_to_liters(amount, m.group(3)), 4)

    m = re.search(r"([\d.,]+)\s*(ml|cl|liter|l)\b", text)
    if m:
        amount = to_float(m.group(1))
        if amount is None:
            return None
        return round(_unit_to_liters(amount, m.group(2)), 4)

    return None


def _unit_to_liters(amount: float, unit: str) -> float:
    if unit == "ml":
        return amount / 1000.0
    if unit == "cl":
        return amount / 100.0
    return amount


def find_price_in_text(text: str) -> Optional[float]:
    """Grab the first plausible €X,XX / X.XX price in a text block."""
    m = re.search(r"(\d{1,3}[.,]\d{2})(?!\d)", text)
    return to_float(m.group(1)) if m else None


def make_beer(supermarket, name, price, volume_liters, url=None) -> Optional[Beer]:
    if not name or price is None or not volume_liters or volume_liters <= 0:
        return None
    name = re.sub(r"\s+", " ", name).strip(" -·|")
    if not name or len(name) > 120:
        return None
    return Beer(
        supermarket=supermarket,
        name=name,
        price=round(price, 2),
        volume_liters=round(volume_liters, 4),
        price_per_liter=round(price / volume_liters, 2),
        url=url,
    )


# --------------------------------------------------------------------------
# Generic Playwright-based "follow product links" scraper
# --------------------------------------------------------------------------
# Rather than guessing CSS class names (which I can't verify without
# rendering the page myself), this finds every link that matches a known
# product-URL pattern for the site, then walks up the DOM from that link
# to the smallest ancestor whose visible text looks like a full product
# card (name + price + size), and parses that block of text.

def scrape_via_links(
    supermarket: str,
    url: str,
    link_pattern: str,
    headless: bool = True,
    wait_selector: Optional[str] = None,
    scroll_passes: int = 6,
) -> List[Beer]:
    print(f"Scraping {supermarket} ({url}) ...")
    results = []
    seen_hrefs = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=UA, locale="nl-NL")
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except PWTimeout:
                    pass
            # Many of these sites lazy-load products as you scroll.
            for _ in range(scroll_passes):
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(600)

            links = page.query_selector_all(f"a[href*='{link_pattern}']")
            print(f"  found {len(links)} candidate product links")

            for link in links:
                href = link.get_attribute("href") or ""
                if not href or href in seen_hrefs:
                    continue
                seen_hrefs.add(href)

                # Walk up to a reasonably-sized "card" container.
                card_text = None
                handle = link
                for _ in range(5):
                    parent = handle.evaluate_handle("el => el.parentElement")
                    parent_el = parent.as_element()
                    if not parent_el:
                        break
                    txt = parent_el.inner_text()
                    if txt and 15 <= len(txt) <= 500:
                        card_text = txt
                    handle = parent_el
                    if card_text and len(txt) > 250:
                        break

                if not card_text:
                    continue

                price = find_price_in_text(card_text)
                volume = parse_volume_to_liters(card_text)
                # crude name guess: first line of the card text
                name = card_text.strip().split("\n")[0]
                full_url = href if href.startswith("http") else requests.compat.urljoin(url, href)
                beer = make_beer(supermarket, name, price, volume, full_url)
                if beer:
                    results.append(beer)
        except PWTimeout:
            print(f"  [!] Timed out loading {url}")
        finally:
            browser.close()

    return results


def scrape_dirk(headless=True) -> List[Beer]:
    return scrape_via_links(
        "Dirk",
        "https://www.dirk.nl/zoeken/producten/bier",
        link_pattern="/boodschappen/",
        headless=headless,
    )


def scrape_lidl(headless=True) -> List[Beer]:
    return scrape_via_links(
        "Lidl",
        "https://www.lidl.nl/c/assortiment-frisdranken-sappen-en-bier/a10008773",
        link_pattern="/p/",
        headless=headless,
    )


def scrape_aldi(headless=True) -> List[Beer]:
    return scrape_via_links(
        "Aldi",
        "https://www.aldi.nl/producten/bier-en-likeuren/bier.html",
        link_pattern=".article.html",
        headless=headless,
    )


def scrape_hoogvliet(headless=True) -> List[Beer]:
    return scrape_via_links(
        "Hoogvliet",
        "https://www.hoogvliet.com/bier-wijn-sterke-drank/bier",
        link_pattern="/product/",
        headless=headless,
    )


def scrape_vomar(headless=True) -> List[Beer]:
    return scrape_via_links(
        "Vomar",
        "https://www.vomar.nl/producten/bier-wijn-sterke-drank/bier",
        link_pattern="/bier-wijn-sterke-drank/bier/",
        headless=headless,
    )


# --------------------------------------------------------------------------
# Albert Heijn: use their public product-search JSON endpoint directly
# instead of rendering the page. This is the same endpoint their own
# website search bar calls. It may still be behind bot protection --
# if you get blocked, the Playwright approach above can be used instead
# (swap in a `scrape_ah_dom` using scrape_via_links with link_pattern
# "/producten/product/").
# --------------------------------------------------------------------------

def scrape_ah(headless=True) -> List[Beer]:
    print("Scraping Albert Heijn (JSON search API) ...")
    results = []
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "nl-NL,nl;q=0.9",
    })

    page_num = 0
    while page_num < 10:  # safety cap
        try:
            resp = session.get(
                "https://www.ah.nl/zoeken/api/products/search",
                params={"query": "bier", "page": page_num, "size": 36},
                timeout=20,
            )
        except requests.RequestException as e:
            print(f"  [!] Request failed: {e}")
            break

        if resp.status_code != 200:
            print(f"  [!] AH search API returned {resp.status_code} "
                  f"(their bot protection may be blocking this -- see comment above)")
            break

        try:
            data = resp.json()
        except ValueError:
            print("  [!] AH search API did not return JSON (likely blocked)")
            break

        cards = data.get("cards") or data.get("products") or []
        if not cards:
            break

        for card in cards:
            products = card.get("products") if isinstance(card, dict) else None
            items = products if products else [card]
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = item.get("title") or item.get("description")
                price_info = item.get("priceBeforeBonus") or item.get("price") or {}
                price = None
                if isinstance(price_info, dict):
                    price = price_info.get("now") or price_info.get("amount")
                elif isinstance(price_info, (int, float)):
                    price = price_info
                unit_size = item.get("unitSize") or item.get("salesUnitSize") or ""
                volume = parse_volume_to_liters(unit_size)
                webshop_id = item.get("webshopId")
                url = f"https://www.ah.nl/producten/product/{webshop_id}" if webshop_id else None
                beer = make_beer("Albert Heijn", title, price, volume, url)
                if beer:
                    results.append(beer)

        page_num += 1
        time.sleep(1)

    return results


SCRAPERS = {
    "Albert Heijn": scrape_ah,
    "Dirk": scrape_dirk,
    "Lidl": scrape_lidl,
    "Aldi": scrape_aldi,
    "Hoogvliet": scrape_hoogvliet,
    "Vomar": scrape_vomar,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="Optional path to save results as CSV")
    parser.add_argument("--only", nargs="*", choices=list(SCRAPERS.keys()))
    parser.add_argument("--headed", action="store_true",
                         help="Show the browser window (useful for debugging)")
    args = parser.parse_args()

    supermarkets = args.only or list(SCRAPERS.keys())
    all_beers: List[Beer] = []

    for name in supermarkets:
        try:
            beers = SCRAPERS[name](headless=not args.headed)
        except Exception as e:
            print(f"  [!] {name} crashed: {e}")
            beers = []
        print(f"  -> found {len(beers)} beers\n")
        all_beers.extend(beers)

    if not all_beers:
        print("No results from any supermarket. Please share the console "
              "output (including any [!] lines) so the specific site's "
              "scraper can be adjusted.")
        return

    all_beers.sort(key=lambda b: b.price_per_liter)

    print(f"\n{'Supermarket':<15}{'Name':<45}{'Price':>8}{'Liters':>9}{'EUR/L':>9}")
    print("-" * 90)
    for b in all_beers:
        print(f"{b.supermarket:<15}{b.name[:43]:<45}{b.price:>8.2f}{b.volume_liters:>9.2f}{b.price_per_liter:>9.2f}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(all_beers[0]).keys()))
            writer.writeheader()
            for b in all_beers:
                writer.writerow(asdict(b))
        print(f"\nSaved {len(all_beers)} rows to {args.csv}")


if __name__ == "__main__":
    main()