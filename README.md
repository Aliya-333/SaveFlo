# SaveFlo - product price comparison

Type a product, see its price at different stores, and which one is cheapest.

## Run it
    python -m venv venv
    venv\Scripts\activate          (Windows)   |   source venv/bin/activate   (Mac/Linux)
    pip install -r requirements.txt
    copy .env.example .env         (Windows)   |   cp .env.example .env       (Mac/Linux)
    python app.py
Open http://127.0.0.1:5000

## Real prices
1. Make a free account at https://serpapi.com and copy your API key.
2. In `.env` set `PROVIDER=serpapi` and `SERPAPI_KEY=your_key`.
3. Restart `python app.py`.

## Files
- app.py              backend: /api/search, compare logic, cache
- providers.py        where prices come from (add new sources here)
- templates/index.html  the page users see
