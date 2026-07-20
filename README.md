# Grocery Price Tool (Notion-backed)

A small Flask app that scans a NZ supermarket receipt and logs every item's
price straight into a Notion database, so you can later look up which store
had the cheapest price for something you buy regularly.

Take a photo on your phone (or upload one from your computer). Tesseract OCR
reads the text off the receipt locally, a set of parsing rules pulls out the
store, date, line items and prices, and each item becomes a new row in a
Notion database via the [Notion API](https://developers.notion.com/). Notion
itself is the only datastore and the only place you review/correct results —
there's no separate web dashboard to maintain.

## Why build this this way?

This is a companion piece to a separate [receipt-tracker](../receipt-tracker)
project, which does the same receipt-scanning idea with its own PostgreSQL
database and custom dashboard pages. This project asks a different question:
what if Notion itself — its filtering, sorting, and grouping — *is* the
dashboard? Every scanned item becomes a page in a Notion database, and
comparing "who's cheapest for milk" is just filtering/sorting that database,
either through this app's `/compare` route or directly in Notion. It also
means there's no in-app edit screen: if OCR misreads something, you fix it
straight in Notion, since that's already a very good spreadsheet-like editor.

The OCR/parsing step (`groceryapp/ocr.py`) is copied from the receipt-tracker
project unchanged — regex over Tesseract's raw text output, plus a keyword
dictionary to guess a spending category. It's free (no API key, runs
locally) but meaningfully less accurate than handing the photo to a
vision-capable LLM, so expect to correct results in Notion from time to time.

## Features

- **Scan a receipt** — on mobile, the file input opens the camera directly;
  on desktop it opens a file picker.
- **Automatic logging** — each line item is written to Notion as its own
  row: item, store, category, quantity, unit price, line total, date.
- **Price comparison** — search an item name and see every store you've
  bought it from, cheapest first.

## Getting this running

1. **Install Tesseract** (the OCR engine — `pytesseract` is just a Python
   wrapper around it)

   ```bash
   brew install tesseract
   ```

2. **Create a virtual environment and install dependencies**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Set up a Notion integration**

   - Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
     and create a new integration. Copy its "Internal Integration Secret".
   - In your Notion workspace, create (or pick) a page, then use its
     **Share** menu to invite your new integration — Notion won't let the
     API touch anything you haven't explicitly shared.
   - Copy that page's ID (the 32-character string in its URL).

4. **Set up your environment variables**

   ```bash
   cp .env.example .env
   ```

   Fill in `NOTION_API_KEY` (the integration secret) and
   `NOTION_PARENT_PAGE_ID` (the page you shared in step 3). Leave
   `NOTION_DATABASE_ID` blank for now — the next step fills it in.
   Also set `FLASK_SECRET_KEY` to any random string.

5. **Create the Notion database**

   ```bash
   python setup_notion.py
   ```

   This creates a "Grocery Prices" database under your parent page and
   prints its ID. Paste that into `.env` as `NOTION_DATABASE_ID`.

6. **Run it**

   ```bash
   python run.py
   ```

   Visit `http://127.0.0.1:5000`. To scan from your phone, both devices need
   to be on the same network — visit your computer's LAN IP instead of
   `127.0.0.1` (or run `app.run(host="0.0.0.0")` in `run.py`).

## Project structure

```
groceryapp/
├── __init__.py     # creates the Flask app, loads .env
├── ocr.py           # Tesseract OCR + regex/keyword parsing (shared with receipt-tracker)
├── notion_sync.py    # writes scanned items to Notion, queries them back for comparison
├── routes.py          # / , /upload , /compare
├── templates/
└── static/
setup_notion.py     # one-off: creates the Notion database, prints its ID
```

## Not in this version

- No in-app editing of a scanned receipt — corrections happen directly in
  Notion.
- No receipt photos are kept — the uploaded image is OCR'd from a temp file
  and deleted immediately after.
- No LLM-based extraction — see `receipt-tracker` for that trade-off explored
  the other way (Claude vision API instead of Tesseract, at a small
  per-request cost).
