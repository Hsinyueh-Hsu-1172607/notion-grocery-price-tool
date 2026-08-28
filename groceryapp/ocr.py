"""Read a receipt photo into structured fields.

Three OCR engines, in preference order:

1. macOS Vision (the engine behind Live Text). Free, local, and very accurate
   on real phone photos of curved thermal receipts — in testing it read every
   price at full confidence where Tesseract read none. macOS only.
2. Google Cloud Vision, when GOOGLE_VISION_API_KEY is set. This is what runs
   when the app is hosted on Linux, where Apple's framework is unavailable.
3. Tesseract, as a last resort. It needs no key and no network, but on real
   receipts it misses most of the prices, so results need heavy correction.

All three return *positioned* text, and parsing works off that geometry:
text runs are grouped into visual rows by their vertical position, and within
a row the rightmost price-shaped run is the amount while what sits to its
left is the item. That is much steadier than pattern-matching a flat string,
because it uses where things actually sit on the paper.
"""
import base64
import io
import math
import os
import re
from datetime import datetime

CATEGORIES = [
    "Fruit", "Vegetables", "Meat & Seafood", "Dairy & Eggs", "Bakery",
    "Pantry", "Frozen", "Beverages", "Household", "Personal Care",
    "Snacks & Confectionery", "Other",
]

# Best-effort keyword categorisation — there's no real language understanding
# here, so anything not matched falls back to "Other" and should be fixed up
# afterwards.
#
# Order matters: the first category with a matching keyword wins, so keep
# narrower produce terms ahead of anything they might also appear in.
_CATEGORY_KEYWORDS = {
    # Avocado sits under vegetables: botanically a fruit, but it's bought and
    # eaten as a vegetable, and these categories are for how you shop.
    "Fruit": ["apple", "banana", "grape", "orange", "lemon",
              "lime", "mandarin", "kiwifruit", "pear", "peach", "nectarine",
              "plum", "berry", "berries", "melon", "pineapple", "mango",
              "cherry", "apricot", "feijoa", "tamarillo"],
    "Vegetables": ["avocado", "potato", "onion", "tomato", "lettuce",
                   "carrot", "kumara",
                   "capsicum", "broccoli", "broccoii", "spinach", "garlic",
                   "mushroom", "cucumber", "cabbage", "cauliflower",
                   "courgette", "zucchini", "pumpkin", "celery", "leek",
                   "beans", "peas", "corn", "silverbeet", "bok choy",
                   "ginger", "chilli", "radish", "raddish", "beetroot",
                   "asparagus", "kale", "parsnip", "turnip", "shallot",
                   "okra", "okr", "eggplant", "aubergine", "sprout"],
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
    # Everything shelf-stable: staples like rice and pasta, and dried fruit,
    # which sits with the ambient goods rather than the fresh fruit.
    "Pantry": ["rice", "pasta", "psta", "spaghetti", "noodle", "macaroni",
               "penne", "fettuccine", "lasagne", "lasagna", "vermicelli",
               "couscous", "quinoa", "risotto", "udon", "ramen", "soba",
               "sultana", "raisin", "prune", "flour", "sugar", "oil",
               "vinegar", "sauce", "stock", "tin", "canned", "honey", "jam",
               "peanut butter", "cereal", "oats", "lentil", "chickpea"],
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

# Receipts tag lines with trailing markers — Pak'nSave and New World print a
# "*" beside GST-applicable items — so a price is not always the last thing on
# the line.
_MARKER = r"[\s*]*"

_PRICE_RE = re.compile(rf"^\$?\s*(\d+\s*[.,]\s*\d{{2}}){_MARKER}$")
_TRAILING_PRICE_RE = re.compile(rf"\$?\s*(\d+\s*[.,]\s*\d{{2}}){_MARKER}$")

# The "how many at what price" part of a line, e.g. "0.840 kg @ $8.99/kg" or
# "2 @ $1.50". It appears on its own indented line at some shops and inline
# after the item name at others, so this is searched for rather than anchored.
# The unit may sit before the "@" (Fruitland) or after the price (Pak'nSave's
# "1 @ $6.99 EA"), so both spots are captured and whichever turns up is used.
_UNIT = r"(kgs?|g|ea(?:ch)?)"
# The space after "\$" matters: Google Vision returns words separately, so a
# price arrives as "$ 7.34" rather than "$7.34".
_QTY_RE = re.compile(
    rf"({_NUMBER})\s*{_UNIT}?\s*@\s*\$?\s*({_NUMBER})\s*/?\s*{_UNIT}?",
    re.IGNORECASE,
)

_UNIT_NAMES = {"kg": "kg", "kgs": "kg", "g": "g", "ea": "ea", "each": "ea"}

# The "@" is small and often the first thing OCR loses — it comes back as "G",
# or "1 @" merges into "10", or it vanishes entirely. The quantity can't be
# recovered then, but the price tail is still clearly not part of the name.
#
# Only the "$…" onwards is removed. Reaching further back to catch the
# stranded quantity would also eat pack sizes like "PAMS SULTANAS 700G", and
# losing that is worse than leaving a stray digit: the size is what makes a
# unit price comparable later.
_PRICE_TAIL_RE = re.compile(
    rf"\s*\$\s*{_NUMBER}\s*/?\s*{_UNIT}?\s*=?\s*$",
    re.IGNORECASE,
)

# A run that is only a line marker, such as Pak'nSave's GST asterisk.
_MARKER_ONLY_RE = re.compile(r"^[\s*\-–—=]+$")


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# NZ receipts date themselves in several ways: 12/07/2026, 19-Jul-2026,
# 12Jul26, 2026-07-07. Ordered most to least specific.
#
# Separators are matched loosely because Google Vision returns each token
# separately, so "19-Jul-2026" reaches us spaced out as "19 - Jul - 2026".
# Only spaces and the separator itself are allowed through, so a run of
# digits and words can't be stitched into a date that was never printed.
_ISO_DATE_RE = re.compile(r"\b(\d{4})\s*-\s*(\d{2})\s*-\s*(\d{2})\b")
_NAMED_MONTH_RE = re.compile(
    r"\b(\d{1,2})[-\s]*([A-Za-z]{3})[A-Za-z]*[-\s]*(\d{4}|\d{2})\b"
)
_NUMERIC_DATE_RE = re.compile(
    r"\b(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{4}|\d{2})\b"
)


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

        # Vision reports the text's actual quadrilateral, so its top edge
        # gives the baseline tilt of this run. Negated because y is flipped.
        top_left = observation.topLeft()
        top_right = observation.topRight()
        angle = -math.atan2(top_right.y - top_left.y, top_right.x - top_left.x)

        blocks.append((text, box.origin.x, y_centre, height, angle))
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
        # Tesseract reports upright boxes only, so it gives us no tilt to
        # work with — treat every run as level.
        blocks.append((text, entry["left"] / width, y_centre, box_height, 0.0))
    return blocks


def _read_blocks_google(image_path):
    """Google Cloud Vision. Returns None unless GOOGLE_VISION_API_KEY is set.

    This is what runs when the app is hosted rather than on the author's Mac:
    Apple's Vision framework is macOS-only, and Tesseract reads real receipt
    photos too poorly to be worth deploying. Like the other engines it hands
    back positioned text, so the layout parsing below is unchanged.
    """
    api_key = os.environ.get("GOOGLE_VISION_API_KEY")
    if not api_key:
        return None

    import httpx
    from PIL import Image, ImageOps

    # Google reads the stored pixels and ignores the EXIF orientation a phone
    # camera writes, so a rotated photo comes back with its coordinates on a
    # sideways page — words in a line share an x instead of a y, and the row
    # grouping below falls apart. Rotate it upright before sending, rather
    # than trying to undo eight possible orientations afterwards.
    upright = ImageOps.exif_transpose(Image.open(image_path))
    if upright.mode != "RGB":
        upright = upright.convert("RGB")

    buffer = io.BytesIO()
    upright.save(buffer, format="JPEG", quality=92)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    try:
        # httpx rather than urllib: it verifies against certifi's bundle, so
        # this works on a python.org install, where urllib has no root
        # certificates and every HTTPS call fails.
        response = httpx.post(
            "https://vision.googleapis.com/v1/images:annotate",
            params={"key": api_key},
            json={
                "requests": [{
                    "image": {"content": encoded},
                    # DOCUMENT_TEXT_DETECTION is tuned for dense printed text;
                    # TEXT_DETECTION is meant for signs and labels.
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                }]
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    responses = payload.get("responses") or [{}]
    annotation = responses[0].get("fullTextAnnotation")
    if not annotation:
        return None

    blocks = []
    for page in annotation.get("pages", []):
        width = page.get("width") or 1
        height = page.get("height") or 1
        for block in page.get("blocks", []):
            for paragraph in block.get("paragraphs", []):
                for word in paragraph.get("words", []):
                    text = "".join(
                        symbol.get("text", "")
                        for symbol in word.get("symbols", [])
                    ).strip()
                    if not text:
                        continue

                    corners = word.get("boundingBox", {}).get("vertices", [])
                    if len(corners) < 4:
                        continue
                    xs = [c.get("x", 0) for c in corners]
                    ys = [c.get("y", 0) for c in corners]

                    top_left, top_right = corners[0], corners[1]
                    angle = math.atan2(
                        top_right.get("y", 0) - top_left.get("y", 0),
                        (top_right.get("x", 0) - top_left.get("x", 0)) or 1,
                    )

                    blocks.append((
                        text,
                        min(xs) / width,
                        ((min(ys) + max(ys)) / 2) / height,
                        (max(ys) - min(ys)) / height,
                        angle,
                    ))
    return blocks or None


def _read_blocks(image_path):
    for reader, name in (
        (_read_blocks_vision, "vision"),
        (_read_blocks_google, "google"),
    ):
        blocks = reader(image_path)
        if blocks:
            return blocks, name
    return _read_blocks_tesseract(image_path), "tesseract"


# --------------------------------------------------------------------------
# Layout: group positioned text into visual rows.
# --------------------------------------------------------------------------

def _median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _estimate_skew(blocks, median_height):
    """Find the page's tilt.

    A receipt is rarely photographed square-on, and on a tilted page a printed
    line's vertical position drifts as you move across it — enough that a
    right-hand price lands nearer the *next* line's text than its own.

    Vision reports a per-run angle, but only meaningfully for runs long enough
    to have a direction; short ones like "$2.97" come back as zero, which drags
    a plain median to nothing. So the long runs set the estimate, and a narrow
    projection-profile search refines it: the best angle is the one that
    collapses the text into the tightest horizontal bands. That search stays
    deliberately narrow, because tilting by a whole line-height also lines the
    text up into neat bands — just against the wrong lines.
    """
    long_runs = [b[4] for b in blocks if len(b[0]) >= 8]
    base = _median(long_runs) if long_runs else 0.0

    bin_width = max(median_height * 0.5, 1e-4)
    best_angle, best_score = base, -1.0

    for step in range(-15, 16):  # base ±1.5°, tenth-of-a-degree steps
        angle = base + math.radians(step * 0.1)
        sin_a, cos_a = math.sin(angle), math.cos(angle)

        # Two bin phases, so a band straddling a bin edge isn't scored as if
        # it were spread out.
        for phase in (0.0, 0.5):
            counts = {}
            for _, x, y, _, _ in blocks:
                key = int((y * cos_a - x * sin_a) / bin_width + phase)
                counts[key] = counts.get(key, 0) + 1
            score = sum(count * count for count in counts.values())
            if score > best_score:
                best_score, best_angle = score, angle

    return best_angle


def _group_rows(blocks):
    """Cluster text blocks into rows, each row's runs ordered left to right."""
    if not blocks:
        return []

    median_height = _median([b[3] for b in blocks]) or 0.01

    skew = _estimate_skew(blocks, median_height)
    sin_skew, cos_skew = math.sin(skew), math.cos(skew)

    def line_position(x, y):
        return y * cos_skew - x * sin_skew

    tolerance = median_height * 0.7

    positioned = sorted(
        ((line_position(x, y), x, text) for text, x, y, _, _ in blocks),
        key=lambda item: item[0],
    )

    rows = []
    for position, x, text in positioned:
        # Measure against where the row started, not a running mean: letting
        # the anchor slide downwards as runs join lets one row swallow the
        # next, one small step at a time.
        if rows and position - rows[-1]["y"] <= tolerance:
            rows[-1]["runs"].append((x, text))
        else:
            rows.append({"y": position, "runs": [(x, text)]})

    for row in rows:
        row["runs"].sort()
    return rows


def _row_parts(row):
    """Split a row into (left_text, price_or_None)."""
    # Receipts flag lines with markers like a trailing "*" (GST-applicable at
    # Pak'nSave), which OCR may hand back as its own run.
    runs = [text for _, text in row["runs"] if not _MARKER_ONLY_RE.match(text)]
    if not runs:
        return "", None

    # A price sitting in its own run on the right (typical of Vision output).
    match = _PRICE_RE.match(runs[-1]) if len(runs) > 1 else None
    if match:
        return " ".join(runs[:-1]).strip(), _to_float(match.group(1))

    # Otherwise the whole row may be one run ending in a price.
    joined = " ".join(runs)
    match = _TRAILING_PRICE_RE.search(joined)
    if match:
        return joined[: match.start()].strip(" .-*:="), _to_float(match.group(1))
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
    # OCR drops spaces into words ("Visa" comes back as "Vi sa"), which would
    # otherwise sneak a payment line through as if it were a purchase.
    squashed = re.sub(r"\s+", "", text).lower()
    return any(kw.replace(" ", "") in squashed for kw in _SKIP_ROW_KEYWORDS)


def _is_detail_row(text):
    """Is this figures rather than a product name — a barcode, a count, a
    column of numbers? Such a row belongs to the name printed above it."""
    return not any(c.isalpha() for c in text)


def _build_date(year, month, day):
    year, month, day = int(year), int(month), int(day)
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _date_in(text):
    match = _ISO_DATE_RE.search(text)
    if match:
        return _build_date(*match.groups())

    match = _NAMED_MONTH_RE.search(text)
    if match:
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.lower())
        if month:
            return _build_date(year, month, day)

    match = _NUMERIC_DATE_RE.search(text)
    if match:
        day, month, year = match.groups()
        return _build_date(year, month, day)
    return None


def _find_date(rows):
    for row in rows:
        found = _date_in(" ".join(t for _, t in row["runs"]))
        if found:
            return found
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
    (NZ produce shops in particular), so check the top first and fall back.

    Within a group the longest candidate wins. The first line of a receipt is
    usually the logo, set in stylised type that OCR reliably mangles —
    "PAK'nSAVE" comes back as "PAKiSAVE", "New World" as "NW NEW WORLD" — and
    the plain-text name is printed just beneath it. That line is also longer,
    because it carries the branch: "PAK'nSAVE Hornby", "New World Lincoln".
    """
    def as_name(text):
        if _is_skippable(text):
            return None
        # Keep the leading run of letters and light punctuation, so a header
        # like "Sunson Asian Food Market =Part Wigram" still yields a name.
        # Requiring it to *start* with letters rejects section rules
        # ("----FOOD----" survives OCR as "-FOOD") and addresses alike.
        match = re.match(r"[A-Za-z][A-Za-z&'. ]*", text)
        if not match:
            return None
        name = match.group(0).strip(" .")
        # Word-level OCR splits "PAK'nSAVE" into "PAK" and "'nSAVE", and the
        # space left between them would file the same shop under two names.
        name = re.sub(r"(?<=[A-Za-z])\s+(?='[A-Za-z])", "", name)
        return name if len(name) >= 3 else None

    # Only the first three rows: past that come opening hours and addresses,
    # whose leading words would otherwise out-length the real name.
    for candidate_rows in (rows[:3], rows[-5:]):
        names = []
        for row in candidate_rows:
            text, price = _row_parts(row)
            if price is not None:
                continue
            name = as_name(text)
            if name:
                names.append(name)
        if names:
            return max(names, key=len)
    return None


def _parse_items(rows):
    items = []
    pending_name = None  # An item name whose price is on the following row.

    for row in rows:
        text, price = _row_parts(row)

        # Purchases are always printed above the totals. Stopping there keeps
        # the payment and footer lines below from being read as items.
        if "total" in re.sub(r"\s+", "", text).lower():
            break

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
        unit = None
        name = text

        qty_match = _QTY_RE.search(text)
        if qty_match:
            try:
                quantity = _to_float(qty_match.group(1))
                unit_price = _to_float(qty_match.group(3))
            except ValueError:
                quantity, unit_price = 1, price

            raw_unit = qty_match.group(2) or qty_match.group(4)
            if raw_unit:
                unit = _UNIT_NAMES.get(raw_unit.lower())

            leading = text[: qty_match.start()].strip(" .-*:=")
            # Two shapes show up. Some shops print the name on its own line
            # and indent the quantity beneath it, leaving nothing before the
            # "@" here; others print name and quantity on one line.
            #
            # A stray character or two ahead of the quantity is OCR noise on
            # an indented line, not a name — reading it as one used to lose
            # the whole item, since it was then too short to keep.
            if len(leading) < 3 and pending_name:
                name = pending_name
            else:
                name = leading or pending_name or text
        elif _is_detail_row(text) and pending_name:
            # No "@" at all: shops like the Asian grocers print the name on
            # one line and a barcode-and-figures line beneath it.
            name = pending_name
        else:
            # The quantity didn't parse — usually a misread "@". Drop the
            # price tail anyway so it doesn't end up inside the item's name.
            name = _PRICE_TAIL_RE.sub("", text).strip(" .-*:=") or text

        if not name or len(name) < 2:
            pending_name = None
            continue

        items.append({
            "name": name,
            "quantity": quantity,
            "unit": unit,
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
        # "Total including GST" is a total, not a GST amount — don't let it
        # answer for both.
        "gst": _find_amount(rows, "gst", exclude=("total",)),
        # "Total Discount" and "Total Savings" are not what was paid.
        "total": _find_amount(
            rows, "total", exclude=("subtotal", "discount", "saving", "items")
        ),
        "ocr_engine": engine,
    }
    return parsed, raw_text
