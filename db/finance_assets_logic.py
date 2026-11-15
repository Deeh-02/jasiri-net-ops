"""Finance > Assets rules (Phase 7F), as pure functions. Tested in
tests/test_finance_assets.py.

An asset is a serialised Inventory item. Its cost is Inventory's own unit
cost; Finance adds the purchase date, useful life, salvage and warranty
(asset_values), with a default life and salvage per category
(asset_category_lives).

Depreciation is straight-line, a management figure only (tax treatment is
for the accountant): (cost - salvage) / life each month, from the month of
purchase, for `life` months. A month is counted whole.

Missing is never invented (owner's call 2026-11-15, option A): with no
purchase date, no life or no cost, an item has no book value and no
depreciation, and says what it is missing.
"""
from datetime import date, timedelta
from decimal import Decimal

ZERO = Decimal("0")
RETIRED = {"Decommissioned"}
DEPLOYED = {"Active"}
IN_STOCK = {"Spare — In Storage", "Spare - In Storage", "Spare"}
NEEDS_REPAIR = {"Faulty", "In Repair"}
WARRANTY_WARNING_DAYS = 60


def _month_index(day):
    return day.year * 12 + day.month - 1


def terms(item, value, category_life):
    """The figures depreciation needs, each from the item's own Finance row
    first, then its category default. `missing` names what isn't known."""
    value = value or {}
    cost = Decimal(str(item["unit_cost"])) if item.get("unit_cost") is not None else None
    life = value.get("life_months") or (category_life or {}).get("life_months")
    if value.get("salvage") is not None:
        salvage = Decimal(str(value["salvage"]))
    elif cost is not None and category_life:
        salvage = (cost * Decimal(str(category_life["salvage_pct"])) / 100).quantize(Decimal("0.01"))
    else:
        salvage = ZERO
    purchased_on = value.get("purchased_on")
    missing = [name for name, v in (("cost", cost), ("purchase date", purchased_on), ("useful life", life)) if v is None]
    return {"cost": cost, "life_months": life, "salvage": salvage, "purchased_on": purchased_on, "missing": missing}


def monthly_charge(t):
    if t["missing"]:
        return None
    return ((t["cost"] - t["salvage"]) / t["life_months"]).quantize(Decimal("0.01"))


def months_used(t, as_of):
    """Whole months of life used up to the end of as_of's month (0 before
    the purchase month, never more than the life)."""
    used = _month_index(as_of) - _month_index(t["purchased_on"]) + 1
    return max(0, min(t["life_months"], used))


def book_value(t, as_of):
    """Cost less depreciation to the end of as_of's month. None if a figure
    is missing. A fully depreciated item stays at its salvage value."""
    charge = monthly_charge(t)
    if charge is None:
        return None
    used = months_used(t, as_of)
    if used >= t["life_months"]:
        return t["salvage"]
    return (t["cost"] - charge * used).quantize(Decimal("0.01"))


def depreciation_in_month(t, month_first_day):
    """This item's charge for one month: the monthly charge while it is
    within its life, otherwise 0; None if a figure is missing."""
    charge = monthly_charge(t)
    if charge is None:
        return None
    index = _month_index(month_first_day) - _month_index(t["purchased_on"])
    if index < 0 or index >= t["life_months"]:
        return ZERO
    # The last month takes the rounding left over, so the total is exact.
    if index == t["life_months"] - 1:
        return (t["cost"] - t["salvage"]) - charge * (t["life_months"] - 1)
    return charge


def month_depreciation(assets, month_first_day):
    """Total depreciation for a month across assets with all their figures,
    and how many assets were left out for missing ones. Retired items still
    count for the months before they were retired only if a date says so;
    without one they are left out, like Inventory leaves them out."""
    total, counted, left_out = ZERO, 0, 0
    for a in assets:
        if a["status"] in RETIRED:
            continue
        charge = depreciation_in_month(a["terms"], month_first_day)
        if charge is None:
            left_out += 1
            continue
        total += charge
        counted += 1
    return {"total": total, "counted": counted, "left_out": left_out}


def describe(item, value, category_life, today):
    """One asset as the page shows it."""
    t = terms(item, value, category_life)
    warranty = (value or {}).get("warranty_until")
    return {
        "id": item["id"], "serial": item.get("serial_number"), "name": item["name"],
        "make_model": item.get("make_model"), "category_id": item["category_id"], "category": item["category_name"],
        "status": item.get("asset_status") or "Unknown", "location": item.get("location_name"),
        "supplier": item.get("supplier"), "segment": (value or {}).get("segment"),
        "expense_id": (value or {}).get("expense_id"), "warranty_until": warranty,
        "terms": t, "monthly": monthly_charge(t),
        "book_value": None if item.get("asset_status") in RETIRED else book_value(t, today),
        "depreciated": None if monthly_charge(t) is None else t["cost"] - (book_value(t, today) or ZERO),
        "warranty_ending": bool(warranty and today <= warranty <= today + timedelta(days=WARRANTY_WARNING_DAYS)),
        "life_override": (value or {}).get("life_months") is not None,
        "salvage_override": (value or {}).get("salvage") is not None,
    }


def summary(assets, today):
    live = [a for a in assets if a["status"] not in RETIRED]
    with_value = [a for a in live if a["book_value"] is not None]
    return {
        "count": len(live),
        "total_cost": sum((a["terms"]["cost"] for a in live if a["terms"]["cost"] is not None), ZERO),
        "book_value": sum((a["book_value"] for a in with_value), ZERO) if with_value else None,
        "valued": len(with_value),
        "deployed": sum(1 for a in live if a["status"] in DEPLOYED),
        "in_stock": sum(1 for a in live if a["status"] in IN_STOCK),
        "needs_repair": sum(1 for a in live if a["status"] in NEEDS_REPAIR),
        "warranty_ending": sum(1 for a in live if a["warranty_ending"]),
        "this_month": month_depreciation(assets, today.replace(day=1)),
        "retired": len(assets) - len(live),
    }


def alerts(assets):
    """What on the Assets page needs someone to look (spec section 7)."""
    live = [a for a in assets if a["status"] not in RETIRED]
    out = []
    no_date = [a for a in live if "purchase date" in a["terms"]["missing"]]
    if no_date:
        out.append({"level": "warn", "kind": "no_date",
                    "title": f"{len(no_date)} item{'s have' if len(no_date) != 1 else ' has'} no purchase date",
                    "detail": "No book value or depreciation is worked out without one. Set a date for a whole "
                              "category under Categories below, or per item."})
    no_life = [a for a in live if "useful life" in a["terms"]["missing"]]
    if no_life:
        out.append({"level": "warn", "kind": "no_life",
                    "title": f"{len(no_life)} item{'s have' if len(no_life) != 1 else ' has'} no useful life",
                    "detail": "Their category has no default life. Set one under Categories below."})
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
