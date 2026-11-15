"""Reads pasted M-Pesa and bank SMS (and one-line cash notes) into expense
drafts. Pure: no database, no clock (today is passed in), no AI. Amounts,
codes and dates come only from these patterns; anything that doesn't match
one exactly is returned as "unrecognised" with its text, never guessed at.

Formats handled (seen in real messages, 2026-11-15):
  paybill           QA1BC2DE3F Confirmed. Ksh1,946.00 sent to SUPPLIER for account 123 on 13/10/26 at 7:24 AM
  till              QA1BC2DE4G Confirmed. Ksh500.00 paid to Sample Petroleum Limited.. on 14/10/26 at 1:40 PM.
  send_money        QA1BC2DE5H Confirmed. Ksh5,000.00 sent to JANE  DOE 0700000002 on 17/10/26 at 1:56 PM.
  merchant_receipt  KES 16,640.00 paid to SUPPLIER LIMITED (Acc 400001) on 19/10/26 at 01:33 PM Ref: QA1BC2DE7K.
  bank_transfer     Dear NAME, Transaction Ref: 20090000001 of KES 1,500.00 from A/c 1*****0001 to A/c 0*****9001
                    on 07/10/2026 is Completed Bank Ref: BXXXXXXXXXX01.
  airtime           QA1BC2DE9M confirmed.You bought Ksh100.00 of airtime on 5/11/26 at 10:15 AM.
(Codes, names and numbers above are made up.) A full M-Pesa message may
also carry "Transaction cost, Ksh23.00", read as the fee. Messages forwarded
through WhatsApp keep their "[10/13, 7:15 AM] Name: " prefix, which is
removed first.
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

# ---- Splitting a paste into single messages ----

# "[10/13, 7:15 AM] Bro: " (copied from WhatsApp) or "13/10/2026, 07:15 - Bro: "
# (WhatsApp's chat export). The name part is kept short so a colon inside a
# real message can't be mistaken for one.
_WHATSAPP_PREFIX = re.compile(
    r"^\s*(?:\[\d{1,2}/\d{1,2}(?:/\d{2,4})?,\s*\d{1,2}:\d{2}(?:\s?[AaPp][Mm])?\]"
    r"|\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}(?:\s?[AaPp][Mm])?\s+-)\s*[^:\n]{1,40}:\s*"
)

# Where a new message begins when several are pasted without blank lines
# between them.
_MESSAGE_START = re.compile(
    r"(?=\b[A-Z0-9]{10}\s+[Cc]onfirmed)|(?=\bDear\s)|(?=\bKES\s?[\d,]+(?:\.\d{2})?\s+paid to\b)"
)


def split_messages(text):
    """One string per message. A WhatsApp prefix or a blank line ends the
    previous message; so does the start of another M-Pesa or bank message."""
    blocks, current = [], []
    for line in (text or "").splitlines():
        without_prefix = _WHATSAPP_PREFIX.sub("", line, count=1)
        if without_prefix != line or not line.strip():
            if current:
                blocks.append(" ".join(current))
            current = [without_prefix.strip()] if without_prefix.strip() else []
            continue
        current.append(line.strip())
    if current:
        blocks.append(" ".join(current))

    messages = []
    for block in blocks:
        for piece in _MESSAGE_START.split(block):
            if piece.strip():
                messages.append(piece.strip())
    return messages


# ---- Reading one message ----

_CODE = r"(?P<code>[A-Z0-9]{10})"
_AMOUNT = r"(?:Ksh|KES)\s?(?P<amount>\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?)"
_MPESA_WHEN = r"on (?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{2}) at (?P<time>\d{1,2}:\d{2}\s?[AaPp][Mm])"
_CONFIRMED = rf"^{_CODE}\s+(?i:confirmed)\.?\s*"

_PATTERNS = [
    # Order matters: a paybill also looks like send money, so it is tried first.
    ("paybill", re.compile(rf"{_CONFIRMED}{_AMOUNT} sent to (?P<payee>.+?) for account (?P<account>.+?) {_MPESA_WHEN}")),
    ("send_money", re.compile(rf"{_CONFIRMED}{_AMOUNT} sent to (?P<payee>.+?)(?: (?P<phone>(?:\+?254|0)\d{{9}}))? {_MPESA_WHEN}")),
    ("till", re.compile(rf"{_CONFIRMED}{_AMOUNT} paid to (?P<payee>.+?)\.* {_MPESA_WHEN}")),
    ("merchant_receipt", re.compile(
        rf"^{_AMOUNT} paid to (?P<payee>.+?)(?: \(Acc (?P<account>[^)]+)\))? {_MPESA_WHEN} Ref: {_CODE}")),
    ("bank_transfer", re.compile(
        rf"Transaction Ref: (?P<bank_txn>\d+) of {_AMOUNT} from A/c (?P<from_account>\S+) to A/c (?P<to_account>\S+) "
        r"on (?P<day>\d{2})/(?P<month>\d{2})/(?P<year>\d{4}) is Completed\.? Bank Ref: (?P<code>[A-Z0-9]+)")),
    ("airtime", re.compile(rf"{_CONFIRMED}You bought {_AMOUNT} of airtime(?: for (?P<phone>\d+))? {_MPESA_WHEN}")),
]

_FEE = re.compile(r"Transaction cost,?\s*(?:Ksh|KES)\s?(?P<fee>[\d,]+(?:\.\d{2})?)")
_REVERSAL = re.compile(r"revers", re.IGNORECASE)
_MONEY_IN = re.compile(r"You have received|received (?:Ksh|KES)", re.IGNORECASE)

# Payees whose name says nothing to an outside service and may be a private
# person: never sent to the AI (the user classifies them once).
_PERSON_TYPES = {"send_money"}
# Business names the AI may later be asked about (Phase 7B DeepSeek step).
_BUSINESS_TYPES = {"paybill", "till", "merchant_receipt"}


def _money(text):
    try:
        return Decimal(text.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def clean_payee(name):
    """'Servco Petroleum Limited..' -> 'Servco Petroleum Limited'; double
    spaces collapsed. What the user sees."""
    return re.sub(r"\s+", " ", name or "").strip().rstrip(".").strip()


def payee_key(name):
    """The name a supplier rule is stored under: upper case, single spaces,
    no trailing dots. 'Servco Petroleum Limited..' -> 'SERVCO PETROLEUM LIMITED'."""
    return clean_payee(name).upper()


def _message_date(found, message_type):
    year = int(found["year"])
    if message_type != "bank_transfer":
        year += 2000
    try:
        return date(year, int(found["month"]), int(found["day"]))
    except ValueError:
        return None


def _payee_for(message_type, found):
    if message_type == "bank_transfer":
        to_account = found["to_account"]
        if to_account.startswith("+254") or to_account.startswith("254"):
            return f"Bank to M-Pesa {to_account}", True
        return f"Bank A/c {to_account}", False
    if message_type == "airtime":
        return "Safaricom airtime", False
    return clean_payee(found["payee"]), message_type in _PERSON_TYPES


def read_message(text, today):
    """One message in, one result out. result['status'] is one of:
      expense       read completely; amount, code and date are from the text
      reversal      mentions a reversal: shown for the user to act on, never saved
      money_in      money received, not an expense
      unrecognised  no pattern matched; the text is shown as it is
    """
    flat = re.sub(r"\s+", " ", text).strip()
    if _REVERSAL.search(flat):
        return {"status": "reversal", "source_message": text}
    if _MONEY_IN.search(flat):
        return {"status": "money_in", "source_message": text}

    for message_type, pattern in _PATTERNS:
        found = pattern.search(flat)
        if not found:
            continue
        amount = _money(found["amount"])
        spent_on = _message_date(found, message_type)
        if amount is None or amount <= 0 or spent_on is None:
            return {"status": "unrecognised", "source_message": text,
                    "reason": "The amount or date in this message couldn't be read."}
        payee, is_person = _payee_for(message_type, found)
        groups = found.groupdict()
        account = clean_payee(groups.get("account")) or None
        fee_found = _FEE.search(flat)
        fee = _money(fee_found["fee"]) if fee_found else None

        key = payee_key(payee)
        rule_keys = [f"{key}|{account.upper()}", key] if account else [key]

        warnings = []
        if spent_on > today:
            warnings.append("The date in this message is in the future. Check it against the phone.")
        return {
            "status": "expense",
            "message_type": message_type,
            "reference": found["code"],
            "spent_on": spent_on,
            "time": groups.get("time"),
            "amount": amount,
            "fee": fee if fee and fee > 0 else None,
            "payee": payee,
            "account": account,
            "bank_txn": groups.get("bank_txn"),
            "is_person": is_person,
            "ai_may_see_payee": message_type in _BUSINESS_TYPES,
            "method": "bank" if message_type == "bank_transfer" else "mpesa",
            "rule_keys": rule_keys,
            "source_message": text,
            "warnings": warnings,
        }

    return {"status": "unrecognised", "source_message": text,
            "reason": "This doesn't look like an M-Pesa or bank payment message the app knows."}


def read_messages(text, today):
    """Every message in a paste, in order. The same code pasted twice in one
    go is returned once, the second copy marked 'repeated'."""
    results, seen = [], set()
    for message in split_messages(text):
        result = read_message(message, today)
        reference = result.get("reference")
        if reference and reference in seen:
            result = {"status": "repeated", "reference": reference, "source_message": message}
        elif reference:
            seen.add(reference)
        results.append(result)
    return results


# ---- Cash: one line per expense, e.g. "fuel 2000" ----

# Which category a cash line's words point to. Checked in order; the first
# word found wins. Category names must match expense_categories exactly; a
# name that isn't there just leaves the line uncategorised.
CASH_KEYWORDS = [
    (("fuel", "petrol", "diesel", "fare", "transport", "boda", "matatu", "parking"), "Transport & fuel"),
    (("airtime", "sms", "bundle", "bundles", "data"), "Airtime & SMS"),
    (("salary", "salaries", "wage", "wages"), "Salaries"),
    (("commission", "commissions"), "Agent commissions"),
    (("rent",), "Office rent"),
    (("token", "tokens", "kplc", "power", "electricity", "solar", "battery", "batteries"), "Power & solar"),
    (("repair", "repairs", "spares", "connector", "connectors", "patchcord", "splice"), "Spares & repairs"),
    (("bandwidth", "upstream", "internet"), "Upstream bandwidth"),
    (("licence", "license", "permit", "tax", "kra", "ca"), "Licences & taxes"),
    (("advert", "adverts", "posters", "flyers", "marketing"), "Marketing"),
]

_CASH_NUMBER = re.compile(r"(?:(?:ksh|kes)\s?)?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?(?:\s?/=)?", re.IGNORECASE)
_CASH_MARKED = re.compile(r"(?:ksh|kes)\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{2})?)|(\d{1,3}(?:,\d{3})+|\d+)\s?/=", re.IGNORECASE)


def cash_category(description):
    words = set(re.findall(r"[a-z]+", description.lower()))
    for keywords, category in CASH_KEYWORDS:
        if words & set(keywords):
            return category
    return None


def read_cash_line(line):
    """'fuel 2000' -> amount 2000, description 'fuel'. With more than one
    number the amount must be marked ('ksh 2000' or '2000/='): the app
    never guesses which number is the money."""
    text = re.sub(r"\s+", " ", line).strip()
    numbers = _CASH_NUMBER.findall(text)
    if not numbers:
        return {"status": "unrecognised", "source_message": line,
                "reason": "No amount found. Write it like: fuel 2000"}
    if len(numbers) == 1:
        amount_text = numbers[0][0] + ("." + numbers[0][1] if numbers[0][1] else "")
        description = _CASH_NUMBER.sub("", text, count=1)
    else:
        marked = _CASH_MARKED.findall(text)
        if len(marked) != 1:
            return {"status": "unrecognised", "source_message": line,
                    "reason": "More than one number: mark the amount, like: 2 litres oil ksh 500"}
        amount_text = marked[0][0] or marked[0][1]
        description = _CASH_MARKED.sub("", text, count=1)
    amount = _money(amount_text)
    description = re.sub(r"\s+", " ", description).strip(" -,:")
    if amount is None or amount <= 0:
        return {"status": "unrecognised", "source_message": line, "reason": "The amount must be more than zero."}
    if not description:
        return {"status": "unrecognised", "source_message": line,
                "reason": "Say what the money was for, like: fuel 2000"}
    return {"status": "expense", "message_type": "cash", "reference": None, "amount": amount,
            "fee": None, "payee": None, "description": description, "method": "cash",
            "is_person": False, "ai_may_see_payee": False, "rule_keys": [],
            "keyword_category": cash_category(description), "source_message": line, "warnings": []}


def read_cash_lines(text):
    return [read_cash_line(line) for line in (text or "").splitlines() if line.strip()]
