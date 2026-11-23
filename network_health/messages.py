"""The SMS texts. Every one must fit ONE segment: at most 160 characters,
GSM-7 basic characters only (no emoji, no curly quotes, no em dashes, none
of [ ] { } ^ ~ | \\ which cost two characters each). A message outside that
becomes a 70-character Unicode segment and costs 3x.
tests/test_network_health_messages.py fills every template with the longest
realistic values and checks both. fit() below is only the safety net.
"""
from datetime import timedelta

from network_health import config
from network_health.rules import DEVICE_LABELS

# The GSM 03.38 basic character set (no escape characters).
GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
PROBE_LABELS = {"p-gw": "Airtel gateway", "p-cf": "1.1.1.1", "p-fra": "Frankfurt", "p-za": "South Africa (Vodacom)"}


def is_gsm7(text):
    return all(character in GSM7_BASIC for character in text)


def fit(text):
    """Last line of defence: swap anything outside GSM-7 for '?', cut to 160."""
    cleaned = "".join(c if c in GSM7_BASIC else "?" for c in text)
    return cleaned[:config.SMS_MAX_LENGTH]


def number(value):
    """Short number for an SMS: whole above 10, one decimal below. Capped so
    a broken reading can't push a message over 160 characters."""
    if value is None:
        return "?"
    value = max(min(value, 99999), -9999)
    return str(round(value)) if abs(value) >= 10 else f"{value:.1f}".rstrip("0").rstrip(".")


def clock_eat(moment):
    return (moment + timedelta(hours=3)).strftime("%H:%M")


def duration_text(minutes):
    minutes = max(int(round(minutes)), 0)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{mins:02d}min"
    return f"{mins}min"


def seconds_text(seconds):
    """'2 min 13 s' (the OLT link coming back); hours once it's that long."""
    seconds = max(int(round(seconds)), 0)
    if seconds >= 3600:
        return duration_text(seconds / 60)
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def pct_text(value):
    """A damaged-frames percentage: one decimal, more when it's small."""
    if value is None:
        return "?"
    value = max(min(value, 100), 0)
    return f"{value:.1f}" if value >= 0.1 else f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def short(text, limit=20):
    """A name from a reading or OPS's lists, cut so the SMS stays one segment."""
    return str(text or "?")[:limit]


def gsm_label(label):
    """Link labels use an arrow on the page; the SMS says 'to' (GSM-7)."""
    return short(str(label or "?").replace(" → ", " to "), 40)


def link_label_of(subject):
    """'crs326 sfp-sfpplus3' (a LANLINK subject) -> its label, GSM-7."""
    dev, _, port = (subject or "").partition(" ")
    return gsm_label(config.LANLINK_LABELS.get((dev, port), subject))


def gsm_name(text, limit=24):
    """A name set by people (an ONT's description, a PPPoE session): any
    character outside GSM-7 becomes '?', cut to limit."""
    return "".join(c if c in GSM7_BASIC else "?" for c in str(text or "?"))[:limit]


def names_text(names, budget):
    """'JS12, JS14, Stima_Hotspot +2' in at most `budget` characters: as
    many names as fit, then how many more."""
    names = [gsm_name(n) for n in names or []]
    shown = []
    for i, name in enumerate(names):
        rest = len(names) - i - 1
        candidate = ", ".join(shown + [name]) + (f" +{rest}" if rest else "")
        if len(candidate) > budget:
            break
        shown.append(name)
    more = len(names) - len(shown)
    text = ", ".join(shown)
    if more:
        text = f"{text} +{more}" if shown else f"{more} ONTs"
    return text[:budget]


def probe_label(name):
    return PROBE_LABELS.get(name, name)


def problem_text(rule, facts):
    """What is wrong, in a few words, for a fire SMS."""
    if rule == "UPLINK_CONGESTED":
        return f"uplink {number(facts.get('pct'))}% full ({facts.get('minutes')}min avg). Customers slowing"
    if rule == "SPEED_LOW":
        if facts.get("failed"):
            return "night speed test failed"
        return (f"night speed test {number(facts.get('mbps'))}Mbps, usual {number(facts.get('usual'))}. "
                f"Load before {number(facts.get('load'))}Mbps")
    if rule == "LATENCY_HIGH":
        usual = facts.get("usual")
        tail = f"usual {number(usual)}ms" if usual is not None else f"limit {number(facts.get('limit'))}ms"
        return f"latency to {probe_label(facts.get('probe', ''))} {number(facts.get('ms'))}ms, {tail}"
    if rule == "LOSS":
        target = probe_label(facts.get("probe", ""))
        if facts.get("outage"):
            return f"no reply from {target} (100% loss)"
        return f"{number(facts.get('loss'))}% packet loss to {target}"
    if rule == "JITTER_HIGH":
        return f"jitter to {probe_label(facts.get('probe', ''))} {number(facts.get('ms'))}ms"
    if rule == "CPU_HIGH":
        return f"CPU {number(facts.get('pct'))}% (5min avg)"
    if rule == "OPTIC_DEGRADING":
        return f"SFP rx {number(facts.get('rx'))}dBm, usual {number(facts.get('usual'))}dBm"
    if rule == "LINK_DOWN":
        return f"uplink link dropped {number(facts.get('link_downs'))}x in 5min. Customers cut off"
    if rule == "LINK_ERRORS":
        return f"uplink errors rising ({number(facts.get('errors'))} new)"
    if rule == "DNS_ROUTER":
        if facts.get("fails"):
            return f"router DNS failing ({number(facts.get('fails'))} of 2 lookups failed)"
        return f"router DNS slow, worst lookup {number(facts.get('ms'))}ms"
    if rule == "ROUTER_SILENT":
        return f"no readings {number(facts.get('minutes'))}min. Airtel or router down"
    if rule == "OLT_CRC":
        return f"OLT uplink damaging frames, {pct_text(facts.get('pct'))}% of frames to customers (CRS326-OLT link)"
    if rule == "OLT_RESTART":
        uptime = facts.get("previous_uptime_s")
        was_up = "" if uptime is None else f", had been up {duration_text(uptime / 60)}"
        return f"OLT restarted (power switchover?){was_up}"
    if rule == "OLT_LINK_DOWN":
        return f"OLT link DOWN (CRS326 {facts.get('interface', '')}). Customers cut off"
    if rule == "OLT_SNMP_SILENT":
        return f"OLT not answering SNMP for {number(facts.get('minutes'))}min"
    if rule == "INNER_PATH_LOSS":
        return f"CCR2004 path out losing packets, {number(facts.get('loss'))}% loss on {short(facts.get('probe'))}"
    if rule == "INNER_PATH_DOWN":
        return f"CCR2004 path out DOWN, no reply on {short(facts.get('probe'))}"
    if rule == "CUSTOMER_PATH_LOSS":
        return f"customer path via OLT losing packets, {number(facts.get('loss'))}% loss on {short(facts.get('probe'))}"
    if rule == "CUSTOMER_PATH_DOWN":
        return f"customer path via OLT DOWN, no reply on {short(facts.get('probe'))}"
    if rule == "BOX_BIG_LOSS":
        to = "Frankfurt" if facts.get("probe") == config.BOX_FRA_BIG_PROBE else "first router"
        return (f"Customer path losing big packets (CRS-OLT link?): {number(facts.get('loss'))}% big-ping loss to {to} "
                f"({number(facts.get('minutes'))}min avg), {gsm_name(facts.get('label') or facts.get('dev'), 20)}")
    if rule == "BOX_SLOW":
        return (f"Customer line slow: {number(facts.get('mbps'))}Mbps of {number(facts.get('plan'))}Mbps plan "
                f"at {gsm_name(facts.get('label') or facts.get('dev'), 20)}")
    if rule == "AREA_SILENT":
        return f"Area {short(facts.get('area'), 30)} has gone silent: possible power or fibre cut at the site"
    if rule == "LANLINK_DOWN":
        return f"Link {gsm_label(facts.get('label'))} went down"
    if rule == "LANLINK_DAMAGE":
        return f"Link {gsm_label(facts.get('label'))} is damaging frames ({number(facts.get('count'))} errors in 5min)"
    if rule == "LANLINK_DROPS":
        return f"Link {gsm_label(facts.get('label'))} dropping frames ({number(facts.get('count'))} in 5min)"
    pon = gsm_name(facts.get("pon"), 12)
    if rule == "PON_DOWN":
        if facts.get("power", 0) > facts.get("light", 0):
            return f"Whole PON {pon} down ({number(facts.get('count'))} ONTs, dying-gasp): power cut in the area likely"
        return f"Whole PON {pon} down ({number(facts.get('count'))} ONTs): feeder/OLT port/main splitter"
    if rule == "PON_FIBRE_FAULT":
        return f"Fibre/splitter fault likely on PON {pon}: {names_text(facts.get('names'), 60)}"
    if rule == "PON_POWER_CUT":
        return f"Power cut affecting PON {pon}: {names_text(facts.get('names'), 66)}"
    if rule == "PON_LIGHT_DEGRADING":
        return f"Shared fibre/splitter degrading on PON {pon}: {names_text(facts.get('names'), 45)} weaker by 2dB+"
    if rule == "ONT_LIGHT_LOSS":
        return f"Light loss at {gsm_name(facts.get('name'))}"
    if rule == "ONT_LIGHT_DEGRADING":
        return f"Light degrading at {gsm_name(facts.get('name'))}, {number(facts.get('drop'))}dB weaker than usual"
    if rule == "SITE_PPP_DOWN":
        return f"Site {gsm_name(facts.get('name'), 40)} offline (PPPoE)"
    if rule == "PPP_MASS_DROP":
        return (f"Mass PPPoE drop, {number(facts.get('dropped'))} of {number(facts.get('before'))} sessions gone "
                f"(RADIUS/router?)")
    if rule == "LINE_NO_INTERNET":
        return f"Line up, no internet: {gsm_name(facts.get('name'))} (PPPoE {gsm_name(facts.get('ppp'), 20)} missing)"
    return rule


def recovered_text(rule, subject):
    """What came back, in a few words, for a clear SMS."""
    return {
        "UPLINK_CONGESTED": "uplink load",
        "SPEED_LOW": "night speed test",
        "LATENCY_HIGH": "latency",
        "LOSS": f"packet loss to {probe_label(subject)}",
        "JITTER_HIGH": f"jitter to {probe_label(subject)}",
        "CPU_HIGH": "CPU",
        "OPTIC_DEGRADING": "SFP signal",
        "LINK_DOWN": "uplink link",
        "LINK_ERRORS": "uplink errors",
        "DNS_ROUTER": "router DNS",
        "ROUTER_SILENT": "readings",
        "OLT_CRC": "OLT uplink frames",
        "OLT_RESTART": "OLT",
        "OLT_LINK_DOWN": "OLT link",
        "OLT_SNMP_SILENT": "OLT SNMP",
        "INNER_PATH_LOSS": f"CCR2004 path out ({short(subject)})",
        "INNER_PATH_DOWN": f"CCR2004 path out ({short(subject)})",
        "CUSTOMER_PATH_LOSS": f"customer path ({short(subject)})",
        "CUSTOMER_PATH_DOWN": f"customer path ({short(subject)})",
        "BOX_BIG_LOSS": f"customer path big packets ({gsm_name(config.BOX_NAMES.get(subject, subject), 20)})",
        "BOX_SLOW": f"customer line speed ({gsm_name(config.BOX_NAMES.get(subject, subject), 20)})",
        "AREA_SILENT": f"area {short(subject, 44)}",
        "LANLINK_DOWN": f"link {link_label_of(subject)}",
        "LANLINK_DAMAGE": f"link {link_label_of(subject)} frames",
        "LANLINK_DROPS": f"link {link_label_of(subject)} drops",
        "PON_DOWN": f"PON {gsm_name(subject, 12)}",
        "PON_FIBRE_FAULT": f"fibre on PON {gsm_name(subject, 12)}",
        "PON_POWER_CUT": f"power on PON {gsm_name(subject, 12)}",
        "PON_LIGHT_DEGRADING": f"light on PON {gsm_name(subject, 12)}",
        "ONT_LIGHT_LOSS": f"ONT {gsm_name(subject, 20)}",
        "ONT_LIGHT_DEGRADING": f"light at ONT {gsm_name(subject, 20)}",
        "SITE_PPP_DOWN": f"site {gsm_name(subject, 40)} PPPoE",
        "PPP_MASS_DROP": "PPPoE sessions",
        "LINE_NO_INTERNET": f"internet at ONT {gsm_name(subject, 20)}",
    }.get(rule, rule)


def fire_message(device, rule, facts, since):
    # A silence rule fires 10 minutes INTO the silence; "Since" should say
    # when the data stopped, not when OPS noticed.
    if rule in ("FEED_SILENT", "ROUTER_SILENT", "OLT_SNMP_SILENT", "SITE_PPP_DOWN") and facts.get("minutes") is not None:
        since = since - timedelta(minutes=facts["minutes"])
    if rule == "OLT_RESTART":
        # Seen at the first reading after it; the uptime says when it came back.
        booted = since - timedelta(seconds=facts.get("uptime_s") or 0)
        return fit(f"JNET ALERT {DEVICE_LABELS.get(device, device)}: {problem_text(rule, facts)}. "
                   f"Back up at {clock_eat(booted)}")
    if rule == "FEED_SILENT":
        return fit(f"JNET ALERT: no data from Frankfurt server {number(facts.get('minutes'))}min. "
                   f"Server or shipper down. Since {clock_eat(since)}")
    return fit(f"JNET ALERT {DEVICE_LABELS.get(device, device)}: {problem_text(rule, facts)}. Since {clock_eat(since)}")


def clear_message(device, rule, subject, lasted_minutes):
    if rule == "OLT_LINK_DOWN":
        return fit(f"JNET OK {DEVICE_LABELS.get(device, device)}: OLT link back after {seconds_text(lasted_minutes * 60)}")
    if rule == "FEED_SILENT":
        return fit(f"JNET OK: data from Frankfurt server is back. Lasted {duration_text(lasted_minutes)}")
    return fit(f"JNET OK {DEVICE_LABELS.get(device, device)}: {recovered_text(rule, subject)} back to normal. "
               f"Lasted {duration_text(lasted_minutes)}")


def combined_fire_message(device, count, worst_rule):
    return fit(f"JNET ALERT: {count} problems on {DEVICE_LABELS.get(device, device)}, worst: {worst_rule}. Check app")


def combined_clear_message(device, count):
    return fit(f"JNET OK {DEVICE_LABELS.get(device, device)}: {count} problems cleared. Check app")


def cap_message(cap):
    return fit(f"JNET: {cap} alert SMS sent today, the daily limit. Alerts paused until midnight. Check app")
