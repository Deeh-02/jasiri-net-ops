"""Finance rules that don't need a database (Phase 7), so each has a
fixed-input test (tests/test_finance_logic.py): what the review screen shows
for each pasted message, and what gets saved from the user's choices.

The save path re-reads the original paste on the server and takes amounts,
codes and dates from that reading only. The browser sends back choices
(category, kind, skip, remember, description), never figures.
"""
from datetime import date, timedelta
from decimal import Decimal

FEE_CATEGORY = "M-Pesa & bank charges"
AIRTIME_CATEGORY = "Airtime & SMS"
KINDS = ("operating", "capital")
SEGMENTS = ("pppoe", "hotspot", "shared")


def _suggestion(item, rules, category_ids_by_name):
    """A category from what the app already knows, or None. A saved rule
    beats everything; the most specific rule (payee + account) is tried
    first. AI suggestions are not made here."""
    active_ids = set(category_ids_by_name.values())
    for key in item.get("rule_keys") or []:
        rule = rules.get(key)
        # A rule pointing at a retired category is ignored, not followed.
        if rule and rule["category_id"] in active_ids:
            return {"category_id": rule["category_id"], "kind": rule["kind"], "source": "rule"}
    if item.get("message_type") == "airtime" and AIRTIME_CATEGORY in category_ids_by_name:
        return {"category_id": category_ids_by_name[AIRTIME_CATEGORY], "kind": "operating", "source": "message"}
    keyword = item.get("keyword_category")
    if keyword and keyword in category_ids_by_name:
        return {"category_id": category_ids_by_name[keyword], "kind": "operating", "source": "keyword"}
    return None


def default_description(item):
    if item.get("description"):
        return item["description"]
    if item.get("account"):
        return f"Account {item['account']}"
    if item.get("bank_txn"):
        return f"Bank transaction {item['bank_txn']}"
    return None


def build_review(message_items, cash_items, rules, existing_references, categories):
    """Every pasted item with a review status and a stable key:
      ready           a category is suggested (rule, keyword or message type)
      needs_category  nothing known about this payee yet
      duplicate       its code is already saved: skipped
      repeated / reversal / money_in / unrecognised   passed through, never saved
    Message items are keyed by their code, cash lines by 'cash-<n>'."""
    category_ids_by_name = {c["name"]: c["id"] for c in categories if c.get("is_active", True)}
    # Only a fully read expense is keyed by its code: a repeated copy carries
    # the same code and must not share its key.
    keyed = [(item["reference"] if item["status"] == "expense" else f"message-{n}", item)
             for n, item in enumerate(message_items)]
    keyed += [(f"cash-{n}", item) for n, item in enumerate(cash_items)]
    review = []
    for key, item in keyed:
        entry = dict(item, key=key)
        if item["status"] != "expense":
            review.append(entry)
            continue
        if item.get("reference") and item["reference"] in existing_references:
            entry["status"] = "duplicate"
            review.append(entry)
            continue
        suggestion = _suggestion(item, rules, category_ids_by_name)
        entry["suggestion"] = suggestion
        entry["status"] = "ready" if suggestion else "needs_category"
        entry["description"] = default_description(item)
        review.append(entry)
    return review


class ChoiceError(ValueError):
    """A choice from the review screen can't be saved; the message says which
    row and why, for the user to fix."""


def _check_choice(key, choice, active_category_ids):
    category_id = choice.get("category_id")
    if category_id not in active_category_ids:
        raise ChoiceError(f"Pick a category for {key}.")
    if choice.get("kind", "operating") not in KINDS:
        raise ChoiceError(f"{key}: type must be operating or capital.")
    segment = choice.get("segment") or None
    if segment is not None and segment not in SEGMENTS:
        raise ChoiceError(f"{key}: segment must be PPPoE, Hotspot or Shared.")


def _chosen_payee(item, choice):
    """Who was paid: what the user typed or left in the box if their choice
    carries a payee (an emptied box means blank, on purpose); otherwise the
    name the message gave, which may itself be none."""
    if "payee" in choice:
        return (choice["payee"] or "").strip() or None
    return item.get("payee")


def build_rows_to_save(review, choices, categories, cash_date):
    """From the review and the user's choices: the expense rows to insert
    (each fee as its own row) and the supplier rules to remember. Only items
    the user chose and didn't skip are saved; amounts and dates come from the
    review (the server's own reading), never from the choices."""
    active_category_ids = {c["id"] for c in categories if c.get("is_active", True)}
    fee_category_id = next((c["id"] for c in categories if c["name"] == FEE_CATEGORY and c.get("is_active", True)), None)
    rows, rules = [], {}
    for item in review:
        if item["status"] not in ("ready", "needs_category"):
            continue
        choice = choices.get(item["key"])
        if not choice or choice.get("skip"):
            continue
        _check_choice(item["key"], choice, active_category_ids)
        kind = choice.get("kind", "operating")
        description = (choice.get("description") or "").strip() or item.get("description")
        spent_on = cash_date if item["method"] == "cash" else item["spent_on"]
        rows.append({
            "reference": item.get("reference"),
            "spent_on": spent_on,
            "amount": item["amount"],
            "payee": _chosen_payee(item, choice),
            "category_id": choice["category_id"],
            "kind": kind,
            "segment": choice.get("segment") or None,
            "description": description,
            "method": item["method"],
            "source_message": item["source_message"],
            # A category the AI suggested and the user kept, or a message the
            # AI read: either way the row carries the ✨ for good.
            "ai_suggested": bool(choice.get("ai_suggested") or item.get("ai_read")),
        })
        if item.get("fee"):
            if fee_category_id is None:
                raise ChoiceError(f"The '{FEE_CATEGORY}' category is missing or retired, so the fee on {item['key']} can't be saved.")
            rows.append({
                "reference": f"{item['reference']}-FEE",
                "spent_on": spent_on,
                "amount": item["fee"],
                "payee": "Bank" if item["method"] == "bank" else "M-Pesa",
                "category_id": fee_category_id,
                "kind": "operating",
                "segment": None,
                "description": f"Transaction cost for {item['reference']}",
                "method": item["method"],
                "source_message": item["source_message"],
                "ai_suggested": False,
            })
        if choice.get("remember") and item.get("rule_keys"):
            rules[item["rule_keys"][0]] = {"category_id": choice["category_id"], "kind": kind}
    return rows, rules


def total_amount(rows):
    return sum((row["amount"] for row in rows), Decimal("0"))


def summarise_expenses(rows):
    """Totals for the Expenses page: everything not voided, split by kind and
    by category (largest first). Money comes back as Decimal."""
    live = [r for r in rows if not r.get("voided_at")]
    by_category = {}
    for r in live:
        entry = by_category.setdefault(r["category"], {"category": r["category"], "group": r["group"],
                                                       "total": Decimal("0"), "count": 0})
        entry["total"] += Decimal(r["amount"])
        entry["count"] += 1
    return {
        "count": len(live),
        "total": sum((Decimal(r["amount"]) for r in live), Decimal("0")),
        "operating": sum((Decimal(r["amount"]) for r in live if r["kind"] == "operating"), Decimal("0")),
        "capital": sum((Decimal(r["amount"]) for r in live if r["kind"] == "capital"), Decimal("0")),
        "voided_count": len(rows) - len(live),
        "by_category": sorted(by_category.values(), key=lambda e: (-e["total"], e["category"])),
    }


def month_bounds(month_text):
    """'2026-11' -> (date(2026, 11, 1), date(2026, 11, 30)). ValueError if
    it isn't a real month."""
    year, month = (int(part) for part in month_text.split("-"))
    first = date(year, month, 1)
    next_first = date(year + (month == 12), month % 12 + 1, 1)
    return first, next_first - timedelta(days=1)
