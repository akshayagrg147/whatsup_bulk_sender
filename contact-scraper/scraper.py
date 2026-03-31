"""
Web scraper for business directories (Google Maps).
Extracts business name, phone, website and basic details.
"""

import random
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from config import (
    USER_AGENTS,
    SLEEP_MIN,
    SLEEP_MAX,
    MAX_RESULTS_PER_SEARCH,
)


@dataclass
class BusinessRecord:
    """Single business entry."""
    name: str
    phone: str
    website: str
    rating: str
    reviews: str
    category: str
    address: str


def _random_sleep() -> None:
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


def _normalize_phone(s: str) -> str:
    if not s or not isinstance(s, str):
        return ""
    s = re.sub(r"\s+", " ", s.strip())
    return s


def _normalize_website(s: str) -> str:
    if not s or not isinstance(s, str):
        return ""
    s = s.strip().lower()
    if s and not (s.startswith("http://") or s.startswith("https://")):
        s = "https://" + s
    return s


def _has_website(website: str) -> bool:
    w = _normalize_website(website)
    if not w:
        return False
    # Ignore placeholder or generic links
    if "google.com" in w and ("maps" in w or "search" in w):
        return False
    return True


def _unwrap_google_redirect(href: str) -> str:
    """
    Google Maps sometimes uses redirect URLs like:
    https://www.google.com/url?q=https://example.com&sa=...
    """
    if not href:
        return ""
    try:
        u = urlparse(href)
        if u.netloc.endswith("google.com") and u.path == "/url":
            q = parse_qs(u.query).get("q", [""])[0]
            return q or href
    except Exception:
        pass
    return href


def scrape_google_maps(
    location: str,
    category: str,
    max_results: int = MAX_RESULTS_PER_SEARCH,
    headless: bool = True,
    on_progress: Optional[Callable[[str], None]] = None,
    on_result: Optional[Callable[["BusinessRecord"], None]] = None,
    only_without_website: bool = False,
    debug_website: bool = False,
) -> list[BusinessRecord]:
    """
    Search Google Maps for "{category} in {location}", extract details.
    If only_without_website is True, returns only businesses that do NOT have a website.
    """
    query = f"{category} in {location}"
    results: list[BusinessRecord] = []
    seen_names: set[str] = set()
    last_panel_title: str = ""
    last_panel_website: str = ""

    def log(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1280, "height": 900},
            locale="en-IN",
        )
        context.set_default_timeout(25000)
        page = context.new_page()

        try:
            log("Opening Google Maps...")
            page.goto("https://www.google.com/maps", wait_until="load", timeout=30000)
            _random_sleep()

            # Dismiss cookie/consent if present (so it doesn't block the search box)
            try:
                consent = page.get_by_role("button", name=re.compile(r"accept all|agree|i agree|accept", re.I))
                consent.first.wait_for(state="visible", timeout=3000)
                consent.first.click()
                _random_sleep()
            except Exception:
                pass

            log("Waiting for search box...")
            # Wait for search input (Maps loads it dynamically); try multiple selectors
            search_input = None
            for selector in ["#searchboxinput", "input[aria-label*='Search'], input[placeholder*='Search']", "input[name='q']"]:
                try:
                    loc = page.locator(selector).first
                    loc.wait_for(state="visible", timeout=10000)
                    search_input = loc
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

            # Wait for results feed
            try:
                page.wait_for_selector('div[role="feed"]', timeout=12000)
            except PlaywrightTimeout:
                log("Results feed did not load in time. You may be rate-limited.")
                return results
            _random_sleep()

            # Business card links (clicking opens the side panel with phone/website)
            card_links_selector = 'a[href*="/maps/place/"]'
            cards = page.locator(card_links_selector)
            n_cards = cards.count()
            if n_cards == 0:
                log("No business cards found. Try a different category or location.")
                return results

            log(f"Found ~{n_cards} results. Extracting (only businesses without website)...")
            collected = 0
            last_count = -1
            last_processed_index = 0
            scroll_attempts = 0
            max_scroll_attempts = 8

            while collected < max_results and scroll_attempts < max_scroll_attempts:
                cards = page.locator(card_links_selector)
                current_count = cards.count()
                if current_count == last_count:
                    scroll_attempts += 1
                else:
                    scroll_attempts = 0
                last_count = current_count

                for i in range(last_processed_index, min(current_count, last_processed_index + 15)):
                    if collected >= max_results:
                        break
                    try:
                        log(f"  Opening result {i+1}/{current_count}…")
                        card = cards.nth(i)
                        card.scroll_into_view_if_needed()
                        _random_sleep()
                        # Click to open side panel (get details)
                        card.click()
                        time.sleep(random.uniform(1.8, 3.0))

                        # Business name (from side panel or list)
                        name = ""
                        name_el = page.locator('h1.DUwDvf').first
                        # Ensure the details panel actually switched to a new place
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
                            name_el = page.locator('.qBF1Pd').first
                            if name_el.count() > 0:
                                name = name_el.inner_text().strip() or ""
                        if not name:
                            continue
                        last_panel_title = name

                        if name in seen_names:
                            continue
                        seen_names.add(name)

                        # Phone: look for tel: link or common \"copy phone\" element
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

                        # Website: pull from the currently open place details panel.
                        # The old logic could accidentally grab the first Website link on the page,
                        # causing many rows to show the same website.
                        website = ""
                        try:
                            # Narrow selector: prefer the dedicated authority field for the currently open place.
                            details_panel = page.locator('div[role="main"]').first
                            authority = details_panel.locator('[data-item-id="authority"]').first
                            if authority.count() > 0:
                                href = (authority.get_attribute("href") or "").strip()
                                aria = (authority.get_attribute("aria-label") or "").strip()
                                text = (authority.inner_text() or "").strip()
                                href = _unwrap_google_redirect(href)
                                if debug_website:
                                    log(f"  Website(authority) href={href!r} aria={aria!r} text={text!r}")
                                if href.startswith("http") and "google." not in href.lower() and (not aria or name.lower() in aria.lower()):
                                    website = href
                                else:
                                    m = re.search(r"website\\s*:\\s*(.+)$", aria, flags=re.I)
                                    if m:
                                        website = m.group(1).strip()
                                    elif re.search(r"(\\.|\\bwww\\b)", text, flags=re.I) and len(text) <= 120:
                                        website = text

                            # Fallback: scan a few visible candidates in the details panel
                            if not website:
                                candidates = details_panel.locator(
                                    'a[data-item-id*="authority"], button[data-item-id*="authority"], '
                                    'a[aria-label^="Website"], button[aria-label^="Website"], '
                                    'a:has-text("Website"), button:has-text("Website")'
                                )
                                for j in range(min(candidates.count(), 12)):
                                    el = candidates.nth(j)
                                    try:
                                        if not el.is_visible():
                                            continue
                                    except Exception:
                                        continue

                                    href = _unwrap_google_redirect((el.get_attribute("href") or "").strip())
                                    aria = (el.get_attribute("aria-label") or "").strip()
                                    text = (el.inner_text() or "").strip()
                                    if debug_website:
                                        log(f"  Website(candidate#{j}) href={href!r} aria={aria!r} text={text!r}")

                                    # Ignore ad/tracking and relative links like /aclk?... or /maps?... etc
                                    if href.startswith("/") or href.startswith("about:") or href.startswith("javascript:"):
                                        continue

                                    # Only accept website link that belongs to the CURRENT place.
                                    # Google often renders multiple Website buttons, including ads or other businesses.
                                    if aria and name.lower() not in aria.lower():
                                        continue

                                    if href.startswith("http") and "google." not in href.lower():
                                        website = href
                                        break
                                    m = re.search(r"website\\s*:\\s*(.+)$", aria, flags=re.I)
                                    if m:
                                        website = m.group(1).strip()
                                        break
                                    if re.search(r"(\\.|\\bwww\\b)", text, flags=re.I) and len(text) <= 120:
                                        website = text
                                        break

                            # If the website didn't change across different place titles, retry once (panel may still be updating)
                            if website and last_panel_website and website == last_panel_website and name and name != last_panel_title:
                                if debug_website:
                                    log("  Website looks stale; retrying extraction after short wait…")
                                time.sleep(1.0)
                                # One retry: re-run the authority read
                                authority = details_panel.locator('[data-item-id="authority"]').first
                                href = _unwrap_google_redirect((authority.get_attribute("href") or "").strip()) if authority.count() > 0 else ""
                                if href.startswith("http") and "google." not in href.lower():
                                    website = href
                        except Exception:
                            pass

                        # Rating / reviews (best-effort)
                        rating = ""
                        reviews = ""
                        try:
                            # Prefer scoping to the details panel so we don't pick up list ratings.
                            details_panel = page.locator('div[role="main"]').first

                            # Common pattern: a span with aria-label like "4.6 stars" (and sometimes "1,234 reviews")
                            stars = details_panel.locator('span[aria-label*="stars"]').first
                            if stars.count() > 0:
                                aria = stars.get_attribute("aria-label") or ""
                                m = re.search(r"([0-9]+(?:\\.[0-9]+)?)\\s*stars", aria)
                                if m:
                                    rating = m.group(1)
                                m2 = re.search(r"([0-9,]+)\\s*reviews", aria)
                                if m2:
                                    reviews = m2.group(1).replace(",", "")

                            # Fallback: sometimes the rating is a plain text element near the title
                            if not rating:
                                rating_text = (details_panel.locator('.F7nice span').first.inner_text() or "").strip()
                                m = re.search(r"^([0-9]+(?:\\.[0-9]+)?)$", rating_text)
                                if m:
                                    rating = m.group(1)

                            # Fallback: parse from any aria-label containing "Rated X" or "Rating: X"
                            if not rating:
                                any_rating = details_panel.locator('[aria-label*="Rated"], [aria-label*="Rating"]').first
                                if any_rating.count() > 0:
                                    aria = any_rating.get_attribute("aria-label") or ""
                                    m = re.search(r"([0-9]+(?:\\.[0-9]+)?)", aria)
                                    if m:
                                        rating = m.group(1)
                        except Exception:
                            pass

                        # Category / address (best-effort)
                        place_category = ""
                        address = ""
                        try:
                            cat_el = page.locator('button[jsaction*="pane.rating.category"]').first
                            if cat_el.count() > 0:
                                place_category = (cat_el.inner_text() or "").strip()
                        except Exception:
                            pass
                        try:
                            addr_el = page.locator('[data-item-id="address"], button[aria-label^="Address:"], div[aria-label^="Address:"]').first
                            if addr_el.count() > 0:
                                address = (addr_el.inner_text() or "").strip()
                                if not address:
                                    aria = addr_el.get_attribute("aria-label") or ""
                                    address = aria.replace("Address:", "").strip()
                        except Exception:
                            pass

                        website_norm = _normalize_website(website)
                        has_site = _has_website(website_norm)
                        if has_site:
                            last_panel_website = website_norm
                        if only_without_website and has_site:
                            log(f"  Skipped (has website): {name}")
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
                        log(f"  Added: {name} ({phone or '—'}){' [has website]' if has_site else ''}")
                        if on_result:
                            on_result(record)

                    except Exception as e:
                        log(f"  Error while extracting a result: {type(e).__name__}: {e}")
                        continue

                last_processed_index = min(current_count, last_processed_index + 15)
                # Scroll the results feed to load more
                feed = page.locator('div[role="feed"]').first
                if feed.count() > 0:
                    feed.evaluate("el => el.scrollTop = el.scrollHeight")
                    _random_sleep()
                else:
                    break

        except Exception as e:
            log(f"Scraping error: {e}")
        finally:
            browser.close()

    return results


def run_scrape(
    location: str,
    category: str,
    max_results: int = MAX_RESULTS_PER_SEARCH,
    headless: bool = True,
    on_progress: Optional[Callable[[str], None]] = None,
    on_result: Optional[Callable[[BusinessRecord], None]] = None,
    only_without_website: bool = False,
    debug_website: bool = False,
) -> list[BusinessRecord]:
    """
    Main entry: run Google Maps scrape and return only businesses without a website.
    """
    return scrape_google_maps(
        location=location,
        category=category,
        max_results=max_results,
        headless=headless,
        on_progress=on_progress,
        on_result=on_result,
        only_without_website=only_without_website,
        debug_website=debug_website,
    )
