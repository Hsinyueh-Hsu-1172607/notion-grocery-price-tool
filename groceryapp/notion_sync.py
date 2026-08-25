import os

from notion_client import Client

_client = None


def get_client():
    global _client
    if _client is None:
        _client = Client(auth=os.environ["NOTION_API_KEY"])
    return _client


def _item_properties(item, store_name, purchase_date):
    properties = {
        "Item": {"title": [{"text": {"content": item["name"]}}]},
        "Category": {"select": {"name": item.get("category") or "Other"}},
        "Quantity": {"number": item.get("quantity") or 1},
    }
    if store_name:
        properties["Store"] = {"select": {"name": store_name}}
    if item.get("unit"):
        properties["Unit"] = {"select": {"name": item["unit"]}}
    if item.get("unit_price") is not None:
        properties["Unit Price"] = {"number": item["unit_price"]}
    if item.get("line_total") is not None:
        properties["Line Total"] = {"number": item["line_total"]}
    if purchase_date:
        properties["Purchase Date"] = {"date": {"start": purchase_date}}
    return properties


def add_purchase_items(items, store_name, purchase_date):
    """Create one Notion page per line item. Returns the number created."""
    client = get_client()
    database_id = os.environ["NOTION_DATABASE_ID"]

    count = 0
    for item in items:
        client.pages.create(
            parent={"database_id": database_id},
            properties=_item_properties(item, store_name, purchase_date),
        )
        count += 1
    return count


def add_expense(description, amount, spent_on, category, store=None):
    """Record a single spend that didn't come from a scanned receipt — rent,
    a bus fare, dinner out. Same database as scanned items, so the spending
    totals cover everything without having to merge two sources.
    """
    properties = {
        "Item": {"title": [{"text": {"content": description}}]},
        "Category": {"select": {"name": category or "Other"}},
        "Line Total": {"number": amount},
        "Purchase Date": {"date": {"start": spent_on}},
    }
    if store:
        properties["Store"] = {"select": {"name": store}}

    get_client().pages.create(
        parent={"database_id": os.environ["NOTION_DATABASE_ID"]},
        properties=properties,
    )


def _plain_title(properties, name):
    parts = properties.get(name, {}).get("title", [])
    return parts[0]["plain_text"] if parts else ""


def _select_name(properties, name):
    value = properties.get(name, {}).get("select")
    return value["name"] if value else None


def _number(properties, name):
    return properties.get(name, {}).get("number")


def _date(properties, name):
    value = properties.get(name, {}).get("date")
    return value["start"] if value else None


def _query_all(**kwargs):
    """Query the database, following Notion's pagination.

    Notion returns at most 100 rows per call; without this a month's worth of
    shopping would quietly go missing from the totals.
    """
    client = get_client()
    database_id = os.environ["NOTION_DATABASE_ID"]

    pages, cursor = [], None
    while True:
        response = client.databases.query(
            database_id=database_id, start_cursor=cursor, **kwargs
        )
        pages.extend(response["results"])
        if not response.get("has_more"):
            return pages
        cursor = response["next_cursor"]


def _as_row(page):
    props = page["properties"]
    return {
        "item_name": _plain_title(props, "Item"),
        "store_name": _select_name(props, "Store"),
        "category": _select_name(props, "Category"),
        "unit_price": _number(props, "Unit Price"),
        "unit": _select_name(props, "Unit"),
        "quantity": _number(props, "Quantity"),
        "line_total": _number(props, "Line Total"),
        "purchase_date": _date(props, "Purchase Date"),
    }


def query_item_history(query_text):
    """Search the Notion database for items matching query_text, sorted by
    unit price ascending (cheapest first) so it reads as a price comparison.
    """
    rows = [
        _as_row(page)
        for page in _query_all(
            filter={"property": "Item", "title": {"contains": query_text}}
        )
    ]
    rows.sort(key=lambda r: (r["unit_price"] is None, r["unit_price"]))
    return rows


def query_spending(start_date, end_date):
    """Every recorded spend between two dates (inclusive), newest first."""
    rows = [
        _as_row(page)
        for page in _query_all(
            filter={
                "and": [
                    {"property": "Purchase Date",
                     "date": {"on_or_after": start_date}},
                    {"property": "Purchase Date",
                     "date": {"on_or_before": end_date}},
                ]
            },
            sorts=[{"property": "Purchase Date", "direction": "descending"}],
        )
    ]
    return rows
