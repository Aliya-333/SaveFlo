"""
app.py - the backend (the "brain") of the price comparison site.

Flow:  browser -> /api/search?q=iphone -> providers fetch prices
       -> compare() finds the cheapest store -> JSON back to the browser
"""
import os
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

from providers import PROVIDERS  # noqa: E402  (must come after load_dotenv)

app = Flask(__name__)

# "serpapi" = real prices, "demo" = fake data (no key needed)
PROVIDER_NAME = os.getenv("PROVIDER", "demo")
COUNTRY = os.getenv("COUNTRY", "in")

# Tiny in-memory cache so repeated searches don't use up your API quota.
CACHE = {}
CACHE_SECONDS = 600  # 10 minutes


def compare(results):
    """Keep the cheapest listing per store, sort low -> high, mark the winner."""
    best_per_store = {}
    for item in results:
        store = item["store"]
        if store not in best_per_store or item["price"] < best_per_store[store]["price"]:
            best_per_store[store] = item

    ordered = sorted(best_per_store.values(), key=lambda r: r["price"])
    if not ordered:
        return {"results": [], "cheapest": None, "max_savings": 0}

    cheapest = ordered[0]
    for item in ordered:
        item["extra_vs_cheapest"] = round(item["price"] - cheapest["price"], 2)

    return {
        "results": ordered,
        "cheapest": cheapest,
        "max_savings": round(ordered[-1]["price"] - cheapest["price"], 2),
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
