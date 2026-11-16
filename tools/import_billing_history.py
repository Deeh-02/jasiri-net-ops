#!/usr/bin/env python3
"""Load the billing system's transactions export (the old billing history)
into Finance: hotspot income before the router started recording sales, and
PPPoE income before PPPoE moved to the new billing system on 20 Jun 2026.
The rules for what counts are in db/billing_history_logic.py.

Nothing is written unless you pass --apply:

  python tools/import_billing_history.py FILE.csv [--check-dir DIR]                 # preview
  python tools/import_billing_history.py FILE.csv --apply --user-id N [--check-dir DIR]

--check-dir points at the summaries made from the same file (the owner's
JASIRI_transactions_for_claude folder). Every day's count and total per type
is compared with its 2_daily_revenue.csv, so the file is known to be read
completely before anything is saved; --apply refuses if any day differs.

The preview works before migrations/0040_billing_history.sql is run (it
reads only); saving needs it. Re-running adds only rows not saved before.
Needs DATABASE_URL in the environment.
"""
import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from db import billing_history  # noqa: E402
from db import billing_history_logic as logic  # noqa: E402
from db.connection import db_cursor  # noqa: E402

KINDS = (("hotspot", "hotspot"), ("pppoe", "pppoe"), ("balance", "balance"))


def check_against_summary(rows, folder):
    """Lines describing every day that differs from 2_daily_revenue.csv
    (empty when the file was read completely)."""
    path = Path(folder) / "2_daily_revenue.csv"
    ours = logic.daily_raw_totals(rows)
    problems, days = [], 0
    with open(path, encoding="utf-8-sig") as f:
        for line in csv.DictReader(f):
            day = date.fromisoformat(line["date"])
            days += 1
            for kind, column in KINDS:
                want = (int(line[f"{column}_count"]), Decimal(line[f"{column}_ksh"]))
                got = ours.get((day, kind), (0, Decimal("0")))
                if want[0] != got[0] or want[1] != got[1]:
                    problems.append(f"  {day} {kind}: summary {want[0]} rows KSh {want[1]:,.2f}, file {got[0]} rows KSh {got[1]:,.2f}")
    print(f"Checked {days} days against {path.name}: " + ("every day matches." if not problems else f"{len(problems)} differ:"))
    for p in problems[:20]:
        print(p)
    return problems


def preview_without_tables(rows):
    """The dry run before migration 0040: classify against the new system's
    recharges, nothing saved yet."""
    with db_cursor() as (conn, cur):
        cur.execute("SELECT username, recharged_at FROM pppoe_recharges")
        logic.classify(rows, cur.fetchall())
    return dict(logic.preview(rows, set()), committed=False)


def report(result, rows):
    print(f"\nRows in file {result['in_file']:,}, already saved {result['already_saved']:,}, new {result['new']:,}.")
    print(f"Counted as income: {result['counted']:,} rows, KSh {result['counted_total']:,.2f}\n")
    by_month = defaultdict(dict)
    for m in result["months"]:
        by_month[m["month"]][m["segment"]] = m
    print(f"{'Month':8} {'Hotspot':>9} {'KSh':>13} {'PPPoE':>7} {'KSh':>11}")
    for month in sorted(by_month):
        h, p = by_month[month].get("hotspot", {}), by_month[month].get("pppoe", {})
        print(f"{month:8} {h.get('count', 0):>9,} {h.get('total', 0):>13,.2f} {p.get('count', 0):>7,} {p.get('total', 0):>11,.2f}")
    print("\nLeft out (kept, not counted):")
    for item in result["left_out"]:
        print(f"  {item['segment']:7} {item['label']}: {item['count']:,} rows, KSh {item['total']:,.2f}")
        for e in item["examples"]:
            print(f"      {e}")
    move_at = logic.datetime.combine(logic.MOVE_DAY, logic.datetime.min.time()) - logic.EAT_OFFSET
    after = [r for r in rows if r["segment"] == "pppoe" and r["left_out"] is None and r["paid_at"] >= move_at]
    if after:
        print(f"\nPPPoE paid into the old system after the move that the new system doesn't have "
              f"(counted): {len(after)} rows, KSh {sum(r['amount'] for r in after):,.2f}")
        for r in after:
            print(f"      {logic._example(r)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file")
    parser.add_argument("--check-dir", help="folder with 2_daily_revenue.csv made from the same file")
    parser.add_argument("--apply", action="store_true", help="save (default: preview only)")
    parser.add_argument("--user-id", type=int, help="the app user the import is recorded under (with --apply)")
    args = parser.parse_args()
    if args.apply and not args.user_id:
        parser.error("--apply needs --user-id")

    path = Path(args.file)
    parsed = logic.parse_export(path.name, path.read_bytes())
    rows = parsed["rows"]
    print(f"Read {len(rows):,} rows from {path.name}" + (f"; unreadable lines: {parsed['unreadable'][:20]}" if parsed["unreadable"] else "."))
    if args.check_dir and check_against_summary(rows, args.check_dir) and args.apply:
        sys.exit("Not saved: the file doesn't match its summary.")

    with db_cursor() as (conn, cur):
        ready = billing_history.available(cur)
    if not ready:
        if args.apply:
            sys.exit("Not saved: run migrations/0040_billing_history.sql on this database first.")
        print("(migrations/0040 not run yet: preview only, nothing compared with saved rows)")
        result = preview_without_tables(rows)
    else:
        result = billing_history.import_rows(path.name, rows, args.user_id, commit=args.apply)
    report(result, rows)
    print("\nSaved." if result["committed"] else "\nPreview only: nothing was saved. Add --apply --user-id N to save.")


if __name__ == "__main__":
    main()
