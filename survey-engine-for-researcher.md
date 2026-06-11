# Survey Engine — A Researcher's Guide

**Status:** Living — companion to the engineering design document
**Last updated:** 2026-05-25
**Version:** 1

This is the methodological view of the survey engine: what it protects, what it
asks of you, and how to read the data that comes out. It is written for the
researcher authoring surveys and analyzing results, not the engineer operating the
system — for architecture and rationale, see
[`survey-engine-design-doc.md`](survey-engine-design-doc.md) (the FR/NFR numbers
referenced below live there).

The one-sentence version: **the system is built so that the judgments only you can
make stay with you, and never get absorbed by a convenient default.**

---

## 1. What the system protects

Commercial survey tools collect responses well but export analytical tables that
age badly — wide files with versioned column names that fragment across study
iterations and that the next analyst reads differently than you did. Longitudinal
studies suffer most, because a quietly reworded question is usually discovered far
too late to untangle.

Three properties guard against that:

- **Immutability.** A published survey version can never change. Every response
  records the exact version — by content hash — that the respondent saw (FR-3).
  You can always answer "what, precisely, was this person asked?"
- **Reproducibility.** The entire analytical warehouse is rebuilt from the raw
  responses on demand; it can be deleted and rebuilt with no data loss (NFR-1). The
  raw response payload is the single source of truth.
- **Methodological honesty.** Where a step requires a judgment about *meaning* —
  not a mechanical transform — the system refuses to guess. It fails closed and
  waits for you (NFR-3). Section 4 is the heart of this.

---

## 2. The shape of a study

```
survey  ──>  version 1 (published, hash-frozen)  ──>  responses reference v1
        ──>  version 2 (published, hash-frozen)  ──>  responses reference v2
```

A **survey** is a stable identity. Each **publication** mints a new immutable
**version** with its own content hash; prior versions persist forever. You don't
edit a published survey — you clone it into a new draft and publish a new version.
Every response carries the version (and hash) it was answered against, so a later
revision can never retroactively change what an earlier respondent was asked.

This is why "just fix the typo in the live survey" is not a thing: silent revision
is exactly the longitudinal failure mode the system exists to prevent. A new version
is cheap; a corrupted time series is not.

---

## 3. Authoring a survey

Surveys are SurveyJS JSON. The workflow:

1. **Create a draft** (or clone a published survey into a new draft). Drafts are
   mutable.
2. **Edit** the JSON — by hand, or with LLM assistance from a natural-language
   description. Start from a **pattern**: [`patterns/`](patterns/) holds annotated,
   known-good examples (single-select, multi-select, ranked, matrix, repeating
   group, scalar, free-text, branching, multi-page, calculated values). Copying a
   pattern keeps the surface for subtle logic errors small.
3. **Preview and self-test** in the live SurveyJS runtime — walk every branch as a
   respondent would.
4. **Publish.** This runs the gate (below). If it passes, the version is hashed and
   frozen.

### The publish gate (FR-2)

Publishing is not a save — it is a set of checks that **block** a survey that would
silently misbehave, in this order:

1. **Schema validation** — the definition is structurally well-formed.
2. **Lint** — duplicate question names, dangling `visibleIf`/`enableIf` references
   (a branch condition pointing at a question that doesn't exist), duplicate option
   values, missing matrix row identifiers.
3. **Round-trip test** — for surveys flagged for real respondents, a headless run
   walks the enumerable branch space and rejects a survey containing a question that
   *can never be shown* (an unreachable branch). Sandbox/test surveys can opt out of
   this gate.
4. **Hash + freeze.**

A blocked publish is the system doing its job. The error tells you which check
failed and why.

### What you can ask (the question-type surface)

The gate accepts a deliberately narrow set of types — only those wired end-to-end
through the runtime, the gate, and the warehouse, so an answer can never land
nowhere. The current surface and where each answer goes in analysis:

| Type | Kind | In analysis |
|---|---|---|
| `radiogroup`, `dropdown` | single-select | one option per respondent |
| `checkbox` | multi-select | one row per chosen option |
| `ranking` | ranked choice | one row per option, each with a `rank` |
| `matrix` | grid of single-selects | one sub-question per row |
| `matrixdropdown` | grid of typed cells | one sub-question per (row, column) |
| `paneldynamic` | repeating group | one sub-question per template field, repeated per `occurrence` |
| `text`, `comment` | free text | `value_text`, PII-routed (§4b) |
| `rating`, `boolean` | scalar | numeric value (`boolean` → 1/0) |
| `text` + `inputType: number`/`range`/`date` | scalar number/date | numeric/date value, **not** free text |

The fully annotated table — including the deferred edges (numeric/date cells inside
a matrix or panel, custom boolean values, text-valued ratings) — is in
[`patterns/README.md`](patterns/README.md). A type outside this set is rejected at
publish, by design: the system won't accept a question whose answers it can't place.

---

## 4. The four commitments

These are the judgments the system will never make for you. Each one is a place
where a "helpful" default would quietly corrupt an analysis, so the system makes the
safe choice and leaves the meaningful one to you.

### a. The system never infers question equivalence

Whether a reworded question in version 2 measures the *same construct* as its
version-1 predecessor is a methodological judgment, not something the system can
infer from name or text similarity. It will not equate questions of *different*
identity on its own.

In the warehouse, `question_id` identifies a question **within one survey** — it is
keyed on `(survey_id, stable_name)`. So it pools that question across the survey's
versions *when you keep the name unchanged* (keeping the name is itself your
assertion of continuity; the per-version wording is preserved in
`dim_question_version`), but it **never** pools across different surveys — two
surveys that both have a `q1` stay distinct. To keep *versions* separate, group by
`question_version_id`.

When you *do* judge two questions of different identity equivalent — a rename across
versions, or the same instrument reused elsewhere — you say so explicitly: you
record a `parent_question_id` link **and a written rationale** for why they pool
(FR-9). The rationale is required; the system rejects the link without it. Pooling
analyses then opt in by using the derived canonical key
(`COALESCE(parent_question_id, question_id)`).

What the system refuses: auto-pooling from prompt similarity. That would absorb your
judgment into a default and produce silently inconsistent longitudinal series. The
same refusal extends to **construct tags**: you can tag a question with
`construct_block: "phq9"` and `construct_item: "phq9_q3"` to record that it came from
a named, reusable scale (PHQ-9, GAD-7, eNPS, …), and those tags travel into
`dim_question` so you can *find* every PHQ-9-tagged question across every survey.
But shared tags do **not** license pooling — two surveys' PHQ-9 items are still
distinct `question_id`s, and pooling them is still the explicit `parent_question_id`
opt-in, with rationale. Tags surface candidates for that judgment; they don't make
it for you.

> Status: the integrity guard ships today (dbt’s `parent_question_integrity` test), but there isn’t yet a supported workflow to *persist* equivalence decisions — `dim_question` emits these columns as nulls.
> The ergonomic tooling + storage around declaring equivalence is the natural next build —
> nothing collected needs to change for it to arrive later.

### b. Free text is treated as sensitive by default

Tech-worker respondents often put proprietary content — internal codenames, project
details — into open-ended fields without realizing it. So **every free-text question
defaults to high PII risk** (FR-8):

- A `high`-risk free-text answer is stored only in a restricted PII schema. In the
  analyst-facing warehouse the cell reads *redacted* (`value_text_redacted = true`),
  with no text behind it.
- The analyst-readable text column (`value_text`) is populated **only** for a
  question you've explicitly downgraded to `low` risk — and that downgrade requires
  a written rationale at definition time.

What the system refuses: downgrading a question because its prompt "looks innocuous."
The safe path (`high`) is the default; the risky path is a deliberate, recorded
decision. Numeric and date answers (even when entered as a `text` box with an
`inputType`) are *not* free text — they resolve straight to a value column and never
touch the PII store.

### c. Shown, skipped, and routed-past are never the same thing

"Missing" hides three very different facts, and analyses depend on telling them
apart (FR-6):

| State | Meaning | In the data |
|---|---|---|
| **Shown & answered** | asked, responded | `was_shown = true`, value present |
| **Shown & skipped** | asked, left blank | `was_shown = true`, value null |
| **Routed past** | never asked (branching) | `was_shown = false`, value null |

`was_shown` comes straight from the **shown-set the SurveyJS engine captured at
submission time** — the list of questions actually rendered to that respondent. It
is never reconstructed by re-evaluating branch conditions in SQL after the fact
(getting that wrong is a classic silent error). A blank cell in a matrix or a single
panel occurrence is held to the same standard, so "this respondent didn't answer
this grid cell" stays distinct from "this respondent's branch never reached the grid."

What the system refuses: collapsing `was_shown = false` and `was_shown = true, value
null` into one "missing" bucket.

### d. Display order is what the respondent saw, never what the definition says

Surveys can randomize what each respondent sees — question order within a page,
row order within a matrix, and choice order within a single/multi-select/ranking
question. Whenever shuffling is on, the **order that respondent actually saw is
captured at submission time**, the same way the shown-set is, and lands in
`fact_response_item.display_order` as an integer position within their rendered
sequence (FR-13).

What you can randomize:

- **Within a page.** Set `questionsOrder: "random"` on a page. Questions on that
  page shuffle per respondent; questions on other pages are unaffected.
- **Within a matrix.** Set `rowsOrder: "random"` on a matrix. The rows (each row
  is its own sub-question — see [`patterns/matrix.json`](patterns/matrix.json))
  shuffle per respondent; the column order (the response scale) is unchanged.
  This is also how you get **block-aware randomization**: a reusable scale fits
  naturally as a matrix (rows = items, columns = response options); tag the
  matrix with `construct_block: "phq9"` (the tag inherits to row sub-questions),
  set `rowsOrder: "random"`, and PHQ-9 items shuffle among themselves while
  staying contiguous because the matrix is the boundary. No separate flag.
- **Within a question.** Set `choicesOrder: "random"` on a `radiogroup`,
  `dropdown`, `checkbox`, or `ranking`. The rendered choice order is captured
  per-respondent at submit time (in `shown_choice_orders` on the raw response).
  Choice-position effects are analyzable; the marts column to surface this is
  deferred until first need (design doc §5 "Choice-order analysis surface").

Not built today: survey-level page shuffling, and randomization in a static `panel`
container — see "What's intentionally not here" (§8) for the deferred paths and
their triggers.

What the system refuses: reconstructing the display order in SQL from the published
definition. The definition records the *rule* (`rowsOrder: "random"`), not the
realized order any individual respondent saw. The engine that drew the screen is the
authoritative source; the warehouse reads it through, never recomputes it.
Reading `MIN(display_order)` vs `MAX(display_order)` across respondents tells you
*which* questions were randomized after the fact; comparing answer rates or
distributions across `display_order` quartiles is how you test for order effects.

---

## 5. PII and the reviewer pass

High-risk free text doesn't have to stay locked away forever. A designated
**reviewer** screens individual high-risk responses and **promotes** the safe ones
into the analytical warehouse, where their text then surfaces; unsafe ones stay
redacted. This single human checkpoint consolidates two questions — "is this PII?"
and "does this contain proprietary information?" — into one pass.

Promotion is per response (and, inside a repeating group, per occurrence): a
reviewer can release one respondent's answer while keeping another redacted. The
reviewer's decisions are recorded as metadata (which response, promoted or rejected,
by whom, when) — the decision log carries no PII text itself. Only a PII-cleared role
ever sees the raw text.

If free-text volume ever outgrows a human reviewer, an automated first-pass PII
detector is a deferred option — not built today.

---

## 6. Withdrawal and right-to-erasure

A respondent can withdraw, and GDPR erasure is satisfiable within the mandated
window (FR-7, NFR-5). Because the raw log is append-only, erasure is a **tombstone**,
not a delete:

1. The withdrawal is recorded (respondent + timestamp) as evidence it occurred.
2. The response *content* is nulled in place — payload, shown-set, client metadata,
   and the definition snapshot — while the row itself remains, so the audit log
   stays structurally complete.
3. The operational read-model and the PII store for that respondent are purged.
4. On the next warehouse rebuild, a tombstoned respondent simply produces no fact
   rows — they vanish from analysis without leaving a hole in the audit trail.

The withdrawal record is the permanent proof that the deletion happened.

---

## 7. Reading the results

The warehouse is a star schema you query with SQL (Postgres; R via `DBI`/`dbplyr`
and Python notebooks are first-class — FR-10). Two facts about its shape matter for
correct analysis:

### Two fact tables — don't conflate selections with respondents

- **`fact_response_item`** is at **selection grain**: one row per chosen option, plus a single row when no option is selected so routing states remain queryable.
  Multi-select and ranking questions fan out to multiple rows; counting rows here counts *selections*, not people.
- **`fact_response`** is at **respondent-question-occurrence grain**: one row per respondent per (sub-)question per `occurrence` (paneldynamic repeats); use it for "how many people answered."

The classic mistake is reading selection counts as respondent counts. When in doubt,
count people explicitly: `COUNT(DISTINCT respondent_id)`.

### Where an answer's value lives

A fact row holds exactly one of `{option_key, value_numeric, value_text,
value_date}`. Which one is determined by the question's `value_kind`
(`option`/`text`/`numeric`/`date`), recorded on the question-version dimension:

- closed-ended → `option_key` (join `dim_option` for the value/label),
- free text → `value_text` (or redacted; §4b),
- numeric/rating/boolean → `value_numeric`,
- date → `value_date`.

`value_kind` is why a numeric answer typed into a text box still lands in
`value_numeric` and not among the free text — worth knowing when you filter by
"response type."

### Order effects, when randomization is on

`fact_response_item.display_order` is the integer position of the question within
the respondent's rendered sequence — the same value across all option rows for the
same `(respondent, question_id, occurrence)` (it's a question-grain property
denormalized onto selection-grain rows). Null when the question was routed past
(`was_shown = false`). Use it to test for ordering effects on randomized questions;
ignore it when randomization wasn't on (it just mirrors definition order).

### Pooling across versions

`question_id` is survey-scoped (`(survey_id, stable_name)`): it pools a survey's
versions of a same-named question and never crosses survey boundaries. So `GROUP BY
question_version_id` keeps versions strictly separate; `GROUP BY question_id` pools
them (within the one survey). Either way, two different surveys' same-named
questions never mix.

To pool across an identity boundary — a rename, or the same instrument reused — opt
in with the canonical key (`COALESCE(parent_question_id, question_id)`), having first
confirmed the recorded equivalence rationale (§4a).

The dimensions you'll join: `dim_respondent`, `dim_survey_version`, `dim_question`
(stable identity, type, equivalence links), `dim_question_version` (the specific
wording + `value_kind`), `dim_option` (option value/label). Matrix and panel
sub-questions appear as their own questions, named for their cell/field
(`matrix.row[.col]`, `panel.element`), with the grid/panel identity recorded so you
can roll them up.

---

## 8. What's intentionally not here

So expectations are calibrated (design doc §5):

- **Equivalence-pooling tooling** beyond the integrity guard — declarable, but not
  yet ergonomic (§4a).
- **Choice-position order in the marts.** The rendered choice order is captured per
  respondent at submit time (`shown_choice_orders` on the raw response), so it's
  there if you need it; a `choice_display_order` column on `fact_response_item` is
  the natural next surface and backfillable without re-collection — built when an
  analysis first wants it (§4d, design doc §5).
- **Static-panel grouping.** A non-repeating `panel` container for organizing long
  surveys is outside today's accepted type surface; use a matrix or a page to
  group items for now. Adding `panel` is a deferred extension (design doc §5).
- **Survey-level page shuffling.** `pagesOrder: "random"` isn't a native SurveyJS
  property; if a study design genuinely needs counterbalanced page order, that's a
  custom shuffler — deferred until a study actually wants it (design doc §5).
- **Automated PII detection** — the reviewer pass is human today (§5).
- **Scheduled/automatic ETL** — the warehouse rebuilds on demand, when you ask.
- **A visual survey designer** — authoring is JSON + live preview + the pattern
  library; a SurveyJS Creator license is a deferred option if non-technical
  authoring becomes a need.
- **Real-time analytics, web-scale volumes** — out of scope by design.

---

## 9. Where to look next

- **Architecture and rationale:** [`survey-engine-design-doc.md`](survey-engine-design-doc.md)
  — §3.5 (warehouse model), §3.9 (free-text/PII), §3.8 (withdrawal), §4 (rejected
  alternatives, with reasons).
- **Authoring examples:** [`patterns/`](patterns/) and its README.
- **What each milestone verifies, with runnable queries:**
  [`docs/verification/`](docs/verification/) — concrete walkthroughs of the routing
  states, the question types, and the PII redaction/promotion behavior on seeded data.
