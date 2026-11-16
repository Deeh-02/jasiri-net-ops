#!/usr/bin/env python3
"""One-off import of the old expenses spreadsheet (expenses_2026-11-16.csv)
into Finance > Expenses, with each row put in OUR categories, not the file's.

The old file used its own category list (Network Infrastructure, Facilities &
Utilities, ...). Several of them are wrong for what the money was (a billing
system fee filed as "Internet Capacity", routers filed as "Facilities"), so
this reads each row's title and vendor and picks from Finance's categories,
and decides whether it was a running cost ("operating") or equipment bought
to keep ("capital"). Rules are in `categorise()`, top to bottom, first match
wins, each with a plain reason. Anything it isn't sure of is marked REVIEW.

Nothing is written unless you pass --apply. Two steps:

  python tools/import_expenses_csv.py FILE.csv            # preview: writes a review CSV, prints totals
  python tools/import_expenses_csv.py FILE.csv --apply --user-id N    # saves what the preview showed

Options that change what is saved (use the same ones for the preview):
  --no-new-categories   file the rows that would go in the two new categories under "Other" instead
  --replace-pasted      where a row is an expense already saved in the app, void the app's copy
                        (with a reason, it stays on record) and import the spreadsheet's instead

Amounts, dates and vendors are copied exactly as the file has them (one date
that was plainly mistyped is corrected and said so). Every saved row keeps the
file's own id, title, vendor and old category in its "original message", so
any figure can be traced back to the spreadsheet row. Re-running skips rows
already imported. Rows that look like an expense already saved in the app are
held back (HELD) rather than counted twice.

Needs DATABASE_URL in the environment (the preview only reads).
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SOURCE_TAG = "Imported from CSV"
METHODS = {"mpesa": "mpesa", "bank_transfer": "bank", "cash": "cash"}
# Two categories the old file needed that Finance's first list didn't have.
NEW_CATEGORIES = (("Office & supplies", "operating", 45), ("Tools & safety gear", "operating", 46))
CAPITAL_THRESHOLD = Decimal("10000")   # a single durable tool or piece of furniture from here up is kept, not spent

# Payees that are clearly one kind of business: a rule is saved for them so the
# next pasted message from them is categorised without asking. People and
# mixed suppliers never get one.
RULE_PAYEES = {
    "SERVCO PETROLEUM LIMITED", "FORMULA FILLING STATION", "WAHANNA STEVE & CO. LTD", "KPLC PREPAID", "KPLC",
    "FREEISPRADIUS BILLING", "PINNACLE WEB SERVICES", "SAFARICOM DATA BUNDLES", "SAFARICOM POSTPAID BUNDLES",
    "SMART OLT", "PHP RADIUS", "QUIDEXPLUS KENYA LIMITED", "E-CITIZEN", "MAGIC HAND CLOTHE CARE",
}

_PHONE = re.compile(r"\s*\b0[17]\d{8}\b\s*")


def squash(text):
    return re.sub(r"\s+", " ", text or "").strip()


def clean_vendor(vendor):
    """The payee as it will be shown: spaces collapsed, trailing dots and a
    personal phone number removed. Not a name at all (a bare number, a bank
    reference) means blank."""
    v = squash(vendor).rstrip(".").strip()
    if not v or v.lower().startswith("bank ref") or re.fullmatch(r"[\d\s]+", v) and len(v) != 10:
        return None
    without_phone = squash(_PHONE.sub(" ", v))
    return without_phone or v


def segment_of(text):
    """Tagged only when the row says so itself."""
    if "hotspot" in text or "freeispradius" in text:
        return "hotspot"
    if "pppoe" in text or re.search(r"\bphp\b|radius|smart ?olt|quidexplus", text):
        return "pppoe"
    return None


def categorise(row):
    """One CSV row -> {category, kind, segment, why, review}. `row` has id,
    title, vendor, amount (Decimal), csv_category."""
    title = squash(row["title"]).lower()
    vendor = squash(row["vendor"]).lower()
    t = f"{title} | {vendor}"
    amount = row["amount"]
    segment = segment_of(t)

    def out(category, kind="operating", why="", review=False):
        return {"category": category, "kind": kind, "segment": segment, "why": why, "review": review}

    # -- equipment bought to keep: a vehicle
    if re.fullmatch(r"(new )?(tvs )?motorbike", title):
        return out("Transport & fuel", "capital", "A motorbike bought to keep")
    # -- government fees
    if re.search(r"kmel.*transfer|business name|e-?citizen", t):
        return out("Licences & taxes", why="Registration or transfer fee")
    # -- software, billing systems and bulk SMS
    if "freeispradius" in t:
        if "sms" in title or amount < 1000:
            return out("Airtime & SMS", why="SMS credit for the hotspot billing system", review="sms" not in title)
        return out("Software & licences", why="Hotspot billing system", review="internet capacity" in title)
    if "pinnacle" in t:
        if "domain" in title:
            return out("Software & licences", why="Domain name")
        return out("Airtime & SMS", why="Bulk SMS credit")
    if re.search(r"smart ?olt|\bphp\b|radius|quidexplus|whatsapp api|text api|billing system", t):
        return out("Software & licences", why="Billing / OLT / messaging system")
    # -- airtime and data bundles
    if re.search(r"airtime|minutes|bundle|postpaid|safaricom", t):
        return out("Airtime & SMS", why="Airtime or data bundle")
    # -- internet capacity bought
    if re.search(r"airtel link|vennet|internet capacity", t):
        return out("Upstream bandwidth", why="Internet capacity bought")
    if "commission" in title:
        return out("Agent commissions", why="Commission paid")
    if re.search(r"\brent\b", t):
        return out("Office rent", why="Rent", review="server rent" in title)
    if re.search(r"brand", title):
        return out("Marketing", why="Branded items")
    # -- people: salaries, advances, casual and contract labour
    technician_work = "khamala" in vendor and re.search(r"labour|install|maintenance|hotspot|advance|salary|repair", title)
    if technician_work or re.search(r"salary|attachee|support fee|customer support|\badvance\b|casual|\bdan\b|labour food|service fee for los|site visit|site survey|\bnoc\b", t):
        return out("Salaries", why="Wages or labour paid to a person", review=bool(re.search(r"\bnoc\b|site visit|site survey", t)) or amount >= CAPITAL_THRESHOLD)
    # -- getting about
    if re.search(r"fuel|petrol|transport|delivery|\bfare\b|car ?wash|bike wash|parcel|motorbike training|\brider\b", t):
        return out("Transport & fuel", why="Fuel, delivery or transport")
    # -- office jobs come before repairs and protective gear in general
    if re.search(r"gypsum|wall repair|laundry|dry clean|washing", t):
        return out("Office & supplies", why="Office wall work or cleaning")
    if re.search(r"repair|spare|\bmechanic\b|puncture|gasket|replacement|fixed|fixing|leakage|boxer.*service|tvs.*service|service parts|(motorbike|bike).*(chain|padlock|accessories)", t):
        return out("Spares & repairs", why="Repair, spare part or service")
    # -- the people who set the server up
    if "tony" in title:
        if "server configuration" in title:
            return out("Network equipment", "capital", "Setting up the server, kept with it", review=True)
        return out("Other", why="Down payment: what it was for isn't in the file", review=True)
    if re.search(r"token|electricity|electicity|kplc|using power", t):
        return out("Power & solar", why="Electricity")
    # -- network equipment bought to keep (any size): the fibre, its closures, the gear
    if re.search(r"adss|enclosure|\bfat\b|server|\bcrs\d|switch|\bolt\b|\btensions?\b|\bupb\b|climber|buckle|steel strap|mechanical|air condition|hisense", t) and "dp switch" not in title:
        return out("Network equipment", "capital", "Fibre, closures, pole hardware or core gear kept in the network")
    # -- electricity and the solar plant
    if re.search(r"solar|inverter|vestwood|sunawatt|change ?over|combiner|\bmcb\b|breaker|solatek|\bavs\b|\bats\b|a\.c power|kwh|using power", t):
        kept = re.search(r"solar panel|monocrystalline|inverter|vestwood|kwh|solar infrastructure|solar framework|metal frame|solar accessories", t)
        return out("Power & solar", "capital" if kept else "operating", "Solar plant kept" if kept else "Electricity or solar parts")
    # -- cutting-edge tools bought to keep
    if re.search(r"splicing|otdr", t):
        return out("Tools & safety gear", "capital", "Fibre splicing and test equipment")
    # -- protective clothing and gear (always a running cost)
    if re.search(r"overall|jacket|helmet|glove|boot|suit\b|reflector|safety|tool ?bag", t):
        return out("Tools & safety gear", why="Protective clothing or gear")
    # -- office: furniture, cleaning, stationery, fittings
    if re.search(r"laptop|seats|office tables|black tables|\bchairs?\b|curtain|shelves|shelf|clothes hanger|notebook|stamp|printer|label|hdmi|bracket|lock|laundry|dry clean|washing|\bmop\b|toilet|towel|bucket|dust|basin|tissue|alcohol|coffee|sugar|water|dispenser|fumigation|carrefour|office shopping|batteries|battery saa|(?<!spray )paint|brush|aqua gloss|twin with earth|multi plug|3 plug|power extension|electrical (wiring|appliances|materials)|dp switch|wiring in the office", t):
        kept = amount >= CAPITAL_THRESHOLD and re.search(r"laptop|seats|office tables|black tables", t)
        return out("Office & supplies", "capital" if kept else "operating", "Office furniture or computers kept" if kept else "Office supplies, cleaning or fittings")
    if "cctv" in t:
        return out("Other", why="CCTV: no category for security yet", review=True)
    if "joseph nduati" in t or re.search(r"decoiler|spirit level|screw ?driver|long nose|\btester\b|drill|walkie|ladder", t):
        durable = amount >= CAPITAL_THRESHOLD and re.search(r"ladder|machine", t)
        return out("Tools & safety gear", "capital" if durable else "operating", "Hand and power tools")
    # -- welding for racks, trays and frames
    if re.search(r"welding|\brod\b", t):
        return out("Network equipment", "capital" if amount >= CAPITAL_THRESHOLD else "operating", "Welded racks, trays and frames", review=True)
    # -- customer-side equipment and installation consumables: a running cost of connecting people
    if re.search(r"xpon|nokia|router|adapt[eo]r|\batb\b|patch|pigtail|splitter|sc-|upc|rj ?45|cat ?6|cca|drop cable|outdoor cable|cable|ties|clips|tronic|tape|sleeve|binding wire|spray paint|conduit|coupler|bends|cage nuts|trunking|tray|tube|\bpdu\b|power adapter|sfp|dac|loaded", t):
        return out("Network equipment", why="Customer equipment or installation consumables", review=amount >= CAPITAL_THRESHOLD)
    return out("Other", why="Nothing in the title says what it was for", review=True)


# ---- Reading the file ----

def load(path):
    """The CSV as clean rows. A date that is plainly mistyped (the year 3600)
    takes its neighbour's, and the row says so."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for r in csv.DictReader(handle):
            rows.append({"id": int(r["ID"]), "date_text": r["Date"], "title": squash(r["Title"]), "csv_category": r["Category"] or "",
                         "amount": Decimal(r["Amount"]), "tax": Decimal(r["Tax"] or "0"), "vendor": r["Vendor"] or "",
                         "method": METHODS[r["Payment Method"]], "status": r["Status"], "notes": []})
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        year = int(r["date_text"][:4])
        if 2025 <= year <= 2030:
            r["date"] = date.fromisoformat(r["date_text"])
            continue
        neighbour = by_id.get(r["id"] - 1) or by_id.get(r["id"] + 1)
        r["date"] = date.fromisoformat(neighbour["date_text"])
        r["notes"].append(f"date was {r['date_text']}: taken from the row beside it, {r['date']}")
    return rows


def same_payee(a, b):
    """Could these two vendors be the same payee? Yes when either is blank or
    one contains the other ("Dan Khamala" / "Dan Khamala Labour"); two
    different names (two people each buying KSh 500 of fuel) are not."""
    a, b = (clean_vendor(a) or "").lower(), (clean_vendor(b) or "").lower()
    return not a or not b or a in b or b in a


def spreadsheet_duplicates(rows):
    """Ids of a row that repeats an earlier one (same day, amount, title, way
    of paying and a compatible vendor): held for a person to decide."""
    seen, held = defaultdict(list), {}
    for r in sorted(rows, key=lambda r: r["id"]):
        key = (r["date"], r["amount"], r["title"].lower(), r["method"])
        twin = next((o for o in seen[key] if same_payee(o["vendor"], r["vendor"])), None)
        if twin:
            held[r["id"]] = f"repeats row {twin['id']} (same day, amount, title and payee)"
        else:
            seen[key].append(r)
    return held


def already_in_app(rows, existing):
    """{row id: app expense id} for rows that match an expense already saved
    in the app: same amount, same way of paying, within 3 days, matched one to
    one (closest date first)."""
    pairs = sorted(((abs((r["date"] - e["spent_on"]).days), r["id"], e["id"]) for r in rows for e in existing
                    if r["amount"] == e["amount"] and r["method"] == e["method"] and abs((r["date"] - e["spent_on"]).days) <= 3))
    held, used_rows, used_existing = {}, set(), set()
    for _, row_id, existing_id in pairs:
        if row_id in used_rows or existing_id in used_existing:
            continue
        used_rows.add(row_id)
        used_existing.add(existing_id)
        held[row_id] = existing_id
    return held


def build(rows, existing, imported_ids, replace_pasted=False, new_categories=True):
    """Every row with its category, kind and what to do with it. With
    replace_pasted, a row that matches an expense already in the app is
    imported and the app's copy (`replaces`) is voided."""
    in_app = already_in_app(rows, existing)
    held = dict(spreadsheet_duplicates(rows))
    if not replace_pasted:
        held.update({row_id: f"looks like expense #{app_id} already saved in the app" for row_id, app_id in in_app.items()})
    new_names = {name for name, _, _ in NEW_CATEGORIES}
    out = []
    for r in rows:
        result = categorise(r)
        if not new_categories and result["category"] in new_names:
            result = {**result, "category": "Other", "why": result["why"] + " (no category for it yet)", "review": True}
        payee = clean_vendor(r["vendor"])
        why_held = held.get(r["id"])
        action = "ALREADY IMPORTED" if r["id"] in imported_ids else "HELD" if why_held else "IMPORT"
        description = r["title"] if len(r["title"]) <= 300 else r["title"][:297] + "..."
        source = (f"{SOURCE_TAG} (expenses_2026-11-16.csv) · ID {r['id']} · \"{r['title']}\" · vendor \"{squash(r['vendor'])}\" · "
                  f"tax {r['tax']} · filed there as \"{r['csv_category'] or 'no category'}\"")
        replaces = in_app.get(r["id"]) if replace_pasted and action == "IMPORT" else None
        out.append({**r, **result, "payee": payee, "action": action, "held_why": why_held or "", "description": description,
                    "source_message": source, "replaces": replaces})
    return out


# ---- Preview ----

def report(built):
    money = lambda v: f"{v:>13,.0f}"
    todo = [b for b in built if b["action"] == "IMPORT"]
    held = [b for b in built if b["action"] == "HELD"]
    print(f"\n{len(built)} rows in the file; {len(todo)} to import ({sum(b['amount'] for b in todo):,.0f}), {len(held)} held back ({sum(b['amount'] for b in held):,.0f}).\n")
    by = defaultdict(lambda: {"operating": [0, Decimal(0)], "capital": [0, Decimal(0)]})
    for b in todo:
        slot = by[b["category"]][b["kind"]]
        slot[0] += 1
        slot[1] += b["amount"]
    print(f"{'category':26}{'running costs':>26}{'equipment kept':>26}")
    for name, kinds in sorted(by.items(), key=lambda kv: -(kv[1]["operating"][1] + kv[1]["capital"][1])):
        print(f"{name:26}{kinds['operating'][0]:>5} {money(kinds['operating'][1])} {kinds['capital'][0]:>5} {money(kinds['capital'][1])}")
    print("\nby month (running costs / equipment kept):")
    months = defaultdict(lambda: {"operating": Decimal(0), "capital": Decimal(0)})
    for b in todo:
        months[b["date"].strftime("%Y-%m")][b["kind"]] += b["amount"]
    for m in sorted(months):
        print(f"  {m}  {money(months[m]['operating'])} {money(months[m]['capital'])}")
    review = [b for b in todo if b["review"]]
    print(f"\n{len(review)} rows marked REVIEW ({sum(b['amount'] for b in review):,.0f}): the rule was a guess")
    print("held back:")
    for b in held:
        print(f"  #{b['id']} {b['date']} {b['amount']:,.2f} {b['title'][:40]!r}: {b['held_why']}")


def write_review(built, path):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        w.writerow(["Row", "Date", "Amount", "What the file says", "Vendor", "Paid by", "Filed there as", "Goes in", "Kind", "Segment", "Why", "Check?", "What happens"])
        for b in sorted(built, key=lambda b: (b["action"] != "IMPORT", b["category"], b["date"])):
            w.writerow([b["id"], b["date"], b["amount"], b["title"], squash(b["vendor"]), b["method"], b["csv_category"], b["category"],
                        "equipment kept" if b["kind"] == "capital" else "running cost", b["segment"] or "", b["why"],
                        "REVIEW" if b["review"] else "", (b["action"] + (": " + b["held_why"] if b["held_why"] else "") + ("; " + "; ".join(b["notes"]) if b["notes"] else ""))])


# ---- Reading the app, and saving ----

def read_app():
    from db.connection import db_cursor
    with db_cursor() as (conn, cur):
        cur.execute("SELECT id, spent_on, amount, method FROM expenses WHERE voided_at IS NULL AND source_message NOT LIKE %s", (SOURCE_TAG + "%",))
        existing = [{"id": r[0], "spent_on": r[1], "amount": r[2], "method": r[3]} for r in cur.fetchall()]
        cur.execute("SELECT source_message FROM expenses WHERE source_message LIKE %s", (SOURCE_TAG + "%",))
        imported = {int(m.group(1)) for (s,) in cur.fetchall() if (m := re.search(r"· ID (\d+) ·", s or ""))}
    return existing, imported


def apply(built, user_id, no_new_categories=False):
    """Adds the two new categories if missing, then saves every IMPORT row (and
    the supplier rules) in one transaction."""
    from db import finance_expenses
    from db.connection import db_cursor
    with db_cursor() as (conn, cur):
        for name, group, order in NEW_CATEGORIES if not no_new_categories else ():
            cur.execute("INSERT INTO expense_categories (name, grp, sort_order) VALUES (%s, %s, %s) ON CONFLICT (name) DO NOTHING", (name, group, order))
        conn.commit()
    ids = {c["name"]: c["id"] for c in finance_expenses.list_categories(include_retired=True)}
    rows = [{"reference": None, "spent_on": b["date"], "amount": b["amount"], "payee": b["payee"], "category_id": ids[b["category"]],
             "kind": b["kind"], "segment": b["segment"], "description": b["description"], "method": b["method"],
             "source_message": b["source_message"], "ai_suggested": False} for b in built if b["action"] == "IMPORT"]
    from db.finance_messages import payee_key
    seen, rules = defaultdict(set), {}
    for b in built:
        if b["action"] == "IMPORT" and b["payee"] and payee_key(b["payee"]) in RULE_PAYEES:
            seen[payee_key(b["payee"])].add((b["category"], b["kind"]))
    existing_rules = finance_expenses.load_rules()
    for key, choices in seen.items():
        if len(choices) == 1 and key not in existing_rules:
            category, kind = next(iter(choices))
            rules[key] = {"category_id": ids[category], "kind": kind}
    result = finance_expenses.save_expenses(rows, rules, user_id)
    print(f"Saved {result['saved']} expenses and {len(rules)} payee rules.")
    for b in built:
        if b["action"] == "IMPORT" and b["replaces"]:
            done = finance_expenses.void_expense(b["replaces"], f"Replaced by the spreadsheet import (row {b['id']})", user_id)
            print(f"  expense #{b['replaces']} {'voided' if done else 'was already voided or missing'}, replaced by row {b['id']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_file")
    parser.add_argument("--review-file", default="expenses_import_review.csv", help="where the preview CSV goes")
    parser.add_argument("--apply", action="store_true", help="save the rows (after you have read the preview)")
    parser.add_argument("--user-id", type=int, help="the user the rows are saved as (needed with --apply)")
    parser.add_argument("--no-new-categories", action="store_true")
    parser.add_argument("--replace-pasted", action="store_true")
    args = parser.parse_args()
    existing, imported = read_app()
    built = build(load(args.csv_file), existing, imported, args.replace_pasted, not args.no_new_categories)
    write_review(built, args.review_file)
    report(built)
    print(f"\nReview file: {args.review_file}")
    if args.apply:
        if not args.user_id:
            sys.exit("--apply needs --user-id")
        apply(built, args.user_id, args.no_new_categories)


if __name__ == "__main__":
    main()
