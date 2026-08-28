"""One-off: restyle item names that were saved in the receipt's capitals.

Receipts print in block capitals, and rows scanned before the parser started
softening them read as shouting in a list. This applies the same rule to what
is already in Notion, so old and new rows look alike.

Only fully upper-case names are touched, and only the letters — pack sizes,
codes and units come through as they were. Nothing else on the row changes.

Nothing is written without --apply; by default the plan is printed for
checking. Reversing it is a matter of upper-casing the names again.

    python tidy_item_names.py
    python tidy_item_names.py --apply
"""
import sys

from dotenv import load_dotenv

from groceryapp import notion_sync, ocr

load_dotenv()


def main():
    apply = "--apply" in sys.argv

    rows = notion_sync._query_all()
    planned = []
    for page in rows:
        name = notion_sync._as_row(page)["item_name"]
        tidied = ocr.tidy_case(name)
        if tidied != name:
            planned.append((page["id"], name, tidied))

    print(f"{len(rows)} row(s) in the database, "
          f"{len(planned)} to restyle.\n")
    for _, old, new in planned:
        print(f"  {old:<34} ->  {new}")

    if not planned:
        return

    if not apply:
        print(f"\nDry run. {len(planned)} row(s) would change. "
              f"Re-run with --apply to write.")
        return

    client = notion_sync.get_client()
    for page_id, _, new in planned:
        client.pages.update(
            page_id=page_id,
            properties={"Item": {"title": [{"text": {"content": new}}]}},
        )
    print(f"\n{len(planned)} row(s) updated.")


if __name__ == "__main__":
    main()
