# Deal Stages and Tags

## Overview

Every deal in the system has two independent classification fields:

- **Stage** — where the deal is in the automated pipeline (set by the system)
- **Tags** — what the research team thinks about it (set by researchers)

These are independent. A deal can be `confirmed` with no tags if no one has reviewed it yet, or `detected` with a `flagged` tag if a researcher spots it early and wants to prioritize it.

---

## Stages

Stages are set automatically by the pipeline as deals move through processing. Researchers can manually override a stage using the Verify or Dismiss buttons in the dashboard.

### `detected`
**Set by:** Pipeline automatically on extraction  
**Meaning:** The scraper found an article mentioning an acquisition and Claude extracted deal details from it. No CMS match found yet.  
**Why no match?** Either the deal is very recent and CMS hasn't updated yet, the extracted entity names didn't fuzzy-match anything in the database, or the article was too short/paywalled to extract reliable details.  
**What happens next:** The pipeline re-checks against CMS every 7 days for up to 12 weeks.

### `pending_cms`
**Set by:** Pipeline automatically on partial match  
**Meaning:** A CMS match was found with a fuzzy score between 70-84% — plausible but not confident. The deal details partially align with a CMS ownership record.  
**What happens next:** Automatically re-checked every 7 days. Moves to `confirmed` if score improves, `unresolved` after 12 failed re-checks.

### `confirmed`
**Set by:** Pipeline automatically on strong match (score ≥ 85%)  
**Meaning:** Strong CMS match. The deal details from the article closely align with a real ownership record in the CMS database. This is the threshold that triggers the daily email digest alert to the team.  
**What happens next:** Appears in the team dashboard for review. Researcher can verify or dismiss.

### `verified`
**Set by:** Researcher manually via dashboard  
**Meaning:** A team member has reviewed this deal and confirmed it is correct and relevant. Highest confidence state — human-in-the-loop approval.  
**Note:** Verified deals remain in the confirmed count for stats purposes.

### `dismissed`
**Set by:** Researcher manually via dashboard  
**Meaning:** A team member reviewed this deal and determined it is not relevant — wrong facility type, duplicate coverage, misextracted data, deal fell through, or otherwise not worth tracking.  
**Note:** Dismissed deals stay in the database permanently for audit purposes but do not appear in the default deal queue.

### `unresolved`
**Set by:** Pipeline automatically after 12 re-check cycles (~12 weeks)  
**Meaning:** The deal went through the full re-check window without ever matching a CMS record confidently.  
**Why?** The deal may have fallen through, involved a non-Medicare-certified facility, used entity names too ambiguous to match, or the article was misclassified as an acquisition.  
**What happens next:** Flagged for optional manual review. No further automated processing.

---

## Stage Lifecycle

```
                    ┌─────────────┐
                    │  detected   │ ← pipeline extracts deal from article
                    └──────┬──────┘
                           │ CMS re-check (every 7 days)
              ┌────────────┴────────────┐
              │ partial match           │ no match after 12 weeks
              ▼                         ▼
       ┌─────────────┐           ┌─────────────┐
       │ pending_cms │           │ unresolved  │
       └──────┬──────┘           └─────────────┘
              │ strong match
              ▼
       ┌─────────────┐
       │  confirmed  │ ← email digest fires
       └──────┬──────┘
              │ researcher action
     ┌────────┴────────┐
     ▼                 ▼
┌──────────┐    ┌───────────┐
│ verified │    │ dismissed │
└──────────┘    └───────────┘
```

---

## Tags

Tags are applied by researchers through the annotation system in the dashboard. A deal can have multiple tags across different annotations. Tags are additive — adding a `regulatory` tag does not remove a previous `research` tag.

Tags are currently fixed (not user-configurable). New tags require an engineering change. The tag owner — the person who decides when a new tag is added — should be designated by the PI.

### `research`
**Used for:** Deals relevant to ongoing research projects.  
**Examples:** Ownership consolidation pattern analysis, geographic concentration studies, chain growth tracking, academic publication research.  
**Default tag** for general research interest when no more specific tag applies.

### `regulatory`
**Used for:** Deals with specific regulatory significance.  
**Examples:**
- Acquiring entity has prior CMS enforcement actions or civil monetary penalties
- Facility is on the Special Focus Facility (SFF) list or is an SFF candidate
- Deal involves a large portfolio that may trigger state Certificate of Need review
- Acquiring entity is a known private equity firm or REIT with a pattern of concern
- Deal involves a facility with 1-2 star overall rating

### `follow-up`
**Used for:** Deals that need attention at a later date.  
**Examples:**
- Waiting for CMS to update ownership records
- Need to cross-reference against another dataset
- Incomplete deal details — waiting for press coverage to fill in gaps
- Team member needs to circle back after other priorities

### `flagged`
**Used for:** High-priority deals needing immediate team attention.  
**Examples:**
- Unusual deal structure worth discussing as a team
- Potential data error or misextraction that needs correction
- Acquirer with a known problematic history
- Deal that may require outreach or notification to another party

---

## Governance

| Decision | Owner |
|---|---|
| Which stage a deal is in | Pipeline (automated) |
| Override to verified/dismissed | Any researcher |
| Which tag to apply to an annotation | Any researcher |
| Adding a new tag type | PI / tag owner |
| Changing stage thresholds (match score) | Engineering |

---

## FAQ

**Can a deal have multiple tags?**  
Yes. Each annotation has one tag, but a deal can have many annotations from different researchers. The deal card in the dashboard shows all unique tags across all annotations.

**Can a dismissed deal be un-dismissed?**  
Yes. A researcher can use the stage update to change it back to `confirmed` or `detected` from the deal drawer.

**Does a tag affect whether an alert fires?**  
No. Alerts fire based on stage only — when a deal moves to `confirmed`. Tags are a research workflow tool, not an alert trigger.

**What's the difference between `confirmed` and `verified`?**  
`confirmed` is the system saying "we're confident this is a real deal based on CMS matching." `verified` is a human saying "I've reviewed this and it's correct." For regulatory research, `verified` is the standard you want before citing a deal in a report or publication.
