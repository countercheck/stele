#!/usr/bin/env python3
"""Lint Stele's load-bearing invariants against the codebase.

Each invariant from CLAUDE.md that has a mechanical check shape lives here as
a Check subclass. The runner discovers all registered checks, runs them, and
reports violations with file:line locations and a pointer back to CLAUDE.md.

Currently implements:
  - Invariant 4: dbt models read only from app.raw_responses
                 (both as SQL references and as declared dbt sources)
  - Invariant 5: dim_question.parent_question_id writes always co-occur with
                 parent_question_rationale writes
  - Invariant 6: marts.fact_response_item.value_text writes only for questions
                 tagged pii_risk='low' (best-effort static check)

Usage:
  scripts/check_invariants.py                  # human output, exit 1 on violation
  scripts/check_invariants.py --json           # JSON output to stdout
  scripts/check_invariants.py --only 4         # run a single check by number
  scripts/check_invariants.py --log .lint.log  # append JSON record to log file

Designed for pre-commit (staged files passed as positional args) and CI
(no args → scan the whole repo).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


@dataclass
class Violation:
    invariant: int
    path: str  # repo-relative
    line: int | None
    snippet: str
    detail: str


@dataclass
class CheckResult:
    invariant: int
    name: str
    files_scanned: int
    violations: list[Violation] = field(default_factory=list)
    skipped_reason: str | None = None  # populated when the check couldn't run

    @property
    def ok(self) -> bool:
        return not self.violations and self.skipped_reason is None


# ---------------------------------------------------------------------------
# Check framework
# ---------------------------------------------------------------------------


class Check(ABC):
    invariant: ClassVar[int]
    name: ClassVar[str]
    summary: ClassVar[str]  # printed in violation header; cite the design doc

    @abstractmethod
    def run(self, staged: list[Path] | None) -> CheckResult:
        """Run the check. If `staged` is not None, restrict to those paths
        (pre-commit mode). Otherwise scan everything the check cares about.
        """


# Subclasses self-register here.
REGISTRY: list[type[Check]] = []


def register(cls: type[Check]) -> type[Check]:
    REGISTRY.append(cls)
    return cls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_sql_comments_preserving_lines(sql: str) -> str:
    """Replace comments with spaces, not empty strings, so line numbers in
    the stripped text match the original. Handles --, /* */, and Jinja {# #}.
    """

    def blank(match: re.Match[str]) -> str:
        # Preserve newlines so line numbering stays aligned; everything else
        # becomes a space.
        return "".join("\n" if c == "\n" else " " for c in match.group(0))

    sql = re.sub(r"--[^\n]*", blank, sql)
    sql = re.sub(r"/\*.*?\*/", blank, sql, flags=re.DOTALL)
    sql = re.sub(r"\{#.*?#\}", blank, sql, flags=re.DOTALL)
    return sql


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _filter_to_staged(paths: list[Path], staged: list[Path] | None) -> list[Path]:
    if staged is None:
        return paths
    staged_resolved = {p.resolve() for p in staged}
    return [p for p in paths if p.resolve() in staged_resolved]


# ---------------------------------------------------------------------------
# Invariant 4 — one JSON parser
# ---------------------------------------------------------------------------


@register
class Invariant4_SqlReferences(Check):
    """dbt SQL must not reference app.responses or app.response_items by name."""

    invariant = 4
    name = "invariant-4-sql-refs"
    summary = (
        "dbt must not read from normalized app tables. "
        "Reads from app.raw_responses only. See CLAUDE.md and design doc § 3.4."
    )

    FORBIDDEN_TABLES = ("responses", "response_items")

    def __init__(self) -> None:
        self._patterns = {t: self._build_pattern(t) for t in self.FORBIDDEN_TABLES}

    @staticmethod
    def _build_pattern(table: str) -> re.Pattern[str]:
        # Match `app.responses`, `"app"."responses"`, case-insensitive, with
        # word boundaries so raw_responses and responses_archive don't match.
        return re.compile(
            r'\b"?app"?\s*\.\s*"?' + re.escape(table) + r'\b"?',
            re.IGNORECASE,
        )

    def run(self, staged: list[Path] | None) -> CheckResult:
        dbt_dir = REPO_ROOT / "dbt"
        if not dbt_dir.is_dir():
            return CheckResult(
                invariant=self.invariant,
                name=self.name,
                files_scanned=0,
                skipped_reason=f"{_rel(dbt_dir)} not found",
            )

        sql_files = _filter_to_staged(sorted(dbt_dir.rglob("*.sql")), staged)
        violations: list[Violation] = []

        for path in sql_files:
            text = path.read_text(encoding="utf-8")
            stripped = _strip_sql_comments_preserving_lines(text)
            for line_no, line in enumerate(stripped.splitlines(), start=1):
                for table, pattern in self._patterns.items():
                    if pattern.search(line):
                        # Use the original line for the snippet (post-strip
                        # whitespace looks weird; original is what the dev sees).
                        original = text.splitlines()[line_no - 1].strip()
                        violations.append(
                            Violation(
                                invariant=self.invariant,
                                path=_rel(path),
                                line=line_no,
                                snippet=original,
                                detail=f"references app.{table}",
                            )
                        )

        return CheckResult(
            invariant=self.invariant,
            name=self.name,
            files_scanned=len(sql_files),
            violations=violations,
        )


@register
class Invariant4_SourcesYaml(Check):
    """dbt sources.yml may declare only raw_responses under the app schema.

    This is the stronger form of the check: it prevents anyone from adding
    `responses` or `response_items` to sources.yml and then referencing them
    via {{ source(...) }}, which the SQL grep wouldn't catch.
    """

    invariant = 4
    name = "invariant-4-sources-yaml"
    summary = (
        "dbt sources.yml must declare only raw_responses under schema 'app'. "
        "Any other table under app makes the normalized read-model parseable "
        "by dbt, violating the one-parser rule. See CLAUDE.md § Invariants."
    )

    ALLOWED_APP_TABLES: ClassVar[set[str]] = {"raw_responses"}

    def run(self, staged: list[Path] | None) -> CheckResult:
        dbt_dir = REPO_ROOT / "dbt"
        if not dbt_dir.is_dir():
            return CheckResult(
                invariant=self.invariant,
                name=self.name,
                files_scanned=0,
                skipped_reason=f"{_rel(dbt_dir)} not found",
            )

        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            return CheckResult(
                invariant=self.invariant,
                name=self.name,
                files_scanned=0,
                skipped_reason="PyYAML not installed; sources.yml check skipped",
            )

        yaml_files = _filter_to_staged(
            sorted(list(dbt_dir.rglob("sources.yml")) + list(dbt_dir.rglob("sources.yaml"))),
            staged,
        )
        violations: list[Violation] = []

        for path in yaml_files:
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                violations.append(
                    Violation(
                        invariant=self.invariant,
                        path=_rel(path),
                        line=None,
                        snippet="",
                        detail=f"could not parse YAML: {e}",
                    )
                )
                continue

            if not isinstance(doc, dict):
                continue
            for src in doc.get("sources", []) or []:
                if not isinstance(src, dict):
                    continue
                # The source's `schema` is what dbt actually queries against;
                # `name` is the dbt-side identifier. We check `schema` when
                # present, falling back to `name`.
                schema_name = (src.get("schema") or src.get("name") or "").lower()
                if schema_name != "app":
                    continue
                for tbl in src.get("tables", []) or []:
                    if not isinstance(tbl, dict):
                        continue
                    tbl_name = (tbl.get("identifier") or tbl.get("name") or "").lower()
                    if tbl_name and tbl_name not in self.ALLOWED_APP_TABLES:
                        violations.append(
                            Violation(
                                invariant=self.invariant,
                                path=_rel(path),
                                line=None,  # could parse with ruamel for line nos
                                snippet=f"source app.{tbl_name}",
                                detail=(
                                    f"declares table '{tbl_name}' under app schema; "
                                    f"only {sorted(self.ALLOWED_APP_TABLES)} allowed"
                                ),
                            )
                        )

        return CheckResult(
            invariant=self.invariant,
            name=self.name,
            files_scanned=len(yaml_files),
            violations=violations,
        )


# ---------------------------------------------------------------------------
# Invariant 5 — parent_question_id always paired with rationale
# ---------------------------------------------------------------------------


@register
class Invariant5_ParentQuestionPaired(Check):
    """Writes to parent_question_id must co-occur with parent_question_rationale.

    This is a best-effort static check: it scans SQL and Python for INSERT/UPDATE
    statements that touch `parent_question_id` and verifies the same statement
    also references `parent_question_rationale`. Won't catch every form (e.g.
    dynamic SQL built at runtime), but it catches the common ones.
    """

    invariant = 5
    name = "invariant-5-parent-question-paired"
    summary = (
        "parent_question_id writes must co-occur with parent_question_rationale. "
        "The judgment is more valuable than the link. See CLAUDE.md § Invariants."
    )

    # Find a statement-ish window around any mention of parent_question_id.
    # We look at INSERT/UPDATE statements (rough heuristic: a SQL statement
    # ending in a semicolon containing the column name).
    STMT_RE = re.compile(
        r"(INSERT\s+INTO|UPDATE)\b[^;]*?\bparent_question_id\b[^;]*?;",
        re.IGNORECASE | re.DOTALL,
    )

    def run(self, staged: list[Path] | None) -> CheckResult:
        candidates = (
            list((REPO_ROOT / "dbt").rglob("*.sql")) if (REPO_ROOT / "dbt").is_dir() else []
        )
        candidates += (
            list((REPO_ROOT / "api").rglob("*.py")) if (REPO_ROOT / "api").is_dir() else []
        )
        candidates += (
            list((REPO_ROOT / "api").rglob("*.sql")) if (REPO_ROOT / "api").is_dir() else []
        )
        candidates = _filter_to_staged(sorted(candidates), staged)

        violations: list[Violation] = []
        for path in candidates:
            text = path.read_text(encoding="utf-8")
            stripped = _strip_sql_comments_preserving_lines(text) if path.suffix == ".sql" else text
            for match in self.STMT_RE.finditer(stripped):
                stmt = match.group(0)
                if "parent_question_rationale" not in stmt.lower():
                    # Find the line of the match start.
                    line_no = stripped.count("\n", 0, match.start()) + 1
                    violations.append(
                        Violation(
                            invariant=self.invariant,
                            path=_rel(path),
                            line=line_no,
                            snippet=stmt.strip()[:200],
                            detail="writes parent_question_id without parent_question_rationale",
                        )
                    )

        return CheckResult(
            invariant=self.invariant,
            name=self.name,
            files_scanned=len(candidates),
            violations=violations,
        )


# ---------------------------------------------------------------------------
# Invariant 6 — value_text only for pii_risk='low'
# ---------------------------------------------------------------------------


@register
class Invariant6_ValueTextGuard(Check):
    """marts.fact_response_item.value_text writes require a pii_risk='low' guard.

    Static check: find writes of `value_text` into the fact table and verify a
    `pii_risk` filter sits with them. A write shows up either as a dbt projection
    `<expr> as value_text` (models don't terminate statements with ';') or as a
    ';'-terminated INSERT/UPDATE in raw SQL. For the projection form the guard
    must appear in the expression immediately preceding the alias; GUARD_WINDOW
    bounds that lookback so an unrelated pii_risk elsewhere in the model can't
    mask an ungated write. This is a coarse net (the guard may be expressed many
    ways), but it catches the obvious omissions and forces the question to be
    visible in code review.
    """

    invariant = 6
    name = "invariant-6-value-text-guard"
    summary = (
        "value_text writes to fact_response_item require a pii_risk='low' guard. "
        "Default is 'high'; downgrades are deliberate. See CLAUDE.md § Invariants."
    )

    ALIAS_RE = re.compile(r"\bas\s+value_text\b", re.IGNORECASE)
    GUARD_WINDOW = 300
    STMT_RE = re.compile(
        r"(INSERT\s+INTO|UPDATE)\b[^;]*?\bvalue_text\b[^;]*?;",
        re.IGNORECASE | re.DOTALL,
    )

    def run(self, staged: list[Path] | None) -> CheckResult:
        dbt_dir = REPO_ROOT / "dbt"
        if not dbt_dir.is_dir():
            return CheckResult(
                invariant=self.invariant,
                name=self.name,
                files_scanned=0,
                skipped_reason=f"{_rel(dbt_dir)} not found",
            )

        # Only check fact_response_item-related models; otherwise too many
        # false positives from incidental value_text references.
        sql_files = [
            p
            for p in dbt_dir.rglob("*.sql")
            if "fact_response_item" in p.name
            or "fact_response_item" in p.read_text(encoding="utf-8", errors="ignore")
        ]
        sql_files = _filter_to_staged(sorted(sql_files), staged)

        violations: list[Violation] = []
        for path in sql_files:
            text = path.read_text(encoding="utf-8")
            stripped = _strip_sql_comments_preserving_lines(text)

            # dbt projection form: `<expr> as value_text`. The guard must sit in
            # the expression just before the alias (bounded by GUARD_WINDOW).
            for match in self.ALIAS_RE.finditer(stripped):
                window = stripped[max(0, match.start() - self.GUARD_WINDOW) : match.start()]
                if "pii_risk" not in window.lower():
                    line_no = stripped.count("\n", 0, match.start()) + 1
                    violations.append(
                        Violation(
                            invariant=self.invariant,
                            path=_rel(path),
                            line=line_no,
                            snippet=stripped[max(0, match.start() - 60) : match.end()].strip()[
                                :200
                            ],
                            detail="value_text projection not gated by a pii_risk condition",
                        )
                    )

            # Raw SQL form: a ';'-terminated INSERT/UPDATE writing value_text.
            for match in self.STMT_RE.finditer(stripped):
                stmt = match.group(0).lower()
                # Only flag statements that actually write value_text (i.e.
                # touch fact_response_item as the target).
                if "fact_response_item" not in stmt:
                    continue
                if "pii_risk" not in stmt:
                    line_no = stripped.count("\n", 0, match.start()) + 1
                    violations.append(
                        Violation(
                            invariant=self.invariant,
                            path=_rel(path),
                            line=line_no,
                            snippet=match.group(0).strip()[:200],
                            detail="writes value_text without referencing pii_risk",
                        )
                    )

        return CheckResult(
            invariant=self.invariant,
            name=self.name,
            files_scanned=len(sql_files),
            violations=violations,
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _print_human(results: list[CheckResult]) -> None:
    any_violation = False
    for r in results:
        if r.skipped_reason:
            print(f"skipped (invariant {r.invariant}, {r.name}): {r.skipped_reason}")
            continue
        if r.ok:
            print(f"ok: invariant {r.invariant} ({r.name}), {r.files_scanned} file(s) scanned")
            continue
        any_violation = True
        # Find the Check class to print its summary.
        cls = next((c for c in REGISTRY if c.name == r.name), None)
        summary = cls.summary if cls else ""
        print(f"\nINVARIANT {r.invariant} VIOLATION ({r.name})")
        if summary:
            print(summary)
        print()
        for v in r.violations:
            loc = f"{v.path}:{v.line}" if v.line else v.path
            print(f"  {loc}: {v.detail}")
            if v.snippet:
                # Indent the snippet for readability.
                for line in v.snippet.splitlines():
                    print(f"    {line}")
        total = len(r.violations)
        files = len({v.path for v in r.violations})
        print(f"\n  {total} violation(s) across {files} file(s).")
    if not any_violation:
        print("\nall invariants clean.")


def _emit_json(results: list[CheckResult], log_path: Path | None) -> str:
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "results": [
            {
                "invariant": r.invariant,
                "name": r.name,
                "files_scanned": r.files_scanned,
                "skipped_reason": r.skipped_reason,
                "violations": [asdict(v) for v in r.violations],
            }
            for r in results
        ],
    }
    blob = json.dumps(record, indent=2)
    if log_path is not None:
        # Append one JSON object per line (JSONL) so the file stays parseable
        # as a stream of records.
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    return blob


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Optional list of staged files (pre-commit mode). If omitted, scan the whole repo.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Append a JSON record to this file (JSONL). Useful for CI history.",
    )
    parser.add_argument(
        "--only",
        type=int,
        default=None,
        help="Run only the check(s) for this invariant number.",
    )
    args = parser.parse_args(argv)

    staged: list[Path] | None = args.files if args.files else None

    checks = [cls() for cls in REGISTRY if args.only is None or cls.invariant == args.only]
    if args.only is not None and not checks:
        print(f"no checks registered for invariant {args.only}", file=sys.stderr)
        return 2

    results = [check.run(staged) for check in checks]

    if args.json or args.log:
        blob = _emit_json(results, args.log)
        if args.json:
            print(blob)
    else:
        _print_human(results)

    any_violation = any(r.violations for r in results)
    return 1 if any_violation else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
