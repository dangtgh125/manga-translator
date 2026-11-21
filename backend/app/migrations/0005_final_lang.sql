-- Language of final_text at the time it was written (manual edit or accepted machine translation).
-- A hand-written final text only counts as a current translation while the project still targets
-- that language; otherwise re-translation keeps it stale instead of silently drawing the old text.
ALTER TABLE regions ADD COLUMN final_lang TEXT NOT NULL DEFAULT '';
UPDATE regions SET final_lang = (SELECT target_lang FROM projects WHERE projects.id = regions.project_id)
 WHERE final_text <> '';
