"""Finance > Assets rules (Phase 7F), as pure functions. Tested in
tests/test_finance_assets.py.

An asset is a serialised Inventory item. Its cost, status and location are
Inventory's own; Finance keeps a few money facts beside it (asset_values):
when it was bought, until when its warranty runs, which segment it serves,
and the capital expense that paid for it.

No book value or depreciation (owner's call, 2026-11-16): the useful lives
they rested on were guesses, so the figures couldn't be checked. Tax
allowances on equipment are the accountant's. The purchase date is
optional: it gives an item's age and nothing is flagged without it. (The
asset_category_lives table and the life/salvage columns stay in the database,
unused, in case the accountant ever wants them.)
"""
from datetime import timedelta
from decimal import Decimal

ZERO = Decimal("0")
RETIRED = {"Decommissioned"}
DEPLOYED = {"Active"}
IN_STOCK = {"Spare — In Storage", "Spare - In Storage", "Spare"}
NEEDS_REPAIR = {"Faulty", "In Repair"}
WARRANTY_WARNING_DAYS = 60


def age_months(purchased_on, today):
    """Whole months since the item was bought (0 in its first month); None
    without a purchase date."""
    if purchased_on is None:
        return None
    months = (today.year - purchased_on.year) * 12 + today.month - purchased_on.month
    if today.day < purchased_on.day:
        months -= 1
    return max(0, months)


def describe(item, value, today):
    """One asset as the page shows it."""
    value = value or {}
    warranty = value.get("warranty_until")
    return {
        "id": item["id"], "serial": item.get("serial_number"), "name": item["name"],
        "make_model": item.get("make_model"), "category_id": item["category_id"], "category": item["category_name"],
        "status": item.get("asset_status") or "Unknown", "location": item.get("location_name"),
        "supplier": item.get("supplier"), "segment": value.get("segment"), "expense_id": value.get("expense_id"),
        "cost": Decimal(str(item["unit_cost"])) if item.get("unit_cost") is not None else None,
        "purchased_on": value.get("purchased_on"), "age_months": age_months(value.get("purchased_on"), today),
        "warranty_until": warranty,
        "warranty_ending": bool(warranty and today <= warranty <= today + timedelta(days=WARRANTY_WARNING_DAYS)),
    }


def summary(assets):
    live = [a for a in assets if a["status"] not in RETIRED]
    return {
        "count": len(live),
        "total_cost": sum((a["cost"] for a in live if a["cost"] is not None), ZERO),
        "deployed": sum(1 for a in live if a["status"] in DEPLOYED),
        "in_stock": sum(1 for a in live if a["status"] in IN_STOCK),
        "needs_repair": sum(1 for a in live if a["status"] in NEEDS_REPAIR),
        "warranty_ending": sum(1 for a in live if a["warranty_ending"]),
        "retired": len(assets) - len(live),
    }


def alerts(assets):
    """What on the Assets page needs someone to look (spec section 7):
    warranties about to run out, faulty equipment, equipment with no
    location. A missing purchase date is not one: the date is optional."""
    live = [a for a in assets if a["status"] not in RETIRED]
    out = []
    for a in sorted((a for a in live if a["warranty_ending"]), key=lambda a: a["warranty_until"]):
        out.append({"level": "warn", "kind": "warranty", "item_id": a["id"],
                    "title": f"Warranty ends {a['warranty_until']:%-d %b %Y}: {a['name']} ({a['serial'] or 'no serial'})",
                    "detail": "Claim anything faulty before then."})
    repair = [a for a in live if a["status"] in NEEDS_REPAIR]
    if repair:
        out.append({"level": "bad", "kind": "repair",
                    "title": f"{len(repair)} item{'s are' if len(repair) != 1 else ' is'} faulty or in repair",
                    "detail": ", ".join(f"{a['name']} ({a['serial'] or 'no serial'})" for a in repair[:5])
                              + (" and more" if len(repair) > 5 else "")})
    no_location = [a for a in live if not a["location"]]
    if no_location:
        out.append({"level": "info", "kind": "no_location",
                    "title": f"{len(no_location)} item{'s have' if len(no_location) != 1 else ' has'} no location",
                    "detail": "Equipment without a location is easy to lose. Set it in Inventory."})
    return out
