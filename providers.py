"""
providers.py - where prices come from.

Each provider is a function that takes a search text and returns a list of dicts:
    {"store": str, "title": str, "price": float, "link": str, "image": str}

To add a new source later (another API, or a scraper), write one more function
that returns the same shape, then add it to PROVIDERS at the bottom.
"""
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()


def parse_price(value):
    """Turn '₹1,299.00' or 'Rs. 1,299' into the number 1299.0 (or None)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    return float(match.group().replace(",", ""))


def search_serpapi(query, country="in"):
    
    """Real prices from many stores at once, using Google Shopping via SerpAPI.
    """

    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise RuntimeError("SERPAPI_KEY is missing. Add it to your .env file.")

    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": key,
        "gl": country,
        "hl": "en",
        "google_domain": f"google.co.{country}" if country == "in" else "google.com",
    }

    response = requests.get(
        "https://serpapi.com/search.json",
        params=params,
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    results = []

    for item in data.get("shopping_results", []):

        price = item.get("extracted_price") or parse_price(item.get("price"))

        if price is None:
            continue

        # Get the product URL supplied by SerpAPI
        raw_link = item.get("link") or item.get("product_link") or ""

        # Try to follow redirects to the final retailer URL
        final_link = raw_link

        if raw_link:
            try:
                redirect_response = requests.get(
                    raw_link,
                    allow_redirects=True,
                    timeout=10,
                    headers={
                        "User-Agent": "Mozilla/5.0"
                    }
                )

                final_link = redirect_response.url

            except requests.RequestException:
                # If the URL cannot be resolved, keep the original URL
                final_link = raw_link

        results.append({
            "store": item.get("source", "Unknown store"),
            "title": item.get("title", ""),
            "price": price,
            "link": final_link,
            "image": item.get("thumbnail", ""),
        })

    return results


def search_demo(query, country="in"):
    """Fake data so you can build and test the website without any API key."""
    base = 1000 + (sum(ord(c) for c in query) % 40) * 100
    stores = [
        ("Amazon", 1.00), ("Flipkart", 0.96), ("Croma", 1.08),
        ("Reliance Digital", 1.05), ("Myntra", 1.12), ("Snapdeal", 0.93),
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


# Used by app.py. Order doesn't matter - results get sorted by price.
PROVIDERS = {
    "serpapi": search_serpapi,
    "demo": search_demo,
}
