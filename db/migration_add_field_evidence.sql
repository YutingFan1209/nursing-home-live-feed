-- News accuracy (2026-11-17): what each field was read from, what a second
-- independent read disagreed with, and what grounding removed. Shown to the
-- person approving each news card (evals/approval.py).
ALTER TABLE deals ADD COLUMN IF NOT EXISTS field_quotes jsonb;   -- {field: sentence from the article}
ALTER TABLE deals ADD COLUMN IF NOT EXISTS second_read jsonb;    -- {"matched": bool, "disagree": {field: value}}
ALTER TABLE deals ADD COLUMN IF NOT EXISTS ungrounded text[];    -- fields grounding blanked, and why
