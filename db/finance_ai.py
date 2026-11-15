"""DeepSeek help on the Expenses review screen (Phase 7B). It SUGGESTS, never
decides: every suggestion is shown with a ✨ and saved only when the user
saves, and nothing it says can set an amount, a code or a date by itself.

What it is asked, in ONE call per paste:
  - a category (from the allowed list only) and type for each business payee
    the app hasn't seen before (paybill, till, payee-side receipts);
  - to point at the amount, date and code in messages the code couldn't read.
    It must quote them exactly as written; verify_reading() then checks each
    quote is really in the message and reads the date itself. A reading that
    doesn't check out is dropped, and the same check runs again on save;
  - the user's optional note, as context for both.

What it is never sent: people paid by Send Money, bank transfers (a masked
account says nothing and may be a person), cash lines, phone numbers,
balances. Accounts go only when they look like a code ("smstopup"), not a
phone number or a person's name.

Off unless FINANCE_AI_ENABLED is true AND DEEPSEEK_API_KEY is set: payee
names leave the server when it's on (spec section 5). With it off, or when a
call fails, the review works exactly as before from rules and the user.
"""
import json
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from db import deepseek

MAX_NOTE_LENGTH = 500
MAX_PAYEES_PER_CALL = 60
MAX_MESSAGES_PER_CALL = 15
FALLBACK_CATEGORY = "Other"


def is_enabled():
    flag = os.environ.get("FINANCE_AI_ENABLED", "").strip().lower()
    return flag in ("1", "true", "yes", "on") and deepseek.is_configured()


# ---- Keeping private details out of what is sent ----

# Kenyan mobiles in every form seen (0712..., 254712..., +254 712...) and the
# masked forms banks use (+254*****7196, 0*****9077).
_PHONE = re.compile(r"(?:\+?254|\b0)\s?[17]\d{2}\s?\d{3}\s?\d{3}\b|\+?254\*+\d+|\b0\*+\d+|\b\d\*+\d+")
# Balance clauses: everything from the phrase to the end of its sentence.
_BALANCE = re.compile(
    r"(?:New M-?PESA balance is|M-?PESA balance is|balance is now|Your account balance is|"
    r"Amount you can transact within the day is|Fuliza M-?PESA outstanding amount is|available balance)"
    r"[^.]*(?:\.\d{2})?\.?", re.IGNORECASE)


def scrub(text):
    """Phone numbers and balances out; everything else as it was."""
    text = _BALANCE.sub("[balance removed]", text or "")
    return _PHONE.sub("[number removed]", text)


def ai_safe_account(account):
    """An account number goes only if it looks like a code: one word, no
    spaces (a name), not a phone number. 'smstopup' yes, '254700000001' no,
    'Jane Doe' no."""
    if not account or not re.fullmatch(r"[A-Za-z0-9_\-]{1,24}", account):
        return None
    if _PHONE.search(account) or re.fullmatch(r"\d{9,}", account):
        return None
    return account


# ---- Building the request ----

PROMPT = """You help a small Kenyan internet service provider (fibre PPPoE customers and \
WiFi hotspots) sort its business expenses. Reply with one JSON object only, no other text.

You get:
- "categories": the only allowed category names.
- "payees": businesses it paid, with how many payments and the total in KSh. For each, \
pick the best category name EXACTLY as written in "categories" (if unsure, use "Other"), \
a "kind": "capital" only for equipment bought to keep for years (routers, OLTs, solar \
panels, vehicles, tools), otherwise "operating", and an optional short "description" \
(at most 8 words) only if the note or the name makes the purpose clear.
- "messages": payment messages the app couldn't read. For each that is clearly money \
PAID OUT, copy from the message exactly as written: "amount_text" (the amount paid, \
without the currency word), "date_text" (the date), "code" (the transaction code or \
reference), and "payee" (the business name, or null). Copy characters exactly; never \
work anything out. If it is not money paid out, or any of those is missing, give null \
for that message.
- "note": what the owner says about this batch; use it as context.

Reply shape:
{"payees": [{"id": "p1", "category": "...", "kind": "operating", "description": null}],
 "messages": [{"id": "m1", "amount_text": "1,500.00", "date_text": "5/11/26", "code": "...", "payee": "..."}]}"""


def build_request(review, note, categories):
    """(payload sent to DeepSeek, id -> group key / item key). Payees are the
    'needs_category' business groups only; messages are the unrecognised ones."""
    groups = {}
    for item in review:
        if item["status"] != "needs_category" or not item.get("ai_may_see_payee"):
            continue
        group_key = item["rule_keys"][0]
        group = groups.setdefault(group_key, {"payee": item["payee"], "account": ai_safe_account(item.get("account")),
                                              "type": item["message_type"], "payments": 0, "total": Decimal("0")})
        group["payments"] += 1
        group["total"] += item["amount"]

    payees, ids = [], {}
    for n, (group_key, group) in enumerate(list(groups.items())[:MAX_PAYEES_PER_CALL], start=1):
        ids[f"p{n}"] = group_key
        payees.append({"id": f"p{n}", "payee": group["payee"], "account": group["account"], "type": group["type"],
                       "payments": group["payments"], "total": str(group["total"])})

    messages = []
    unread = [i for i in review if i["status"] == "unrecognised" and i["key"].startswith("message-")]
    for n, item in enumerate(unread[:MAX_MESSAGES_PER_CALL], start=1):
        ids[f"m{n}"] = item["key"]
        messages.append({"id": f"m{n}", "text": scrub(item["source_message"])[:600]})

    payload = {
        "categories": [c["name"] for c in categories if c.get("is_active", True)],
        "payees": payees,
        "messages": messages,
        "note": scrub((note or "").strip())[:MAX_NOTE_LENGTH] or None,
    }
    return payload, ids


def has_work(payload):
    return bool(payload["payees"] or payload["messages"])


# ---- Reading the reply ----

class BadReply(Exception):
    """DeepSeek answered, but not with the JSON asked for."""


def _json_object(content):
    text = (content or "").strip()
    # Tolerate a ```json fence even though JSON output was requested.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
    except ValueError:
        raise BadReply()
    if not isinstance(value, dict):
        raise BadReply()
    return value


def _short_text(value, limit):
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit] or None


def parse_reply(content, ids, categories):
    """{'payees': {group_key: {category_id, kind, description}},
        'readings': {item_key: {amount_text, date_text, code, payee}}}.
    A category not in the list becomes Other; anything unexpected is dropped."""
    reply = _json_object(content)
    ids_by_name = {c["name"].lower(): c["id"] for c in categories if c.get("is_active", True)}
    fallback_id = ids_by_name.get(FALLBACK_CATEGORY.lower())

    payees = {}
    for entry in reply.get("payees") or []:
        if not isinstance(entry, dict) or not str(entry.get("id", "")).startswith("p"):
            continue
        group_key = ids.get(entry["id"])
        if group_key is None:
            continue
        name = entry.get("category") if isinstance(entry.get("category"), str) else ""
        category_id = ids_by_name.get(name.strip().lower(), fallback_id)
        if category_id is None:
            continue
        payees[group_key] = {
            "category_id": category_id,
            "kind": entry.get("kind") if entry.get("kind") in ("operating", "capital") else "operating",
            "description": _short_text(entry.get("description"), 120),
        }

    readings = {}
    for entry in reply.get("messages") or []:
        if not isinstance(entry, dict) or not str(entry.get("id", "")).startswith("m"):
            continue
        item_key = ids.get(entry["id"])
        if item_key is None:
            continue
        reading = {k: _short_text(entry.get(k), 120) for k in ("amount_text", "date_text", "code", "payee")}
        if reading["amount_text"] and reading["date_text"] and reading["code"]:
            readings[item_key] = reading
    return {"payees": payees, "readings": readings}


# ---- Checking a reading against the message ----

_MONTHS = {m: n for n, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def read_date_text(text):
    """A date the code itself can read: 5/11/26, 05/11/2026, 5-11-2026,
    5.11.2026, 2026-11-05, 5 Nov 2026, 5-Nov-26. Day first, as in Kenya.
    None if it isn't one of those or isn't a real day."""
    text = (text or "").strip()
    try:
        found = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
        if found:
            return date(int(found[1]), int(found[2]), int(found[3]))
        found = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})", text)
        if found:
            year = int(found[3]) + (2000 if len(found[3]) == 2 else 0)
            return date(year, int(found[2]), int(found[1]))
        found = re.fullmatch(r"(\d{1,2})[ \-]([A-Za-z]{3})[A-Za-z]*[ \-,]+(\d{2}|\d{4})", text)
        if found and found[2].lower() in _MONTHS:
            year = int(found[3]) + (2000 if len(found[3]) == 2 else 0)
            return date(year, _MONTHS[found[2].lower()], int(found[1]))
    except ValueError:
        return None
    return None


def verify_reading(source_message, reading, today):
    """The reading as an expense draft if every part of it is really in the
    message, else (None, why). The amount must appear exactly as quoted, as a
    number on its own (not the tail of a longer one); so must the code and the
    date, which the code then reads itself."""
    if not isinstance(reading, dict):
        return None, "No reading."
    flat = re.sub(r"\s+", " ", source_message or "")
    amount_text = (reading.get("amount_text") or "").strip()
    date_text = (reading.get("date_text") or "").strip()
    code = (reading.get("code") or "").strip()

    if not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?", amount_text):
        return None, "The amount isn't a plain number."
    if not re.search(rf"(?<![\d,.]){re.escape(amount_text)}(?![\d,]|\.\d)", flat):
        return None, "That amount isn't in the message."
    try:
        amount = Decimal(amount_text.replace(",", ""))
    except InvalidOperation:
        return None, "The amount isn't a plain number."
    if amount <= 0:
        return None, "The amount must be more than zero."

    if not re.fullmatch(r"[A-Za-z0-9]{6,24}", code) or not re.search(rf"\b{re.escape(code)}\b", flat):
        return None, "That code isn't in the message."

    if date_text not in flat:
        return None, "That date isn't in the message."
    spent_on = read_date_text(date_text)
    if spent_on is None:
        return None, "The date couldn't be read."
    if spent_on > today or spent_on.year < 2020:
        return None, "The date is in the future or too far back."

    payee = _short_text(reading.get("payee"), 120)
    if payee:
        payee = _PHONE.sub("", payee).strip(" -,") or None
    return {
        "status": "expense", "message_type": "ai_read", "reference": code.upper(), "spent_on": spent_on,
        "amount": amount, "fee": None, "payee": payee, "label": payee or "Read by AI", "account": None,
        "bank_txn": None, "is_person": False, "ai_may_see_payee": False,
        "method": "mpesa" if re.fullmatch(r"[A-Z0-9]{10}", code.upper()) else None,
        "rule_keys": [], "source_message": source_message, "warnings": [], "ai_read": True,
    }, None


def apply_readings(review, choices, today):
    """On save: each unrecognised message whose choice carries an AI reading
    becomes an expense draft again, if the reading still checks out against
    the message. Returns the new review list and any problems by key."""
    updated, problems = [], {}
    for item in review:
        reading = (choices.get(item["key"]) or {}).get("ai_reading")
        if item["status"] != "unrecognised" or not reading:
            updated.append(item)
            continue
        draft, why = verify_reading(item["source_message"], reading, today)
        if draft is None:
            problems[item["key"]] = why
            updated.append(item)
            continue
        updated.append(dict(draft, key=item["key"], status="needs_category", suggestion=None, description=None))
    return updated, problems


# ---- The call ----

def suggest(review, note, categories, today):
    """One DeepSeek call for the whole paste. Returns
    {'payees': ..., 'readings': {key: {..., 'amount', 'spent_on', ...}}};
    readings that don't check out against their message are left out.
    Raises deepseek.DeepSeekError or BadReply."""
    payload, ids = build_request(review, note, categories)
    if not has_work(payload):
        return {"payees": {}, "readings": {}}
    reply = deepseek.chat(
        [{"role": "system", "content": PROMPT},
         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        timeout_seconds=40, max_tokens=2500, json_output=True,
    )
    parsed = parse_reply(reply.get("content"), ids, categories)
    sources = {item["key"]: item["source_message"] for item in review}
    readings = {}
    for key, reading in parsed["readings"].items():
        draft, _ = verify_reading(sources.get(key, ""), reading, today)
        if draft:
            readings[key] = dict(reading, amount=draft["amount"], spent_on=draft["spent_on"],
                                 reference=draft["reference"], payee=draft["payee"])
    return {"payees": parsed["payees"], "readings": readings}

