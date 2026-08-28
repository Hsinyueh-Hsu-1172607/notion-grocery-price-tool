"""One-off: drop receipts that were scanned into Notion more than once.

A re-scan writes a second full set of rows, which then counts twice in every
monthly total. Rows are grouped by purchase date, and two groups on the same
date holding the same number of rows for the same amount to the cent are the
same receipt read twice — two different shops matching on all three is not a
thing that happens.

The newest copy is kept, on the grounds that a re-scan is usually a re-scan of
something that came out wrong: the older rows here carry a mangled store name
and price tails left in the item names.

Archived, not deleted — the rows go to Notion's trash and can be restored.

    python remove_duplicate_scans.py
    python remove_duplicate_scans.py --apply
"""
import sys
from collections import defaultdict

from dotenv import load_dotenv

from groceryapp import notion_sync

load_dotenv()


def main():
    apply = "--apply" in sys.argv

    groups = defaultdict(list)
    for page in notion_sync._query_all():
        row = notion_sync._as_row(page)
        groups[(row["purchase_date"], row["store_name"])].append((page, row))

    # Keyed on what identifies a receipt regardless of how its store name was
    # read: the date, how many lines it had, and what it came to.
    by_receipt = defaultdict(list)
    for (purchase_date, store), group in groups.items():
        total = round(sum(r["line_total"] or 0 for _, r in group), 2)
        by_receipt[(purchase_date, len(group), total)].append((store, group))

    doomed = []
    for (purchase_date, count, total), copies in by_receipt.items():
        if len(copies) < 2:
            continue
        # Newest first, by when the rows were written.
        copies.sort(key=lambda c: max(p["created_time"] for p, _ in c[1]),
                    reverse=True)
        keep_store, _ = copies[0]
        print(f"{purchase_date}: {count} rows, ${total:.2f}, "
              f"scanned {len(copies)} times")
        print(f"   keep    {keep_store!r} (newest)")
        for store, group in copies[1:]:
            print(f"   archive {store!r} — {len(group)} rows")
            doomed.extend(page for page, _ in group)

    if not doomed:
        print("No duplicate scans found.")
        return

    if not apply:
        print(f"\nDry run. {len(doomed)} row(s) would be archived. "
              f"Re-run with --apply to write.")
        return

    client = notion_sync.get_client()
    for page in doomed:
        client.pages.update(page_id=page["id"], archived=True)
    print(f"\n{len(doomed)} row(s) archived.")


if __name__ == "__main__":
    main()
