-- Technical Activity Log: the extra questions to match what technicians
-- already report on WhatsApp (installation, support, deployment, hotspot
-- restoration), per the owner's answers of 2026-11-06. Data only: no table or
-- column changes. The new "longtext" field kind needs the matching code, so
-- deploy that first or together with this.
--
-- What each category asks (every field is optional unless noted):
--   Customer Installation   customer (search), client name, account, package,
--                           contact (phone), address / landmark, optical
--                           signal (dBm), materials used
--   Customer Support        client name, account, finding at site, solution,
--                           materials used. The reported problem is the
--                           activity type itself.
--   Network Maintenance     fault, repair, materials used
--     Hotspot chain restoration also: chain, routers restored
--   Infrastructure Deployment  materials used
--     FAT installation, New fibre deployment, New network distribution
--     also: signal before / after splitting (dBm)
--   Infrastructure Relocation, Power & Equipment   materials used
--   Network Inspection      none
--
-- "Materials used" is a LIST ONLY. It is words, not stock: nothing is deducted
-- from Inventory (owner's call: Materials is not built yet; when it is, it
-- replaces this field). Cable length stays here only as the technician's own
-- note in that list, never as a quantity the system adds up.
-- The keys "account" and "client_name" matter: the Overview's repeat visits
-- recognise a customer by an account typed in (a new customer is not in the
-- customer list yet).
--
-- Also adds one type, Network Maintenance > "Cable re-tensioning" (for jobs like
-- re-stringing a sagging cable). Deactivate it on the Manage screen if unwanted.
--
-- Safe to re-run, and it never overwrites an admin's work: a type's questions
-- are only replaced while they are still unset (NULL) or exactly what 0027
-- seeded. Once changed (here or on the Manage screen) they are left alone.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0028_activity_log_field_sets.sql

-- One transaction: it all applies or none of it does.
BEGIN;

INSERT INTO activity_types (category_id, name, sort_order)
SELECT c.id, 'Cable re-tensioning',
       COALESCE((SELECT max(t.sort_order) FROM activity_types t WHERE t.category_id = c.id), 0) + 1
FROM activity_categories c
WHERE c.name = 'Network Maintenance'
ON CONFLICT (category_id, name) DO NOTHING;

CREATE TEMP TABLE field_sets (category text, type_name text, fields jsonb) ON COMMIT DROP;

-- A type's own set wins over its category's, so these rows go in first.
INSERT INTO field_sets VALUES
('Network Maintenance', 'Hotspot chain restoration', '[
    {"key": "chain",            "label": "Chain",                "kind": "text",     "required": false},
    {"key": "routers_restored", "label": "Routers restored",     "kind": "number",   "required": false, "min": 0, "max": 500},
    {"key": "fault",            "label": "Fault encountered",    "kind": "longtext", "required": false},
    {"key": "repair",           "label": "Repair performed",     "kind": "longtext", "required": false},
    {"key": "materials",        "label": "Materials used (list only, stock is not reduced)", "kind": "longtext", "required": false}
]'::jsonb),
('Infrastructure Deployment', 'FAT installation', '[
    {"key": "signal_before", "label": "Signal before splitting (dBm)", "kind": "number", "required": false, "min": -60, "max": 10},
    {"key": "signal_after",  "label": "Signal after splitting (dBm)",  "kind": "number", "required": false, "min": -60, "max": 10},
    {"key": "materials",     "label": "Materials used (list only, stock is not reduced)", "kind": "longtext", "required": false}
]'::jsonb),
('Infrastructure Deployment', 'New fibre deployment', '[
    {"key": "signal_before", "label": "Signal before splitting (dBm)", "kind": "number", "required": false, "min": -60, "max": 10},
    {"key": "signal_after",  "label": "Signal after splitting (dBm)",  "kind": "number", "required": false, "min": -60, "max": 10},
    {"key": "materials",     "label": "Materials used (list only, stock is not reduced)", "kind": "longtext", "required": false}
]'::jsonb),
('Infrastructure Deployment', 'New network distribution', '[
    {"key": "signal_before", "label": "Signal before splitting (dBm)", "kind": "number", "required": false, "min": -60, "max": 10},
    {"key": "signal_after",  "label": "Signal after splitting (dBm)",  "kind": "number", "required": false, "min": -60, "max": 10},
    {"key": "materials",     "label": "Materials used (list only, stock is not reduced)", "kind": "longtext", "required": false}
]'::jsonb);

-- Then each category as a whole.
INSERT INTO field_sets VALUES
('Customer Installation', NULL, '[
    {"key": "customer",    "label": "Customer",                     "kind": "customer", "required": false},
    {"key": "client_name", "label": "Client name",                  "kind": "text",     "required": false},
    {"key": "account",     "label": "Account",                      "kind": "text",     "required": false},
    {"key": "package",     "label": "Package",                      "kind": "text",     "required": false},
    {"key": "contact",     "label": "Contact (phone)",              "kind": "text",     "required": false},
    {"key": "address",     "label": "Address / landmark",           "kind": "text",     "required": false},
    {"key": "signal_dbm",  "label": "Optical signal reading (dBm)", "kind": "number",   "required": false, "min": -60, "max": 10},
    {"key": "materials",   "label": "Materials used (list only, stock is not reduced)", "kind": "longtext", "required": false}
]'::jsonb),
('Customer Support', NULL, '[
    {"key": "client_name", "label": "Client name",     "kind": "text",     "required": false},
    {"key": "account",     "label": "Account",         "kind": "text",     "required": false},
    {"key": "finding",     "label": "Finding at site", "kind": "longtext", "required": false},
    {"key": "solution",    "label": "Solution",        "kind": "longtext", "required": false},
    {"key": "materials",   "label": "Materials used (list only, stock is not reduced)", "kind": "longtext", "required": false}
]'::jsonb),
('Network Maintenance', NULL, '[
    {"key": "fault",     "label": "Fault encountered", "kind": "longtext", "required": false},
    {"key": "repair",    "label": "Repair performed",  "kind": "longtext", "required": false},
    {"key": "materials", "label": "Materials used (list only, stock is not reduced)", "kind": "longtext", "required": false}
]'::jsonb),
('Infrastructure Deployment', NULL, '[
    {"key": "materials", "label": "Materials used (list only, stock is not reduced)", "kind": "longtext", "required": false}
]'::jsonb),
('Infrastructure Relocation', NULL, '[
    {"key": "materials", "label": "Materials used (list only, stock is not reduced)", "kind": "longtext", "required": false}
]'::jsonb),
('Power & Equipment', NULL, '[
    {"key": "materials", "label": "Materials used (list only, stock is not reduced)", "kind": "longtext", "required": false}
]'::jsonb);

-- What 0027 seeded, so an untouched type can be told apart from an edited one.
-- A type's set is applied by whichever row matches it first (own set, then category).
UPDATE activity_types t
SET details_schema = f.fields
FROM (
    SELECT DISTINCT ON (t2.id) t2.id AS type_id, s.fields
    FROM activity_types t2
    JOIN activity_categories c ON c.id = t2.category_id
    JOIN field_sets s ON s.category = c.name AND (s.type_name = t2.name OR s.type_name IS NULL)
    ORDER BY t2.id, (s.type_name IS NULL)        -- the type's own row sorts first
) f
WHERE t.id = f.type_id
  AND (
        t.details_schema IS NULL
        OR t.details_schema = '[
            {"key": "customer",   "label": "Customer",                     "kind": "customer", "required": false},
            {"key": "package",    "label": "Package",                      "kind": "text",     "required": false},
            {"key": "signal_dbm", "label": "Optical signal reading (dBm)", "kind": "number",   "required": false, "min": -60, "max": 10}
        ]'::jsonb
        OR t.details_schema = '[
            {"key": "fault",  "label": "Fault encountered", "kind": "text", "required": false},
            {"key": "repair", "label": "Repair performed",  "kind": "text", "required": false}
        ]'::jsonb
      );

COMMIT;

-- ROLLBACK: nothing to undo structurally. To go back to a type's earlier
-- questions, edit them on the Manage Activity Types screen (answers already
-- saved on reports are never touched). "Cable re-tensioning" can be deactivated
-- there too.
