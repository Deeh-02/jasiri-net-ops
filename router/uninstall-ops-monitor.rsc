# JASIRI NET OPS — remove the CCR2116 network monitor completely.
#
# Removes the two schedulers, the two scripts and every global they used
# (all named opsNm*). Touches nothing else. Safe to run twice: a [find] that
# matches nothing removes nothing. Run it in the terminal, or with
#   /import uninstall-ops-monitor.rsc
# after uploading this file. Check afterwards (all three should be empty):
#   /system scheduler print where name~"^ops-(live|sessions)"
#   /system script print where name~"^ops-(live|sessions)"
#   /system script environment print where name~"^opsNm"
#
# This file is the ONE place write commands are allowed — it is not part of
# the monitor and tests/test_router_scripts.py does not lint it.

/system scheduler remove [find where name="ops-live" || name="ops-sessions"]
/system script remove [find where name="ops-live" || name="ops-sessions"]
/system script environment remove [find where name~"^opsNm"]
