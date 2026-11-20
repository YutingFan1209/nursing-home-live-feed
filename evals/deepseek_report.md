# DeepSeek vs Opus as the accuracy judge: result (2026-11-19, adjudicated)

Rules: `evals/deepseek_prereg.md` (frozen, amendments 1–3). Reference = the Claude-written
`labels.jsonl` + `hand_verdict()`, with the person's verdicts on 69 cards replacing the labels
there: the 49 cards where the models disagree + 20 random agreed cards. File:
`data/evals/adjudication_edited_2026-11-19.csv`, gitignored.

## Verdict against the pre-registered rules

| Use | Result | Deciding numbers (DeepSeek Flash, majority of 3) |
|---|---|---|
| (a) Tuning runs | **FAIL** (T1, T3, T4, T5 fail; T2 fails in the primary) | 63% consistent across repeats (90% needed); 16% invalid replies (2% allowed) |
| (b) Deploy gate | **FAIL** (G3 fails; G1 can't be tested on this set) | ships 17/46 bad cards |

T4 and T5 don't depend on the labels, so no adjudication could change (a).

## Adjudication changed the picture for Opus too

- **The Claude-written labels were too lenient.** You moved 22 cards from ship to hold: 13 wrong,
  5 duplicate, 4 uncertain (UCC 10, news 8, CON 2, CHOW 2). Bad cards: 24 → 46.
- You agreed with both models on all 20 agreed spot checks.
- On the 49 disagreements you gave Opus's exact verdict 29 times and DeepSeek's 18 times. On the
  ship/hold call itself, you sided with Opus on 19 cards and with DeepSeek on 18. On the other 12,
  the models' verdicts differed but both landed on the same side of ship/hold as you.

| (measured; majority of 3 repeats) | DeepSeek Flash | Opus 5.5 |
|---|---|---|
| **Bad cards shipped** (n = 46) | 17/46 = 37% (25–51%) | **18/46 = 39% (26–54%)** |
| Good cards held (n = 104) | 31/104 = 30% (22–39%) | 19/104 = 18% (12–27%) |
| κ / AC1 vs reference (bootstrap 95% CI) | 0.31 (0.15–0.46) / 0.41 (0.26–0.57) | 0.42 (0.26–0.58) / 0.57 (0.43–0.69) |
| Bad UCC cards shipped (n = 14) | 1 | **10** |
| Bad news cards shipped (n = 23) | 12 | 6 |
| Same ship/hold call in all 3 repeats (Fleiss κ) | 88/150 (0.47) | 142/150 (0.92) |
| Invalid replies | 73/450 = 16% | 0/450 |
| Cost / 1,000 correct calls | $3.82 | $18.45 (batch price) |
| Latency p50 / p95 | 10.7 s / 72 s | n/a (batch) |

**Paired:** McNemar p = 0.12, so the two models' per-card correctness doesn't differ
significantly. DeepSeek − Opus: bad cards shipped +6 points (95% CI −11 to +23); good cards held
+14 points (+9 to +20).

**Sensitivity** (not primary: `null` arrays read as `[]`): DeepSeek ships 10/46 bad cards
(12–36%), holds 24/104 good ones, 3.6% invalid, 82% consistent. Only T2 passes. Under this
reading it would ship fewer bad cards than Opus, mostly on UCC, but it still fails the
consistency and invalid-reply rules.

**Cascades** (simulated, exploratory; pooled over repeats). None of them is safe, because Opus
itself isn't: Opus alone ships 49/138 bad-card calls. Best case, "DeepSeek ×3, unanimous → its
call, else Opus", ships 44/138 at $3.28 per 150 cards vs $4.22 for real-time Opus, holding the
same number of good cards (58/312).

## Correction after adjudication: 7 sellers verified

7 of the 10 bad UCC cards that Opus shipped were marked wrong because "the seller isn't in the
evidence". All 7 sellers match the CMS CHOW file exactly. The judge's evidence
(`chow_seen_records`) has no seller column, so neither you nor the models could see them.
Counting those 7 as good (scratchpad `final_seller_fixed.txt`):

| | DeepSeek Flash | Opus 5.5 |
|---|---|---|
| Bad cards shipped | 17/39 = 44% (29–59%) | **11/39 = 28% (17–44%)** |
| Bad UCC cards shipped | 1/7 | 3/7 |
| Good cards held | 38/111 = 34% | 19/111 = 17% |
| AC1 | 0.34 (0.18–0.50) | 0.66 (0.54–0.77) |

DeepSeek − Opus, bad cards shipped: +21 points (95% CI +7 to +35); McNemar p < 0.001. DeepSeek
still fails every rule. The 3 real UCC misses:
- **57d5c281:** Opus noticed the same-home card a day later but voted it right 2 of 3 times.
- **61656f86:** a 2014 filing read as "may be acquisition financing", though the CHOW is from 2023.
- **46e99848:** the debtor doesn't map to the linked home.

## What it means

1. **DeepSeek can't replace Opus.** It's cheaper per correct call, but too inconsistent and too
   often unparseable.
2. **The bigger finding: the Opus deploy gate is much leakier than its recorded score.**
   `measured.json` records "caught 28/31" against the Claude labels. Against your verdicts,
   corrected for the 7 verified sellers, it ships 11 of 39 bad cards (28%, 17–44%). Even that is likely an
   underestimate: you only saw the cards where the models disagreed, plus 20 others, so bad cards
   that both models pass and that weren't spot-checked are still unseen.
3. For cheaper tuning runs, use Opus with `--batch`: half price, same answers.

## What this can't show

- You saw both models' verdicts and reasons (blind to which model, not to their output), so your
  verdicts may be anchored on them. 81 cards still carry the unreviewed Claude labels.
- Contaminated: the judge prompt was tuned for Opus on these 150 cards.
- One gold set, one seed, DeepSeek Flash only. DeepSeek fingerprint `bc19ba97…` was unchanged for
  all 450 calls.
- **Holdout:** to clear any judge for the gate at a 5% upper bound takes a fresh seed, labelled by
  a person blind to model output, with at least 73 bad cards (0 misses), or ≥ 35 for a 10% bound.
  A 120-card holdout was drawn on 11-19 (prereg amendment 4).

## Spend (measured tokens × list price)

Dry + 10 cards $0.32, DeepSeek $1.11, Opus batch $6.33: **≈ $7.76**.

## Files

Repo, uncommitted: `evals/deepseek_prereg.md`, this file, `evals/adjudication.csv` (blank sheet,
not gitignored: don't commit it). Scratchpad `…/scratchpad/ds/`: frozen inputs, per-call logs,
scripts, `final.txt` (full adjudicated output), `adjudication_key.json`.

## Blind holdout (2026-11-19, prereg amendment 4)

The setup:
- 120 cards: seed 20261219, fresh export, gold quotas ×0.8.
- Labelled by the person blind to any model output: 20 bad (news 12, CON 4, UCC 3, CHOW 1).
- The current Opus judge (fingerprint `8afd433ccef253c2`) ran once through Batch. The person
  chose 1 repeat, so this is a single-call decision, not the pre-registered majority of 3.
- Batch `msgbatch_01WMLdpkwMStnNREJ7QgpJvs`, 120/120 valid, $1.68 at batch price.

| (measured) | Opus 5.5 |
|---|---|
| **Bad cards shipped** | **11/20 = 55% (Wilson 34–74%)** |
| Bad news cards shipped | 8/12 |
| Bad CON / CHOW / UCC cards shipped | 2/4, 1/1, 0/3 |
| Good cards held | 9/100 = 9% (5–16%) |
| Same exact verdict as the person | 87/120 |

**H1 FAILS.** Even the lower end of the interval (34%) is far above 10%, so the top-up draw
(seed 20261220) isn't needed.

The shipped bad cards are mostly news, and Opus's notes usually state the problem and still pass
the card:
- **Several deals merged into one card:** 81ffb866, 7d3e373f.
- **Wrong facility type** (independent/assisted living or behavioral health shown as SNF): 122858b1, eca43729.
- **Operator or tenant shown as buyer:** 67a958f6, 335c39ed.
- **A joint-venture partner left out:** a8386fd3.
- **An extra facility added:** ea24c91c.
- **A pending review shown as completed:** 5d3a0935.
- **A landlord shown as lender:** 06d16d9c.
- **A duplicate of another card:** 590ff1d3.

Some of these may be places where `SITE_RULES` and the person's standard differ (for example,
whether an incoming operator is the "buyer" of an operations transfer), not only judge errors.

## Judge fix, 2026-11-20

The changes:
- **Evidence:** the CHOW buyer and seller are read from the CMS file. Neighbours now match on
  facility name and buyer brand, and show status.
- **Judge rules:** landlord as lender; checks on CCN and facility type; incoming operator as buyer;
  pending status; same-loan UCC duplicates; a note that contradicts its verdict.
- **Shared field rules:** joint-venture buyers name every partner, and one card per state.
- **Export:** a blank count is filled with 1 on UCC, CMS and state cards linked to one home.

Holdout labels were corrected per the person: ea24c91c and 7d3e373f are right, and so is
a2e0e059 now that its count is filled. That leaves 17 bad cards. Results from 1 batch run, with
the old judge rescored against the same corrected labels:

| (measured) | Old judge | Fixed judge |
|---|---|---|
| Bad cards shipped | 9/17 (31–74%) | **3/17 = 18% (6–41%)** |
| Good cards held | 10/103 | 19/103 |

**Caveat: the holdout is no longer blind for this judge.** The fixes were written from its
misses, so 3/17 is optimistic. Confirming it, and testing the 10% bound, needs a fresh holdout.

On the gold set the fixed judge matched the old one on the 92 cards unchanged since 11-16 (6/14
shipped, 13/78 held). The other cards had been corrected on the site since they were labelled.
Extraction re-measure: 35/64 news cards fully right (was 37/64, within the ±5 noise).

## Second blind holdout (2026-11-20, prereg amendment 5)

The setup:
- 120 non-news cards (UCC 50, CON 40, CHOW 30), seed 20261222.
- Labelled by the person before any model output existed: 25 bad.
- Judge `e2024918` (commit 8365dc34) ran ×1 through Batch, $0.83 at batch price.

| (measured) | Person's labels as written | 17 CON cards following SITE_RULES counted right |
|---|---|---|
| Bad cards shipped | 16/25 = 64% (45–80%) | **2/8 = 25% (7–59%)** |
| Good cards held | 15/95 = 16% | 18/112 = 16% |

**H1 FAILS either way.**

17 of the 25 bad cards are CON cards that follow two written site rules the person disagrees with:
- a filing with no closing date shows its received date (12 cards);
- a pending filing is shown as "reported, assumed closed" after its expected date or 90 days
  (9 cards).

The judge passes 14 of those 17 because it was told the rules are right. So most of the leak is
in the rules, not the judge. Of the 8 other bad cards, the judge ships 2, both UCC filings with no
identified home (947b31c6, d85081f7), and holds all 4 landlord-as-lender cards and both noise
liens.
