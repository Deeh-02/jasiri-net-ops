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
LANLINK_COUNTERS = ("link_downs", "rx_fcs", "rx_err", "rx_overflow", "tx_drop")
LANLINK_DAMAGE_OVER = 100           # rx_fcs or rx_err up by more than this in one reading
LANLINK_DROPS_OVER = 1000           # tx_drop up by more than this: page only
LANLINK_MAX_GAP_MINUTES = 15
# The CRS326's OLT port already has OLT_LINK_DOWN (its own up/down events,
# with the outage time): its link_downs don't text a second time.
LANLINK_DOWN_SKIP = (("crs326", "sfp-sfpplus3"),)

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
             "LANLINK_DOWN", "LANLINK_DAMAGE")
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

# ---- Diagnosis by Claude (the "Diagnose" card) ----

# The owner's choice (2026-11-21): a quick check on Sonnet at medium effort;
# a detailed "find what's wrong" analysis on Opus. It runs only when someone
# presses a button, never per reading, and sends a summary of what the page
# shows (diagnose.py), never raw readings or phone numbers. The key is
# ANTHROPIC_API_KEY (Render env); without it the card says so.
DIAGNOSE_MODELS = {"quick": "claude-sonnet-5-5", "deep": "claude-opus-5-5"}
DIAGNOSE_EFFORT = {"quick": "medium", "deep": "high"}
DIAGNOSE_MAX_TOKENS = {"quick": 16000, "deep": 64000}
# US$ per million tokens (input, output), for the cost shown under an answer.
DIAGNOSE_PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
# Calls per Nairobi day (counted in memory: a deploy or restart resets it).
DIAGNOSE_DAILY_LIMIT = {"quick": 40, "deep": 10}
# Pressing the same button again within this, with no question, shows the
# last answer instead of paying for a new one.
DIAGNOSE_REUSE_MINUTES = 5
DIAGNOSE_QUESTION_MAX = 500
DIAGNOSE_TIMEOUT_SECONDS = 600
