"""One-off script: creates the "Grocery Prices" database under
NOTION_PARENT_PAGE_ID and prints its ID. Run this once, then paste the
printed ID into .env as NOTION_DATABASE_ID before running the main app.
"""
import os

from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

client = Client(auth=os.environ["NOTION_API_KEY"])

database = client.databases.create(
    parent={"type": "page_id", "page_id": os.environ["NOTION_PARENT_PAGE_ID"]},
    title=[{"type": "text", "text": {"content": "Grocery Prices"}}],
    properties={
        "Item": {"title": {}},
        "Store": {"select": {}},
        "Category": {"select": {}},
        "Unit Price": {"number": {}},
        "Unit": {"select": {}},
        "Quantity": {"number": {}},
        "Line Total": {"number": {}},
        "Purchase Date": {"date": {}},
    },
)

print("Created database:", database["id"])
print("Add this to your .env as:")
print(f"NOTION_DATABASE_ID={database['id']}")
