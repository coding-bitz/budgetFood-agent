import logging
import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests
from strands import tool

logger = logging.getLogger(__name__)

AGGREGATOR_DOMAINS = (
    "yelp.com",
    "tripadvisor.com",
    "grubhub.com",
    "ubereats.com",
    "doordash.com",
    "seamless.com",
    "postmates.com",
    "facebook.com",
    "instagram.com",
    "mapquest.com",
    "yellowpages.com",
)

MENU_KEYWORDS = ("menu", "food menu", "carta", "lunch", "dinner", "food")
PRICE_PATTERN = re.compile(r"\$\s*(\d+(?:\.\d{2})?)")


def _is_aggregator(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return any(agg in domain for agg in AGGREGATOR_DOMAINS)


def _search_official_website(place_name: str, address: str) -> str | None:
    serper_api_key = os.environ["SERPER_API_KEY"]
    headers = {
        "X-API-KEY": serper_api_key,
        "Content-Type": "application/json",
    }
    query = f"{place_name} {address} menu"
    response = requests.post(
        "https://google.serper.dev/search",
        headers=headers,
        json={"q": query, "num": 5},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    for item in data.get("organic", []):
        link = item.get("link")
        if link and not _is_aggregator(link):
            return link
    return None


def _find_menu_url(base_url: str, soup: BeautifulSoup) -> str:
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        text = link.get_text(strip=True).lower()
        href_lower = href.lower()
        if any(keyword in text or keyword in href_lower for keyword in MENU_KEYWORDS):
            full_url = urljoin(base_url, href)
            # Ensure menu link stays on same domain or valid HTTP
            if urlparse(full_url).scheme in ("http", "https"):
                return full_url
    return base_url


def _extract_dishes_from_soup(soup: BeautifulSoup) -> list[dict]:
    dishes: list[dict] = []
    seen_names: set[str] = set()

    # Look for menu item containers or elements with prices
    candidate_elements = soup.find_all(["div", "li", "tr", "article", "section", "p"])
    for elem in candidate_elements:
        text = elem.get_text(" ", strip=True)
        price_match = PRICE_PATTERN.search(text)
        if not price_match:
            continue

        try:
            price = float(price_match.group(1))
        except (ValueError, IndexError):
            continue

        # Extract item name from headings or leading text
        name = ""
        heading = elem.find(["h3", "h4", "h5", "h6", "strong", "b"])
        if heading:
            heading_text = heading.get_text(strip=True)
            if heading_text and not PRICE_PATTERN.search(heading_text):
                name = heading_text

        if not name:
            # Fall back to text preceding the price match in the element
            before_price = text[: price_match.start()].strip()
            # Clean common separators
            parts = [p.strip() for p in re.split(r"[\n\r|\-–•]", before_price) if p.strip()]
            if parts:
                name = parts[-1]

        # Clean and validate name
        name = re.sub(r"^[^\w]+|[^\w]+$", "", name).strip()
        if not name or len(name) < 3 or len(name) > 80:
            continue
        if name.lower() in seen_names:
            continue

        description = None
        desc_elem = elem.find(["p", "span"], class_=re.compile(r"desc|detail|ingredient", re.I))
        if desc_elem and desc_elem.get_text(strip=True) != name:
            desc_text = desc_elem.get_text(" ", strip=True)
            if desc_text and not PRICE_PATTERN.search(desc_text):
                description = desc_text

        seen_names.add(name.lower())
        dishes.append({"name": name, "price": price, "description": description})

    return dishes


@tool
def read_web_menu(
    place_id: str,
    place_name: str,
    address: str,
    website: str | None,
) -> dict:
    """
    Determines whether a place has a menu published on its website and, if
    so, extracts the dishes with their real prices.

    Step 1: if website is None, use the external search API (Serper.dev) with
    a query like "{place_name} {address} menu" to try to find the official
    site's URL. If no reasonable URL is found, has_website = False.

    Step 2: if there is a website, download its content and look for a menu
    section or page (links with text like "menu", "food menu", "carta"). If
    none is found, has_menu = False.

    Step 3: if there is a menu, extract the dishes and prices EXACTLY as they
    appear on the page (never estimated, never rounded, never invented).

    Returns exactly:
    {
      "has_website": bool,
      "has_menu": bool,
      "menu_url": str | None,
      "dishes": [{"name": str, "price": float, "description": str | None}]
    }

    If has_website or has_menu is False, "dishes" must be an empty list.
    Never invent a dish or price that isn't literally on the page.
    """
    target_url = website

    # Step 1: Fall back to Serper.dev if website is not provided
    if not target_url:
        try:
            target_url = _search_official_website(place_name, address)
        except Exception as exc:
            logger.error("Serper API search failed for %s: %s", place_name, exc)
            return {
                "has_website": False,
                "has_menu": False,
                "menu_url": None,
                "dishes": [],
            }

    if not target_url:
        return {
            "has_website": False,
            "has_menu": False,
            "menu_url": None,
            "dishes": [],
        }

    # Step 2: Download website content and locate menu page
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    try:
        response = requests.get(target_url, headers=headers, timeout=12)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
    except Exception as exc:
        logger.warning("Failed to retrieve website %s: %s", target_url, exc)
        return {
            "has_website": True,
            "has_menu": False,
            "menu_url": None,
            "dishes": [],
        }

    menu_url = _find_menu_url(target_url, soup)
    menu_soup = soup
    if menu_url != target_url:
        try:
            menu_response = requests.get(menu_url, headers=headers, timeout=12)
            menu_response.raise_for_status()
            menu_soup = BeautifulSoup(menu_response.text, "lxml")
        except Exception as exc:
            logger.warning("Failed to retrieve menu page %s: %s", menu_url, exc)
            # Fall back to analyzing base page
            menu_url = target_url

    # Step 3: Extract dishes and exact prices from menu page
    dishes = _extract_dishes_from_soup(menu_soup)

    if dishes:
        return {
            "has_website": True,
            "has_menu": True,
            "menu_url": menu_url,
            "dishes": dishes,
        }

    return {
        "has_website": True,
        "has_menu": False,
        "menu_url": None,
        "dishes": [],
    }
