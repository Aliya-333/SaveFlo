"""
providers.py - where prices come from, and which listings are allowed through.

Works with your app.py:  PROVIDERS[name](query, country)  must return a LIST of listings.
Each listing is a dict with: store, title, price, link, image, condition, storage_gb,
retailer_priority  (+ url_status / url_verified / url_warnings for the fake-link check).

What happens on every search:
    1. fetch listings from SerpAPI (Google Shopping)
    2. work out which store each listing really belongs to
    3. FAKE URL DETECTION on every link  -> suspicious links are dropped
    4. keep only trusted stores (Amazon, Flipkart, Meesho, Myntra, Nykaa, ...)
    5. drop accessories / unrelated items
    6. every kept listing gets retailer_priority = 0, so your app.py sorts purely by PRICE
       (lowest first)
"""
import ipaddress
import os
import re
from urllib.parse import quote_plus, urlparse

import requests

# --------------------------------------------------------------------------
# 1. Trusted stores. Add or remove stores here - nothing else needs changing.
#    domains = the ONLY web addresses that count as this store's real website.
#    aliases = names Google Shopping may show in its "source" field.
# --------------------------------------------------------------------------
TRUSTED_RETAILERS = {
    "Amazon":           {"domains": ["amazon.in", "amazon.com"],                          "aliases": ["amazon"]},
    "Flipkart":         {"domains": ["flipkart.com"],                                     "aliases": ["flipkart"]},
    "Meesho":           {"domains": ["meesho.com"],                                       "aliases": ["meesho"]},
    "Myntra":           {"domains": ["myntra.com"],                                       "aliases": ["myntra"]},
    "Nykaa":            {"domains": ["nykaa.com", "nykaafashion.com", "nykaaman.com"],    "aliases": ["nykaa"]},
    "Ajio":             {"domains": ["ajio.com"],                                         "aliases": ["ajio"]},
    "Tata CLiQ":        {"domains": ["tatacliq.com"],                                     "aliases": ["tata cliq", "tatacliq"]},
    "Croma":            {"domains": ["croma.com"],                                        "aliases": ["croma"]},
    "Reliance Digital": {"domains": ["reliancedigital.in"],                               "aliases": ["reliance digital"]},
    "JioMart":          {"domains": ["jiomart.com"],                                      "aliases": ["jiomart"]},
    "Snapdeal":         {"domains": ["snapdeal.com"],                                     "aliases": ["snapdeal"]},
    "Vijay Sales":      {"domains": ["vijaysales.com"],                                   "aliases": ["vijay sales"]},
    "Shoppers Stop":    {"domains": ["shoppersstop.com"],                                 "aliases": ["shoppers stop"]},
    "Purplle":          {"domains": ["purplle.com"],                                      "aliases": ["purplle"]},
    "FirstCry":         {"domains": ["firstcry.com"],                                     "aliases": ["firstcry"]},
    "Lenskart":         {"domains": ["lenskart.com"],                                     "aliases": ["lenskart"]},
    "Decathlon":        {"domains": ["decathlon.in"],                                     "aliases": ["decathlon"]},
    "Pepperfry":        {"domains": ["pepperfry.com"],                                    "aliases": ["pepperfry"]},
    "Apple":            {"domains": ["apple.com"],                                        "aliases": ["apple"]},
    "Samsung":          {"domains": ["samsung.com"],                                      "aliases": ["samsung"]},
}

# --------------------------------------------------------------------------
# 2. Optional settings (put in .env if you want to change them)
# --------------------------------------------------------------------------
def _flag(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")

ALLOW_OTHER_STORES = _flag("ALLOW_OTHER_STORES", "false")   # true = also show stores not listed above
ALLOW_SECOND_QUERY = _flag("ALLOW_SECOND_QUERY", "true")    # 1 extra search if too few trusted stores (costs 1 credit)
MIN_TRUSTED_STORES = int(os.getenv("MIN_TRUSTED_STORES", "3"))
SERPAPI_PAGES = max(1, int(os.getenv("SERPAPI_PAGES", "2")))          # pages of results per search (1 credit each)
SEARCH_EACH_STORE = _flag("SEARCH_EACH_STORE", "false")                # true = one extra search per store below (more options, more credits)
EACH_STORE_LIST = [s.strip() for s in os.getenv("EACH_STORE_LIST", "Amazon,Flipkart,Meesho,Myntra,Nykaa").split(",") if s.strip()]

# --------------------------------------------------------------------------
# 3. Fake URL detection
# --------------------------------------------------------------------------
GOOGLE_HOSTS = ("google.com", "google.co.in", "googleadservices.com", "googleusercontent.com")
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "cutt.ly", "rb.gy", "is.gd", "shorturl.at", "ow.ly", "tiny.cc"}
RISKY_TLDS = {"xyz", "top", "click", "buzz", "tk", "ml", "ga", "cf", "gq", "icu", "cyou", "rest",
              "monster", "sbs", "cfd", "quest", "fit", "live", "vip", "work", "loan"}
LEET = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s"})

BRAND_WORDS = sorted({d.split(".")[0] for r in TRUSTED_RETAILERS.values() for d in r["domains"]
                      if len(d.split(".")[0]) >= 5})
SUBSTRING_SKIP = {"apple"}   # common word ("pineapple") - only checked for misspellings


def host_matches(host, domain):
    return host == domain or host.endswith("." + domain)


def retailer_from_host(host):
    for name, info in TRUSTED_RETAILERS.items():
        if any(host_matches(host, d) for d in info["domains"]):
            return name
    return None


def retailer_from_name(source):
    text = (source or "").lower()
    for name, info in TRUSTED_RETAILERS.items():
        for alias in info["aliases"]:
            if re.search(r"\b" + re.escape(alias) + r"\b", text):
                return name
    return None


def edit_distance(a, b):
    """How many single-letter changes turn a into b (gives up early if far apart)."""
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def check_url(url, claimed_store=None):
    """
    Decide whether a link is safe to show.
    Returns {"status": ..., "verified": bool, "reasons": [...]}
      verified   - on the store's official website, over https
      unverified - goes through Google Shopping (final site can't be seen yet)
      unknown    - clean link, but the site is not in our trusted list
      suspicious - fake / look-alike warning signs  (these get DROPPED)
      missing    - no usable link
    """
    if not url:
        return {"status": "missing", "verified": False, "reasons": ["No link provided"]}
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return {"status": "missing", "verified": False, "reasons": ["Not a valid web address"]}

    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not host:
        return {"status": "missing", "verified": False, "reasons": ["Not a valid web address"]}

    reasons = []
    is_google = any(host_matches(host, g) for g in GOOGLE_HOSTS)
    official = retailer_from_host(host)

    if parsed.scheme != "https":
        reasons.append("Link is not secure (http instead of https)")
    if parsed.username or parsed.password:
        reasons.append("Link hides its real destination behind an '@' sign")
    if is_ip(host):
        reasons.append("Link uses a raw IP address instead of a store name")
    if "xn--" in host:
        reasons.append("Domain uses look-alike (punycode) characters")
    if host in SHORTENERS:
        reasons.append("Link is a shortened URL that hides the real site")

    if official is None and not is_google:
        tld = host.rsplit(".", 1)[-1]
        if tld in RISKY_TLDS:
            reasons.append(f"Domain ends in .{tld}, often used by scam sites")
        if host.count("-") >= 3:
            reasons.append("Domain has many hyphens, a common scam pattern")

        compact = host.replace(".", "").replace("-", "")
        compact_norm = compact.translate(LEET)
        for brand in BRAND_WORDS:
            if brand not in SUBSTRING_SKIP and (brand in compact or brand in compact_norm):
                reasons.append(f"Domain imitates '{brand}' but is not its official website")
                break
        else:
            labels = [l for part in host.split(".") for l in part.split("-") if len(l) >= 5]
            done = False
            for label in labels:
                norm = label.translate(LEET)
                for brand in BRAND_WORDS:
                    if label != brand and edit_distance(norm, brand) <= 1:
                        reasons.append(f"Domain looks like a misspelling of '{brand}'")
                        done = True
                        break
                if done:
                    break

    if claimed_store:
        if official and official != claimed_store:
            reasons.append(f"Listing says {claimed_store} but the link goes to {official}")
        elif official is None and not is_google:
            reasons.append(f"Listing says {claimed_store} but the link goes to an unrelated site ({host})")

    if reasons:
        return {"status": "suspicious", "verified": False, "reasons": reasons}
    if official:
        return {"status": "verified", "verified": True, "reasons": []}
    if is_google:
        return {"status": "unverified", "verified": False,
                "reasons": ["Link opens through Google Shopping; the store site shows after you click"]}
    return {"status": "unknown", "verified": False, "reasons": [f"{host} is not in the trusted store list"]}


# --------------------------------------------------------------------------
# 4. Reading details out of a listing
# --------------------------------------------------------------------------
def parse_price(value):
    """'₹1,299.00' or 'Rs. 1,299' -> 1299.0 (or None)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    return float(match.group().replace(",", "")) if match else None


def detect_condition(title):
    t = (title or "").lower()
    if re.search(r"\b(rent|rental|on rent)\b", t):
        return "rental"
    if re.search(r"\b(refurbished|renewed|pre-owned|preowned|used|second hand|open box)\b", t):
        return "refurbished"
    return "new"


def detect_storage_gb(title):
    """Biggest 'NN GB/TB' number in the title ('8GB RAM 256GB' -> 256). 0 if there is none.
    0 means 'not applicable' (shirts, makeup, ...). Your app.py needs a number here to
    compare listings fairly, so 0 is used instead of None."""
    values = []
    for number, unit in re.findall(r"\b(\d{2,4})\s?(gb|tb)\b", title or "", flags=re.I):
        values.append(int(number) * (1024 if unit.lower() == "tb" else 1))
    return max(values) if values else 0


ACCESSORY_WORDS = {"case", "cover", "charger", "protector", "tempered", "cable", "adapter", "sticker"}


def _tokens(text):
    text = re.sub(r"(\d)\s+(gb|tb|ml|mah|kg|g)\b", r"\1\2", (text or "").lower())
    return set(re.findall(r"[a-z0-9]+", text))


def is_relevant(query, title):
    """Title must contain most of the search words, and must not be an accessory
    unless the search itself asked for one."""
    q, t = _tokens(query), _tokens(title)
    if not q:
        return True
    if len(q & t) / len(q) < 0.6:
        return False
    if (t & ACCESSORY_WORDS) and not (q & ACCESSORY_WORDS):
        return False
    return True


# --------------------------------------------------------------------------
# 5. Raw listings -> checked listings (the list your app.py receives)
# --------------------------------------------------------------------------
def classify(raw_listings, query=""):
    kept, dropped_fake, dropped_other = [], 0, 0

    for raw in raw_listings:
        price = raw.get("price")
        if price is None or price <= 0:
            continue

        title = raw.get("title", "")
        link = raw.get("link", "")
        source = raw.get("source", "") or "Unknown store"
        claimed = retailer_from_name(source)
        check = check_url(link, claimed)

        if check["status"] in ("suspicious", "missing"):
            dropped_fake += 1
            print(f"BLOCKED link | {source} | {price} | {check['reasons'][0]}")
            continue

        official = retailer_from_host(urlparse(link).hostname or "")
        if claimed is None and official is None and not ALLOW_OTHER_STORES:
            dropped_other += 1
            continue

        kept.append({
            "store": claimed or official or source,
            "title": title,
            "price": float(price),
            "link": link,
            "image": raw.get("image", ""),
            "condition": detect_condition(title),
            "storage_gb": detect_storage_gb(title),
            "retailer_priority": 0,          # same for all -> app.py sorts by price only
            "url_status": check["status"],
            "url_verified": check["verified"],
            "url_warnings": check["reasons"],
        })

    # drop accessories / unrelated items, but never leave the page empty because of it
    relevant = [i for i in kept if is_relevant(query, i["title"])]
    final = relevant if len(relevant) >= 2 else kept

    print(f"SEARCH '{query}': kept {len(final)}, blocked {dropped_fake} fake/unsafe links, "
          f"skipped {dropped_other} other stores")
    return final


# --------------------------------------------------------------------------
# 6. Providers
# --------------------------------------------------------------------------
def _fetch_serpapi(query, key, country, start=0):
    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": key,
        "gl": country,
        "hl": "en",
        "google_domain": "google.co.in" if country == "in" else "google.com",
    }
    if start:
        params["start"] = start                  # result offset = next page
    response = requests.get("https://serpapi.com/search.json", params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):                       # e.g. "out of searches", "no results"
        print("SERPAPI SAYS:", data["error"])
    results = []
    for item in data.get("shopping_results", []):
        results.append({
            "title": item.get("title", ""),
            "source": item.get("source", ""),
            "price": item.get("extracted_price") or parse_price(item.get("price")),
            "link": item.get("link") or item.get("product_link") or "",
            "image": item.get("thumbnail", ""),
        })
    return results


def _merge(raw, more):
    seen = {(r["title"], r["source"], r["price"]) for r in raw}
    return raw + [r for r in more if (r["title"], r["source"], r["price"]) not in seen]


def search_serpapi(query, country="in"):
    """Real prices from Google Shopping (SerpAPI): many listings per store, trusted stores only."""
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise RuntimeError("SERPAPI_KEY is missing. Add it to your .env file.")

    # 1) first page, then more pages (each page = 1 SerpAPI credit)
    raw, start = [], 0
    for _ in range(SERPAPI_PAGES):
        batch = _fetch_serpapi(query, key, country, start)
        if not batch:
            break
        raw = _merge(raw, batch)
        start += len(batch)

    # 2) optionally search each big store by name to get more of ITS listings
    if SEARCH_EACH_STORE:
        for store in EACH_STORE_LIST:
            raw = _merge(raw, _fetch_serpapi(f"{query} {store}", key, country))
        return classify(raw, query)

    items = classify(raw, query)

    # 3) too few trusted stores? search once more, naming the stores
    if ALLOW_SECOND_QUERY and len({i["store"] for i in items}) < MIN_TRUSTED_STORES:
        raw = _merge(raw, _fetch_serpapi(f"{query} amazon flipkart myntra meesho nykaa", key, country))
        items = classify(raw, query)

    return items


def search_demo(query, country="in"):
    """Sample data (including fake-link listings) so you can test without an API key."""
    q = quote_plus(query)
    base = 1000 + (sum(ord(c) for c in query) % 40) * 100
    title = query.title()
    raw = [
        {"title": f"{title} 128GB", "source": "Amazon.in", "price": base * 1.00, "link": f"https://www.amazon.in/s?k={q}"},
        {"title": f"{title} 128GB", "source": "Flipkart",  "price": base * 0.96, "link": f"https://www.flipkart.com/search?q={q}"},
        {"title": f"{title} 128GB", "source": "Myntra",    "price": base * 1.05, "link": f"https://www.myntra.com/{q}"},
        {"title": f"{title} 128GB", "source": "Meesho",    "price": base * 0.93, "link": f"https://www.meesho.com/search?q={q}"},
        {"title": f"{title} 128GB", "source": "Nykaa",     "price": base * 1.08, "link": f"https://www.nykaa.com/search/result/?q={q}"},
        # fake listings - these must be blocked:
        {"title": f"{title} 128GB", "source": "Amazon",    "price": base * 0.35, "link": f"https://amaz0n-offers.xyz/deal?item={q}"},
        {"title": f"{title} 128GB", "source": "Flipkart",  "price": base * 0.40, "link": f"https://flipkart.com.mega-sale.shop/p/{q}"},
        {"title": f"{title} 128GB", "source": "Myntra",    "price": base * 0.50, "link": "http://myntraa.com/offer"},
    ]
    return classify(raw, query)


PROVIDERS = {
    "serpapi": search_serpapi,
    "demo": search_demo,
}

