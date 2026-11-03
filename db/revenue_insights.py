"""Revenue insights — the AI analysis panel on the Revenue Report.

DeepSeek writes the WORDS; the report writes every figure (see
db/revenue_insights_logic.py for the placeholder rules). This module is the
glue: gather the same figures the page shows, decide whether the model needs
to be asked again, ask it once, and keep the answer for everyone to share.

WHEN THE MODEL IS CALLED. Page polling never calls it by itself. For each
(period, date, comparison, card choices) the wording is regenerated only when
the figures have changed AND at least INSIGHTS_MIN_INTERVAL_SECONDS (default
600) have passed since the last attempt. Ten admins on the same view share
one entry, and while one request is generating, the others get the existing
text (or "preparing") instead of making a second call.

WHAT IS KEPT. The wording with its placeholders — not the finished sentences.
Every request fills the placeholders from today's figures, so the panel's
numbers always equal the cards' even though the wording may be a few minutes
old. The cache lives in this process's memory: a restart (or a second worker)
just means one extra generation, never a wrong number.

Nothing here writes to the database. Audit lines (time, view key, status; never
the text) go to the log.
"""
import logging
import os
import threading
from datetime import datetime, timezone

from db import deepseek, revenue_alerts, revenue_report
from db.revenue_insights_logic import (InsightsInvalid, build_figures, build_messages, figures_hash, fill_in,
                                       parse_model_output, placeholder_values, sites_from_ids)

log = logging.getLogger(__name__)

DEFAULT_MIN_INTERVAL_SECONDS = 600
# After a failed attempt, wait this long before trying again, so a DeepSeek
# outage costs one call per couple of minutes, not one per page poll.
RETRY_AFTER_FAILURE_SECONDS = 120
MAX_CACHED_VIEWS = 50

_lock = threading.Lock()
_cache = {}       # view key -> {hash, template, site_ids, generated_at, attempted_at}
_generating = set()


def _seconds_setting(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        log.error("%s must be a whole number of seconds; using %s", name, default)
        return default


def gather(period, anchor, compare, modes, custom_anchor=None):
    """The figures behind the panel, from the SAME report functions the page's
    own endpoints call. Returns (figures, site_rows)."""
    overview = revenue_report.overview(modes=modes)
    sites = revenue_report.sites_report(period, anchor, mode=compare or None, custom_anchor=custom_anchor)
    alerts = revenue_alerts.report()
    return build_figures(overview, sites, alerts), sites["sites"]


def _view_key(period, anchor, compare, modes, custom_anchor=None):
    return "|".join([period, str(anchor or ""), compare or "", str(custom_anchor or ""),
                     ",".join(f"{k}={v}" for k, v in sorted(modes.items()))])


def _due(entry, new_hash, now):
    """Is it time to ask the model again for this view?"""
    if entry is None:
        return True
    waited = (now - entry["attempted_at"]).total_seconds()
    if entry["template"] is None or entry.get("failed"):
        return waited >= RETRY_AFTER_FAILURE_SECONDS
    return entry["hash"] != new_hash and waited >= _seconds_setting("INSIGHTS_MIN_INTERVAL_SECONDS",
                                                                    DEFAULT_MIN_INTERVAL_SECONDS)


def generate_template(figures):
    """Ask DeepSeek for the wording; one retry if it breaks the rules (a digit
    or an unknown placeholder). Returns the template, or None. Raises nothing."""
    allowed = set(placeholder_values(figures))
    timeout = _seconds_setting("INSIGHTS_TIMEOUT_SECONDS", 30) or 30
    for attempt in (1, 2):
        try:
            raw = deepseek.chat_json(build_messages(figures), timeout_seconds=timeout)
            return parse_model_output(raw, allowed)
        except InsightsInvalid as e:
            # Reason only (e.g. "text contains a digit"), never the text itself.
            log.warning("revenue insights: model output rejected on attempt %s: %s", attempt, e)
        except deepseek.DeepSeekError as e:
            log.warning("revenue insights: DeepSeek call failed (%s)", e.kind)
            return None  # a down or slow service is not worth a second wait
    return None


def _audit(key, status):
    log.info("revenue insights audit at=%s view=%s status=%s", datetime.now(timezone.utc).isoformat(), key, status)


def _remember(key, entry):
    _cache[key] = entry
    while len(_cache) > MAX_CACHED_VIEWS:
        oldest = min(_cache, key=lambda k: _cache[k]["attempted_at"])
        del _cache[oldest]


def _refresh(key, figures, new_hash, now, generate):
    """Runs one generation for `key` and stores the outcome."""
    try:
        template = generate(figures)
    except Exception:
        # generate_template swallows its own failures; this is the net for a
        # bug, so a crash can never leave the view stuck as "generating".
        log.exception("revenue insights: unexpected error while generating")
        template = None
    with _lock:
        previous = _cache.get(key)
        if template is not None:
            _remember(key, {"hash": new_hash, "template": template, "failed": False,
                            "site_ids": [s["site_id"] for s in figures["sites"]],
                            "generated_at": now, "attempted_at": now})
        else:
            # Keep older wording if there is some: it is still filled from
            # today's figures, and the "Analysis from" time tells the truth.
            kept = dict(previous) if previous else {"hash": None, "template": None, "site_ids": [], "generated_at": None}
            _remember(key, {**kept, "failed": True, "attempted_at": now})
        _generating.discard(key)
    _audit(key, "generated" if template is not None else "failed")


def get_insights(period, anchor=None, compare=None, modes=None, custom_anchor=None, now=None,
                 gather_figures=gather, generate=generate_template):
    """What the panel shows: {status, generated_at, insights}. status is
    'ok', 'preparing' (first text still being written) or 'unavailable'.
    Report errors (a bad period) raise ValueError, like the report itself."""
    modes = modes or {}
    now = now or datetime.now(timezone.utc)
    figures, rows = gather_figures(period, anchor, compare, modes, custom_anchor)
    if not deepseek.is_configured():
        return {"status": "unavailable", "generated_at": None, "insights": None}

    key = _view_key(period, anchor, compare, modes, custom_anchor)
    new_hash = figures_hash(figures)
    with _lock:
        entry = _cache.get(key)
        start = _due(entry, new_hash, now) and key not in _generating
        if start:
            _generating.add(key)
    if start:
        _refresh(key, figures, new_hash, now, generate)

    with _lock:
        entry = _cache.get(key)
        busy = key in _generating
    if entry is None or entry["template"] is None:
        return {"status": "preparing" if busy else "unavailable", "generated_at": None, "insights": None}

    values = placeholder_values({**figures, "sites": sites_from_ids(rows, entry["site_ids"])})
    return {"status": "ok", "generated_at": entry["generated_at"].isoformat(),
            "insights": fill_in(entry["template"], values)}
