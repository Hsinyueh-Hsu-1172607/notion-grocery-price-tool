import os
import tempfile

from flask import render_template, request

from groceryapp import app, notion_sync, ocr

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


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
