import random
import re
import time
from dataclasses import asdict, dataclass
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

DEFAULT_SCRAPER_LOCATIONS = [
    "Gurugram",
    "Delhi",
    "Mumbai",
    "Bangalore",
    "Hyderabad",
    "Chennai",
    "Kolkata",
    "Pune",
    "Noida",
    "Faridabad",
    "Ghaziabad",
]

DEFAULT_SCRAPER_CATEGORIES = [
    "Clinics",
    "Dentists",
    "Hospitals",
    "Real Estate",
    "Schools",
    "Restaurants",
    "Gyms",
    "Salons",
    "Pharmacies",
    "Lawyers",
    "Accountants",
    "Plumbers",
    "Electricians",
    "Carpenters",
    "Photographers",
    "Travel Agents",
    "Insurance Agents",
]

SCRAPER_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

SCRAPER_SLEEP_MIN = 1.5
SCRAPER_SLEEP_MAX = 4.0
SCRAPER_MAX_RESULTS = 50


@dataclass
class BusinessRecord:
    name: str
    phone: str
    website: str
    rating: str
    reviews: str
    category: str
    address: str


def _random_sleep() -> None:
    time.sleep(random.uniform(SCRAPER_SLEEP_MIN, SCRAPER_SLEEP_MAX))


def _normalize_phone(value: str) -> str:
    if not value or not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip())


def _normalize_website(value: str) -> str:
    if not value or not isinstance(value, str):
        return ""
    value = value.strip().lower()
    if value and not (value.startswith("http://") or value.startswith("https://")):
        value = "https://" + value
    return value


def _has_website(website: str) -> bool:
    website = _normalize_website(website)
    if not website:
        return False
    if "google.com" in website and ("maps" in website or "search" in website):
        return False
    return True


def _unwrap_google_redirect(href: str) -> str:
    if not href:
        return ""
    try:
        parsed = urlparse(href)
        if parsed.netloc.endswith("google.com") and parsed.path == "/url":
            return parse_qs(parsed.query).get("q", [""])[0] or href
    except Exception:
        return href
    return href


def scrape_google_maps(
    location: str,
    category: str,
    max_results: int = SCRAPER_MAX_RESULTS,
    headless: bool = True,
    on_progress: Optional[Callable[[str], None]] = None,
    on_result: Optional[Callable[[BusinessRecord], None]] = None,
    only_without_website: bool = False,
    debug_website: bool = False,
) -> list[BusinessRecord]:
    query = f"{category} in {location}"
    results: list[BusinessRecord] = []
    seen_names: set[str] = set()
    last_panel_title = ""
    last_panel_website = ""

    def log(message: str) -> None:
        if on_progress:
            on_progress(message)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=random.choice(SCRAPER_USER_AGENTS),
            viewport={"width": 1280, "height": 900},
            locale="en-IN",
        )
        context.set_default_timeout(25000)
        page = context.new_page()

        try:
            log("Opening Google Maps...")
            page.goto("https://www.google.com/maps", wait_until="load", timeout=30000)
            _random_sleep()

            try:
                consent = page.get_by_role("button", name=re.compile(r"accept all|agree|i agree|accept", re.I))
                consent.first.wait_for(state="visible", timeout=3000)
                consent.first.click()
                _random_sleep()
            except Exception:
                pass

            log("Waiting for search box...")
            search_input = None
            for selector in [
                "#searchboxinput",
                "input[aria-label*='Search'], input[placeholder*='Search']",
                "input[name='q']",
            ]:
                try:
                    candidate = page.locator(selector).first
                    candidate.wait_for(state="visible", timeout=10000)
                    search_input = candidate
                    break
                except Exception:
                    continue

            if not search_input:
                log("Search box not found. Google Maps may have changed or the page did not load.")
                return results

            log(f"Searching: {query}")
            search_input.click()
            _random_sleep()
            search_input.fill("")
            _random_sleep()
            search_input.fill(query)
            _random_sleep()
            page.keyboard.press("Enter")
            _random_sleep()

            try:
                page.wait_for_selector('div[role="feed"]', timeout=12000)
            except PlaywrightTimeout:
                log("Results feed did not load in time. You may be rate-limited.")
                return results

            card_links_selector = 'a[href*="/maps/place/"]'
            cards = page.locator(card_links_selector)
            if cards.count() == 0:
                log("No business cards found. Try a different category or location.")
                return results

            log(f"Found ~{cards.count()} results. Extracting details...")
            collected = 0
            last_count = -1
            last_processed_index = 0
            scroll_attempts = 0

            while collected < max_results and scroll_attempts < 8:
                cards = page.locator(card_links_selector)
                current_count = cards.count()
                scroll_attempts = scroll_attempts + 1 if current_count == last_count else 0
                last_count = current_count

                for index in range(last_processed_index, min(current_count, last_processed_index + 15)):
                    if collected >= max_results:
                        break
                    try:
                        log(f"Opening result {index + 1}/{current_count}...")
                        card = cards.nth(index)
                        card.scroll_into_view_if_needed()
                        _random_sleep()
                        card.click()
                        time.sleep(random.uniform(1.8, 3.0))

                        name = ""
                        name_el = page.locator("h1.DUwDvf").first
                        try:
                            if last_panel_title:
                                page.wait_for_function(
                                    """prev => {
                                      const h = document.querySelector('h1.DUwDvf');
                                      return !!h && !!h.innerText && h.innerText.trim() !== prev;
                                    }""",
                                    last_panel_title,
                                    timeout=12000,
                                )
                        except Exception:
                            pass

                        if name_el.count() > 0:
                            name = name_el.inner_text().strip() or ""
                        if not name:
                            name_el = page.locator(".qBF1Pd").first
                            if name_el.count() > 0:
                                name = name_el.inner_text().strip() or ""
                        if not name or name in seen_names:
                            continue

                        seen_names.add(name)
                        last_panel_title = name

                        phone = ""
                        tel_links = page.locator('a[href^="tel:"]')
                        if tel_links.count() > 0:
                            phone = (tel_links.first.get_attribute("href") or "").replace("tel:", "").strip()
                        if not phone:
                            try:
                                phone_section = page.locator(
                                    'button[data-item-id*="phone"], div[data-item-id*="phone"], '
                                    'button[aria-label*="Copy phone number"], span[aria-label*="Phone"]'
                                ).first
                                if phone_section.count() > 0:
                                    phone = re.sub(r"[^\d+\-\s()]", "", phone_section.inner_text()).strip()
                            except Exception:
                                pass
                        phone = _normalize_phone(phone)

                        website = ""
                        try:
                            details_panel = page.locator('div[role="main"]').first
                            authority = details_panel.locator('[data-item-id="authority"]').first
                            if authority.count() > 0:
                                href = _unwrap_google_redirect((authority.get_attribute("href") or "").strip())
                                aria = (authority.get_attribute("aria-label") or "").strip()
                                text = (authority.inner_text() or "").strip()
                                if debug_website:
                                    log(f"Website(authority) href={href!r} aria={aria!r} text={text!r}")
                                if href.startswith("http") and "google." not in href.lower() and (not aria or name.lower() in aria.lower()):
                                    website = href
                                else:
                                    match = re.search(r"website\s*:\s*(.+)$", aria, flags=re.I)
                                    if match:
                                        website = match.group(1).strip()
                                    elif re.search(r"(\.|\bwww\b)", text, flags=re.I) and len(text) <= 120:
                                        website = text

                            if not website:
                                candidates = details_panel.locator(
                                    'a[data-item-id*="authority"], button[data-item-id*="authority"], '
                                    'a[aria-label^="Website"], button[aria-label^="Website"], '
                                    'a:has-text("Website"), button:has-text("Website")'
                                )
                                for idx in range(min(candidates.count(), 12)):
                                    element = candidates.nth(idx)
                                    try:
                                        if not element.is_visible():
                                            continue
                                    except Exception:
                                        continue
                                    href = _unwrap_google_redirect((element.get_attribute("href") or "").strip())
                                    aria = (element.get_attribute("aria-label") or "").strip()
                                    text = (element.inner_text() or "").strip()
                                    if debug_website:
                                        log(f"Website(candidate#{idx}) href={href!r} aria={aria!r} text={text!r}")
                                    if href.startswith("/") or href.startswith("about:") or href.startswith("javascript:"):
                                        continue
                                    if aria and name.lower() not in aria.lower():
                                        continue
                                    if href.startswith("http") and "google." not in href.lower():
                                        website = href
                                        break
                                    match = re.search(r"website\s*:\s*(.+)$", aria, flags=re.I)
                                    if match:
                                        website = match.group(1).strip()
                                        break
                                    if re.search(r"(\.|\bwww\b)", text, flags=re.I) and len(text) <= 120:
                                        website = text
                                        break

                            if website and last_panel_website and website == last_panel_website and name and name != last_panel_title:
                                time.sleep(1.0)
                                authority = details_panel.locator('[data-item-id="authority"]').first
                                href = _unwrap_google_redirect((authority.get_attribute("href") or "").strip()) if authority.count() > 0 else ""
                                if href.startswith("http") and "google." not in href.lower():
                                    website = href
                        except Exception:
                            pass

                        rating = ""
                        reviews = ""
                        try:
                            details_panel = page.locator('div[role="main"]').first
                            stars = details_panel.locator('span[aria-label*="stars"]').first
                            if stars.count() > 0:
                                aria = stars.get_attribute("aria-label") or ""
                                match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*stars", aria)
                                if match:
                                    rating = match.group(1)
                                match = re.search(r"([0-9,]+)\s*reviews", aria)
                                if match:
                                    reviews = match.group(1).replace(",", "")
                            if not rating:
                                rating_text = (details_panel.locator(".F7nice span").first.inner_text() or "").strip()
                                match = re.search(r"^([0-9]+(?:\.[0-9]+)?)$", rating_text)
                                if match:
                                    rating = match.group(1)
                        except Exception:
                            pass

                        place_category = ""
                        address = ""
                        try:
                            category_el = page.locator('button[jsaction*="pane.rating.category"]').first
                            if category_el.count() > 0:
                                place_category = (category_el.inner_text() or "").strip()
                        except Exception:
                            pass
                        try:
                            address_el = page.locator('[data-item-id="address"], button[aria-label^="Address:"], div[aria-label^="Address:"]').first
                            if address_el.count() > 0:
                                address = (address_el.inner_text() or "").strip()
                                if not address:
                                    address = (address_el.get_attribute("aria-label") or "").replace("Address:", "").strip()
                        except Exception:
                            pass

                        website_norm = _normalize_website(website)
                        has_site = _has_website(website_norm)
                        if has_site:
                            last_panel_website = website_norm
                        if only_without_website and has_site:
                            log(f"Skipped (has website): {name}")
                            continue

                        record = BusinessRecord(
                            name=name,
                            phone=phone or "—",
                            website=website_norm if has_site else "",
                            rating=rating,
                            reviews=reviews,
                            category=place_category,
                            address=address,
                        )
                        results.append(record)
                        collected += 1
                        log(f"Added: {name} ({phone or '—'})")
                        if on_result:
                            on_result(record)
                    except Exception as exc:
                        log(f"Error while extracting a result: {type(exc).__name__}: {exc}")

                last_processed_index = min(current_count, last_processed_index + 15)
                feed = page.locator('div[role="feed"]').first
                if feed.count() > 0:
                    feed.evaluate("el => el.scrollTop = el.scrollHeight")
                    _random_sleep()
                else:
                    break
        finally:
            browser.close()

    return results


def run_scrape(**kwargs) -> list[BusinessRecord]:
    return scrape_google_maps(**kwargs)


def record_to_dict(record: BusinessRecord) -> dict:
    return asdict(record)
