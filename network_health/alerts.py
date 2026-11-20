"""Which fires and clears become an SMS, and sending them.

plan() is pure: it gets the rule events and what was already sent today,
and returns one log row per SMS-worthy event, either to send ('pending') or
'suppressed' with the reason. The cost rules, in the order they apply:

  1. Only SMS rules (config.SMS_RULES) and anything at RED; everything else
     is dashboard only and gets no row at all.
  2. Old readings never text (a backfill replays history).
  3. Flapping: a rule firing again within 30 min of clearing is shown on the
     page, not texted; its clear isn't either.
  4. NH_SMS_MODE=log_only (the first week): logged, not sent.
  5. A clear is only texted if its fire was.
  6. 3+ fires on one device within 2 min -> one combined SMS.
  7. NH_SMS_DAILY_CAP per day (EAT); the first one over the cap is replaced
     by one "alerts paused until midnight" SMS.

deliver() sends the planned rows with db/alert_channels.send_sms, the one
function this feature may use from outside (see the isolation test). It
never raises: a failed SMS is logged as 'failed' and ingest carries on.
"""
import logging
import os

from db.alert_channels import send_sms

from network_health import config, messages
from network_health.rules import RED, SEVERITY_RANK

log = logging.getLogger(__name__)


# ---- Settings from the environment (never stored in the database) ----

def alert_phones():
    """NH_ALERT_PHONES: comma-separated numbers. Never logged or shown."""
    return [p.strip() for p in (os.environ.get("NH_ALERT_PHONES") or "").split(",") if p.strip()]


def sms_mode():
    """'live' sends; anything else (default 'log_only') only logs."""
    return "live" if (os.environ.get("NH_SMS_MODE") or "").strip().lower() == "live" else "log_only"


def daily_cap():
    try:
        return max(int(os.environ.get("NH_SMS_DAILY_CAP", config.DEFAULT_SMS_DAILY_CAP)), 1)
    except ValueError:
        log.warning("NH_SMS_DAILY_CAP is not a whole number; using %s", config.DEFAULT_SMS_DAILY_CAP)
        return config.DEFAULT_SMS_DAILY_CAP


# ---- Planning (pure) ----

def is_sms_worthy(event):
    return event["rule"] in config.SMS_RULES or event.get("peak_severity") == RED or event["severity"] == RED


def _row(kind, device, rule, message, incident_ids, outcome="pending", reason=None):
    return {"kind": kind, "device": device, "rule": rule, "message": message, "incident_ids": incident_ids,
            "outcome": outcome, "reason": reason}


def _single_row(event):
    if event["kind"] == "fire":
        message = messages.fire_message(event["device"], event["rule"], event["facts"], event["started_at"])
    else:
        lasted = (event["ended_at"] - event["started_at"]).total_seconds() / 60
        message = messages.clear_message(event["device"], event["rule"], event["subject"], lasted)
    return _row(event["kind"], event["device"], event["rule"], message, [event["incident_id"]])


def _suppression(event, context):
    """Why this event must not be texted, or None."""
    age_minutes = (context["now"] - event["record_ts"]).total_seconds() / 60
    if age_minutes > config.SMS_MAX_RECORD_AGE_MINUTES:
        return "old reading (backfill)"
    if event["is_flapping"]:
        return "flapping"
    if context["mode"] != "live":
        return "log-only mode"
    if event["kind"] == "clear" and not event["fire_sms_sent"]:
        return "its alert was not sent"
    if not context["recipients"]:
        return "NH_ALERT_PHONES is empty"
    return None


def _combine(device, kind, events, recent_fires):
    """One device's fires (or clears) from this pass -> the rows to send."""
    if kind == "fire" and len(events) + recent_fires >= config.COMBINE_AT:
        worst = max(events, key=lambda e: SEVERITY_RANK[e["severity"]])
        message = messages.combined_fire_message(device, len(events) + recent_fires, worst["rule"])
        return [_row("combined", device, worst["rule"], message, [e["incident_id"] for e in events])]
    if kind == "clear" and len(events) >= config.COMBINE_AT:
        message = messages.combined_clear_message(device, len(events))
        return [_row("combined", device, "CLEARED", message, [e["incident_id"] for e in events])]
    return [_single_row(event) for event in events]


def plan(events, context):
    """events: dicts from service.py (kind fire|clear, device, rule, subject,
    severity, facts, started_at, ended_at, record_ts, incident_id,
    is_flapping, fire_sms_sent). context: now, mode, cap, sent_today,
    cap_notice_sent, recent_fires {device: count}, recipients (count).
    Returns the log rows, in order."""
    rows, to_send = [], {}
    for event in events:
        if not is_sms_worthy(event):
            continue
        reason = _suppression(event, context)
        if reason:
            row = _single_row(event)
            row.update(outcome="suppressed", reason=reason)
            rows.append(row)
        else:
            to_send.setdefault((event["device"], event["kind"]), []).append(event)
    sent_today, cap_notice_sent = context["sent_today"], context["cap_notice_sent"]
    for (device, kind), group in to_send.items():
        for row in _combine(device, kind, group, context["recent_fires"].get(device, 0)):
            if sent_today >= context["cap"]:
                row.update(outcome="suppressed", reason="daily cap reached")
                if not cap_notice_sent:
                    rows.append(_row("cap", device, "CAP", messages.cap_message(context["cap"]), []))
                    cap_notice_sent = True
            else:
                sent_today += 1
            rows.append(row)
    return rows


# ---- Sending ----

def deliver(message, phones, sender=send_sms):
    """Sends one message to every phone. Returns (outcome, reason):
    'sent' if at least one phone accepted it, else 'failed'. Never raises."""
    errors = []
    accepted = 0
    for phone in phones:
        try:
            ok, response = sender(phone, message)
        except Exception as e:          # send_sms promises not to raise; don't rely on it
            ok, response = False, {"error": str(e)}
        if ok:
            accepted += 1
        else:
            errors.append(str((response or {}).get("error") or response)[:200])
    if accepted:
        return "sent", (None if not errors else f"{len(errors)} of {len(phones)} numbers failed: {errors[0]}")
    return "failed", (errors[0] if errors else "no recipients")
