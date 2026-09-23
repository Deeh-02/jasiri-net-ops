"""Outbound SMS/WhatsApp for Phase 4.8 alerting. Kept separate from
db/monitoring_alerts.py so the alerting *logic* (debounce, flapping, quiet
hours) never has to know which provider is behind a phone number — only
this file does.

Provider: HostPinnacle (smsportal.hostpinnacle.co.ke), per owner's account
on 2026-09-23. SMS is wired for real against their documented REST API
(https://smsportal.hostpinnacle.co.ke/docs/api/). WhatsApp is NOT — their
public docs describe a separate "WhatsApp Marketing" product with no
publicly documented send endpoint (only reachable from the logged-in
account dashboard), so send_whatsapp() below is a stub that logs and
records 'not_configured' rather than guessing an API shape and silently
failing in production. Wiring it for real needs three things from that
dashboard: the endpoint URL, its parameter names, and an API key/sender id
— once in hand, send_whatsapp() becomes a copy of send_sms() below with
those three things swapped in.

Both functions return (ok: bool, response: dict) and NEVER raise — a
provider outage must not take down alert evaluation or crash the ingest
background task that calls it.
"""
import logging
import os
import re
import requests

log = logging.getLogger(__name__)

SMS_ENDPOINT = "https://smsportal.hostpinnacle.co.ke/SMSApi/send"
SMS_TIMEOUT_SECONDS = 10


def _normalize_kenyan_phone(phone):
    """"0712345678" / "+254712345678" / "254712345678" -> "254712345678",
    the "mobile with country code" HostPinnacle's API expects. Returns None
    for anything that doesn't look like a Kenyan mobile number rather than
    sending a malformed request — a missing/garbled number in `users.phone`
    is data entry, not something worth failing an alert run over."""
    if not phone or not isinstance(phone, str):
        return None
    digits = re.sub(r"[^\d]", "", phone)
    if digits.startswith("254") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return "254" + digits[1:]
    if digits.startswith("7") and len(digits) == 9:
        return "254" + digits
    return None


def send_sms(phone, message):
    """One SMS via HostPinnacle's 'quick' send method (single/comma-separated
    mobile numbers, no group setup needed — see
    https://smsportal.hostpinnacle.co.ke/docs/api/?action=send-sms-batch).
    Auth: HOSTPINNACLE_API_KEY as a header if set, else
    HOSTPINNACLE_PASSWORD as a fallback param — never both. HOSTPINNACLE_USER_ID
    is required EITHER WAY: confirmed empirically 2026-09-23 against
    /SMSApi/info/responsecodes that apiKey alone gets rejected with their
    code 216 "Invalid credentials" — the account's own docs say the header
    is sufficient by itself, it isn't. It also must be the exact lowercase
    query param name `userid`; `userId` (matching this file's other casing)
    silently gets the same 216 rather than a clearer complaint."""
    mobile = _normalize_kenyan_phone(phone)
    if not mobile:
        return False, {"error": f"not a recognizable Kenyan mobile number: {phone!r}"}

    api_key = os.environ.get("HOSTPINNACLE_API_KEY")
    user_id = os.environ.get("HOSTPINNACLE_USER_ID")
    password = os.environ.get("HOSTPINNACLE_PASSWORD")
    sender_id = os.environ.get("HOSTPINNACLE_SENDER_ID")
    if not sender_id or not user_id or not (api_key or password):
        return False, {"error": "HostPinnacle SMS is not configured (missing sender id, user id, or credentials)"}

    # Every alert in db/monitoring_alerts.py uses ⚠/✅/— — none of them
    # GSM-7 — so msgType can't be hardcoded to "text". Confirmed live
    # 2026-09-23: HostPinnacle's "Msg Text and MsgType Mismatch" (171)
    # fires on ANY non-ASCII character sent as msgType=text, even a lone
    # em dash; "unicode" is required whenever that's true, and safe to
    # always use ("text" is a shorter-per-segment optimization we don't
    # need — this app's alert volume doesn't justify keeping msgType
    # right on the edge of breaking every time someone edits a message
    # to add an em dash or emoji).
    params = {
        "sendMethod": "quick",
        "mobile": mobile,
        "msg": message,
        "senderid": sender_id,
        "msgType": "text" if message.isascii() else "unicode",
        "output": "json",
        "userid": user_id,
    }
    headers = {}
    if api_key:
        headers["apiKey"] = api_key
    else:
        params["password"] = password

    try:
        resp = requests.post(SMS_ENDPOINT, data=params, headers=headers, timeout=SMS_TIMEOUT_SECONDS)
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text}
    except requests.RequestException as e:
        log.warning("HostPinnacle SMS send failed for %s: %s", mobile, e)
        return False, {"error": str(e)}

    ok = resp.ok and str(body.get("status", "")).lower() == "success"
    if not ok:
        log.warning("HostPinnacle SMS to %s not accepted: %s", mobile, body)
    return ok, body


def send_whatsapp(phone, message):
    """STUB. See file header — HostPinnacle's WhatsApp API is not publicly
    documented and no credentials for it exist yet. Logs and returns
    (False, ...) rather than sending nothing while claiming success."""
    log.info("WhatsApp send skipped (not configured): would have sent to %s: %s", phone, message)
    return False, {"error": "WhatsApp sending is not yet configured — see db/alert_channels.py header"}
