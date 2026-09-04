"""One-off: tidy item names already saved in Notion.

Two things, both from rows written before the parser learned better:

Receipts print in block capitals, and rows scanned before the parser started
softening them read as shouting in a list. Only fully upper-case names are
touched, and only the letters, so pack sizes, codes and units come through as
they were.

And a name can end in leftover punctuation, most often a currency symbol:
Google Vision returns the "$" as a word of its own, and until the parser
accounted for that it stayed on the end of the name as "Regular $".

Nothing else on the row changes.

Nothing is written without --apply; by default the plan is printed for
checking. Reversing it is a matter of upper-casing the names again.

    python tidy_item_names.py
    python tidy_item_names.py --apply
"""
import re
import sys

from dotenv import load_dotenv

from groceryapp import notion_sync, ocr

load_dotenv()

# Rows saved before the parser learned that Google Vision returns the currency
# symbol as its own word kept it on the end of the name: "Regular $".
_TRAILING_DEBRIS_RE = re.compile(r"[\s$*.\-:=]+$")


def tidy(name):
    return ocr.tidy_case(_TRAILING_DEBRIS_RE.sub("", name))


def main():
    apply = "--apply" in sys.argv

    rows = notion_sync._query_all()
    planned = []
    for page in rows:
        name = notion_sync._as_row(page)["item_name"]
        tidied = tidy(name)
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
