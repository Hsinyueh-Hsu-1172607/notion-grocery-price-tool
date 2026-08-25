import calendar
import os
import tempfile
from datetime import date

from flask import redirect, render_template, request, url_for

from groceryapp import app, notion_sync, ocr

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

# For spending that doesn't come off a supermarket receipt. Kept separate
# from ocr.CATEGORIES, which describes what's *in* a grocery bag.
EXPENSE_CATEGORIES = [
    "Groceries", "Dining Out", "Transport", "Rent", "Utilities",
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

    count = notion_sync.add_purchase_items(
        parsed["items"], parsed.get("store_name"), parsed.get("purchase_date"),
    )

    return render_template(
        "upload_result.html",
        store_name=parsed.get("store_name"),
        purchase_date=parsed.get("purchase_date"),
        items=parsed["items"],
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

    start, end = _period_range(period, on)
    rows = notion_sync.query_spending(start.isoformat(), end.isoformat())

    total = sum(row["line_total"] or 0 for row in rows)

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
