-- One-line summary of each deal for its card (2026-10-07): the extractor's
-- per-deal rationale, or a state filing's own summary. Roundup articles
-- often keep the parties anonymous ("a regional operator"); the summary
-- keeps what they do say (lender, amount, beds, sale vs refinancing).
ALTER TABLE deals ADD COLUMN IF NOT EXISTS summary TEXT;
