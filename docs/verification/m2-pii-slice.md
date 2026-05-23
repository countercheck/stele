# M2.1 free-text + PII-risk routing — verification

Proves free-text answers route by `pii_risk` (design-doc §3.9, invariant 6):
low-risk text reaches the analyst marts; high-risk text is redacted there and
copied to the restricted `pii.free_text_responses` reviewer store. Builds on the
same seed + warehouse as `m1-slice.md` — run that first (steps 1–3).

The example survey adds two free-text (`comment`) questions:

| question | pii_risk | rationale |
|---|---|---|
| `ft_low` | `low` | explicit downgrade with rationale → text reaches marts |
| `ft_high` | `high` | default-safe → redacted in marts, copied to `pii` |

## 1. Publish gate enforces "no silent downgrade"

A free-text question downgraded to `pii_risk='low'` without a rationale is
rejected at publish (invariant 6); absent `pii_risk` defaults to `high`. Covered
by `api/tests/test_surveys.py::test_publish_rejects_low_risk_without_rationale`
and `::test_publish_rejects_invalid_pii_risk`.

## 2. Submission routing (operational)

`api/tests/test_responses.py` asserts: high-risk free text lands in
`pii.free_text_responses` (and the read-model keeps a faithful copy); low-risk
does not; absent `pii_risk` routes as high.

After seeding, the `pii` store holds only the **answered high-risk** values:

```bash
PGPASSWORD=dev psql -h localhost -U stele_dev -d stele -c "
  SELECT ft.question_name, ft.value_text, ft.pii_risk
  FROM pii.free_text_responses ft
  JOIN app.raw_responses rr ON rr.id = ft.raw_response_id
  WHERE rr.client_metadata ->> 'source' = 'seed_example_survey';"
```

Expect 2 rows, both `ft_high` (the two respondents who answered it); the
shown-but-skipped and routed-past respondents contribute none.

## 3. Warehouse redaction (analyst-facing)

```bash
PGPASSWORD=dev psql -h localhost -U stele_etl -d stele -c "
  SELECT dq.stable_name, dq.pii_risk, fri.value_text, fri.value_text_redacted, fri.was_shown
  FROM marts.fact_response_item fri
  JOIN marts.dim_question dq ON dq.question_id = fri.question_id
  WHERE dq.stable_name IN ('ft_low','ft_high')
  ORDER BY dq.stable_name, fri.value_text NULLS LAST;"
```

Expected (per the 4 seeded respondents):

| stable_name | pii_risk | value_text | value_text_redacted | note |
|---|---|---|---|---|
| `ft_high` | high | _(null)_ | `t` | every high-risk row redacted, answered or not |
| `ft_low` | low | `great` / `good` / `ok` | `f` | low-risk text reaches marts |
| `ft_low` | low | _(null)_ | `f` | routed-past — no answer, not redacted |

The high-risk text is **absent from marts** (only in `pii.free_text_responses`);
the low-risk text **is** in marts. This is invariant 6 end-to-end.

## 4. Guardrails

- `python3 scripts/check_invariants.py --only 6` is clean for the gated
  `fact_response_item.sql`, and **fires** if the `value_text` projection is
  ungated (no `pii_risk` in its expression) — the lint binds to the dbt
  projection form, not just `;`-terminated SQL.
- `dbt build` runs `free_text_redaction_parity` (cross-checks marts redaction
  against the snapshot `pii_risk`) and the extended `polymorphic_value_invariant`
  (now counts `value_text` as a value slot, invariant 8).
