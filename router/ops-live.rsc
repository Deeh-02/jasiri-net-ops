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
# LINKS ARE DISCOVERED, NOT CONFIGURED. Every enabled default route in the
# main table is one internet link (Airtel, Veenet, anything added later). For
# each, the script takes the route's comment as its name, the gateway, the
# route's distance and whether it is the ACTIVE route, finds the interface the
# route leaves through (following a VLAN to the physical port it rides on,
# because the port's counters include hardware-offloaded traffic and the VLAN's
# do not), and measures that interface. A new link needs no edit here.
#
# Message (fixed by routers/network.py LiveMessage):
#   {"router":"ccr2116","uptime_s":N,
#    "links":[{"interface":"sfp-sfpplus1","label":"airtel Primary","active":true,"distance":1,
#              "running":true,"link_rate":"1Gbps","rx_bps":N,"tx_bps":N,
#              "rx_errors":N,"tx_errors":N,"rx_drops":N,"tx_drops":N,
#              "sfp":{"temp_c":..,"rx_dbm":..,"tx_dbm":..},
#              "gateway":{"address":"a.b.c.d","source":"auto|none","alive":true,"loss_pct":0,
#                         "rtt_ms_avg":N,"rtt_ms_max":N,"arp":"reachable"}}, ...],
#    "internet":{...only when internetPingTarget is set...},
#    "system":{"cpu_pct":N,"mem_used_pct":N,"temp_c":..,"routeros":"7.x"},
#    "pppoe_online":N,"events":[]}
# rx on a link = download, tx = upload. No clock is sent: Ops uses its own
# receive time for everything.
#
# CHECKED IN THE DRY RUN on the CCR2116 (RouterOS 7.24.2): uptime_s is a plain
# number of seconds; the SFP readings are thousandths of a dBm (Ops converts);
# /tool flood-ping is blocked by device-mode, so the gateway check uses
# /ping ... as-value (rtt_ms_avg arrives as a number such as 1.0). NOT yet
# confirmed: how the default routes report their egress interface (the dry run
# for the links rewrite shows it). See the runbook's "Dry run" checklist.

# --- CONFIGURE (do not commit real values) ---------------------------------
:local ingestUrl "https://YOUR-RENDER-APP.onrender.com/network/ingest"
# No commas in the token: RouterOS splits http-header-field on commas.
:local ingestToken "PASTE-NETWORK_INGEST_TOKEN_CCR2116-HERE"
:local routerSlug "ccr2116"
# At most this many links are reported (a safety cap, not a target).
:local maxLinks 4
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

    :local quoteOrNull do={ :if ([:len $1] = 0) do={ :return "null" }; :return ("\"" . $1 . "\"") }

    # /ping ... as-value: one row per packet, nothing printed. A reply row has
    # a "time" (00:00:00.001115); a lost packet has none. NOT /tool flood-ping:
    # the CCR2116's device-mode refuses it ("not allowed by device-mode"). A plain
    # /ping returns only the reply count and prints a table. 3 packets, 200 ms apart.
    # Liveness comes from the row count alone; the RTT maths is fenced off in its
    # own :onerror so a parse problem costs the RTT, never the "alive" answer.
    # Results go out through globals (opsNm*), which the uninstall file clears.
    :local check do={
        :global opsNmPingRecv
        :global opsNmPingTimed
        :global opsNmPingSumUs
        :global opsNmPingMaxUs
        :global opsNmPingAvg
        :global opsNmPingMax
        :set opsNmPingRecv 0
        :set opsNmPingTimed 0
        :set opsNmPingSumUs 0
        :set opsNmPingMaxUs 0
        :set opsNmPingAvg ""
        :set opsNmPingMax ""
        :local addr $1
        :onerror e in={
            :foreach row in=[/ping $addr count=3 interval=200ms as-value] do={
                :global opsNmPingRecv
                :global opsNmPingTimed
                :global opsNmPingSumUs
                :global opsNmPingMaxUs
                :local t [:tostr ($row->"time")]
                :if ([:len $t] > 0) do={
                    :set opsNmPingRecv ($opsNmPingRecv + 1)
                    :onerror pe in={
                        :global opsNmPingTimed
                        :global opsNmPingSumUs
                        :global opsNmPingMaxUs
                        :if ([:typeof [:find $t ":"]] = "num") do={
                            :local us ([:tonum [:pick $t 6 8]] * 1000000)
                            :local dot [:find $t "."]
                            :if ([:typeof $dot] = "num") do={
                                :local frac ([:pick $t ($dot + 1) [:len $t]] . "000000")
                                :set us ($us + [:tonum [:pick $frac 0 6]])
                            }
                            :set opsNmPingTimed ($opsNmPingTimed + 1)
                            :set opsNmPingSumUs ($opsNmPingSumUs + $us)
                            :if ($us > $opsNmPingMaxUs) do={ :set opsNmPingMaxUs $us }
                        }
                    } do={}
                }
            }
        } do={}
        # Microseconds -> "1.1" (milliseconds, one decimal; RouterOS has no floats).
        :if ($opsNmPingTimed > 0) do={
            :local avgUs ($opsNmPingSumUs / $opsNmPingTimed)
            :set opsNmPingAvg (($avgUs / 1000) . "." . (($avgUs % 1000) / 100))
            :set opsNmPingMax (($opsNmPingMaxUs / 1000) . "." . (($opsNmPingMaxUs % 1000) / 100))
        }
        :return $opsNmPingRecv
    }

    # The interface named in a route's gateway text. Accepts "a.b.c.d%name"
    # (immediate-gw) and "a.b.c.d reachable via  name" (gateway-status); anything
    # else gives "". Interface names may contain spaces.
    :local ifaceOf do={
        :local t [:tostr $1]
        :local out ""
        :local pct [:find $t "%"]
        :if ([:typeof $pct] = "num") do={ :set out [:pick $t ($pct + 1) [:len $t]] } else={
            :local via [:find $t " via "]
            :if ([:typeof $via] = "num") do={ :set out [:pick $t ($via + 5) [:len $t]] }
        }
        :while ([:len $out] > 0 && [:pick $out 0 1] = " ") do={ :set out [:pick $out 1 [:len $out]] }
        :return $out
    }

    # ---- Links: one per enabled default route ----
    # Everything that must leave the loop goes through globals; each pass keeps
    # its own :local values.
    :global opsNmLinks
    :global opsNmSeen
    :global opsNmLinkN
    :set opsNmLinks ""
    :set opsNmSeen "|"
    :set opsNmLinkN 0
    # The ACTIVE default route(s): found by the same filter, not a property, so
    # the answer doesn't depend on what `get` happens to call it.
    :local activeIds [/ip route find where dst-address=0.0.0.0/0 && !disabled && routing-table=main && active]
    :foreach r in=[/ip route find where dst-address=0.0.0.0/0 && !disabled && routing-table=main] do={
        :global opsNmLinks
        :global opsNmSeen
        :global opsNmLinkN
        :global opsNmEth
        :local rr [/ip route get $r]
        :local gwRaw [:tostr ($rr->"gateway")]
        :local label [:tostr ($rr->"comment")]
        :local distance [:tostr ($rr->"distance")]
        :local isActive "false"
        :if ([:typeof [:find $activeIds $r]] = "num") do={ :set isActive "true" }

        # Where does this route leave? Try the resolved next hop first.
        :local egress [$ifaceOf [:tostr ($rr->"immediate-gw")]]
        :if ([:len $egress] = 0) do={ :set egress [$ifaceOf $gwRaw] }
        :if ([:len $egress] = 0) do={ :set egress [$ifaceOf [:tostr ($rr->"gateway-status")]] }
        # The gateway as an address ("a.b.c.d%name" -> "a.b.c.d"); a route whose
        # gateway IS an interface has none and cannot be pinged.
        :local gwAddr $gwRaw
        :local pct [:find $gwAddr "%"]
        :if ([:typeof $pct] = "num") do={ :set gwAddr [:pick $gwAddr 0 $pct] }
        :if ([:typeof [:toip $gwAddr]] != "ip") do={
            :if ([:len $egress] = 0 && [:len [/interface find where name=$gwAddr]] > 0) do={ :set egress $gwAddr }
            :set gwAddr ""
        }
        # A VLAN rides on a physical port. Measure the port: it counts traffic
        # the switch chip forwarded in hardware, which the VLAN's own counters miss.
        :local phys $egress
        :local vlan [/interface vlan find where name=$phys]
        :if ([:len $vlan] > 0) do={ :set phys [/interface vlan get [:pick $vlan 0] interface] }

        :local ifId [/interface find where name=$phys]
        :local isNew ([:len $phys] > 0 && [:typeof [:find $opsNmSeen ("|" . $phys . "|")]] = "nil")
        :if ($isNew && [:len $ifId] > 0 && $opsNmLinkN < $maxLinks) do={
            :set opsNmSeen ($opsNmSeen . $phys . "|")
            :set opsNmLinkN ($opsNmLinkN + 1)
            :local upRec [/interface get [:pick $ifId 0]]
            :local running [:tostr ($upRec->"running")]
            :if ($running != "true") do={ :set running "false" }
            :local traffic [/interface monitor-traffic $phys once as-value]
            :local rxBps ($traffic->"rx-bits-per-second")
            :local txBps ($traffic->"tx-bits-per-second")

            # SFP readings: not every port has them, so a failure leaves them null.
            :set opsNmEth [:toarray ""]
            :onerror e in={
                :global opsNmEth
                :set opsNmEth [/interface ethernet monitor $phys once as-value]
            } do={}
            :local rate [:tostr ($opsNmEth->"rate")]
            :local sfpTemp [:tostr ($opsNmEth->"sfp-temperature")]
            :local sfpRx [:tostr ($opsNmEth->"sfp-rx-power")]
            :local sfpTx [:tostr ($opsNmEth->"sfp-tx-power")]

            # This link's gateway: ping it, and read its ARP state (a gateway
            # that ignores ping can still show as resolved there).
            :local gwJson "{\"address\":null,\"source\":\"none\",\"alive\":null}"
            :if ([:len $gwAddr] > 0) do={
                :global opsNmPingAvg
                :global opsNmPingMax
                :local got [$check $gwAddr]
                :local avgMs $opsNmPingAvg
                :local maxMs $opsNmPingMax
                :if ([:len $avgMs] = 0) do={ :set avgMs "null" }
                :if ([:len $maxMs] = 0) do={ :set maxMs "null" }
                :local arp ""
                :foreach a in=[/ip arp find where address=[:toip $gwAddr]] do={
                    :local ar [/ip arp get $a]
                    :set arp [:tostr ($ar->"status")]
                    :if ([:len $arp] = 0) do={ :if (($ar->"complete") = true) do={ :set arp "complete" } }
                }
                :local alive "false"
                :if ($got > 0) do={ :set alive "true" }
                :if ($got = 0) do={ :set avgMs "null"; :set maxMs "null" }
                :set gwJson ("{\"address\":\"" . $gwAddr . "\",\"source\":\"auto\",\"alive\":" . $alive . ",\"loss_pct\":" . (((3 - $got) * 100) / 3) . ",\"rtt_ms_avg\":" . $avgMs . ",\"rtt_ms_max\":" . $maxMs . ",\"arp\":" . [$quoteOrNull [$esc $arp]] . "}")
            }

            :local activeJson "false"
            :if ($isActive = "true") do={ :set activeJson "true" }
            :local distJson "null"
            :if ([:typeof [:tonum $distance]] = "num") do={ :set distJson $distance }
            :local one ("{\"interface\":\"" . [$esc $phys] . "\",\"label\":" . [$quoteOrNull [$esc $label]] . \
                ",\"active\":" . $activeJson . ",\"distance\":" . $distJson . ",\"running\":" . $running . \
                ",\"link_rate\":" . [$quoteOrNull [$esc $rate]] . ",\"rx_bps\":" . [$num $rxBps] . ",\"tx_bps\":" . [$num $txBps] . \
                ",\"rx_errors\":" . [$num ($upRec->"rx-error")] . ",\"tx_errors\":" . [$num ($upRec->"tx-error")] . \
                ",\"rx_drops\":" . [$num ($upRec->"rx-drop")] . ",\"tx_drops\":" . [$num ($upRec->"tx-drop")] . \
                ",\"sfp\":{\"temp_c\":" . [$quoteOrNull [$esc $sfpTemp]] . ",\"rx_dbm\":" . [$quoteOrNull [$esc $sfpRx]] . ",\"tx_dbm\":" . [$quoteOrNull [$esc $sfpTx]] . "}" . \
                ",\"gateway\":" . $gwJson . "}")
            :if ([:len $opsNmLinks] > 0) do={ :set opsNmLinks ($opsNmLinks . ",") }
            :set opsNmLinks ($opsNmLinks . $one)
        }
    }
    :if ([:len $opsNmLinks] = 0) do={
        :if ($mayLog) do={ :log error "ops-live: no default route with a usable interface" }
        :set opsNmLiveLock
        :error "ops-live: no links found"
    }

    :local inetJson ""
    :if ([:len $internetPingTarget] > 0) do={
        :global opsNmPingAvg
        :local got [$check $internetPingTarget]
        :local avgMs $opsNmPingAvg
        :if ([:len $avgMs] = 0) do={ :set avgMs "null" }
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
        ",\"links\":[" . $opsNmLinks . "]" . $inetJson . \
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
