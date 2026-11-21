-- Stale tracking (PLAN.md §10 P1): a page's rendered image is outdated once its text or layout
-- changes; a region whose reading order was set by hand must survive an OCR re-run.
ALTER TABLE pages ADD COLUMN render_stale INTEGER NOT NULL DEFAULT 0;
ALTER TABLE regions ADD COLUMN reading_order_edited INTEGER NOT NULL DEFAULT 0;
