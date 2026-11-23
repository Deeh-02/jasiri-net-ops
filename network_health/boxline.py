"""The Customer line card's numbers: rolling averages and the alert's window.
Pure code: no database, no clock.

The charts draw a rolling average (the main line) over the raw readings (faint
dots behind it); the alert judges a rolling average too, so one bad reading
neither fires nor hides a problem. A reading's value may be None (the probe
had no answer): it is left out of the average, never counted as 0.
"""
from datetime import timedelta

from network_health import config


def rolling_mean(times, values, window_seconds):
    """For each time, the mean of the non-None values in (time - window, time].
    `times` ascending (datetimes or epoch seconds); None where the window has
    no value. One pass."""
    out, start, total, count = [], 0, 0.0, 0
    for i, t in enumerate(times):
        if values[i] is not None:
            total += values[i]
            count += 1
        while start <= i and _seconds(t, times[start]) >= window_seconds:
            if values[start] is not None:
                total -= values[start]
                count -= 1
            start += 1
        out.append(total / count if count else None)
    return out


def _seconds(later, earlier):
    gap = later - earlier
    return gap.total_seconds() if isinstance(gap, timedelta) else gap


def _round(value):
    return None if value is None else round(value, 1)


def line_series(rows, since, window_hours):
    """Bucketed readings [{t, name, loss}] (t: datetime, ascending) -> the loss chart's
    data: one shared time axis (epoch seconds) and, per probe name, its raw
    readings and their rolling average over `window_hours`. Readings before
    `since` only warm the average up."""
    times = sorted({r["t"] for r in rows})
    by_name = {}
    for r in rows:
        by_name.setdefault(r["name"], {})[r["t"]] = r["loss"]
    keep = [i for i, t in enumerate(times) if t >= since]
    out = {"t": [int(times[i].timestamp()) for i in keep], "probes": {}}
    for name, found in sorted(by_name.items()):
        raw = [found.get(t) for t in times]
        avg = rolling_mean(times, raw, window_hours * 3600)
        out["probes"][name] = {"raw": [_round(raw[i]) for i in keep], "avg": [_round(avg[i]) for i in keep]}
    return out


def speed_series(runs, since, window_hours):
    """Speed runs [{t, plan_mbps, cf_mbps, fra_mbps}] ascending -> the speed
    chart's data: the time axis, Cloudflare and Frankfurt (raw and rolling
    average) and the plan as it was at each run."""
    times = [r["t"] for r in runs]
    keep = [i for i, t in enumerate(times) if t >= since]
    out = {"t": [int(times[i].timestamp()) for i in keep], "plan": [r["plan_mbps"] for r in (runs[i] for i in keep)]}
    for key, field in (("cf", "cf_mbps"), ("fra", "fra_mbps")):
        raw = [r[field] for r in runs]
        avg = rolling_mean(times, raw, window_hours * 3600)
        out[key] = {"raw": [_round(raw[i]) for i in keep], "avg": [_round(avg[i]) for i in keep]}
    return out


def hour_average(rows, name, now, hours=1):
    """The mean of one probe's readings in the last `hours`, and how many
    readings that was: (None, 0) when there are none."""
    values = [r["loss"] for r in rows if r["name"] == name and r["loss"] is not None and now - r["t"] < timedelta(hours=hours)]
    return (sum(values) / len(values) if values else None), len(values)


def window_average(window, name):
    """One probe's mean loss over the alert window's readings
    [{ts, probes}], None when it has no value."""
    values = [p.get("loss_pct") for reading in window for p in reading["probes"] if p["name"] == name]
    values = [v for v in values if v is not None]
    return (sum(values) / len(values)) if values else None


def is_window_full(window):
    """Enough readings for a 30-minute average to mean something."""
    return len(window) >= config.BOX_MIN_READINGS_IN_WINDOW
