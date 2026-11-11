"""How to do things in OPS, for the Ask assistant ("how do I send a
broadcast?"). Each guide is tied to the permission its page or button needs,
so a person only gets steps for what their role can actually do.

`where` is the sidebar section and the page, exactly as the menu shows them
(tests/test_app_guide.py checks both against static/index.html), so when a
page or button is renamed, change it here too.

`needs`: None = everyone who can log in; ADMIN = the built-in admin only;
otherwise a list of (section, action) and any one of them is enough.
`also`: permissions needed as well (all of them), e.g. the OLT buttons only
work with Network & OLT switched on, the same rule the server uses.
`quiet`: never mention it to someone who can't do it (money).
"""
import re

ADMIN = "admin"
_NET = [("sites", "view_network")]

GUIDES = [
    # ---- Everyone ----
    {"id": "change_password", "title": "Change your password",
     "words": "password reset forgot login change my password",
     "where": None, "needs": None,
     "steps": ["Click your name at the top right, then Settings.",
               "Open the Password tab.",
               "Type your current password, then the new one twice.",
               "Click Save Password."]},
    {"id": "edit_profile", "title": "Change your name, phone number or email",
     "words": "profile phone number email name my details update account",
     "where": None, "needs": None,
     "steps": ["Click your name at the top right, then Settings.",
               "On the Profile tab, change your first name, last name, phone number or email.",
               "Click Save Changes. Alert SMS and WhatsApp go to the phone number saved here."]},
    {"id": "alert_channels", "title": "Choose how you get site alerts (in-app, SMS, WhatsApp)",
     "words": "notifications alerts sms whatsapp receive get alerts bell delivery settings channel",
     "where": None, "needs": None,
     "steps": ["Click your name at the top right, then Settings.",
               "Open the Notifications tab.",
               "Switch on In-app (the bell), SMS and/or WhatsApp. SMS and WhatsApp use the phone number on your Profile tab.",
               "Click Save Changes."],
     "notes": ["You only get site down and back-online alerts if your role has Receive Alerts. If you get none, ask an admin."]},
    {"id": "notifications", "title": "Read and clear your notifications",
     "words": "notifications bell unread mark read inbox",
     "where": ("Alerts", "Notifications"), "needs": None,
     "steps": ["Go to Alerts → Notifications in the sidebar (or click the bell).",
               "Use All or Unread to filter.",
               "Click Mark all read to clear the unread count."]},
    {"id": "ask_assistant", "title": "Use the Ask assistant",
     "words": "ask assistant ai chat new conversation clear chat",
     "where": None, "needs": None,
     "steps": ["Click Ask and type a question in plain words.",
               "Under each answer, Checked: shows what was looked up.",
               "Click New to start a fresh conversation."]},

    # ---- Batteries ----
    {"id": "add_battery", "title": "Add a battery",
     "words": "add new battery register",
     "where": ("Operations", "Batteries"), "needs": [("batteries", "add")],
     "steps": ["Go to Operations → Batteries.",
               "Click + Add Battery.",
               "Enter the battery number (for example BAT-002); serial number, model and capacity are optional.",
               "Click Add Battery."]},
    {"id": "edit_battery", "title": "Edit or deactivate a battery",
     "words": "edit change battery details deactivate remove delete battery",
     "where": ("Operations", "Batteries"), "needs": [("batteries", "edit"), ("batteries", "delete")],
     "steps": ["Go to Operations → Batteries.",
               "In the battery's row, use the pencil icon (Edit battery) to change its details, then Save Changes.",
               "To take a battery out of use, use the Deactivate battery icon in its row and confirm."]},
    {"id": "move_battery", "title": "Move a battery to a site",
     "words": "move battery take battery to site put battery send battery swap battery transfer power deliver",
     "where": ("Operations", "Batteries"), "needs": [("movements", "create")],
     "steps": ["Go to Operations → Batteries.",
               "In the battery's row, click the Move battery icon.",
               "Pick the site it's going to, pick a reason (for example Site has no power or Returning to storage) and who is moving it.",
               "Click Confirm Move. The move then shows on the Movements page."]},
    {"id": "track_movement", "title": "Update a battery move (in transit, arrived, site online)",
     "words": "movement in transit arrived site online still down cancel movement track battery move",
     "where": None, "needs": [("movements", "manage")],
     "steps": ["Click Movement at the top of the page to open Movements.",
               "On the move, click Mark In Transit when it leaves.",
               "Click Mark Arrived when it reaches the site.",
               "Then click Site Online if the site came back, or Still Down if it didn't. OPS shows a hint from monitoring to help.",
               "To call off a move, use its Cancel movement button."]},

    # ---- Sites (contact list) ----
    {"id": "add_site", "title": "Add, edit or delete a site's contact details",
     "words": "add site new site edit site contact person phone address delete site home base",
     "where": ("Operations", "Sites"), "needs": [("sites", "add"), ("sites", "edit"), ("sites", "delete")],
     "steps": ["Go to Operations → Sites.",
               "Click + Add Site, or the pencil icon on a site to edit it, or the bin icon to delete it.",
               "Fill in the site name, contact person, phone number and address (optional).",
               "Click Add Site or Save Changes."]},

    # ---- Network ----
    {"id": "site_status", "title": "Check which sites are up or down",
     "words": "site status down offline online up network status needs attention uptime",
     "where": ("Network", "Network Status"), "needs": [("sites", "view_status")],
     "steps": ["Go to Network → Network Status.",
               "Needs attention lists sites that are down or dropping; All sites lists everything, problems first.",
               "Click Open site (or a row) for that site's report: people online, busiest hours and what happened.",
               "The Trends tab shows every site over a day, week or month, worst uptime first."]},
    {"id": "acknowledge_site", "title": "Acknowledge a site that's down (known issue)",
     "words": "acknowledge known issue silence site down note un-acknowledge",
     "where": ("Network", "Network Status"), "needs": [("sites", "manage_monitoring")],
     "steps": ["Go to Network → Network Status.",
               "Under Needs attention, click Acknowledge on the site and add a short note.",
               "It moves to Known issues and stops counting in the headline; it clears by itself when the site comes back.",
               "Click Un-acknowledge to undo."]},
    {"id": "manage_monitoring", "title": "Add a site to monitoring",
     "words": "manage sites monitoring add site to monitoring pppoe vlan liveness new on the network ignore",
     "where": ("Network", "Network Status"), "needs": [("sites", "manage_monitoring")],
     "steps": ["Go to Network → Network Status and click Manage sites.",
               "Under New on the network, pick something the router sees, or click + Add Site.",
               "Choose which site it is, how OPS knows it's alive (PPPoE login, or Activity) and the PPPoE username and VLAN.",
               "Click Save."]},
    {"id": "package_prices", "title": "Set hotspot package prices", "quiet": True,
     "words": "package price hotspot packages cost free",
     "where": ("Network", "Network Status"), "needs": [("sites", "manage_monitoring")],
     "steps": ["Go to Network → Network Status, click Manage sites, then the Packages tab.",
               "Click Manage on the package, set the price in KES and how long it lasts, or tick Given away free.",
               "Click Save."]},
    {"id": "network_analysis", "title": "Check the internet links, traffic and PPPoE clients",
     "words": "network analysis internet link airtel veenet traffic bandwidth pppoe clients online expired ccr router",
     "where": ("Network", "Network Analysis"), "needs": [("sites", "view_network")],
     "steps": ["Go to Network → Network Analysis.",
               "Internet links shows each uplink; the graph shows download and upload (Live, 1h, 6h, 24h or 7d).",
               "PPPoE clients lists who is connected; search by name, username or IP, and filter Active or Expired."]},

    # ---- OLT ----
    {"id": "olt_find_onu", "title": "Find a customer's ONU and check its signal",
     "words": "olt onu fibre find onu signal dbm los offline power fail smartolt zone splitter",
     "where": ("Network", "OLT"), "needs": [("sites", "view_network")],
     "steps": ["Go to Network → OLT.",
               "On Configured, search by name, serial, zone or VLAN, or filter by zone or PON port.",
               "Click View on the ONU for its live status, signal, ports, speed, traffic graphs and up and down history.",
               "LOS now lists every ONU that has lost its fibre signal."]},
    {"id": "olt_ack_los", "title": "Acknowledge a LOS alert",
     "words": "los alert acknowledge fibre cut los now",
     "where": ("Network", "OLT"), "needs": [("sites", "view_network")],
     "steps": ["Go to Network → OLT and open LOS now.",
               "Click Acknowledge on the alert and confirm, so others know someone is on it."]},
    {"id": "olt_authorize", "title": "Authorize a new ONU",
     "words": "authorize new onu add onu install unconfigured register onu",
     "where": ("Network", "OLT"), "needs": [("olt", "authorize")], "also": _NET,
     "steps": ["Go to Network → OLT and open Unconfigured. A newly connected ONU shows up within a minute (click Refresh).",
               "Click Authorize on it.",
               "Pick the ONU type, Routing or Bridging, the VLAN, zone and splitter, and type a name (for example Mark_JS45). Speeds are optional.",
               "Click Review and authorize, check the summary and confirm."]},
    {"id": "olt_link", "title": "Link an ONU to a customer or hotspot site",
     "words": "link onu customer site unlink not a customer suggested links olt links",
     "where": ("Network", "OLT"), "needs": [("olt", "link")], "also": _NET,
     "steps": ["Go to Network → OLT and open Links.",
               "Under Suggested links, confirm the ones that are right.",
               "Under ONUs with nothing linked, click Link to a customer…, Link to a site… or Not a customer.",
               "To undo a link, click Unlink on it."]},
    {"id": "olt_control", "title": "Reboot, disable or enable an ONU",
     "words": "reboot restart disable enable onu turn off onu",
     "where": ("Network", "OLT"), "needs": [("olt", "control")], "also": _NET,
     "steps": ["Go to Network → OLT, find the ONU and click View.",
               "Under Actions, click Reboot, Disable ONU or Enable ONU.",
               "Check the customer and serial in the box and confirm. The result shows under Actions on this ONU from OPS."]},
    {"id": "olt_configure", "title": "Change an ONU's settings (speed, VLANs, ports, location, ONU mode, resync)",
     "words": "configure onu speed profile vlan vlans ethernet port lan location zone splitter name address contact onu mode routing bridging wan pppoe static ip dhcp resync config",
     "where": ("Network", "OLT"), "needs": [("olt", "configure")], "also": _NET,
     "steps": ["Go to Network → OLT, find the ONU and click View.",
               "Under Configuration, click Resync config, Speed…, Ethernet port…, Location…, VLANs… or ONU mode…. You can also click the zone, splitter, name, address or contact to change them.",
               "Make the change, check the before and after in the box, then click Apply.",
               "OPS reads the ONU back afterwards and tells you if anything didn't take."],
     "notes": ["Restore factory defaults and Wi-Fi settings aren't in OPS; use SmartOLT for those."]},
    {"id": "olt_delete", "title": "Delete an ONU from the OLT",
     "words": "delete onu remove onu",
     "where": ("Network", "OLT"), "needs": [("olt", "delete")], "also": _NET,
     "steps": ["Go to Network → OLT, find the ONU and click View.",
               "Under Configuration, click Delete ONU….",
               "Type the serial number to confirm, then click Apply. This can't be undone."]},
    {"id": "olt_password", "title": "See a customer's PPPoE password",
     "words": "pppoe password show password customer password",
     "where": ("Network", "OLT"), "needs": [("olt", "view_password")], "also": _NET,
     "steps": ["Go to Network → OLT, find the ONU and click View.",
               "Next to PPPoE password, click Show. It hides again after 30 seconds, and each view is recorded."]},

    # ---- Technical Activity Log ----
    {"id": "submit_report", "title": "Submit an activity report (a job you did)",
     "words": "submit activity report job installation repair maintenance log work technician visit",
     "where": ("Technical Activity Log", "Submit Report"), "needs": [("activity_log", "submit")],
     "steps": ["Go to Technical Activity Log → Submit Report.",
               "Pick the date, category and activity type, and answer any extra questions it asks.",
               "Type the area and where it was, pick the outcome and the technicians, and add the time spent and what was done.",
               "Click Submit report."],
     "notes": ["If it finishes an earlier job, use + This finishes an earlier report.",
               "You can edit your own report for 48 hours: open it from Reports and click Edit."]},
    {"id": "activity_reports", "title": "Find or export activity reports",
     "words": "activity reports search filter export csv technician reports edit report delete report",
     "where": ("Technical Activity Log", "Reports"), "needs": [("activity_log", "view"), ("activity_log", "submit")],
     "steps": ["Go to Technical Activity Log → Reports.",
               "Search, or filter by date, category, type, area, technician or outcome.",
               "Click a report to open it. Export CSV downloads what's listed."]},
    {"id": "activity_overview", "title": "See the activity overview",
     "words": "activity overview summary jobs per technician",
     "where": ("Technical Activity Log", "Overview"), "needs": [("activity_log", "view_overview")],
     "steps": ["Go to Technical Activity Log → Overview.", "Pick From and To, then click Apply."]},
    {"id": "activity_types", "title": "Add or change activity types", "words": "activity types category manage questions",
     "where": ("Technical Activity Log", "Activity Types"), "needs": ADMIN,
     "steps": ["Go to Technical Activity Log → Activity Types.",
               "Click + Add category, or open a category to add or change its types and extra questions.",
               "A type you deactivate leaves the Submit form, but old reports keep it."]},

    # ---- Inventory ----
    {"id": "issue_materials", "title": "Issue materials for a job",
     "words": "issue materials give out stock take items job cable router installation",
     "where": ("Inventory", "Stock"), "needs": [("inventory_transactions", "issue")],
     "steps": ["Go to Inventory → Stock and click Issue materials.",
               "Search for each item and add it to the cart with the quantity or length.",
               "Pick the site or destination, the activity (optional) and who it's issued to, and add a note.",
               "Click Issue Materials."]},
    {"id": "return_materials", "title": "Return materials to the store",
     "words": "return materials bring back stock leftover",
     "where": ("Inventory", "Stock"), "needs": [("inventory_transactions", "return")],
     "steps": ["Go to Inventory → Stock and click Return materials.",
               "Search for each item you're returning and add it to the cart.",
               "Add a note and click Return Materials."]},
    {"id": "check_stock", "title": "Check stock levels and history",
     "words": "stock levels in store deployed how many stock history reorder",
     "where": ("Inventory", "Stock"), "needs": [("inventory_items", "view_stock")],
     "steps": ["Go to Inventory → Stock.",
               "Search by name, SKU or serial, and filter by category, In Store or Deployed.",
               "Click an item for its units and history. Export CSV downloads the list."]},
    {"id": "add_item", "title": "Add a new inventory item or units",
     "words": "add item new product inventory receive stock units serial add units",
     "where": ("Inventory", "Items"), "needs": [("inventory_items", "add")],
     "steps": ["Go to Inventory → Items and click + Add Item.",
               "Choose Existing product (to add units of something already set up) or New product.",
               "For a new product, pick the category and enter the SKU, name and spec, then click Next: Add Unit.",
               "Enter the unit details (serial number, quantity or reel length, location, supplier, cost) and click Save Unit or Add Units."]},
    {"id": "log_transaction", "title": "Log a transfer, adjustment, return or write-off",
     "words": "log transaction transfer adjustment write-off write off inventory log move stock",
     "where": ("Inventory", "Transaction Log"), "needs": [("inventory_transactions", "add")],
     "steps": ["Go to Inventory → Transaction Log and click + Log Transaction.",
               "Pick the action (Transfer, Adjustment, Return or Write-off) and search for the item.",
               "Fill in the quantity or length, the locations and a note.",
               "Click Log Transaction."]},
    {"id": "reconcile_cut", "title": "Reconcile a cable cut",
     "words": "reconcile cut cable length used returned offcut reel",
     "where": ("Inventory", "Transaction Log"), "needs": [("inventory_transactions", "reconcile")],
     "steps": ["Go to Inventory → Transaction Log and click the cut that went out (action Out, still pending).",
               "In its details, click Reconcile.",
               "Enter the length used and the length returned, and a new Cut ID for what came back.",
               "Click Reconcile."]},
    {"id": "inventory_categories", "title": "Add or edit inventory categories",
     "words": "inventory category categories manage",
     "where": ("Inventory", "Manage"), "needs": [("inventory_categories", "add"), ("inventory_categories", "edit")],
     "steps": ["Go to Inventory → Manage.",
               "Click + Add Category, or the edit icon on one.",
               "Enter the name, pick the core (Asset, Consumables or Cable) and custody (Per-Job or Custody).",
               "Click Add Category or Save Changes."]},

    # ---- Reports ----
    {"id": "inventory_report", "title": "See the inventory report",
     "words": "inventory report value sku summary cable summary offcuts",
     "where": ("Reports", "Inventory Report"), "needs": [("reports", "view")],
     "steps": ["Go to Reports → Inventory Report.",
               "Switch between SKU Summary, Cable Summary and Offcut Rollup. Export CSV downloads it."]},
    {"id": "sms_status", "title": "Check which alert messages were sent or failed",
     "words": "sms status alert log sent failed whatsapp messages delivered",
     "where": ("Reports", "SMS Status"), "needs": [("reports", "view")],
     "steps": ["Go to Reports → SMS Status.",
               "Pick From and To, and filter by channel (SMS, WhatsApp, In-app) or status (Sent, Failed and so on).",
               "Provider response shows why a message failed."]},
    {"id": "revenue_report", "title": "See the revenue report", "quiet": True,
     "words": "revenue report sales money earnings",
     "where": ("Reports", "Revenue"), "needs": [("sites", "view_revenue")],
     "steps": ["Go to Reports → Revenue.",
               "Pick Day, Week, Month or Year and the period, and what to compare with.",
               "Key findings and the Sites table show what changed and where."]},

    # ---- Alerts ----
    {"id": "send_broadcast", "title": "Send an SMS to customers (broadcast)",
     "words": "broadcast send sms text message customers bulk sms notify customers outage announcement",
     "where": ("Alerts", "Broadcast"), "needs": [("sites", "send_broadcasts")],
     "steps": ["Go to Alerts → Broadcast.",
               "Tick the router(s) the customers are on.",
               "Choose who to include: Active (paying) only, Expired only, Everyone, or Pick customers myself.",
               "Tick the people to text (Tick all / Untick all); search by name, account or phone to find someone, even to text just one person.",
               "Type the message. Click {{NAME}} to put each customer's name in.",
               "Click Send, check the count and confirm Send SMS. It can't be recalled; progress shows under Sent broadcasts."]},
    {"id": "customers", "title": "Add, edit or import customers",
     "words": "customers add customer edit customer phone number import billing export xlsx csv",
     "where": ("Alerts", "Customers"), "needs": [("sites", "send_broadcasts")],
     "steps": ["Go to Alerts → Customers.",
               "Click + Add Customer, or Edit on a customer, fill in the details and click Save.",
               "To import: under Import from the billing export, pick the router, choose the .xlsx or .csv file, click Check file, then Import."],
     "notes": ["Importing again only refreshes plan, expiry and account status; names and phone numbers entered in OPS are kept and nobody is deleted."]},
    {"id": "sms_templates", "title": "Change the alert SMS wording and quiet hours",
     "words": "sms templates alert message wording template quiet hours alert hours",
     "where": ("Alerts", "SMS Templates"), "needs": [("sites", "edit_alert_messages")],
     "steps": ["Go to Alerts → SMS Templates.",
               "Edit the message for each case (down, still down, back online, keeps dropping). Click a label under a box, such as {{SITE_NAME}}, to insert it.",
               "Under When alerts are sent, set the hours if you want alerts only during set hours.",
               "Click Save. Reset to default puts the original text back."]},

    # ---- System ----
    {"id": "add_user", "title": "Add or edit a user",
     "words": "add user new user staff account create login edit user deactivate user",
     "where": ("System", "Users"), "needs": [("users", "add"), ("users", "edit")],
     "steps": ["Go to System → Users.",
               "Click + Add User, or the edit icon on someone.",
               "Enter their name, phone and email, pick their role, and set a password.",
               "Click Save."]},
    {"id": "roles", "title": "Create a role or change what a role can do",
     "words": "roles permissions access give access role allow technician manager",
     "where": ("System", "Roles"), "needs": [("roles", "add"), ("roles", "edit")],
     "steps": ["Go to System → Roles.",
               "Click + Add Role, or the edit icon on a role.",
               "Switch on each area the role may use and tick what it may do there (for example Network & OLT → OLT: Reboot, disable and enable ONUs).",
               "Click Save. People with that role see the change after a refresh."]},
    {"id": "alert_recipients", "title": "Choose who gets site alerts by SMS or WhatsApp",
     "words": "alert recipients who gets alerts receive alerts sms whatsapp",
     "where": ("System", "Roles"), "needs": [("roles", "edit")],
     "steps": ["Go to System → Roles and click Alert Recipients.",
               "Everyone listed has Receive Alerts through their role. Switch SMS or WhatsApp on or off for each person.",
               "To add someone to the list, give their role Receive Alerts (Roles → edit → Site Status)."]},
]

BY_ID = {g["id"]: g for g in GUIDES}

STOP = {"how", "do", "i", "to", "a", "an", "the", "can", "we", "you", "in", "on", "of", "for", "my", "is",
        "it", "and", "or", "what", "where", "with", "app", "ops", "please", "want", "should", "does", "this"}


def _words(text):
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in STOP or len(w) < 2:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


_INDEX = {g["id"]: _words(g["title"] + " " + g["words"]) for g in GUIDES}
# A word in many guides ("see", "add", "onu") says little; a rare one
# ("broadcast", "reconcile") says a lot.
_WEIGHT = {}
for _words_of in _INDEX.values():
    for _w in _words_of:
        _WEIGHT[_w] = _WEIGHT.get(_w, 0) + 1
_WEIGHT = {w: 1 / n for w, n in _WEIGHT.items()}
MIN_SCORE = 0.15    # common words alone ("how do I see it") match nothing
CLOSE = 0.6         # other guides shown if they score this close to the best


def can_do(user, guide, has_permission, is_admin):
    if not all(has_permission(user, s, a) for s, a in guide.get("also", ())):
        return False
    needs = guide["needs"]
    if needs is None:
        return True
    if needs == ADMIN:
        return is_admin(user)
    return any(has_permission(user, s, a) for s, a in needs)


def _shown(guide):
    g = {"title": guide["title"], "steps": guide["steps"]}
    if guide.get("notes"):
        g["notes"] = guide["notes"]
    return g


def how_to(user, topic, has_permission, is_admin):
    """Steps for what this person asked about, only for what they can do."""
    mine = [g for g in GUIDES if can_do(user, g, has_permission, is_admin)]
    asked = _words(topic)
    if not asked:
        return {"guides": [], "topics": [g["title"] for g in mine]}
    scored = sorted(((sum(_WEIGHT[w] for w in asked & _INDEX[g["id"]]), i, g) for i, g in enumerate(GUIDES)),
                    key=lambda t: (-t[0], t[1]))
    best = scored[0][0]
    hits = [g for score, _i, g in scored if best >= MIN_SCORE and score >= best * CLOSE][:4]
    found = [_shown(g) for g in hits if g in mine][:3]
    result = {"guides": found}
    if not found and hits and not hits[0].get("quiet"):
        result["not_on_your_account"] = [g["title"] for g in hits if g not in mine and not g.get("quiet")][:2]
    else:
        result["topics"] = [g["title"] for g in mine] if not found else []
    if not result.get("topics"):
        result.pop("topics", None)
    return result
