-- Phase 4 — the 4.0c site mapping, as known on 2026-09-21. Data only; no DDL.
-- Run AFTER 0006_monitoring_core_tables.sql.
--
-- This is deliberately a SEPARATE file from 0006. 0006 is structure and is
-- finished; this is the fleet mapping, which is expected to change — the
-- owner has flagged that VLAN numbering and naming will be reorganised. None
-- of it is hardcoded anywhere in application code: these rows are ordinary
-- CRUD data, edited and soft-deleted from the UI. This file exists only to
-- avoid typing 21 rows in by hand the first time. It is NOT re-run after
-- edits, and ON CONFLICT (vlan_id) DO NOTHING guarantees that re-running it
-- by accident cannot overwrite an owner's corrections.
--
-- location_id values were derived by name-matching against the live
-- `locations` table on 2026-09-21, not supplied by the owner. Spot-check
-- them once against the UI.
--
-- Rows with location_id NULL are real sites that have no `locations` row
-- yet. That is a legitimate, supported state (see 0006's header) — link them
-- by setting location_id once someone creates those rows in Sites.
--
-- liveness_source: 'pppoe' where the site has a confirmed PPPoE uplink,
-- otherwise 'activity'. 'activity' can only ever report online-or-unknown,
-- never a false 'down', so it is the correct default for anything not yet
-- understood. Switch a row to 'ping' and fill ap_ip once a site AP has a
-- known stable address (PHASES.md 4.6).
--
--   psql "$DATABASE_URL" -f migrations/0007_seed_monitored_sites.sql

INSERT INTO monitored_sites
    (vlan_id, location_id, gateway_cidr, pppoe_username, liveness_source, is_active, notes)
VALUES
    -- Mapped and confirmed: location_id matched, PPPoE uplink present.
    (5,   19,   '10.50.5.1/24',   'Njeri_House',         'pppoe',    true,
     'Owner-confirmed 2026-09-21. NOTE: the hotspot server on this VLAN is named PHASE3 on the router, which is a stale name — Phase 3 is VLAN 35. Renaming it is a router change and out of scope.'),
    (10,  21,   '10.50.10.1/24',  'Ndambaki',            'pppoe',    true,  NULL),
    (15,  20,   '10.50.15.1/24',  'Kwamlima',            'pppoe',    true,  NULL),
    (25,  18,   '10.50.25.1/24',  'ACK',                 'pppoe',    true,  NULL),
    (30,  8,    '10.50.30.1/24',  'CatholicRd_Hotspot',  'pppoe',    true,  NULL),
    (35,  7,    '10.50.35.1/24',  'Phase3_HOTSPOT',      'pppoe',    true,
     'Owner confirmed 2026-09-21 there is only ONE real Phase 3 site — linked to locations id 7 (active). Id 6 is a stale duplicate row in `locations`; cleaning it up belongs to the battery domain, not Phase 4.'),
    (40,  12,   '10.50.40.1/24',  'Redsoil_Hotspot',     'pppoe',    true,
     'Had zero hotspot sessions in the 4.0 snapshot; owner confirmed 2026-09-21 the site is fine. Do NOT treat that snapshot as a fault baseline.'),
    (45,  17,   '10.50.45.1/24',  'Sunton_Hotspot',      'pppoe',    true,  NULL),
    (60,  3,    '10.50.60.1/24',  'Maji_Hotspot',        'pppoe',    true,  NULL),
    (65,  22,   '10.50.65.1/24',  'LowerSunton_Hotspot', 'pppoe',    true,  NULL),
    (70,  5,    '10.50.70.1/24',  'Policelin_Hotspot',   'pppoe',    true,  NULL),

    -- Real sites with no `locations` row yet. Monitored now, linkable later.
    (20,  NULL, '10.50.20.1/24',  NULL,                  'activity', true,
     'Owner name: "Street 10". No locations row yet, no PPPoE uplink identified.'),
    (55,  NULL, '10.50.55.1/24',  NULL,                  'activity', true,
     'Owner name: "Monitoring" — owner confirmed 2026-09-21 this IS a customer site despite the name. No locations row yet.'),
    (77,  NULL, '10.50.77.1/24',  NULL,                  'activity', true,
     'Owner name: "Cyber". No locations row yet. Zero sessions in the 4.0 snapshot.'),
    (99,  NULL, NULL,             'Policelinestreet9',   'pppoe',    true,
     'Owner name: "Street 9". Has a PPPoE uplink but NO hotspot server was found on VLAN 99 in the 4.0 sweep — confirm with /interface vlan print whether this VLAN exists at all.'),

    -- Known to exist on the router, not yet mapped to a site. Seeded so the
    -- ingest endpoint recognises them instead of quarantining every poll.
    -- is_active=false keeps them off the dashboard until someone names them;
    -- ingest matches on vlan_id regardless of is_active.
    (75,  NULL, '10.50.75.1/24',  NULL,                  'activity', false, 'Unmapped — owner has not named this VLAN yet.'),
    (80,  NULL, '10.50.80.1/24',  NULL,                  'activity', false, 'Unmapped — owner has not named this VLAN yet.'),
    (85,  NULL, '10.50.85.1/24',  NULL,                  'activity', false, 'Unmapped, and zero sessions in the 4.0 snapshot — likely provisioned but not yet live.'),
    (90,  NULL, '10.50.90.1/24',  NULL,                  'activity', false, 'Unmapped, and zero sessions in the 4.0 snapshot — likely provisioned but not yet live.'),
    (105, NULL, '10.50.105.1/24', NULL,                  'activity', false, 'Unmapped, and zero sessions in the 4.0 snapshot — likely provisioned but not yet live.'),
    (110, NULL, '10.50.110.1/24', NULL,                  'activity', false, 'Unmapped, and zero sessions in the 4.0 snapshot — likely provisioned but not yet live.'),
    (115, NULL, '10.50.115.1/24', NULL,                  'activity', false, 'Unmapped, and zero sessions in the 4.0 snapshot — likely provisioned but not yet live.')
ON CONFLICT (vlan_id) DO NOTHING;

-- Not seeded, on purpose:
--
--   - The eight `locations` rows with no VLAN yet (1 Benbro, 9 Kwa Mafuta,
--     10 Kamutini, 11 Garage, 13 Stima, 14 Maternity, 15 Garage Escarpments,
--     16 Hunters). Six of the still-unmatched PPPoE names end in _Hotspot and
--     plausibly belong to six of them, but guessing which would put wrong
--     revenue against a real site. Add them from the UI once known.
--
--   - PPPoE names `Prisca` and `Stage`, which match no `locations` row and
--     are probably home internet customers rather than sites. If either is
--     actually a site, add it as a row here.
--
--   - `hotspot1` on hotspot-bridge (192.168.180.1/22), pending the owner
--     saying whether it is retired.
