# JASIRI NET OPS — read-only heartbeat (Phase 4.3, revenue added in 4.7)
#
# Reads /interface vlan, /ip hotspot active, /ppp active and /ip hotspot user,
# and POSTs ONE JSON payload for the whole fleet to Ops every 60s. Changes
# nothing on the router, disconnects nobody.
# Rollback: /system scheduler disable ops-heartbeat
#
# A HUMAN pastes this onto the router — it is never run by an agent.
# Before pasting, check for name collisions:
#     /system script print where name~"ops-heartbeat"
#     /system scheduler print where name~"ops-heartbeat"
#
# Payload contract (fixed by routers/monitoring.py validate_snapshot):
#   {"seq":N,"router_ts":"YYYY-MM-DD HH:MM:SS","gmt_offset":"14400",
#    "sites":[{"vlan_id":35,"sessions":12},...],"pppoe":["user",...]}
#
# Whenever the router's clock minute is a multiple of $usersEvery, the run also
# carries the two revenue keys:
#   "users":[{"n":"254716855331-1:FA","p":"Quick Surf10","e":"2026-09-21 17:00:08","v":35},...]
#   "active":[{"v":35,"n":"254716855331-1:FA"},...]
# A user's "v" is the VLAN of the hotspot server its ACCOUNT is bound to, and
# it is the reliable way to place a sale: it is true whether or not that
# person is online when the list is read. "active" only says where someone is
# right now, so a pass bought and finished between two users runs was
# unplaceable before this key existed. Absent "v" = bound to 'all' or to a
# non-VLAN server; Ops falls back to "active" then to the buyer's last known
# site, exactly as it did before.
# "users" is the whole hotspot user list (340 accounts, ~20 KB) — it is NOT
# sent every minute because /tool fetch caps http-data at roughly 64 KB and
# sales do not move minute to minute. "active" is only sent alongside it: it
# is what attributes a sale to a site, and it is useless without it. An
# ABSENT key means "nothing to say about revenue this run" — Ops must never
# read it as "there are no users", or every account would look cancelled.
#
# gmt_offset is sent as a JSON STRING on purpose: /system clock get gmt-offset
# returns a number on some builds and "+04:00" on others, and an unquoted
# "+04:00" is not valid JSON — the whole payload would be quarantined. Ops
# accepts either form inside the quotes (parse_gmt_offset_minutes).
#
# Assumptions to confirm on the router (see the notes at the bottom):
#   - Every VLAN's subnet is 10.50.<vlan-id>.0/24, so a hotspot session's
#     VLAN is the third octet of its address. Change $subnetPrefix if that
#     convention ever changes.
#   - Every /interface vlan is reported. VLANs Ops doesn't know are
#     quarantined once (not per poll) and can be resolved from Ops.

# --- CONFIGURE (do not commit real values) ---------------------------------
:local ingestUrl "https://YOUR-RENDER-APP.onrender.com/monitoring/ingest"
# No commas in the token: RouterOS separates http-header-field entries with
# commas, so a comma inside the value would split the header.
:local ingestToken "PASTE-MONITORING_INGEST_TOKEN-HERE"
:local subnetPrefix "10.50"
# Send the hotspot user list when the clock minute is a multiple of this (5 =
# at :00, :05, :10 ...). Revenue resolution, nothing more: a sale seen 4
# minutes late is still the same sale on the same day. Set to 1 only if you
# want to pay 20 KB a minute for it. Use a number that divides 60.
:local usersEvery 5
# /tool fetch refuses http-data near 64 KB. Above this size the users list is
# left out of that run rather than risking the whole heartbeat. 340 users is
# about 25 KB, so this leaves room for roughly 700 before it ever triggers.
:local maxPayload 55000
# true = build and print the payload, send nothing. Use for the first run.
:local dryRun true
# ---------------------------------------------------------------------------

:global opsHeartbeatSeq
:global opsHeartbeatFails
:if ([:typeof $opsHeartbeatSeq] = "nothing") do={ :set opsHeartbeatSeq 0 }
:if ([:typeof $opsHeartbeatFails] = "nothing") do={ :set opsHeartbeatFails 0 }
# Globals are cleared on reboot, so seq restarts at 1. That is fine: Ops keys
# idempotency on the payload hash, not on seq, and seq is only read to spot
# gaps within one uptime window.
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
:local d [:tostr [/system clock get date]]
:if ([:pick $d 4 5] != "-") do={
    :local months {"jan"="01";"feb"="02";"mar"="03";"apr"="04";"may"="05";"jun"="06";"jul"="07";"aug"="08";"sep"="09";"oct"="10";"nov"="11";"dec"="12"}
    # Key looked up through a variable, not inline — ($arr->[:pick ...]) is not
    # reliably parsed.
    :local mon [:pick $d 0 3]
    :set d ([:pick $d 7 11] . "-" . ($months->$mon) . "-" . [:pick $d 4 6])
}
:local routerTs ($d . " " . [:tostr [/system clock get time]])
:local gmtOffset [:tostr [/system clock get gmt-offset]]

# Sessions per VLAN: count active hotspot users by the third octet of their
# address. Users on the hotspot1 bridge (192.168.180.0/22) do not match the
# prefix and are skipped, which is intended — it is not a VLAN site.
# Decided by the clock, NOT by $opsHeartbeatSeq: RouterOS did not keep that
# global between scheduled runs (seq reached Ops as 1 every time), so a
# run-count test never came true and the list stopped being sent.
:local minute (([:tonum [:pick $routerTs 14 15]] * 10) + [:tonum [:pick $routerTs 15 16]])
:local sendUsers (($minute % $usersEvery) = 0)
:local counts [:toarray ""]
:local activeJson ""
:local prefixLen [:len $subnetPrefix]
:foreach a in=[/ip hotspot active find] do={
    # :tostr because 'address' is an ip value, not a string — :pick and :len
    # on a raw ip value do not give characters.
    :local addr [:tostr [/ip hotspot active get $a address]]
    :if ([:pick $addr 0 $prefixLen] = $subnetPrefix) do={
        :local rest [:pick $addr ($prefixLen + 1) [:len $addr]]
        :local dot [:find $rest "."]
        :if ([:typeof $dot] = "num") do={
            :local vid [:pick $rest 0 $dot]
            # "v" prefix: a bare numeric key like "35" can be taken as a list
            # index rather than a key, which silently loses the count.
            :local key ("v" . $vid)
            :local n ($counts->$key)
            :if ([:typeof $n] != "num") do={ :set n 0 }
            :set ($counts->$key) ($n + 1)
            # Same walk, so the headcount and the sale attribution can never
            # disagree about which VLAN someone is on.
            :if ($sendUsers) do={
                :local who [:tostr [/ip hotspot active get $a user]]
                :if ($activeJson != "") do={ :set activeJson ($activeJson . ",") }
                :set activeJson ($activeJson . "{\"v\":" . $vid . ",\"n\":\"" . [$esc $who] . "\"}")
            }
        }
    }
}

# Hotspot server name -> VLAN id, for the user list below.
#
# This is what makes a sale placeable. "active" can only say where someone is
# while they are online, and a short pass is often bought and finished between
# two users runs — on 2026-09-22 that was 53 sales (KES 840) that Ops could
# see but could not attribute. A user's SERVER does not expire: one hotspot
# server per VLAN means the account record itself says where it belongs,
# whether or not anyone is connected at the moment it is read.
#
# The VLAN is read out of the SERVER NAME (hs-v55 -> 55), not resolved via
# /interface — confirmed on the live router (2026-09-22) that hotspot servers
# here are not bound to an interface literally named "vlan<id>" (the lookup
# came back empty for all of them), while every site server IS named
# "hs-v<vlan-id>" with no exceptions. hotspot1 (the non-VLAN bridge) and
# PHASE3 (not a site server) don't match the prefix and correctly get no
# entry. If servers are ever renamed off this convention, this needs to go
# back to an interface-based lookup instead.
#
# Built once per users run rather than per user: ~20 servers, 340 users.
# "s" prefix for the same reason "v" is used above — a server named "1" would
# otherwise be read as a list index.
:local srvVlan [:toarray ""]
:if ($sendUsers) do={
    :foreach h in=[/ip hotspot find] do={
        :local nm [:tostr [/ip hotspot get $h name]]
        :if ([:pick $nm 0 4] = "hs-v") do={
            :local vnum [:tonum [:pick $nm 4 [:len $nm]]]
            # A server named e.g. "hs-view" would also start with "hs-v" but
            # not parse as a number; :tonum returns "nothing" for it rather
            # than a bogus VLAN, so it is skipped the same as hotspot1/PHASE3.
            :if ([:typeof $vnum] = "num") do={
                # Key looked up through a variable, not inline — same reason
                # as the clock parsing above: ($arr->("s" . $nm)) is not
                # reliably parsed and silently never matches on read.
                :local key ("s" . $nm)
                :set ($srvVlan->$key) $vnum
            }
        }
    }
}

# The hotspot user list — one entry per sellable account, not per session.
# Ops decides what is new; the router just reports.
:local usersJson ""
:if ($sendUsers) do={
    :foreach u in=[/ip hotspot user find] do={
        # One get for the whole record: an unset 'comment' read on its own
        # is nothing, and reading it per-property risks failing the run.
        :local rec [/ip hotspot user get $u]
        :local nm [:tostr ($rec->"name")]
        :local pf [:tostr ($rec->"profile")]
        :local cm [:tostr ($rec->"comment")]
        # A user with no explicit profile IS on 'default' as far as the
        # router is concerned; say so rather than sending an empty string
        # that Ops would have to treat as an unknown package.
        :if ([:len $pf] = 0) do={ :set pf "default" }
        # "Exp: 2026-09-21 17:00:08 | MAC: ..." — take the 19 characters
        # after the marker. No marker (or a shorter comment) sends "", and
        # Ops skips that user rather than guessing an expiry.
        :local ex ""
        :local at [:find $cm "Exp: "]
        :if ([:typeof $at] = "num") do={ :set ex [:pick $cm ($at + 5) ($at + 24)] }
        # The VLAN this account's own hotspot server sits on. Omitted (not
        # sent as 0) when the user is bound to 'all', to a non-VLAN server, or
        # to a server that no longer exists — an absent key means "the router
        # has nothing to say about where this account lives", which Ops reads
        # as a reason to fall back, not as a VLAN.
        :local vj ""
        :local srvKey ("s" . [:tostr ($rec->"server")])
        :local uv ($srvVlan->$srvKey)
        :if ([:typeof $uv] = "num") do={ :set vj (",\"v\":" . [:tostr $uv]) }
        :if ([:len $nm] > 0) do={
            :if ($usersJson != "") do={ :set usersJson ($usersJson . ",") }
            :set usersJson ($usersJson . "{\"n\":\"" . [$esc $nm] . "\",\"p\":\"" . [$esc $pf] . "\",\"e\":\"" . [$esc $ex] . "\"" . $vj . "}")
        }
    }
}

:local sitesJson ""
:foreach v in=[/interface vlan find] do={
    :local vid [/interface vlan get $v vlan-id]
    :local n ($counts->("v" . [:tostr $vid]))
    :if ([:typeof $n] != "num") do={ :set n 0 }
    :if ($sitesJson != "") do={ :set sitesJson ($sitesJson . ",") }
    :set sitesJson ($sitesJson . "{\"vlan_id\":" . [:tostr $vid] . ",\"sessions\":" . [:tostr $n] . "}")
}

:local pppoeJson ""
:foreach p in=[/ppp active find] do={
    :if ($pppoeJson != "") do={ :set pppoeJson ($pppoeJson . ",") }
    :set pppoeJson ($pppoeJson . "\"" . [$esc [:tostr [/ppp active get $p name]]] . "\"")
}

:local payload ("{\"seq\":" . [:tostr $opsHeartbeatSeq] . ",\"router_ts\":\"" . $routerTs . "\",\"gmt_offset\":\"" . $gmtOffset . "\",\"sites\":[" . $sitesJson . "],\"pppoe\":[" . $pppoeJson . "]")
# Appended only on a users run — see the payload contract at the top. The
# keys are absent the rest of the time, which is NOT the same as empty.
:if ($sendUsers) do={
    :local withUsers ($payload . ",\"users\":[" . $usersJson . "],\"active\":[" . $activeJson . "]}")
    # The user list grows with the customer base. Past the cap the POST would
    # be refused and this minute's up/down report would be lost with it, so
    # liveness wins: send without revenue this run and say so in the log.
    :if ([:len $withUsers] < $maxPayload) do={
        :set payload $withUsers
    } else={
        :set payload ($payload . "}")
        :log warning ("ops-heartbeat: user list too large (" . [:len $withUsers] . " bytes), sent without revenue")
    }
} else={
    :set payload ($payload . "}")
}

:if ($dryRun) do={
    :put $payload
} else={
    # Never let a failed POST block or throw. output=none discards the response
    # so no file is written every 60s (flash wear); check-certificate=yes keeps
    # the shared secret from being interceptable — it needs a CA store, see
    # step 1 of INSTALL.
    #
    # keep-result=no is NOT passed: on 7.24.2 the pair output=none keep-result=no
    # is rejected outright with "please use 'output' option", and the POST never
    # goes out. output=none alone already suppresses the file.
    #
    # Both branches only touch the GLOBAL fail counter and log from inside the
    # handler. An earlier version set a :local from the do= block and read it
    # after; the block is its own scope, the write did not reach the outer
    # variable, and every failure was recorded as a success — silently.
    :onerror e in={
        /tool fetch url=$ingestUrl http-method=post http-header-field=("Content-Type: application/json,X-Ingest-Token: " . $ingestToken) http-data=$payload output=none check-certificate=yes
        :set opsHeartbeatFails 0
    } do={
        # Swallowing the error entirely makes a heartbeat that never arrives
        # impossible to diagnose; one line a minute makes the log useless.
        # Log the first failure, then roughly hourly while it persists.
        :set opsHeartbeatFails ($opsHeartbeatFails + 1)
        :if ($opsHeartbeatFails = 1 || $opsHeartbeatFails = 60) do={
            :log warning ("ops-heartbeat: POST failed: " . $e)
            :if ($opsHeartbeatFails = 60) do={ :set opsHeartbeatFails 1 }
        }
    }
}

# ---------------------------------------------------------------------------
# INSTALL (human, once):
#
#   1. CA certificates. check-certificate=yes fails unless the router has a
#      trust store, and RouterOS does not ship with one. Check first:
#          /certificate print where trusted=yes
#      If that is empty, import a CA bundle before going further:
#          /tool fetch url="https://curl.se/ca/cacert.pem" mode=https check-certificate=no
#          /certificate import file-name=cacert.pem passphrase=""
#      Then confirm TLS to Ops works AND shows its result (not silenced):
#          /tool fetch url="https://YOUR-APP.onrender.com/monitoring/ingest" http-method=post http-header-field="Content-Type: application/json,X-Ingest-Token: test" http-data="{}" output=user check-certificate=yes
#      A 401 here is the SUCCESS case — it means TLS verified and Ops answered.
#      (On 7.24.2 that 401 surfaces as "ERROR parsing http: 401 should contain
#       www-authenticate header" — still a success: Ops replied.) Omitting the
#      Content-Type header makes this POST hang instead: "timeout waiting data".
#      "certificate verification failed" means step 1 is not done.
#
#   2. Paste the script. Do NOT use `/system script add source="..."` from the
#      terminal — the script contains double quotes and the shell-style
#      quoting will mangle it. Use the Winbox/WebFig script editor instead:
#          System > Scripts > Add New, name=ops-heartbeat, policy=read,write,test
#      ('write' is included because /tool fetch is refused without it on some
#       builds even with output=none; drop it if your build does not need it.)
#
#   3. /system script run ops-heartbeat        <- dryRun=true prints the payload.
#      Check: router_ts looks like 2026-09-21 15:04:05, gmt_offset is "14400"
#      or "+04:00", vlan_ids match /interface vlan print, sessions are non-zero
#      on sites you know are busy, pppoe names match /ppp active print.
#      SEND THIS PAYLOAD TO OPS BEFORE GOING FURTHER — it is the one check that
#      the VLAN-from-third-octet assumption actually holds on this router.
#
#   4. Set dryRun to false, run once by hand, and confirm the snapshot landed.
#      /log print where message~"ops-heartbeat" shows any POST failure.
#
#   5. /system scheduler add name=ops-heartbeat interval=1m start-time=startup \
#          policy=read,write,test on-event="/system script run ops-heartbeat"
#      The scheduler's own policy must include the script's, or the run is
#      silently refused.
#
# UPGRADING AN ALREADY-INSTALLED HEARTBEAT (4.7):
#
#   The scheduler keeps running the old copy until the script body is
#   replaced — editing this file changes nothing on the router. Replace the
#   body in System > Scripts > ops-heartbeat, keeping the same name so the
#   scheduler still finds it, and keep your ingestUrl/ingestToken values.
#
#   Set dryRun true for one run first. The users payload only appears when the
#   clock minute is a multiple of $usersEvery, so run it then, or set
#   usersEvery to 1 for the test (and back to 5 after). Check before sending:
#     - "users" holds ~340 entries, each with a non-empty "e"
#     - most "users" entries carry a "v" matching the site that account is
#       sold at. A run where NO user has one means the hs-v<N> naming
#       convention doesn't hold here: check /ip hotspot print for the server
#       names. Revenue still works without it, it just goes back to being
#       unplaceable for short passes.
#     - "active" entries carry the VLAN the user is really on
#     - the whole payload is well under 64 KB (:put [:len $payload])
#
#   THE FIRST REAL USERS PAYLOAD WRITES A BASELINE, NOT SALES. Every existing
#   account is recorded once at price 0 so that today's revenue is not 340
#   imaginary sales. Only movement after that is money. This means revenue
#   starts at 0 on the day you install it and is correct from then on.

