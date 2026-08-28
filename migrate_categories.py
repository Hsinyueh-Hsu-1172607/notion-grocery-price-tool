"""One-off: re-file rows whose category the keyword list can now place better.

"Produce" was split into "Fruit" and "Vegetables" after some rows were
written, so they kept the old label. Rows can also sit in "Other" simply
because they were scanned before the keyword list knew the word. This
re-runs the matcher over each item's name and updates what it can place.

Nothing is written without --apply; by default the plan is printed for
checking.

    python migrate_categories.py                     # rows still on "Produce"
    python migrate_categories.py --also-other        # those, plus "Other"
    python migrate_categories.py --also-other --apply
"""
import sys

from dotenv import load_dotenv

from groceryapp import notion_sync, ocr

load_dotenv()

# Categories no longer offered, so any row still carrying one is stale.
RETIRED = {"Produce"}


def main():
    apply = "--apply" in sys.argv
    stale = set(RETIRED)
    if "--also-other" in sys.argv:
        # "Other" is a real category, not a retired one — only rows the
        # keywords can now place will move, the rest stay put.
        stale.add("Other")

    rows = notion_sync._query_all(
        filter={"or": [{"property": "Category", "select": {"equals": name}}
                       for name in sorted(stale)]}
    )
    print(f"{len(rows)} row(s) in {', '.join(sorted(stale))}\n")
    if not rows:
        return

    planned, skipped = [], []
    for page in rows:
        item = notion_sync._as_row(page)
        name = item["item_name"]
        guess = ocr._guess_category(name)
        # "Other" means the keywords still can't place it — leave the row
        # alone rather than moving it somewhere less accurate than it is.
        if guess in RETIRED or guess == "Other" or guess == item["category"]:
            skipped.append((name, guess))
        else:
            planned.append((page["id"], name, item["category"], guess))

    for _, name, old, new in planned:
        print(f"  {name:<34} {old}  ->  {new}")
    for name, guess in skipped:
        print(f"  {name:<34} left as is (keywords gave {guess!r})")

    if not apply:
        print(f"\nDry run. {len(planned)} row(s) would change. "
              f"Re-run with --apply to write.")
        return

    client = notion_sync.get_client()
    for page_id, name, _, new in planned:
        client.pages.update(
            page_id=page_id,
            properties={"Category": {"select": {"name": new}}},
        )
        print(f"  updated {name!r} -> {new}")
    print(f"\n{len(planned)} row(s) updated.")


if __name__ == "__main__":
    main()
