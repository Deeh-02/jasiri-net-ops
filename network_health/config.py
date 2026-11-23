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
from datetime import datetime, timezone

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
# About 3,200 readings a day at roughly 1 KB each. (`route` and `crs_link`
# rows are kept; `olt` rows KEEP_OLT_DAYS, below.)
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

# ---- Inside network (`olt` every 5 min, `crs_link` on each port change) ----

# The OLT (Huawei MA5680T) uplink is its port 0/19/0, fed by the CRS326's
# sfp-sfpplus3. Damaged frames = up_crc's increase / (up_rx's + up_crc's),
# per 5-minute pair of readings (up_rx counts good frames only).
# Under GREEN_UNDER is clean; over RED_OVER is bad and, OLT_CRC_FIRE_AFTER
# readings in a row, opens OLT_CRC and texts. In between: amber on the page.
# On 21 Nov 2026 it measured ~10 % (a bad DAC; replacement planned).
OLT_CRC_GREEN_UNDER_PCT = 0.01
OLT_CRC_RED_OVER_PCT = 1
OLT_CRC_FIRE_AFTER = 2
OLT_CRC_CLEAR_AFTER = 2
OLT_ERRORS_COUNTER_WRAP = 2 ** 32   # up_crc is ifInErrors, a 32-bit counter
# No complete OLT reading received for this long = OLT not answering SNMP.
OLT_SNMP_SILENT_MINUTES = 15
OLT_LINK_INTERFACE = "sfp-sfpplus3"
CRS_PORT_LABELS = {"sfp-sfpplus1": "CCR2116", "sfp-sfpplus2": "CCR2004", "sfp-sfpplus3": "OLT uplink",
                   "sfp-sfpplus4": "Proxmox"}
# The restart list and the link events table reach back this far. `olt`
# rows are kept KEEP_OLT_DAYS (the DAC before/after); `crs_link` rows are kept.
INSIDE_LOOKBACK_DAYS = 30
KEEP_OLT_DAYS = 90
INSIDE_EVENTS_LIMIT = 100

# ---- Inside network, part 2 (owner, 2026-11-21) ----

# Paths judged one reading at a time (each reading is a 10-ping test):
# bad loss is LOSS_PCT or more in LOSS_IN_A_ROW readings; DOWN is no reply at
# all (rtt null, loss 100) in DOWN_IN_A_ROW. A DOWN reading isn't counted as
# "loss" too, so an outage texts once, as DOWN.
#   inner: the CCR2004's way out (through the CRS326, CCR2116 and Airtel).
#   hop:   h-cust* only (a customer path through the OLT). The other h-*
#          probes ping the boxes themselves, which answer at low priority:
#          shown, never judged.
INNER_PATH_PROBES = ("i-fra", "i-fra-big")
CUSTOMER_PATH_PREFIX = "h-cust"
PATH_LOSS_PCT = 20
PATH_LOSS_IN_A_ROW = 3
PATH_DOWN_IN_A_ROW = 2

# ---- Customer line (probe) (owner, 2026-11-23) ----
# A probe machine on a real customer line (`box` every 5 min: pings to gw /
# core / airtel / fra, each with a 1400-byte "-big" twin; `boxspeed` every
# 30 min: 6-second downloads from Cloudflare and Frankfurt). Nothing else
# reads these; both kinds are stored in nh_readings' existing probes / speed
# columns, the probe's `dev` inside each entry.
# Frankfurt is a real end-to-end server: the primary loss measure. The others are
# router hops; a router answers pings at low priority, so small-packet loss
# there can be the router, not the line.
BOX_PRIMARY = "fra"
BOX_HOPS = ("gw", "core", "airtel")
BOX_TARGETS = (BOX_PRIMARY, *BOX_HOPS)                 # the loss chart's panels, in this order
BOX_NAMES = {"home-kwamlima": "Kwa Mlima"}              # a dev's name on the page and in the SMS
BOX_CHART_DAYS = 7
BOX_READING_SECONDS = 300            # the probe machine pings every 5 minutes
# Alerts. BIG_LOSS (owner, 2026-11-23): the rolling BOX_LOSS_WINDOW_MINUTES average
# of Frankfurt's 1400-byte ping (fra-big, a real end-to-end server) is
# BOX_FRA_BIG_LOSS_PCT or more, OR the first router's (gw-big) is
# BOX_GW_BIG_LOSS_PCT or more. It clears when both averages are under
# BOX_LOSS_CLEAR_FRACTION of those lines (the band between is a hold, so it
# doesn't flap). Needs BOX_MIN_READINGS_IN_WINDOW readings in the window.
# SLOW: the Cloudflare download is under this fraction of the line's plan,
# BOX_SLOW_IN_A_ROW runs in a row.
BOX_FRA_BIG_PROBE = "fra-big"
BOX_GW_BIG_PROBE = "gw-big"
BOX_FRA_BIG_LOSS_PCT = 8
BOX_GW_BIG_LOSS_PCT = 10
BOX_LOSS_CLEAR_FRACTION = 0.5
BOX_LOSS_WINDOW_MINUTES = 30
BOX_MIN_READINGS_IN_WINDOW = 5       # of the 6 in 30 minutes: one may be missing
# Both averages of a path whose SMALL pings are all lost are left out: the
# path is down, not "losing big packets".
BOX_DEAD_SMALL_LOSS_PCT = 90
BOX_SLOW_FRACTION = 0.30
BOX_SLOW_IN_A_ROW = 3
# The charts' main line is a rolling average of this many hours; the raw
# readings are the dots behind it.
BOX_ROLLING_HOURS = 1
# The probe's "fra" / "fra-big" read 100 % loss before this moment (a probe
# bug, fixed 23 Nov 2026 03:30 EAT). Those values are dropped, never stored.
BOX_FRA_PROBES = ("fra", "fra-big")
BOX_FRA_VALID_FROM = datetime(2026, 11, 23, 0, 30, tzinfo=timezone.utc)
# Manual markers on the two charts ("CRS-OLT DAC swapped"): nh_settings rows
# "box_marker:<id>", one per marker.
BOX_MARKER_PREFIX = "box_marker:"
BOX_MARKER_LABEL_MAX = 80
BOX_MARKERS_MAX = 200

# Areas (`area`, one record per VLAN on the CCR2004 uplink every 5 min).
# Names come from OPS's own VLAN list (monitored_sites + locations); VLAN 99
# is the CCR2004's own link to the core, shown as its total, never judged.
AREA_TOTAL_VLAN = 99
AREA_TOTAL_LABEL = "CCR2004 total"
# AREA_SILENT: 07:00-23:00 EAT, download under SILENT_MBPS in
# SILENT_IN_A_ROW readings, where the median at that hour over the previous
# BASELINE_DAYS was over USUAL_MBPS, and the VLAN has HISTORY_DAYS of readings.
AREA_SILENT_FROM_HOUR_EAT = 7
AREA_SILENT_UNTIL_HOUR_EAT = 23
AREA_SILENT_MBPS = 0.05
AREA_SILENT_IN_A_ROW = 2
AREA_USUAL_MBPS = 1
AREA_HISTORY_DAYS = 2
AREA_SPARK_BUCKET_SECONDS = 900     # the 24 h sparklines: one point per 15 min

# Inside links (`lanlink`, every 5 min). Increases are per reading; a pair of
# readings over MAX_GAP_MINUTES apart isn't judged (not "in 5 min").
LANLINK_LABELS = {
    ("ccr2116", "sfp-sfpplus4"): "CCR2116 → CRS326",
    ("ccr2004", "sfp-sfpplus1"): "CCR2004 → CRS326",
    ("crs326", "sfp-sfpplus1"): "CRS326 → CCR2116",
    ("crs326", "sfp-sfpplus2"): "CRS326 → CCR2004",
    ("crs326", "sfp-sfpplus3"): "CRS326 → OLT",
    ("crs326", "sfp-sfpplus4"): "CRS326 → Proxmox",
}
# The physical cables, one row each, both ends. A port counts errors only on
# frames it RECEIVES, so damage on the way A -> B shows at B and damage B -> A
# at A. An end is a `lanlink` port, the OLT's uplink (`olt` readings:
# received CRC errors) or a box we don't read (the Proxmox server).
LANLINK_CABLES = (
    {"name": "CCR2116 ↔ CRS326", "a": {"label": "CCR2116", "dev": "ccr2116", "port": "sfp-sfpplus4"},
     "b": {"label": "CRS326", "dev": "crs326", "port": "sfp-sfpplus1"}},
    {"name": "CCR2004 ↔ CRS326", "a": {"label": "CCR2004", "dev": "ccr2004", "port": "sfp-sfpplus1"},
     "b": {"label": "CRS326", "dev": "crs326", "port": "sfp-sfpplus2"}},
    {"name": "CRS326 ↔ OLT", "a": {"label": "CRS326", "dev": "crs326", "port": "sfp-sfpplus3"},
     "b": {"label": "OLT", "olt": True, "port": "0/19/0"}},
    {"name": "CRS326 ↔ Proxmox", "a": {"label": "CRS326", "dev": "crs326", "port": "sfp-sfpplus4"},
     "b": {"label": "Proxmox", "port": None}},
)
LANLINK_RED_PCT = 1                 # a direction is red when over this share of frames is damaged
LANLINK_COUNTERS = ("link_downs", "rx_fcs", "rx_err", "rx_overflow", "tx_drop")
LANLINK_DAMAGE_OVER = 100           # rx_fcs or rx_err up by more than this in one reading
LANLINK_DROPS_OVER = 1000           # tx_drop up by more than this: page only
LANLINK_MAX_GAP_MINUTES = 15
# The CRS326's OLT port already has OLT_LINK_DOWN (its own up/down events,
# with the outage time): its link_downs don't text a second time.
LANLINK_DOWN_SKIP = (("crs326", "sfp-sfpplus3"),)

# Router physical ports in `crs_link` events (they also come from the CCR2116
# and CCR2004 now, not just the CRS326): named like the inside links;
# anything else shows as "CCR2116 sfp-sfpplus1". Shown, never judged: only
# the CRS326's OLT port (OLT_LINK_INTERFACE) alerts.
CRS_LINK_DEVICE = "crs326"

# ---- Access network (owner, 2026-11-22): PON ports, ONTs, PPPoE sessions ----
# `pon`, `ont` and `ppp` records every 5 min. Field jobs and colours use the
# light levels below; the owner may tune them (the PON module's class, B+ or
# C+, is still to confirm).
#   ONT side (rx_dbm, downstream: what the ONT receives):
ONT_RX_OVERLOAD_ABOVE_DBM = -8      # above: overloaded, needs an attenuator
ONT_RX_WEAK_BELOW_DBM = -25         # -25 down to -27: weak
ONT_RX_BAD_BELOW_DBM = -27          # under -27: bad
#   OLT side (olt_rx_dbm, upstream: what the OLT receives from that ONT):
OLT_RX_WEAK_BELOW_DBM = -28
OLT_RX_BAD_BELOW_DBM = -30
# Why an ONT went down, from the OLT's last_down_text (lower-cased, matched
# by these words): light = a fibre problem, power = the ONT lost power,
# reboot = it was reset. Anything else is "other".
DOWN_TEXT_LIGHT = ("losi", "lobi", "light")
DOWN_TEXT_POWER = ("dying", "power")
DOWN_TEXT_REBOOT = ("reset", "reboot")

# ONTs going down together on one PON, in one 5-minute poll:
#   ACCESS_GROUP_MIN_ONTS or more, mostly light loss -> PON_FIBRE_FAULT (texts)
#   ACCESS_GROUP_MIN_ONTS or more, mostly power      -> PON_POWER_CUT (texts)
#   every ONT that was online (at least WHOLE_PON_MIN_ONTS) -> PON_DOWN (texts)
#   fewer, light loss -> ONT_LIGHT_LOSS per ONT (page only); power: listed only
# One poll = ONT readings within ACCESS_POLL_GAP_SECONDS of each other. A
# poll is judged once the next poll has arrived, or nothing new has been
# received for ACCESS_POLL_SETTLE_SECONDS (the router writes a poll over a
# few seconds, and the shipper may post it in two batches).
ACCESS_GROUP_MIN_ONTS = 3
WHOLE_PON_MIN_ONTS = 2
ACCESS_POLL_GAP_SECONDS = 120
ACCESS_POLL_SETTLE_SECONDS = 120
# A group incident clears once at most this share of its ONTs (at least 1)
# is still offline.
ACCESS_GROUP_CLEAR_LEFT_FRACTION = 0.25

# Light degrading: per ONT, the median of its hourly average rx over the
# last 24 h against the 7 days before. Checked once an hour.
LIGHT_DROP_ONT_DB = 3               # one ONT this much weaker: ONT_LIGHT_DEGRADING (page)
LIGHT_DROP_SHARED_DB = 2            # this much on PON_SHARED_MIN_ONTS ONTs of one PON:
PON_SHARED_MIN_ONTS = 2             #   PON_LIGHT_DEGRADING (texts)
LIGHT_RECENT_MIN_HOURS = 12         # hours with a reading needed in the last 24 h
LIGHT_USUAL_MIN_HOURS = 72          # and in the 7 days before (3 days)

# PPPoE (`ppp`, one record per router every 5 min, with every session name).
# ccr2116: home customers (JSxx); ccr2004: mostly hotspot SITE routers.
SITE_NAME_WORD = "hotspot"          # a session whose name has this is a site
SITE_PPP_MISSING_IN_A_ROW = 2       # SITE_PPP_DOWN: missing in 2 readings in a row (5 min)
PPP_MASS_DROP_FRACTION = 0.3        # this share of a router's sessions gone in one reading
PPP_MASS_DROP_MIN_SESSIONS = 5      # ...of at least this many: PPP_MASS_DROP
PPP_MAX_GAP_MINUTES = 15            # readings further apart than this aren't "within 5 min"
LINE_NO_INTERNET_IN_A_ROW = 4       # ONT online, its PPPoE missing 4 readings in a row (15 min): page
# Matching an ONT to its PPPoE session: the JS code in its name (JS26 = JS26),
# else the site name with case, spaces, underscores and these words removed.
# The page's manual matches (nh_ont_ppp_matches) win over both.
SITE_NAME_NOISE = ("hotspot", "cyber")

# Field jobs: a PON with at least PON_JOB_MIN_ONLINE ONTs online, half or
# more of them (and at least PON_JOB_MIN_POOR) weak or bad, is one job (the
# shared fibre or splitter).
PON_JOB_WEAK_FRACTION = 0.5
PON_JOB_MIN_ONLINE = 2
PON_JOB_MIN_POOR = 2                # (ONTs bad in one direction only don't count: that's the ONT)
# "Likely same splitter": ONTs on one PON within this distance of each
# other, or that went down in the same poll at least this many times.
SPLITTER_DISTANCE_M = 60
SPLITTER_TOGETHER_MIN = 2
ACCESS_LOOKBACK_DAYS = 30           # down-together history, the events list
ACCESS_EVENTS_LIMIT = 200
LIGHT_SPARK_BUCKET_SECONDS = 3 * 3600   # the 7-day light sparklines: one point per 3 h
LIGHT_SPARK_DAYS = 7
# Raw `ont` readings are 18,000 a day: kept KEEP_ONT_DAYS (the light history
# lives on in nh_ont_hourly, KEEP_ONT_HOURLY_DAYS). Events KEEP_ACCESS_EVENTS_DAYS.
# An ONT or PPPoE session not seen for FORGET_DAYS is forgotten.
KEEP_ONT_DAYS = 3
KEEP_ONT_HOURLY_DAYS = 30
KEEP_ACCESS_EVENTS_DAYS = 90
ACCESS_FORGET_DAYS = 30

# A window must hold at least this many readings to be judged at all;
# otherwise the rule keeps its current state (no data is not "fine").
MIN_READINGS_IN_WINDOW = 3
MIN_LOSS_READINGS_IN_WINDOW = 2

# Hysteresis, in checks (one check per new reading).
FIRE_AFTER = 1    # the window average already spans several minutes
CLEAR_AFTER = 3

# ---- SMS ----

# Rules that send an SMS when they fire. Any rule at RED also sends.
SMS_RULES = ("SPEED_LOW", "UPLINK_CONGESTED", "LATENCY_HIGH", "LINK_DOWN", "FEED_SILENT", "ROUTER_SILENT",
             "OLT_CRC", "OLT_RESTART", "OLT_LINK_DOWN", "OLT_SNMP_SILENT",
             "INNER_PATH_LOSS", "INNER_PATH_DOWN", "CUSTOMER_PATH_LOSS", "CUSTOMER_PATH_DOWN", "AREA_SILENT",
             "LANLINK_DOWN", "LANLINK_DAMAGE", "BOX_BIG_LOSS", "BOX_SLOW",
             "PON_DOWN", "PON_FIBRE_FAULT", "PON_POWER_CUT", "PON_LIGHT_DEGRADING", "SITE_PPP_DOWN", "PPP_MASS_DROP")
# A restart is over by the time it is seen: its fire SMS says so, and there
# is no "back to normal" SMS after it.
NO_CLEAR_SMS = ("OLT_RESTART",)
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

# ---- Diagnose: a chat with Claude on the Network Health page ----

# The owner's choices (2026-11-21): everyone gets Claude Sonnet 5.5; admins
# can switch to Claude Opus 5.5; everyone picks medium or high effort. It
# runs only when someone sends a question, and sends a summary of what the
# page shows (diagnose.py), never raw readings or phone numbers. The key is
# ANTHROPIC_API_KEY (Render env); without it the panel says so.
DIAGNOSE_MODELS = {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
DIAGNOSE_EFFORTS = ("medium", "high")
DIAGNOSE_MAX_TOKENS = {"sonnet": 16000, "opus": 32000}
# US$ per million tokens, for the cost shown under an answer. A cache write
# costs 1.25x "in".
DIAGNOSE_PRICES = {"claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.10},
                   "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20}}
# Questions per Nairobi day, all users together (counted in memory: a deploy
# or restart resets it).
DIAGNOSE_DAILY_LIMIT = {"sonnet": 100, "opus": 20}
# A conversation keeps its snapshot this long, so follow-up questions read
# it from Anthropic's cache (a tenth of the price); then it is rebuilt.
DIAGNOSE_SNAPSHOT_MINUTES = 10
DIAGNOSE_MAX_TURNS = 20             # messages sent back per question (the page drops older ones)
DIAGNOSE_QUESTION_MAX = 4000        # characters in one question
DIAGNOSE_ANSWER_MAX = 20000         # characters in one earlier answer sent back
DIAGNOSE_TIMEOUT_SECONDS = 600
# Rounds of lookups (diagnose_tools) Claude may make for one answer.
DIAGNOSE_MAX_LOOKUPS = 6
