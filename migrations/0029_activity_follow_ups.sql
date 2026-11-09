-- Technical Activity Log: a report can say it finishes (follows up) an earlier
-- unfinished one. Owner's call, 2026-11-06: a "Partially completed" or
-- "Unable to resolve" report stays true about that visit, and the visit that
-- finishes the job points back at it, so the Overview can tell "finished
-- later" from "still open" without rewriting history. Not ticketing: no
-- assignment, status or deadline, only a link written after the fact.
--
-- One column added; nothing dropped or rewritten:
--   activity_reports.follows_up_report_id   the earlier report this one
--       follows up, or NULL. Set on the LATER report. A report can be followed
--       up by at most one live report (the unique index below; a soft-deleted
--       follow-up frees it again).
-- The rules (only an unfinished, earlier or same-day, live report; never
-- itself) are checked in code, db/activity_logic.check_follow_up.
--
-- Safe and backwards compatible: the code already deployed ignores the
-- column, so this can be applied before the new code.
--
-- Run this once against the target database:
--   psql "$DATABASE_URL" -f migrations/0029_activity_follow_ups.sql
-- Idempotent — re-running is safe.

ALTER TABLE activity_reports
    ADD COLUMN IF NOT EXISTS follows_up_report_id integer REFERENCES activity_reports(id);

CREATE UNIQUE INDEX IF NOT EXISTS activity_reports_one_follow_up
    ON activity_reports (follows_up_report_id)
    WHERE follows_up_report_id IS NOT NULL AND NOT is_deleted;

-- ROLLBACK (by hand, only if wanted; the links are lost, the reports are not):
--   DROP INDEX activity_reports_one_follow_up;
--   ALTER TABLE activity_reports DROP COLUMN follows_up_report_id;
