-- Per-field manual-edit flags: a polygon, text direction or source language set by hand must
-- survive an OCR re-run (merge keeps the user's value) and must keep an undetected region alive.
ALTER TABLE regions ADD COLUMN polygon_edited INTEGER NOT NULL DEFAULT 0;
ALTER TABLE regions ADD COLUMN direction_edited INTEGER NOT NULL DEFAULT 0;
ALTER TABLE regions ADD COLUMN source_lang_edited INTEGER NOT NULL DEFAULT 0;
