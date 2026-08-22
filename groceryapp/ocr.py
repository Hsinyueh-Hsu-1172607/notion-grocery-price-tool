"""Read a receipt photo into structured fields.

Two OCR engines, in preference order:

1. macOS Vision (the engine behind Live Text). Free, local, and far more
   accurate on real phone photos of curved thermal receipts — in testing it
   read every price at full confidence where Tesseract read none.
2. Tesseract, as a fallback so the project still runs off a Mac.

Both engines return *positioned* text, and parsing works off that geometry:
text runs are grouped into visual rows by their vertical position, and within
a row the rightmost price-shaped run is the amount while what sits to its
left is the item. That is much steadier than pattern-matching a flat string,
because it uses where things actually sit on the paper.
"""
import re
from datetime import datetime

CATEGORIES = [
    "Produce", "Meat & Seafood", "Dairy & Eggs", "Bakery", "Pantry",
    "Frozen", "Beverages", "Household", "Personal Care",
    "Snacks & Confectionery", "Other",
]

# Best-effort keyword categorisation — there's no real language understanding
# here, so anything not matched falls back to "Other" and should be fixed up
# afterwards.
_CATEGORY_KEYWORDS = {
    "Produce": ["apple", "banana", "potato", "onion", "tomato", "lettuce",
                "carrot", "avocado", "kumara", "capsicum", "broccoli",
                "broccoii", "spinach", "garlic", "grape", "orange", "lemon",
                "mushroom", "kiwifruit", "pear", "berry", "melon", "cucumber"],
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

# Rows that end in something price-shaped but are totals/payment lines,
# not purchases.
_SKIP_ROW_KEYWORDS = [
    "subtotal", "total", "gst", "cash", "eftpos", "change", "balance",
    "visa", "mastercard", "card", "tender", "auth", "approved", "account",
    "member", "loyalty", "points", "savings", "receipt", "thank you", "www.",
]

# OCR sometimes splits a number across a space ("$8. 99"), so allow spaces
# around the decimal separator and strip them out when converting.
_NUMBER = r"\d+(?:\s*[.,]\s*\d+)?"

_PRICE_RE = re.compile(rf"^\$?\s*(\d+\s*[.,]\s*\d{{2}})$")
_TRAILING_PRICE_RE = re.compile(rf"\$?\s*(\d+\s*[.,]\s*\d{{2}})\s*$")

# e.g. "0.840 kg @ $8.99/kg" or "2 @ $1.50"
_QTY_LINE_RE = re.compile(
    rf"^({_NUMBER})\s*(?:kg|kgs|g|ea|each)?\s*@\s*\$?({_NUMBER})", re.IGNORECASE
)


_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"), 4),
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2})\b"), 2),
]


def _to_float(text):
    """Parse a possibly OCR-mangled number like '8. 99' or '1,55'."""
    return float(re.sub(r"\s+", "", text).replace(",", "."))


# --------------------------------------------------------------------------
# OCR engines. Each returns a list of (text, x_left, y_centre, height) with
# coordinates normalised to 0..1 and y measured from the top of the image.
# --------------------------------------------------------------------------

def _read_blocks_vision(image_path):
    """macOS Vision. Returns None if unavailable (not a Mac, or pyobjc missing)."""
    try:
        import Quartz
        import Vision
        from Foundation import NSURL
    except ImportError:
        return None

    url = NSURL.fileURLWithPath_(image_path)
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if source is None:
        return None
    cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if cg_image is None:
        return None

    # CGImageSource hands back the raw pixels, ignoring the EXIF orientation a
    # phone camera writes. Left uncorrected, Vision reads a sideways image and
    # its coordinates come back with the axes effectively swapped.
    properties = Quartz.CGImageSourceCopyPropertiesAtIndex(source, 0, None) or {}
    orientation = properties.get(Quartz.kCGImagePropertyOrientation, 1)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_orientation_options_(
        cg_image, orientation, None
    )
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    success, _ = handler.performRequests_error_([request], None)
    if not success:
        return None

    blocks = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        text = candidates[0].string().strip()
        if not text:
            continue
        box = observation.boundingBox()
        # Vision's origin is bottom-left; flip to top-down.
        height = box.size.height
        y_centre = 1.0 - (box.origin.y + height / 2)
        blocks.append((text, box.origin.x, y_centre, height))
    return blocks


def _read_blocks_tesseract(image_path):
    """Tesseract fallback, using word boxes so we keep the same geometry."""
    import pytesseract
    from PIL import Image, ImageOps
    from pytesseract import Output

    image = ImageOps.exif_transpose(Image.open(image_path))
    width, height = image.size
    data = pytesseract.image_to_data(image, output_type=Output.DICT)

    # Merge words back into their detected lines, keeping each line's box.
    lines = {}
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        left, top = data["left"][i], data["top"][i]
        w, h = data["width"][i], data["height"][i]
        if key not in lines:
            lines[key] = {"words": [], "left": left, "top": top,
                          "right": left + w, "bottom": top + h}
        entry = lines[key]
        entry["words"].append((left, text))
        entry["left"] = min(entry["left"], left)
        entry["top"] = min(entry["top"], top)
        entry["right"] = max(entry["right"], left + w)
        entry["bottom"] = max(entry["bottom"], top + h)

    blocks = []
    for entry in lines.values():
        text = " ".join(t for _, t in sorted(entry["words"]))
        box_height = (entry["bottom"] - entry["top"]) / height
        y_centre = ((entry["top"] + entry["bottom"]) / 2) / height
        blocks.append((text, entry["left"] / width, y_centre, box_height))
    return blocks


def _read_blocks(image_path):
    blocks = _read_blocks_vision(image_path)
    if blocks:
        return blocks, "vision"
    return _read_blocks_tesseract(image_path), "tesseract"


# --------------------------------------------------------------------------
# Layout: group positioned text into visual rows.
# --------------------------------------------------------------------------

def _group_rows(blocks):
    """Cluster text blocks into rows by vertical position, each row's runs
    ordered left to right."""
    if not blocks:
        return []

    heights = sorted(b[3] for b in blocks)
    median_height = heights[len(heights) // 2] or 0.01
    tolerance = median_height * 0.7

    rows = []
    for text, x, y, h in sorted(blocks, key=lambda b: b[2]):
        if rows and abs(y - rows[-1]["y"]) <= tolerance:
            row = rows[-1]
            row["runs"].append((x, text))
            # Running mean keeps the row anchor stable as runs are added.
            row["y"] = (row["y"] * (len(row["runs"]) - 1) + y) / len(row["runs"])
        else:
            rows.append({"y": y, "runs": [(x, text)]})

    for row in rows:
        row["runs"].sort()
    return rows


def _row_parts(row):
    """Split a row into (left_text, price_or_None)."""
    runs = [text for _, text in row["runs"]]

    # A price sitting in its own run on the right (typical of Vision output).
    match = _PRICE_RE.match(runs[-1]) if len(runs) > 1 else None
    if match:
        return " ".join(runs[:-1]).strip(), _to_float(match.group(1))

    # Otherwise the whole row may be one run ending in a price.
    joined = " ".join(runs)
    match = _TRAILING_PRICE_RE.search(joined)
    if match:
        return joined[: match.start()].strip(" .-*:"), _to_float(match.group(1))
    return joined.strip(), None


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _guess_category(item_name):
    lowered = item_name.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return category
    return "Other"


def _is_skippable(text):
    lowered = text.lower()
    return any(kw in lowered for kw in _SKIP_ROW_KEYWORDS)


def _find_date(rows):
    for row in rows:
        text = " ".join(t for _, t in row["runs"])
        for pattern, year_digits in _DATE_PATTERNS:
            match = pattern.search(text)
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


def _find_amount(rows, keyword, exclude=()):
    for row in rows:
        text = " ".join(t for _, t in row["runs"])
        lowered = text.lower()
        if keyword in lowered and not any(x in lowered for x in exclude):
            _, price = _row_parts(row)
            if price is not None:
                return price
    return None


def _find_store_name(rows):
    """Store name sits at the top on most receipts, but at the bottom on some
    (NZ produce shops in particular), so check the top first and fall back."""
    def usable(text):
        if len(text) < 3 or _is_skippable(text):
            return False
        # Must read as a plain name: starts with a letter, and holds only
        # letters and light punctuation from there. This rejects section
        # rules ("----FOOD----" survives OCR as "-FOOD") as well as
        # addresses and phone numbers ("Lincoln 7608").
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z&'. ]*", text))

    for candidate_rows in (rows[:2], rows[-5:]):
        for row in candidate_rows:
            text, price = _row_parts(row)
            if price is None and usable(text):
                return text
    return None


def _parse_items(rows):
    items = []
    pending_name = None  # An item name whose price is on the following row.

    for row in rows:
        text, price = _row_parts(row)

        if price is None:
            # A bare line with no amount — most likely an item name whose
            # weight and price land on the next row.
            if text and not _is_skippable(text):
                pending_name = text
            continue

        if _is_skippable(text):
            pending_name = None
            continue

        quantity = 1
        unit_price = price
        name = text

        qty_match = _QTY_LINE_RE.match(text)
        if qty_match:
            # This row is "0.840 kg @ $8.99/kg   $7.55" — the real name was
            # on the row above it.
            try:
                quantity = _to_float(qty_match.group(1))
                unit_price = _to_float(qty_match.group(2))
            except ValueError:
                quantity, unit_price = 1, price
            name = pending_name or text

        if not name or len(name) < 2:
            pending_name = None
            continue

        items.append({
            "name": name,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": price,
            "category": _guess_category(name),
        })
        pending_name = None

    return items


def extract_receipt(image_path):
    """Read a receipt photo into (parsed_dict_or_None, raw_text).

    Accuracy depends on the engine that was available — see the module
    docstring. Results should always be reviewable and correctable by hand.
    """
    blocks, engine = _read_blocks(image_path)
    rows = _group_rows(blocks)
    raw_text = "\n".join(" ".join(t for _, t in row["runs"]) for row in rows)

    if not rows:
        return None, raw_text

    parsed = {
        "store_name": _find_store_name(rows),
        "purchase_date": _find_date(rows),
        "currency": "NZD",
        "items": _parse_items(rows),
        "subtotal": _find_amount(rows, "subtotal"),
        "gst": _find_amount(rows, "gst"),
        "total": _find_amount(rows, "total", exclude=("subtotal",)),
        "ocr_engine": engine,
    }
    return parsed, raw_text
