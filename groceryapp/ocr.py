import re
from datetime import datetime

import pytesseract
from PIL import Image

CATEGORIES = [
    "Produce", "Meat & Seafood", "Dairy & Eggs", "Bakery", "Pantry",
    "Frozen", "Beverages", "Household", "Personal Care",
    "Snacks & Confectionery", "Other",
]

# Best-effort keyword categorisation — there's no real language understanding
# here, so anything not matched falls back to "Other" and should be fixed up
# by hand on the receipt detail screen.
_CATEGORY_KEYWORDS = {
    "Produce": ["apple", "banana", "potato", "onion", "tomato", "lettuce",
                "carrot", "avocado", "kumara", "capsicum", "broccoli",
                "spinach", "garlic", "grape", "orange", "lemon", "mushroom"],
    "Meat & Seafood": ["chicken", "beef", "lamb", "pork", "mince", "sausage",
                        "bacon", "fish", "salmon", "steak", "ham"],
    "Dairy & Eggs": ["milk", "cheese", "yoghurt", "yogurt", "butter",
                      "cream", "egg", "anchor", "mainland"],
    "Bakery": ["bread", "bun", "roll", "bagel", "vogel", "loaf", "muffin",
               "cake"],
    "Frozen": ["frozen", "ice cream", "icecream"],
    "Beverages": ["juice", "cola", "coke", "water", "soda", "beer", "wine",
                  "coffee", "tea", "l&p", "sprite", "fanta"],
    "Household": ["paper", "detergent", "cleaner", "foil", "toilet",
                  "tissue", "laundry", "dishwash"],
    "Personal Care": ["shampoo", "soap", "toothpaste", "deodorant",
                       "conditioner", "sunscreen"],
    "Snacks & Confectionery": ["chip", "chocolate", "candy", "lolly",
                                "biscuit", "cracker", "snack"],
}

# Footer/metadata lines that end in something price-shaped but aren't items.
_SKIP_LINE_KEYWORDS = [
    "subtotal", "total", "gst", "cash", "eftpos", "change", "balance",
    "visa", "mastercard", "card", "tender", "auth", "approved",
    "member", "loyalty", "points", "savings", "receipt", "thank you", "www.",
]

_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"), 4),
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2})\b"), 2),
]

_PRICE_RE = re.compile(r"\$?\s*(\d+\.\d{2})\s*$")


def _guess_category(item_name):
    lowered = item_name.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return category
    return "Other"


def _find_date(lines):
    for line in lines:
        for pattern, year_digits in _DATE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            day, month, year = match.groups()
            if year_digits == 2:
                year = "20" + year
            try:
                return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def _find_amount(lines, keyword, exclude=()):
    for line in lines:
        lowered = line.lower()
        if keyword in lowered and not any(x in lowered for x in exclude):
            match = _PRICE_RE.search(line)
            if match:
                return float(match.group(1))
    return None


def _parse_items(lines):
    items = []
    for line in lines:
        lowered = line.lower()
        if any(kw in lowered for kw in _SKIP_LINE_KEYWORDS):
            continue
        match = _PRICE_RE.search(line)
        if not match:
            continue
        price = float(match.group(1))
        name = line[: match.start()].strip(" .-*")
        if len(name) < 2:
            continue
        items.append({
            "name": name,
            "quantity": 1,
            "unit_price": price,
            "line_total": price,
            "category": _guess_category(name),
        })
    return items


def extract_receipt(image_path):
    """Run Tesseract OCR on a receipt photo and best-effort parse it into
    the same shape the upload/edit flow expects: store, date, items, totals.

    This is regex/keyword parsing over noisy OCR text, not a model that
    understands receipt layout — accuracy is well below an LLM-based
    approach, so every result should be checked and corrected on the
    receipt detail screen before being trusted.

    Returns (parsed_dict_or_None, raw_text).
    """
    raw_text = pytesseract.image_to_string(Image.open(image_path))
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    if not lines:
        return None, raw_text

    store_name = next(
        (line for line in lines[:5] if not re.search(r"\d{2}[/-]\d{2}", line)),
        lines[0],
    )

    parsed = {
        "store_name": store_name,
        "purchase_date": _find_date(lines),
        "currency": "NZD",
        "items": _parse_items(lines),
        "subtotal": _find_amount(lines, "subtotal"),
        "gst": _find_amount(lines, "gst"),
        "total": _find_amount(lines, "total", exclude=("subtotal",)),
    }
    return parsed, raw_text
