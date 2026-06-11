---
name: gen-survey
description: Generate a SurveyJS JSON definition from a list of questions (loose markdown/text or structured YAML/JSON). Emits a single file; does not publish or load.
---

# gen-survey

Turn a question list into a gate-valid SurveyJS JSON file. Grounded in [patterns/](../../../patterns/) — copy the nearest pattern's shape rather than inventing structure.

Output a file only. Do not publish, hash, or load into the API.

## Inputs

Invocation: `/gen-survey <arg>` where `<arg>` is one of:

- **A path to a file** (`.md`, `.txt`, `.yaml`, `.yml`, `.json`) — read it, then detect format.
- **Inline text** — treat as the question list directly.
- **Empty** — ask the user to paste or point at the list.

### Format detection

- **Structured** if the input parses as YAML/JSON and the top level is a list of objects with at least `prompt` (or `title`) and `type`. Translate 1:1.
- **Loose** otherwise (bulleted list, numbered list, free prose). Infer `type` from cues (see below) and ask for anything ambiguous.

### Type inference from loose input

| Cue in prompt | Inferred `type` |
|---|---|
| "select one", "choose one", "which of" + short list | `radiogroup` |
| "pick all", "select all that apply", "which of the following" | `checkbox` |
| "rank", "order from" | `ranking` |
| "rate", "on a scale of N to M", "1–5" | `rating` |
| "yes/no", "true/false" | `boolean` |
| "how many", "how old", "count of" | `text` + `inputType: number` |
| "when", "date of", "birthday" | `text` + `inputType: date` |
| "describe", "tell us about", "comments", "explain" | `comment` |
| "name", "email", short open answer | `text` (free-text → PII routing applies) |
| grid like "rate each of these on …" | `matrix` or `matrixdropdown` |
| "for each X you …" | `paneldynamic` |

When the cue is weak, ask. Do not silently pick.

## Workflow

1. **Read input.** Resolve `<arg>` per above.
2. **Read the relevant pattern.** Pick the closest match from [patterns/](../../../patterns/) for each question type you'll emit, and copy its element shape. The pattern files are the source of truth for field names, choice format, and `pii_risk` handling.
3. **Walk the question list.** For each item, build a SurveyJS element. Generate `name` as a stable kebab/snake-case slug from the prompt (e.g. "How many years…" → `years_gaming`). For choices, `value` is a stable key, `text` is the human label.
4. **Stop and ask** at the points in the checklist below. Do not infer or default.
5. **Assemble.** Wrap elements in one or more `pages`. Default to a single page unless the input is grouped or has >~10 questions.
6. **Validate locally** before writing (see checks below).
7. **Write** to `/tmp/<slug>.json` where `<slug>` derives from the survey title. If the user passed an output path as a second arg or in the request, honor it.
8. **Report** the file path and a one-line summary (question count, types used, any items you flagged).

## Stop-and-ask checklist

These are methodological judgments. The design doc forbids silent defaults for them. Ask the user before emitting:

- **Free-text PII downgrade.** Every `text`/`comment` defaults to `pii_risk: "high"`. If a prompt looks innocuous (e.g. "first name only", "favorite color"), ask whether to downgrade to `"low"`. Downgrade requires a `pii_risk_rationale` from the user — never write one yourself. See [patterns/free_text_pii.json](../../../patterns/free_text_pii.json).
- **Branching.** If the input mentions "if they answered X, then …" or implies conditional questions, ask for the exact `visibleIf` expression (or confirm SurveyJS expression syntax). Do not guess from prompt similarity. See [patterns/branching.json](../../../patterns/branching.json).
- **Choice values.** When loose input gives only labels (e.g. "North America, Europe, …"), propose stable `value` keys (`na`, `eu`) and confirm before emitting. Stable keys outlive label edits.
- **Matrix rows / columns.** For `matrix` / `matrixdropdown`, ask for the row IDs explicitly. Row `value`s are the join key into facts — they need to be deliberate, not auto-generated from row labels.
- **Repeating groups.** For `paneldynamic`, confirm the per-occurrence template elements and whether occurrence count is bounded.

Never ask about `parent_question_id`. The skill does not write it under any circumstances (see invariants).

## Type surface (gate-allowed only)

Reject anything outside this set. These are the only types the publish gate, runtime, and dbt staging all handle.

| Type | Kind | Notes |
|---|---|---|
| `radiogroup` | single-select | choices as `{value, text}` or scalar |
| `dropdown` | single-select | same as radiogroup |
| `checkbox` | multi-select | array answer, fans out per option |
| `ranking` | ranked-choice | array answer, each carries `rank` |
| `text` | free-text (single line) | `pii_risk` required (see checklist) |
| `comment` | free-text (multi-line) | `pii_risk` required |
| `text` + `inputType: number` | scalar numeric | not free text, no PII |
| `text` + `inputType: range` | scalar numeric | not free text, no PII |
| `text` + `inputType: date` | scalar date | `YYYY-MM-DD`, no PII |
| `matrix` | grid of single-selects | rows + columns required |
| `matrixdropdown` | grid of typed cells | option-typed cells only at top level |
| `paneldynamic` | repeating group | template elements per occurrence |
| `rating` | scalar | numeric `rateValues` only |
| `boolean` | scalar | no custom `valueTrue`/`valueFalse` |

If the user asks for a type not in this table (e.g. `imagepicker`, `signaturepad`, `file`), refuse and explain that the publish gate will reject it. Offer the closest gate-allowed alternative.

## Pre-write validation

Before writing the file, check:

- Every element has a unique `name` within the survey.
- Every `choices` entry on single/multi-select has a unique `value`.
- Every `matrix`/`matrixdropdown` has a non-empty `rows` and `columns` with unique ids.
- Every `text`/`comment` (free text, no numeric/date `inputType`) carries `pii_risk`. If `"low"`, it also carries `pii_risk_rationale`.
- No element references `parent_question_id` or `parent_question_rationale`.
- No `visibleIf` references a `name` that doesn't exist in the survey.
- The whole thing parses as JSON.

If any check fails, fix it (or ask the user) before writing. Do not write a file you know is gate-invalid.

## Output

- Default path: `/tmp/<slug>.json` where `<slug>` = kebab-cased survey title.
- Indent with 2 spaces, trailing newline.
- After writing, print the absolute path and a summary like: `Wrote /tmp/foo.json — 12 questions across 2 pages (radiogroup×4, checkbox×3, text×2, rating×3). Flagged: q7 PII downgrade pending confirmation.`

## What NOT to do

These rules come from [CLAUDE.md](../../../CLAUDE.md) and [survey-engine-design-doc.md](../../../survey-engine-design-doc.md):

- Do not auto-populate `parent_question_id` or `parent_question_rationale`. Cross-version equivalence is a researcher's call, not a heuristic.
- Do not silently downgrade `pii_risk` from `high` to `low`. The default is `high`; downgrades are explicit, with a user-supplied rationale.
- Do not emit a question type outside the gate-allowed surface above.
- Do not publish, hash, or load the survey. This skill writes a file and stops. The user runs the publish gate separately.
- Do not collapse `was_shown` semantics — but that's runtime concern, not authoring; you shouldn't be writing it anyway.

## Patterns to copy from

In order of frequency:

- [patterns/single_select.json](../../../patterns/single_select.json) — `radiogroup` and `dropdown` shapes.
- [patterns/multi_select.json](../../../patterns/multi_select.json) — `checkbox`.
- [patterns/scalar.json](../../../patterns/scalar.json) — `rating`, `boolean`, numeric/date `text`.
- [patterns/free_text_pii.json](../../../patterns/free_text_pii.json) — `text`/`comment` with `pii_risk` annotation.
- [patterns/ranked.json](../../../patterns/ranked.json) — `ranking`.
- [patterns/matrix.json](../../../patterns/matrix.json) and [patterns/matrixdropdown.json](../../../patterns/matrixdropdown.json) — grids.
- [patterns/repeating_group.json](../../../patterns/repeating_group.json) — `paneldynamic`.
- [patterns/branching.json](../../../patterns/branching.json) — `visibleIf`.
- [patterns/multi_page.json](../../../patterns/multi_page.json) — multi-page with cross-page `visibleIf`.
- [patterns/calculated_values.json](../../../patterns/calculated_values.json) — named expressions reused in `visibleIf`.

Read the specific pattern before emitting that question type. The shape there is gate-valid by construction; the shape you remember may not be.

## Portability note

The gate-specific rules in this skill (narrow type surface, `pii_risk` routing, no `parent_question_id`) are survey-engine invariants. If this skill is ever extracted to a generic SurveyJS skill for other repos, those sections need to be stripped or made configurable.
