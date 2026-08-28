import calendar
import os
import tempfile
from datetime import date

from flask import redirect, render_template, request, url_for

from groceryapp import app, notion_sync, ocr

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

# For spending that doesn't come off a supermarket receipt. Kept separate
# from ocr.CATEGORIES, which describes what's *in* a grocery bag.
# Offered on the review screen; blank stays blank when a receipt gives no unit.
UNITS = ["", "ea", "kg", "g", "L"]

EXPENSE_CATEGORIES = [
    "Groceries", "Dining Out", "Transport", "Fuel", "Rent", "Utilities",
    "Phone & Internet", "Health", "Education", "Household", "Clothing",
    "Entertainment", "Other",
]


def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[-1].lower() in ALLOWED_EXTENSIONS


def _notion_database_url():
    database_id = os.environ["NOTION_DATABASE_ID"]
    return f"https://www.notion.so/{database_id.replace('-', '')}"


@app.route("/")
def index():
    return render_template("index.html", notion_url=_notion_database_url())


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        return render_template("upload.html")

    photo = request.files.get("photo")
    if not photo or photo.filename == "" or not _allowed(photo.filename):
        return render_template(
            "upload.html",
            error="Please choose a jpg/png/webp receipt photo.",
        )

    ext = photo.filename.rsplit(".", 1)[-1].lower()
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        photo.save(tmp.name)
        image_path = tmp.name

    try:
        parsed, raw_text = ocr.extract_receipt(image_path)
    finally:
        os.remove(image_path)

    if parsed is None or not parsed.get("items"):
        return render_template(
            "upload.html",
            error="Couldn't read anything from this receipt. Try a clearer, better-lit photo.",
        )

    # Nothing is written yet. OCR gets a few things wrong on most receipts —
    # a misread "@" leaves debris in a name, a category is guessed from
    # keywords — and fixing those here is far easier than hunting the rows
    # down in Notion afterwards.
    return render_template(
        "review.html",
        store_name=parsed.get("store_name") or "",
        purchase_date=parsed.get("purchase_date") or "",
        items=parsed["items"],
        categories=ocr.CATEGORIES,
        units=UNITS,
    )


def _as_number(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@app.route("/confirm", methods=["POST"])
def confirm():
    """Write the reviewed rows to Notion. Only the ticked ones."""
    store_name = request.form.get("store_name", "").strip()
    purchase_date = request.form.get("purchase_date", "").strip()

    names = request.form.getlist("item_name")
    categories = request.form.getlist("item_category")
    quantities = request.form.getlist("item_quantity")
    item_units = request.form.getlist("item_unit")
    unit_prices = request.form.getlist("item_unit_price")
    line_totals = request.form.getlist("item_line_total")

    # Unchecked boxes aren't submitted at all, so each carries its row index.
    keep = {int(i) for i in request.form.getlist("include") if i.isdigit()}

    items = []
    for i, name in enumerate(names):
        if i not in keep or not name.strip():
            continue
        items.append({
            "name": name.strip(),
            "category": categories[i] if i < len(categories) else None,
            "quantity": _as_number(quantities[i]) if i < len(quantities) else None,
            "unit": (item_units[i] or None) if i < len(item_units) else None,
            "unit_price": _as_number(unit_prices[i]) if i < len(unit_prices) else None,
            "line_total": _as_number(line_totals[i]) if i < len(line_totals) else None,
        })

    if not items:
        return render_template(
            "upload.html",
            error="Nothing was ticked, so nothing was saved.",
        )

    count = notion_sync.add_purchase_items(
        items, store_name or None, purchase_date or None
    )

    return render_template(
        "upload_result.html",
        store_name=store_name,
        purchase_date=purchase_date,
        items=items,
        count=count,
        notion_url=_notion_database_url(),
    )


@app.route("/compare")
def compare():
    query = request.args.get("q", "").strip()
    results = notion_sync.query_item_history(query) if query else []
    return render_template("compare.html", query=query, results=results)


def _period_range(period, on):
    """The first and last date of the day or month `on` falls in."""
    if period == "day":
        return on, on
    last_day = calendar.monthrange(on.year, on.month)[1]
    return on.replace(day=1), on.replace(day=last_day)


# Which column the detail table can be sorted on. Every key copes with a
# missing value, since a row typed in by hand may have no store or category.
_SPENDING_SORTS = {
    "date": lambda row: row["purchase_date"] or "",
    "item": lambda row: (row["item_name"] or "").lower(),
    "store": lambda row: (row["store_name"] or "").lower(),
    "category": lambda row: (row["category"] or "").lower(),
    "amount": lambda row: row["line_total"] or 0,
}


@app.route("/spending")
def spending():
    period = "day" if request.args.get("period") == "day" else "month"

    raw_on = request.args.get("on", "")
    try:
        # A month input sends "2026-07"; a date input sends "2026-07-19".
        parts = [int(p) for p in raw_on.split("-")]
        on = date(parts[0], parts[1], parts[2] if len(parts) > 2 else 1)
    except (ValueError, IndexError):
        on = date.today()

    sort = request.args.get("sort", "date")
    if sort not in _SPENDING_SORTS:
        sort = "date"
    order = "asc" if request.args.get("order") == "asc" else "desc"

    start, end = _period_range(period, on)
    rows = notion_sync.query_spending(start.isoformat(), end.isoformat())

    total = sum(row["line_total"] or 0 for row in rows)

    # Sorted newest first, then by the chosen column. Python's sort is stable,
    # so rows sharing a category still come out in date order underneath it.
    rows.sort(key=_SPENDING_SORTS["date"], reverse=True)
    if sort != "date" or order == "asc":
        rows.sort(key=_SPENDING_SORTS[sort], reverse=(order == "desc"))

    by_category = {}
    for row in rows:
        name = row["category"] or "Other"
        by_category[name] = by_category.get(name, 0) + (row["line_total"] or 0)
    by_category = sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)

    return render_template(
        "spending.html",
        period=period,
        on=on,
        start=start,
        end=end,
        rows=rows,
        total=total,
        by_category=by_category,
        sort=sort,
        order=order,
        notion_url=_notion_database_url(),
    )


@app.route("/expense", methods=["GET", "POST"])
def expense():
    today = date.today().isoformat()

    if request.method == "GET":
        return render_template(
            "expense.html", categories=EXPENSE_CATEGORIES, today=today
        )

    description = request.form.get("description", "").strip()
    amount_raw = request.form.get("amount", "").strip()
    spent_on = request.form.get("spent_on", "").strip() or today
    category = request.form.get("category") or "Other"
    store = request.form.get("store", "").strip()

    try:
        amount = float(amount_raw)
    except ValueError:
        amount = None

    if not description or amount is None:
        return render_template(
            "expense.html",
            categories=EXPENSE_CATEGORIES,
            today=today,
            error="Enter a description and an amount.",
            form=request.form,
        )

    notion_sync.add_expense(description, amount, spent_on, category, store or None)
    return redirect(url_for("spending", period="day", on=spent_on))
