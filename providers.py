
import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()


# ==================================================
# 1. PRODUCT CATEGORIES
# ==================================================

PRODUCT_TYPES = {
    "case": [
        "wallet case",
        "phone case",
        "mobile case",
        "back cover",
        "flip cover",
        "bumper case",
        "phone pouch",
        "case",
        "cover",
    ],
    "screen_protector": [
        "screen protector",
        "tempered glass",
        "screen guard",
        "display protector",
        "protective film",
    ],
    "charger": [
        "charging adapter",
        "power adapter",
        "wall charger",
        "wireless charger",
        "charger",
    ],
    "cable": [
        "charging cable",
        "usb cable",
        "type c cable",
        "usb c cable",
    ],
    "other_accessory": [
        "phone stand",
        "phone holder",
        "camera protector",
        "lens protector",
        "phone skin",
        "mobile skin",
        "replacement screen",
        "screen replacement",
    ],
}

# Accessories should not appear in searches for the main product.
ACCESSORY_TYPES = set(PRODUCT_TYPES)


# ==================================================
# 2. TEXT NORMALIZATION
# ==================================================

def normalize_text(text):
    """Normalize text for consistent comparisons."""
    text = (text or "").lower().strip()

    # Keep model numbers and storage capacities consistent.
    text = re.sub(r"(\d+)\s*(gb|tb)\b", r"\1\2", text)

    # Preserve + because it can identify models such as S24+.
    text = re.sub(r"[^a-z0-9+]+", " ", text)

    return re.sub(r"\s+", " ", text).strip()


# ==================================================
# 3. PRODUCT TYPE DETECTION
# ==================================================

def detect_product_type(text):
    """Detect which type of product the user wants."""
    text = normalize_text(text)

    # Specific phrases first, to avoid ambiguous matching.
    priority = [
        "screen_protector",
        "other_accessory",
        "charger",
        "cable",
        "case",
    ]

    for product_type in priority:
        for keyword in PRODUCT_TYPES[product_type]:
            if re.search(
                r"\b" + re.escape(keyword) + r"\b",
                text,
            ):
                return product_type

    return "main_product"


def has_product_type(title, requested_type):
    """Reject category mismatches."""
    actual_type = detect_product_type(title)

    if requested_type == "main_product":
        return actual_type == "main_product"

    return actual_type == requested_type


# ==================================================
# 4. SAMSUNG GALAXY S-SERIES VARIANTS
# ==================================================

def samsung_model_variant(text, model):
    """Return the variant following a Samsung S-series model."""
    text = normalize_text(text)

    pattern = (
        r"\b" + re.escape(model)
        + r"\s*(ultra|plus|fe|pro|max|mini|lite|edge|neo|fold|flip|\+)?\b"
    )

    match = re.search(pattern, text)

    if not match:
        return None

    variant = match.group(1) or ""

    if variant == "+":
        variant = "plus"

    return variant


# ==================================================
# 5. IPHONE MODEL VARIANTS
# ==================================================

def iphone_model_variant(text, model_number):
    """Return the variant following an iPhone model number."""
    text = normalize_text(text)

    pattern = (
        r"\biphone\s+" + re.escape(model_number)
        + r"(?:\s+(pro\s+max|pro|plus|mini|e))?\b"
    )

    match = re.search(pattern, text)

    if not match:
        return None

    return match.group(1) or ""


# ==================================================
# 6. PRODUCT MATCHING
# ==================================================

def matches_query(query, title):
    """
    Match the requested category and model.

    Examples:
    Galaxy S24 -> phone listings only
    Galaxy S24 case -> compatible case listings
    Galaxy S24 Ultra -> Ultra listings only
    """
    query_text = normalize_text(query)
    title_text = normalize_text(title)

    if not query_text or not title_text:
        return False

    requested_type = detect_product_type(query_text)

    # Step A: Reject results from the wrong category.
    if not has_product_type(title_text, requested_type):
        return False

    # Step B: Check Samsung Galaxy S-series model number.
    samsung_match = re.search(
        r"\b(s\d{2,3})\b",
        query_text,
    )

    if samsung_match:
        model = samsung_match.group(1)

        # The exact model number must appear in the title.
        if not re.search(
            r"\b" + re.escape(model) + r"\b",
            title_text,
        ):
            return False

        query_variant = samsung_model_variant(query_text, model)
        title_variant = samsung_model_variant(title_text, model)

        # S24 should not match S24 Ultra, FE, or Plus.
        if query_variant != title_variant:
            return False

    # Step C: Check iPhone model number and variant.
    iphone_match = re.search(
        r"\biphone\s+(\d{1,2})\b",
        query_text,
    )

    if iphone_match:
        model_number = iphone_match.group(1)

        # Require the same iPhone generation.
        if not re.search(
            r"\biphone\s+" + re.escape(model_number) + r"\b",
            title_text,
        ):
            return False

        query_variant = iphone_model_variant(
            query_text, model_number
        )
        title_variant = iphone_model_variant(
            title_text, model_number
        )

        if query_variant != title_variant:
            return False

    # Step D: Check the important query words.
    # Generic category words are excluded because retailers may
    # phrase compatible accessory titles differently.
    ignored_words = {
        "the", "a", "an", "for", "with", "and", "or",
        "phone", "mobile", "case", "cover", "wallet",
        "screen", "protector", "tempered", "glass",
        "charger", "charging", "adapter", "cable",
        "usb", "type", "stand", "holder", "back",
        "flip", "guard", "film", "power", "wireless",
    }

    query_words = set(re.findall(r"[a-z0-9]+", query_text))
    title_words = set(re.findall(r"[a-z0-9]+", title_text))

    important_words = query_words - ignored_words

    if not important_words.issubset(title_words):
        return False

    return True


# ==================================================
# 7. PRICE PARSING
# ==================================================

def parse_price(value):
    """Extract a positive numeric price."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        price = float(value)
        return price if price > 0 else None

    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))

    if not match:
        return None

    try:
        price = float(match.group().replace(",", ""))
        return price if price > 0 else None
    except ValueError:
        return None


# ==================================================
# 8. SERPAPI GOOGLE SHOPPING SEARCH
# ==================================================

def search_serpapi(query, country="in"):
    """Fetch and filter shopping listings using SerpAPI."""
    api_key = os.getenv("SERPAPI_KEY")

    if not api_key:
        raise RuntimeError(
            "SERPAPI_KEY is missing from environment variables."
        )

    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": api_key,
        "gl": country,
        "hl": "en",
    }

    response = requests.get(
        "https://serpapi.com/search.json",
        params=params,
        timeout=20,
    )

    response.raise_for_status()
    data = response.json()

    results = []

    for item in data.get("shopping_results", []):
        title = (item.get("title") or "").strip()

        # Skip accessory listings or incompatible model variants.
        if not title or not matches_query(query, title):
            continue

        price = item.get("extracted_price")

        if price is None:
            price = parse_price(item.get("price"))

        if price is None:
            continue

        try:
            price = float(price)
        except (TypeError, ValueError):
            continue

        if price <= 0:
            continue

        link = (
            item.get("product_link")
            or item.get("link")
            or ""
        )

        # Only accept standard HTTP/HTTPS URLs.
        if not re.match(r"^https?://", link, re.IGNORECASE):
            link = ""

        results.append({
            "store": item.get("source") or "Unknown store",
            "title": title,
            "price": price,
            "link": link,
            "image": item.get("thumbnail") or "",
        })

    return results


# ==================================================
# 9. DEMO PROVIDER
# These prices are simulated, not live prices.
# ==================================================

def search_demo(query, country="in"):
    """Generate example results for UI testing."""
    base = 1000 + (
        sum(ord(char) for char in query) % 40
    ) * 100

    stores = [
        ("Amazon", 1.00),
        ("Flipkart", 0.96),
        ("Croma", 1.08),
        ("Reliance Digital", 1.05),
        ("Myntra", 1.12),
        ("Snapdeal", 0.93),
    ]

    return [
        {
            "store": store_name,
            "title": query.title(),
            "price": round(base * factor, 2),
            "link": "https://example.com",
            "image": "",
        }
        for store_name, factor in stores
    ]


# ==================================================
# 10. PROVIDER REGISTRY
# ==================================================

PROVIDERS = {
    "serpapi": search_serpapi,
    "demo": search_demo,
}


# ==================================================
# 11. LOCAL MATCHING TESTS
# Run: python providers.py
# ==================================================

if __name__ == "__main__":
    tests = [
        ("Samsung Galaxy S24",
         "Samsung Galaxy S24 256GB", True),

        ("Samsung Galaxy S24",
         "Samsung Galaxy S24 Wallet Case", False),

        ("Samsung Galaxy S24",
         "Samsung Galaxy S24 Ultra", False),

        ("Samsung Galaxy S24",
         "Samsung Galaxy S24 FE", False),

        ("Samsung Galaxy S24 case",
         "Case for Samsung Galaxy S24", True),

        ("Samsung Galaxy S24 case",
         "Samsung Galaxy S24 Ultra Case", False),

        ("Samsung Galaxy S24 screen protector",
         "Tempered Glass for Samsung Galaxy S24", True),

        ("Samsung Galaxy S24 charger",
         "Samsung 25W Charger for Galaxy S24", True),

        ("iPhone 15",
         "Apple iPhone 15 128GB", True),

        ("iPhone 15",
         "Apple iPhone 15 Pro Max", False),

        ("iPhone 15 back cover",
         "iPhone 15 Back Cover", True),
    ]

    failures = 0

    for query, title, expected in tests:
        actual = matches_query(query, title)
        passed = actual == expected

        if not passed:
            failures += 1

        print(
            f"{'PASS' if passed else 'FAIL'}: "
            f"{query} -> {title} "
            f"(got {actual}, expected {expected})"
        )

    print(f"\n{len(tests) - failures}/{len(tests)} tests passed.")