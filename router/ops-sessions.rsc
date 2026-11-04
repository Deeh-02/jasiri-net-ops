# JASIRI NET OPS — CCR2116 PPPoE session list (Network page), every 60 s.
#
# READ-ONLY. Reads /ppp active and POSTs the list to Ops in parts of
# $chunkSize rows, so no single POST comes near RouterOS's 64 KB limit. Ops
# swaps the list in only when every part of a batch has arrived; until then
# the previous full list stays on the page. Never reads /ppp secret — no
# password goes anywhere. The command allow-list is enforced by
# tests/test_router_scripts.py — run it after ANY edit to this file.
#
# Installed by a human following docs/network-monitor-runbook.md; real URL
# and token come from tools/render_router_script.py. Stateless, like
# ops-live.rsc (globals don't survive between runs here) — the batch id is
# the router's uptime, which only ever goes up between restarts.
#
# Each POST (fixed by routers/network.py SessionsMessage):
#   {"router":"ccr2116","batch_id":"u123456","part":1,"parts":3,
#    "rows":[{"name":"john","address":"10.x.x.x","caller_id":"AA:BB:..","uptime_s":N,
#             "service":"pppoe","session_id":"0x81"},...]}
# No sessions at all still sends one part with "rows":[] — "nobody online"
# must replace the old list, not leave it showing.

# --- CONFIGURE (do not commit real values) ---------------------------------
:local ingestUrl "https://YOUR-RENDER-APP.onrender.com/network/ingest"
:local ingestToken "PASTE-NETWORK_INGEST_TOKEN_CCR2116-HERE"
:local routerSlug "ccr2116"
:local chunkSize 30
:local dryRun true
# ---------------------------------------------------------------------------

:local esc do={
    :if ([:len $1] = 0) do={ :return "" }
    :local out ""
    :for i from=0 to=([:len $1] - 1) do={
        :local c [:pick $1 $i ($i + 1)]
        :if ($c = "\"" || $c = "\\") do={ :set out ($out . "\\" . $c) } else={ :set out ($out . $c) }
    }
    :return $out
}
:local quoteOrNull do={ :if ([:len $1] = 0) do={ :return "null" }; :return ("\"" . $1 . "\"") }
:local num do={ :if ([:typeof $1] = "num") do={ :return [:tostr $1] }; :return "null" }

:local uptimeS [:tonum [/system resource get uptime]]
:if ([:typeof $uptimeS] != "num") do={
    :log error "ops-sessions: uptime is not a number on this RouterOS build - see the runbook"
    :error "ops-sessions: no uptime"
}

# Lock with expiry, same as ops-live.rsc.
:global opsNmSessLock
:local locked false
:if ([:typeof $opsNmSessLock] = "num") do={
    :local age ($uptimeS - $opsNmSessLock)
    :if ($age >= 0 && $age < 60) do={ :set locked true }
}

:if (!$locked) do={
    :set opsNmSessLock $uptimeS
    :local clock [:tostr [/system clock get time]]
    :local mayLog ((([:tonum [:pick $clock 3 5]] % 15) = 0) && ([:tonum [:pick $clock 6 8]] < 60))

    :local ids [/ppp active find]
    :local total [:len $ids]
    :local parts (($total + $chunkSize - 1) / $chunkSize)
    :if ($parts < 1) do={ :set parts 1 }
    :local batchId ("u" . $uptimeS)

    # Set from inside the fetch error handler, so it has to be a global (a
    # :local written in there does not reach this scope). One failed part
    # stops the rest of this run: the link is probably down, and Ops would
    # throw an incomplete batch away anyway.
    :global opsNmSessFailed
    :set opsNmSessFailed false

    :local send do={
        :global opsNmSessFailed
        :if ($opsNmSessFailed) do={ :return false }
        # Compared as text: a value passed as a named argument may arrive as
        # the string "true" rather than a boolean.
        :if ([:tostr $dryRun] = "true") do={ :put $body; :return true }
        :onerror e in={
            /tool fetch url=$url http-method=post http-header-field=("Content-Type: application/json,Authorization: Bearer " . $token) http-data=$body output=none check-certificate=yes idle-timeout=5s
        } do={
            :global opsNmSessFailed
            :set opsNmSessFailed true
            :if ([:tostr $mayLog] = "true") do={ :log warning ("ops-sessions: POST failed: " . $e) }
        }
        :return true
    }

    :local head ("{\"router\":\"" . $routerSlug . "\",\"batch_id\":\"" . $batchId . "\",\"parts\":" . $parts . ",\"part\":")
    :local part 1
    :local inChunk 0
    :local rows ""
    :foreach p in=$ids do={
        # One get for the whole record (same reason as ops-heartbeat.rsc).
        # A session that ended between find and get just errors: skip it.
        :global opsNmRow
        :set opsNmRow ""
        :onerror e in={
            :global opsNmRow
            :set opsNmRow [/ppp active get $p]
        } do={}
        :if ([:typeof $opsNmRow] = "array") do={
            :local rec $opsNmRow
            :if ($rows != "") do={ :set rows ($rows . ",") }
            :set rows ($rows . "{\"name\":\"" . [$esc [:tostr ($rec->"name")]] . "\"" . \
                ",\"address\":" . [$quoteOrNull [$esc [:tostr ($rec->"address")]]] . \
                ",\"caller_id\":" . [$quoteOrNull [$esc [:tostr ($rec->"caller-id")]]] . \
                ",\"uptime_s\":" . [$num [:tonum ($rec->"uptime")]] . \
                ",\"service\":" . [$quoteOrNull [$esc [:tostr ($rec->"service")]]] . \
                ",\"session_id\":" . [$quoteOrNull [$esc [:tostr ($rec->"session-id")]]] . "}")
        }
        :set inChunk ($inChunk + 1)
        :if ($inChunk = $chunkSize && $part < $parts) do={
            [$send url=$ingestUrl token=$ingestToken body=($head . $part . ",\"rows\":[" . $rows . "]}") dryRun=$dryRun mayLog=$mayLog]
            :set part ($part + 1)
            :set inChunk 0
            :set rows ""
        }
    }
    # The last (or only) part — also the empty list when nobody is online.
    [$send url=$ingestUrl token=$ingestToken body=($head . $part . ",\"rows\":[" . $rows . "]}") dryRun=$dryRun mayLog=$mayLog]
    :set opsNmSessLock
}
