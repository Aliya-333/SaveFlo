
import os
import re
import statistics

import requests
from dotenv import load_dotenv

load_dotenv()


def parse_price(value):
    """Convert prices such as ₹1,299.00 or Rs. 1,299 into floats."""
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


def matches_query(query, title):
    """Reject results that do not contain the important search terms."""
    query_words = re.findall(r"[a-z0-9]+", query.lower())
    title_words = set(re.findall(r"[a-z0-9]+", title.lower()))

    stop_words = {"the", "a", "an", "for", "with", "and", "or"}

    important_words = [
        word for word in query_words
        if word not in stop_words
    ]

    return all(word in title_words for word in important_words)


def search_serpapi(query, country="in"):
    """Fetch product listings using SerpAPI Google Shopping."""

    key = os.getenv("SERPAPI_KEY")

    if not key:
        raise RuntimeError(
            "SERPAPI_KEY is missing from your environment variables."
        )

    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": key,
        "gl": country,
        "hl": "en",
        "google_domain": (
            f"google.co.{country}"
            if country == "in"
            else "google.com"
        ),
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
        title = item.get("title", "").strip()

        if not title or not matches_query(query, title):
            continue

        price = item.get("extracted_price")

        if price is None:
            price = parse_price(item.get("price"))

        if price is None or price <= 0:
            continue

        # Do not follow redirects: they may lead to Google's
        # "sorry" or blocking page.
        raw_link = (
            item.get("product_link")
            or item.get("link")
            or ""
        )

        # Reject known Google blocking URLs.
        if "google.com/sorry" in raw_link.lower():
            raw_link = ""

        results.append({
            "store": item.get("source", "Unknown store"),
            "title": title,
            "price": float(price),
            "link": raw_link,
            "image": item.get("thumbnail", ""),
        })

    # Filter extreme low-price outliers when enough results exist.
    if len(results) >= 3:
        median_price = statistics.median(
            item["price"] for item in results
        )

        results = [
            item for item in results
            if item["price"] >= median_price * 0.20
        ]

    return results


def search_demo(query, country="in"):
    """Fake listings for testing without an API key."""

    base = 1000 + (sum(ord(c) for c in query) % 40) * 100

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
            "store": name,
            "title": query.title(),
            "price": round(base * factor, 2),
            "link": "https://example.com",
            "image": "",
        }
        for name, factor in stores
    ]


PROVIDERS = {
    "serpapi": search_serpapi,
    "demo": search_demo,
}