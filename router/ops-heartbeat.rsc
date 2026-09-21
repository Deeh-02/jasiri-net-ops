# JASIRI NET OPS — read-only heartbeat (Phase 4.3)
#
# Reads /interface vlan, /ip hotspot active and /ppp active, and POSTs ONE JSON
# payload for the whole fleet to Ops every 60s. Changes nothing on the router,
# disconnects nobody. Rollback: /system scheduler disable ops-heartbeat
#
# A HUMAN pastes this onto the router — it is never run by an agent.
# Before pasting, check for name collisions:
#     /system script print where name~"ops-heartbeat"
#     /system scheduler print where name~"ops-heartbeat"
#
# Payload contract (fixed by routers/monitoring.py validate_snapshot):
#   {"seq":N,"router_ts":"YYYY-MM-DD HH:MM:SS","gmt_offset":14400,
#    "sites":[{"vlan_id":35,"sessions":12},...],"pppoe":["user",...]}
#
# Assumptions to confirm on the router (see the notes at the bottom):
#   - Every VLAN's subnet is 10.50.<vlan-id>.0/24, so a hotspot session's
#     VLAN is the third octet of its address. Change $subnetPrefix if that
#     convention ever changes.
#   - Every /interface vlan is reported. VLANs Ops doesn't know are
#     quarantined once (not per poll) and can be resolved from Ops.

# --- CONFIGURE (do not commit real values) ---------------------------------
:local ingestUrl "https://YOUR-RENDER-APP.onrender.com/monitoring/ingest"
:local ingestToken "PASTE-MONITORING_INGEST_TOKEN-HERE"
:local subnetPrefix "10.50"
# true = build and print the payload, send nothing. Use for the first run.
:local dryRun true
# ---------------------------------------------------------------------------

:global opsHeartbeatSeq
:if ([:typeof $opsHeartbeatSeq] = "nothing") do={ :set opsHeartbeatSeq 0 }
:set opsHeartbeatSeq ($opsHeartbeatSeq + 1)

# JSON string escape for usernames.
:local esc do={
    :if ([:len $1] = 0) do={ :return "" }
    :local out ""
    :for i from=0 to=([:len $1] - 1) do={
        :local c [:pick $1 $i ($i + 1)]
        :if ($c = "\"" || $c = "\\") do={ :set out ($out . "\\" . $c) } else={ :set out ($out . $c) }
    }
    :return $out
}

# Clock. /system clock get date is ISO ("2026-09-21") on some versions and
# "sep/21/2026" on others; normalise to ISO either way.
:local d [/system clock get date]
:if ([:pick $d 4 5] != "-") do={
    :local months {"jan"="01";"feb"="02";"mar"="03";"apr"="04";"may"="05";"jun"="06";"jul"="07";"aug"="08";"sep"="09";"oct"="10";"nov"="11";"dec"="12"}
    :set d ([:pick $d 7 11] . "-" . ($months->[:pick $d 0 3]) . "-" . [:pick $d 4 6])
}
:local routerTs ($d . " " . [/system clock get time])
:local gmtOffset [/system clock get gmt-offset]

# Sessions per VLAN: count active hotspot users by the third octet of their address.
:local counts [:toarray ""]
:local prefixLen [:len $subnetPrefix]
:foreach a in=[/ip hotspot active find] do={
    :local addr [/ip hotspot active get $a address]
    :if ([:pick $addr 0 $prefixLen] = $subnetPrefix) do={
        :local rest [:pick $addr ($prefixLen + 1) [:len $addr]]
        :local octet [:pick $rest 0 [:find $rest "."]]
        :local n [:tonum ($counts->$octet)]
        :if ([:typeof $n] != "num") do={ :set n 0 }
        :set ($counts->$octet) ($n + 1)
    }
}

:local sitesJson ""
:foreach v in=[/interface vlan find] do={
    :local vid [/interface vlan get $v vlan-id]
    :local n [:tonum ($counts->[:tostr $vid])]
    :if ([:typeof $n] != "num") do={ :set n 0 }
    :if ($sitesJson != "") do={ :set sitesJson ($sitesJson . ",") }
    :set sitesJson ($sitesJson . "{\"vlan_id\":" . $vid . ",\"sessions\":" . $n . "}")
}

:local pppoeJson ""
:foreach p in=[/ppp active find] do={
    :if ($pppoeJson != "") do={ :set pppoeJson ($pppoeJson . ",") }
    :set pppoeJson ($pppoeJson . "\"" . [$esc [/ppp active get $p name]] . "\"")
}

:local payload ("{\"seq\":" . $opsHeartbeatSeq . ",\"router_ts\":\"" . $routerTs . "\",\"gmt_offset\":" . $gmtOffset . ",\"sites\":[" . $sitesJson . "],\"pppoe\":[" . $pppoeJson . "]}")

:if ($dryRun) do={
    :put $payload
} else={
    # Never let a failed POST block or throw. output=none keep-result=no stops
    # a file being written every 60s (flash wear); check-certificate=yes keeps
    # the shared secret from being interceptable.
    :onerror e in={
        /tool fetch url=$ingestUrl http-method=post http-header-field=("Content-Type: application/json,X-Ingest-Token: " . $ingestToken) http-data=$payload output=none keep-result=no check-certificate=yes
    } do={}
}

# ---------------------------------------------------------------------------
# INSTALL (human, once):
#   1. Fill in the three CONFIGURE lines above.
#   2. /system script add name=ops-heartbeat policy=read,test source="<paste>"
#      (policy needs 'test' for /tool fetch and 'read' for the reads; the
#       script does not need write.)
#   3. /system script run ops-heartbeat        <- dryRun=true prints the payload.
#      Check: date looks like 2026-09-21, gmt_offset is 14400, vlan_ids match
#      /interface vlan print, pppoe names match /ppp active print.
#   4. Set dryRun to false, re-run once by hand, and confirm Ops shows the
#      snapshot (an unauthenticated POST should return 401, a wrong URL nothing).
#   5. /system scheduler add name=ops-heartbeat interval=1m start-time=startup on-event="/system script run ops-heartbeat"
# Not done here on purpose: /ip hotspot user reads for revenue belong to 4.7,
# and Ops does not parse them yet.
