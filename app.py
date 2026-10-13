"""
app.py - the backend (the "brain") of the price comparison site.

Flow:  browser -> /api/search?q=iphone -> providers fetch prices
       -> compare() finds the cheapest store -> JSON back to the browser
"""
import os
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv(override=True)

from providers import PROVIDERS  # noqa: E402  (must come after load_dotenv)

app = Flask(__name__)

# "serpapi" = real prices, "demo" = fake data (no key needed)
PROVIDER_NAME = os.getenv("PROVIDER", "demo")
COUNTRY = os.getenv("COUNTRY", "in")
print("ACTIVE PROVIDER:", PROVIDER_NAME)
print("SERPAPI KEY LOADED:", bool(os.getenv("SERPAPI_KEY")))

# Tiny in-memory cache so repeated searches don't use up your API quota.
CACHE = {}
CACHE_SECONDS = 600  # 10 minutes

# How many listings to show from each store (the cheapest ones are kept). Change in .env if you like.
MAX_PER_STORE = int(os.getenv("MAX_PER_STORE", "10"))



def compare(results):
    """
    Show MANY options from each store, sorted from the lowest price to the highest.

    - rentals are removed
    - exact duplicates are removed
    - each store keeps its cheapest MAX_PER_STORE listings
    - "cheapest" is chosen from listings that are truly comparable (same condition + storage)
    """
    cleaned, seen = [], set()
    for original in results:
        item = dict(original)
        item.setdefault("condition", "new")
        item.setdefault("storage_gb", 0)
        if item["condition"] == "rental":                 # rentals are never purchase options
            continue
        dup_key = (item.get("store"), (item.get("title") or "").strip().lower(), item["price"])
        if dup_key in seen:
            continue
        seen.add(dup_key)
        cleaned.append(item)

    # keep the cheapest N listings of every store
    per_store, kept = {}, []
    for item in sorted(cleaned, key=lambda i: i["price"]):
        store = item.get("store", "Unknown store")
        if per_store.get(store, 0) >= MAX_PER_STORE:
            continue
        per_store[store] = per_store.get(store, 0) + 1
        kept.append(item)

    # ---- lowest price first, always -----------------------------------------
    ordered = sorted(kept, key=lambda i: i["price"])

    for item in ordered:
        item["extra_vs_cheapest"] = None
        item["is_cheapest_comparable"] = False

    # ---- group like with like ---------------------------------------------
    groups = {}
    for item in ordered:
        if item["condition"] == "unspecified" or item["storage_gb"] is None:
            continue
        groups.setdefault((item["condition"], item["storage_gb"]), []).append(item)

    for group in groups.values():
        low = min(i["price"] for i in group)
        for item in group:
            item["extra_vs_cheapest"] = round(item["price"] - low, 2)

    # headline group: prefer new items, then the group with the most stores, then the lowest price
    comparable = {
        k: g for k, g in groups.items() if len({i["store"] for i in g}) >= 2
    }
    if comparable:
        main_key = min(
            comparable,
            key=lambda k: (
                0 if k[0] == "new" else 1,
                -len({i["store"] for i in comparable[k]}),
                min(i["price"] for i in comparable[k]),
            ),
        )
        main = comparable[main_key]
        cheapest = min(main, key=lambda i: i["price"])
        cheapest["is_cheapest_comparable"] = True
        max_savings = round(max(i["price"] for i in main) - cheapest["price"], 2)
    else:
        cheapest, max_savings = None, 0

    return {
        "results": ordered,
        "cheapest": cheapest,
        "max_savings": max_savings,
    }


def cached_search(query):
    key = (PROVIDER_NAME, COUNTRY, query.lower())
    hit = CACHE.get(key)
    if hit and time.time() - hit["time"] < CACHE_SECONDS:
        return hit["data"]

    raw = PROVIDERS[PROVIDER_NAME](query, COUNTRY)
    data = compare(raw)
    CACHE[key] = {"time": time.time(), "data": data}
    return data


@app.route("/")
def home():
    currency = "INR" if COUNTRY == "in" else "USD"
    return render_template("index.html", demo=(PROVIDER_NAME == "demo"), currency=currency)


@app.route("/api/search")
def search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"error": "Please type at least 2 characters."}), 400
    if len(query) > 100:
        return jsonify({"error": "That search is too long."}), 400

    try:
        data = cached_search(query)
    except Exception as exc:  # network problem, bad key, quota used up...
        app.logger.error("Search failed: %s", exc)
        return jsonify({"error": "Could not fetch prices right now. Try again later."}), 502

    data["query"] = query
    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True)