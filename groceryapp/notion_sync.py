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


def query_item_history(query_text):
    """Search the Notion database for items matching query_text, sorted by
    unit price ascending (cheapest first) so it reads as a price comparison.
    """
    client = get_client()
    database_id = os.environ["NOTION_DATABASE_ID"]

    response = client.databases.query(
        database_id=database_id,
        filter={"property": "Item", "title": {"contains": query_text}},
    )

    rows = []
    for page in response["results"]:
        props = page["properties"]
        rows.append({
            "item_name": _plain_title(props, "Item"),
            "store_name": _select_name(props, "Store"),
            "category": _select_name(props, "Category"),
            "unit_price": _number(props, "Unit Price"),
            "unit": _select_name(props, "Unit"),
            "quantity": _number(props, "Quantity"),
            "line_total": _number(props, "Line Total"),
            "purchase_date": _date(props, "Purchase Date"),
        })

    rows.sort(key=lambda r: (r["unit_price"] is None, r["unit_price"]))
    return rows
