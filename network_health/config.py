"""Every number Network Health decides with, in one file.

Change a threshold here and redeploy; the page shows these values read-only
(Network Health > Settings). The two things an admin can change in the app
(uplink capacity, the provider switch time) live in nh_settings instead,
because they change with the network, not with the code.

How a rule fires: each check looks at an AVERAGE over a short window of
readings (the owner's call, 2026-11-20: "average loss over 5 min above 2 %",
not "every minute above 2 %"). A probe's loss moves in 10 % steps (10 pings a
test), so one bad minute must not decide on its own. A rule fires after
FIRE_AFTER bad checks in a row and clears after CLEAR_AFTER good ones, so
it doesn't flap on a reading that sits right at the line.
"""

# ---- The feed ----

# No POST at all from the Frankfurt shipper for this long = shipper/server
# down. The shipper sends an empty batch every minute when nothing is new.
FEED_SILENT_MINUTES = 10
# POSTs still arriving but no `probe` record for this long = the routers
# can't reach Frankfurt (expected during an Airtel outage: same uplink).
ROUTER_SILENT_MINUTES = 10

# A batch bigger than this is refused (413). The contract says about 400 KB.
MAX_BODY_BYTES = 1024 * 1024
# The shipper posts about once a minute; a backfill may post batches back to
# back. Over this, it gets a 429 and simply resends on its next run.
INGEST_LIMIT_PER_MINUTE = 120
# Raw readings older than this are deleted (checked once an hour on ingest).
# The 7-day baselines need 7 full days; the rest is margin for the 7d graphs.
# About 3,200 readings a day at roughly 1 KB each.
KEEP_READINGS_DAYS = 10

# ---- Which readings the rules look at ----

# Probes judged for loss and jitter, and the one judged for latency. Every
# other probe (p-nbo, p-goog, i-*, anything added later) is drawn on the page
# but never alerts. Latency is judged on Frankfurt, our own server: 1.1.1.1
# gives ping low priority, so its round trip isn't a clean signal (the
# switch plan PDF, "How we measure").
LOSS_PROBES = ("p-gw", "p-cf")
JITTER_PROBES = ("p-gw", "p-cf")
LATENCY_PROBE = "p-fra"
# Readings for the provider we're switching to are dashboard only: probe
# names ending in this, and the `linknew` / `speednew` kinds.
NEW_PROVIDER_SUFFIX = "-new"

# ---- Rule thresholds ----

UPLINK_CONGESTED_PCT = 85          # max(down, up) / capacity, 5-minute average
UPLINK_WINDOW_MINUTES = 5
DEFAULT_UPLINK_CAPACITY_MBPS = 350  # until an admin sets it on the page

# Frankfurt more than 40 ms over its usual for this hour of day, averaged
# over 10 minutes (the switch plan's alert rule; owner agreed 2026-11-20).
LATENCY_WINDOW_MINUTES = 10
LATENCY_OVER_USUAL_MS = 40          # usual = 7-day median rtt for this hour of day (EAT)
LATENCY_FALLBACK_MS = 250           # before there are 7 days of readings (Frankfurt is ~203 ms)
BASELINE_DAYS = 7

LOSS_WINDOW_MINUTES = 5
LOSS_AMBER_PCT = 2                  # average loss over the window
LOSS_RED_PCT = 20

JITTER_WINDOW_MINUTES = 5
JITTER_HIGH_MS = 30

CPU_WINDOW_MINUTES = 5
CPU_HIGH_PCT = 80

OPTIC_FLOOR_DBM = -25.0             # rx below this is bad on its own
OPTIC_DROP_DB = 2.0                 # or this far below its 7-day average
OPTIC_BASELINE_MIN_READINGS = 12    # an hour of link readings before comparing

# link_downs going up is its own rule, LINK_DOWN: it fires at once and texts
# (a flap on the uplink cuts every customer). These counters going up are
# LINK_ERRORS: they need LINK_ERRORS_FIRE_AFTER readings in a row (one every
# 5 minutes) and stay on the page.
LINK_ERROR_COUNTERS = ("rx_fcs", "rx_err_events", "rx_overflow", "tx_drop")
LINK_ERRORS_FIRE_AFTER = 3

# Speed tests run at 03:30 and 20:30 (EAT). Only the 03:30 one is judged:
# 20:30 runs at peak and is shown with load_before_mbps, never alerted on.
# The router's single-stream fetch over a 203 ms path gives about 30 Mbps,
# so a result is compared with past 03:30 results, never with capacity.
SPEED_JUDGED_HOUR_EAT = 3
SPEED_LOW_FRACTION = 0.5            # under half the 7-day median of 03:30 results
SPEED_BASELINE_MIN_TESTS = 3

# Where content is served from (`route` records, 21:00 and 04:00). The last
# hop that answered: under 15 ms = Kenya; 15 up to 50 = elsewhere in Africa
# or unclear; 50 up to 110 = South Africa (likely; 1.1.1.1 and the Vodacom
# probe sit at about 72); 110 up to 140 = unclear again; 140 and over =
# Europe / abroad. Page only, never an SMS.
ROUTE_SITES = ("www.youtube.com", "www.facebook.com", "www.tiktok.com", "api.safaricom.co.ke")
ROUTE_KENYA_UNDER_MS = 15
ROUTE_ZA_FROM_MS = 50
ROUTE_ZA_UNDER_MS = 110
ROUTE_ABROAD_FROM_MS = 140
# Traceroutes that watch the international path (transit changes), not where
# content is served: shown as their own group, below the content sites.
ROUTE_INTERNATIONAL = ("195.238.122.164", "1.1.1.1")
ROUTE_HOST_LABELS = {"195.238.122.164": "Frankfurt (our server)", "1.1.1.1": "Cloudflare DNS"}
# The card compares with the previous check of the same slot within this long.
ROUTE_LOOKBACK_DAYS = 30

# DNS (`dns` records, every 5 minutes). DNS_ROUTER fires when the router's
# own resolver has a failed lookup, or its slower lookup takes over 500 ms,
# in DNS_FIRE_AFTER runs in a row (a failed lookup counts as 10 s, so it is
# also "slow"). Page only for now: not in SMS_RULES, and AMBER (RED texts).
DNS_ROUTER_RESOLVER = "rt"
DNS_SLOW_MS = 500
DNS_FIRE_AFTER = 2
DNS_CLEAR_AFTER = 2

# A window must hold at least this many readings to be judged at all;
# otherwise the rule keeps its current state (no data is not "fine").
MIN_READINGS_IN_WINDOW = 3
MIN_LOSS_READINGS_IN_WINDOW = 2

# Hysteresis, in checks (one check per new reading).
FIRE_AFTER = 1    # the window average already spans several minutes
CLEAR_AFTER = 3

# ---- SMS ----

# Rules that send an SMS when they fire. Any rule at RED also sends.
SMS_RULES = ("SPEED_LOW", "UPLINK_CONGESTED", "LATENCY_HIGH", "LINK_DOWN", "FEED_SILENT", "ROUTER_SILENT")
# Only readings this fresh may cause an SMS. A backfill replays old readings
# into incidents and history, but never texts about them.
SMS_MAX_RECORD_AGE_MINUTES = 10
# Fires again within this long of clearing = flapping: shown, not texted.
FLAPPING_MINUTES = 30
# This many fires on one device within COMBINE_MINUTES = one combined SMS.
COMBINE_AT = 3
COMBINE_MINUTES = 2
DEFAULT_SMS_DAILY_CAP = 20          # NH_SMS_DAILY_CAP overrides
SMS_MAX_LENGTH = 160
