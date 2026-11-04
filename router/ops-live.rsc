# JASIRI NET OPS — CCR2116 live reading (Network page), every 15 s.
#
# READ-ONLY. Reads the uplink, the gateway, CPU/memory/temperature and the
# PPPoE session count, and POSTs one JSON message to Ops. Changes nothing on
# the router and disconnects nobody. The command allow-list is enforced by
# tests/test_router_scripts.py — run it after ANY edit to this file.
#
# A HUMAN installs this, following docs/network-monitor-runbook.md. It is
# never run by an agent. Real URL and token are filled in by
# tools/render_router_script.py; the repo copy holds placeholders only.
# Rollback: /import uninstall-ops-monitor.rsc, or remove the two schedulers.
#
# STATELESS ON PURPOSE. On this fleet RouterOS globals did not survive
# between scheduled runs (CCR2004, 2026-09-21: a run counter reached Ops as 1
# every time). So this script remembers nothing: Ops works out "two failed
# gateway checks in a row", restarts (uptime going down) and outages (gaps
# between readings) from what it receives. Globals ARE used, but only WITHIN
# one run, to carry a value out of an error-handling block (a :local set in
# there does not reach the outer scope — seen on the 2004) and as a lock;
# all are named opsNm* so the uninstall file can clear them.
#
# Message (fixed by routers/network.py LiveMessage):
#   {"router":"ccr2116","uptime_s":N,
#    "uplink":{"interface":"sfp-sfpplus1","running":true,"link_rate":"10Gbps",
#              "rx_bps":N,"tx_bps":N,"rx_errors":N,"tx_errors":N,"rx_drops":N,"tx_drops":N,
#              "sfp":{"temp_c":..,"rx_dbm":..,"tx_dbm":..}},
#    "gateway":{"address":"a.b.c.d","source":"auto|config|none","alive":true,"loss_pct":0,
#               "rtt_ms_avg":N,"rtt_ms_max":N,"arp":"reachable"},
#    "internet":{...same as gateway, only when internetPingTarget is set...},
#    "system":{"cpu_pct":N,"mem_used_pct":N,"temp_c":..,"routeros":"7.x"},
#    "pppoe_online":N,"events":[]}
# rx on the uplink = download from Airtel, tx = upload. No clock is sent:
# Ops uses its own receive time for everything.
#
# TO CHECK IN THE DRY RUN (written for RouterOS 7, the 2004's 7.24.2; the
# 2116's own version is not confirmed yet): uptime_s is a plain number of
# seconds; rtt_ms_avg is a number; temp_c is filled; gateway address is the
# Airtel next hop. See the runbook's "Dry run" checklist.

# --- CONFIGURE (do not commit real values) ---------------------------------
:local ingestUrl "https://YOUR-RENDER-APP.onrender.com/network/ingest"
# No commas in the token: RouterOS splits http-header-field on commas.
:local ingestToken "PASTE-NETWORK_INGEST_TOKEN_CCR2116-HERE"
:local routerSlug "ccr2116"
:local uplinkInterface "sfp-sfpplus1"
# Empty = use the gateway of the ACTIVE default route, re-read every run so a
# failover is noticed. Set it only if that gateway is an interface, not an IP.
:local gatewayIp ""
# Optional second check beyond the gateway, e.g. "1.1.1.1". Empty = skip.
:local internetPingTarget ""
# true = build and print the message, send nothing. Use for the first runs.
:local dryRun true
# ---------------------------------------------------------------------------

# JSON string escape for router-sourced text. Same function as
# ops-heartbeat.rsc (proven on 7.24.2).
:local esc do={
    :if ([:len $1] = 0) do={ :return "" }
    :local out ""
    :for i from=0 to=([:len $1] - 1) do={
        :local c [:pick $1 $i ($i + 1)]
        :if ($c = "\"" || $c = "\\") do={ :set out ($out . "\\" . $c) } else={ :set out ($out . $c) }
    }
    :return $out
}

# A value as a JSON number, or null when it isn't one.
:local num do={
    :if ([:typeof $1] = "num") do={ :return [:tostr $1] }
    :return "null"
}

# A RouterOS time (an RTT) as whole milliseconds, or null. Accepts both the
# "00:00:00.004" and the "4ms" forms; anything else is null, never a guess.
:local toMs do={
    :local t [:tostr $1]
    :if ([:len $t] = 0) do={ :return "null" }
    :if ([:typeof [:find $t ":"]] = "num") do={
        :local ms (([:tonum [:pick $t 3 5]] * 60 + [:tonum [:pick $t 6 8]]) * 1000)
        :local dot [:find $t "."]
        :if ([:typeof $dot] = "num") do={
            :local frac ([:pick $t ($dot + 1) [:len $t]] . "000")
            :set ms ($ms + [:tonum [:pick $frac 0 3]])
        }
        :return [:tostr $ms]
    }
    :local msAt [:find $t "ms"]
    :if ([:typeof $msAt] = "num") do={
        :local v [:tonum [:pick $t 0 $msAt]]
        :if ([:typeof $v] = "num") do={ :return [:tostr $v] }
    }
    :if ([:typeof [:find $t "us"]] = "num") do={ :return "0" }
    :return "null"
}

# Uptime in seconds. :tonum of a time gives seconds on RouterOS 7. If this
# build does not, stop here: a wrong uptime would make Ops see a restart on
# every reading.
:local uptimeS [:tonum [/system resource get uptime]]
:if ([:typeof $uptimeS] != "num") do={
    :log error "ops-live: uptime is not a number on this RouterOS build - see the runbook"
    :error "ops-live: no uptime"
}

# Lock with expiry: if the previous run is still going (and started less
# than 60 s ago), skip this one. If globals don't survive between runs on
# this router the lock simply never blocks, which is harmless.
:global opsNmLiveLock
:local locked false
:if ([:typeof $opsNmLiveLock] = "num") do={
    :local age ($uptimeS - $opsNmLiveLock)
    :if ($age >= 0 && $age < 60) do={ :set locked true }
}

:if (!$locked) do={
    :set opsNmLiveLock $uptimeS

    # One log line per 15 minutes at most while a problem lasts, decided by
    # the clock (no counter: it would not survive between runs).
    :local clock [:tostr [/system clock get time]]
    :local mayLog ((([:tonum [:pick $clock 3 5]] % 15) = 0) && ([:tonum [:pick $clock 6 8]] < 15))

    # ---- Uplink ----
    :local upId [/interface find where name=$uplinkInterface]
    :if ([:len $upId] = 0) do={
        :if ($mayLog) do={ :log error ("ops-live: interface " . $uplinkInterface . " not found") }
        :set opsNmLiveLock
        :error "ops-live: uplink interface not found"
    }
    :local upRec [/interface get [:pick $upId 0]]
    :local running [:tostr ($upRec->"running")]
    :if ($running != "true") do={ :set running "false" }
    :local traffic [/interface monitor-traffic $uplinkInterface once as-value]
    :local rxBps ($traffic->"rx-bits-per-second")
    :local txBps ($traffic->"tx-bits-per-second")

    # SFP readings: not every port has them, so a failure leaves them null.
    :global opsNmEth
    :set opsNmEth [:toarray ""]
    :onerror e in={
        :global opsNmEth
        :set opsNmEth [/interface ethernet monitor $uplinkInterface once as-value]
    } do={}
    :local rate [:tostr ($opsNmEth->"rate")]
    :local sfpTemp [:tostr ($opsNmEth->"sfp-temperature")]
    :local sfpRx [:tostr ($opsNmEth->"sfp-rx-power")]
    :local sfpTx [:tostr ($opsNmEth->"sfp-tx-power")]
    :local quoteOrNull do={ :if ([:len $1] = 0) do={ :return "null" }; :return ("\"" . $1 . "\"") }

    # ---- Gateway ----
    :local gwAddr ""
    :local gwSource "none"
    :if ([:len $gatewayIp] > 0) do={
        :set gwAddr $gatewayIp
        :set gwSource "config"
    } else={
        :foreach r in=[/ip route find where dst-address=0.0.0.0/0 && active] do={
            :if ([:len $gwAddr] = 0) do={
                :local g [:tostr [/ip route get $r gateway]]
                # "a.b.c.d%interface" -> "a.b.c.d"
                :local pct [:find $g "%"]
                :if ([:typeof $pct] = "num") do={ :set g [:pick $g 0 $pct] }
                # An interface name instead of an IP can't be pinged: say
                # "not detectable" and let the owner set gatewayIp.
                :if ([:typeof [:toip $g]] = "ip") do={
                    :set gwAddr $g
                    :set gwSource "auto"
                }
            }
        }
    }

    # flood-ping, not /ping: in a script /ping only returns per-packet rows,
    # while flood-ping hands back sent/received/avg-rtt/max-rtt. 3 packets.
    :local check do={
        :global opsNmPingRecv
        :global opsNmPingAvg
        :global opsNmPingMax
        :set opsNmPingRecv 0
        :set opsNmPingAvg ""
        :set opsNmPingMax ""
        :onerror e in={
            /tool flood-ping $1 count=3 size=64 timeout=500ms do={
                :global opsNmPingRecv
                :global opsNmPingAvg
                :global opsNmPingMax
                :if ($sent = 3) do={
                    :set opsNmPingRecv $received
                    :set opsNmPingAvg $"avg-rtt"
                    :set opsNmPingMax $"max-rtt"
                }
            }
        } do={}
        :return $opsNmPingRecv
    }

    :local gwJson ""
    :if ([:len $gwAddr] = 0) do={
        :set gwJson "{\"address\":null,\"source\":\"none\",\"alive\":null}"
    } else={
        :global opsNmPingAvg
        :global opsNmPingMax
        :local got [$check $gwAddr]
        :local avgMs [$toMs $opsNmPingAvg]
        :local maxMs [$toMs $opsNmPingMax]
        :local arp ""
        :foreach a in=[/ip arp find where address=[:toip $gwAddr]] do={
            :local ar [/ip arp get $a]
            :set arp [:tostr ($ar->"status")]
            :if ([:len $arp] = 0) do={ :if (($ar->"complete") = true) do={ :set arp "complete" } }
        }
        :local alive "false"
        :if ($got > 0) do={ :set alive "true" }
        :if ($got = 0) do={ :set avgMs "null"; :set maxMs "null" }
        :set gwJson ("{\"address\":\"" . $gwAddr . "\",\"source\":\"" . $gwSource . "\",\"alive\":" . $alive . ",\"loss_pct\":" . (((3 - $got) * 100) / 3) . ",\"rtt_ms_avg\":" . $avgMs . ",\"rtt_ms_max\":" . $maxMs . ",\"arp\":" . [$quoteOrNull [$esc $arp]] . "}")
    }

    :local inetJson ""
    :if ([:len $internetPingTarget] > 0) do={
        :global opsNmPingAvg
        :local got [$check $internetPingTarget]
        :local avgMs [$toMs $opsNmPingAvg]
        :local alive "false"
        :if ($got > 0) do={ :set alive "true" } else={ :set avgMs "null" }
        :set inetJson (",\"internet\":{\"target\":\"" . $internetPingTarget . "\",\"alive\":" . $alive . ",\"loss_pct\":" . (((3 - $got) * 100) / 3) . ",\"rtt_ms_avg\":" . $avgMs . "}")
    }

    # ---- System ----
    :local res [/system resource get]
    :local total ($res->"total-memory")
    :local memPct "null"
    :if ([:typeof $total] = "num" && $total > 0) do={ :set memPct [:tostr ((($total - ($res->"free-memory")) * 100) / $total)] }
    # Health readings differ by model and version; a failure leaves null.
    :global opsNmTemp
    :set opsNmTemp ""
    :onerror e in={
        :global opsNmTemp
        :foreach h in=[/system health find] do={
            :local hr [/system health get $h]
            :local hn [:tostr ($hr->"name")]
            :if ([:len $opsNmTemp] = 0 && ($hn = "temperature" || $hn = "cpu-temperature" || $hn = "board-temperature1")) do={
                :set opsNmTemp [:tostr ($hr->"value")]
            }
        }
    } do={}

    :local pppoeOnline [:len [/ppp active find]]

    :local payload ("{\"router\":\"" . $routerSlug . "\",\"uptime_s\":" . $uptimeS . \
        ",\"uplink\":{\"interface\":\"" . [$esc $uplinkInterface] . "\",\"running\":" . $running . \
        ",\"link_rate\":" . [$quoteOrNull [$esc $rate]] . ",\"rx_bps\":" . [$num $rxBps] . ",\"tx_bps\":" . [$num $txBps] . \
        ",\"rx_errors\":" . [$num ($upRec->"rx-error")] . ",\"tx_errors\":" . [$num ($upRec->"tx-error")] . \
        ",\"rx_drops\":" . [$num ($upRec->"rx-drop")] . ",\"tx_drops\":" . [$num ($upRec->"tx-drop")] . \
        ",\"sfp\":{\"temp_c\":" . [$quoteOrNull [$esc $sfpTemp]] . ",\"rx_dbm\":" . [$quoteOrNull [$esc $sfpRx]] . ",\"tx_dbm\":" . [$quoteOrNull [$esc $sfpTx]] . "}}" . \
        ",\"gateway\":" . $gwJson . $inetJson . \
        ",\"system\":{\"cpu_pct\":" . [$num ($res->"cpu-load")] . ",\"mem_used_pct\":" . $memPct . \
        ",\"temp_c\":" . [$quoteOrNull [$esc $opsNmTemp]] . ",\"routeros\":\"" . [$esc [:tostr ($res->"version")]] . "\"}" . \
        ",\"pppoe_online\":" . $pppoeOnline . ",\"events\":[]}")

    :if ($dryRun) do={
        :put $payload
    } else={
        # output=none: no file on flash, ever (keep-result=no is NOT added —
        # 7.24.2 rejects that pair). check-certificate=yes: the token must not
        # be interceptable. idle-timeout=5s: a dead link can't hold this run
        # anywhere near the next one, 15 s later.
        :onerror e in={
            /tool fetch url=$ingestUrl http-method=post http-header-field=("Content-Type: application/json,Authorization: Bearer " . $ingestToken) http-data=$payload output=none check-certificate=yes idle-timeout=5s
        } do={
            # A failed POST is normal during an Airtel outage. Ops notices the
            # gap by itself; the log only gets a line every 15 minutes.
            :if ($mayLog) do={ :log warning ("ops-live: POST failed: " . $e) }
        }
    }
    :set opsNmLiveLock
}
