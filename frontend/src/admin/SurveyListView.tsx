import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import {
  clearSurveyShortCode,
  createSurvey,
  downloadSurveyExport,
  listSurveys,
  setSurveyShortCode,
  type SurveySummary,
} from '../api';
import { formatDate } from '../format';
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  INPUT_CLASSES,
  LoadingState,
  PageHeader,
  statusTone,
} from '../ui';

// A minimal valid starter so a freshly-created draft renders in the editor and
// preview; the author replaces it. One empty page keeps publish-validation honest
// (an author must add elements before publishing).
const STARTER_DEFINITION = { pages: [{ name: 'page1', elements: [] }] };

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// Group versions under their survey, preserving the server's newest-first order
// (both across surveys and across versions within a survey).
function groupBySurvey(rows: SurveySummary[]): SurveySummary[][] {
  const groups = new Map<string, SurveySummary[]>();
  for (const row of rows) {
    const existing = groups.get(row.survey_id);
    if (existing) existing.push(row);
    else groups.set(row.survey_id, [row]);
  }
  return [...groups.values()];
}

// The highest-version published row, or undefined if nothing's published yet.
// A short link only resolves to a published version, so the copy-link control
// keys off this.
function latestPublished(versions: SurveySummary[]): SurveySummary | undefined {
  return versions
    .filter((v) => v.status === 'published')
    .reduce<
      SurveySummary | undefined
    >((best, v) => (best === undefined || v.version > best.version ? v : best), undefined);
}

function SurveyCard({ versions }: { versions: SurveySummary[] }) {
  const survey = versions[0];
  const [code, setCode] = useState<string | null>(survey?.short_code ?? null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(survey?.short_code ?? '');
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  // Holds the "Copied!" reset timer so we can cancel it if the card unmounts
  // before it fires (avoids a state update on an unmounted component).
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(copiedTimer.current), []);

  if (!survey) return null;
  const totalResponses = versions.reduce((sum, v) => sum + v.response_count, 0);
  const published = latestPublished(versions);

  const origin = typeof window === 'undefined' ? '' : window.location.origin;
  // Only published versions are reachable; with no published version there's no
  // working link to copy, so the control is disabled until then.
  const link = published
    ? code
      ? `${origin}/s/${code}`
      : `${origin}/?survey=${survey.survey_id}&version=${published.version}`
    : null;

  const handleCopy = async (): Promise<void> => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setError(null);
      window.clearTimeout(copiedTimer.current);
      copiedTimer.current = window.setTimeout(() => setCopied(false), 1500);
    } catch (err: unknown) {
      setError(errorMessage(err));
    }
  };

  const handleSave = (): void => {
    setBusy(true);
    setError(null);
    setSurveyShortCode(survey.survey_id, draft.trim())
      .then((result) => {
        setCode(result.short_code);
        setDraft(result.short_code);
        setEditing(false);
      })
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  const handleClear = (): void => {
    setBusy(true);
    setError(null);
    clearSurveyShortCode(survey.survey_id)
      .then(() => {
        setCode(null);
        setDraft('');
        setEditing(false);
      })
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  const handleExport = (): void => {
    setExporting(true);
    setError(null);
    downloadSurveyExport(survey.survey_id)
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setExporting(false));
  };

  return (
    <Card>
      <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
        <code className="truncate text-sm font-semibold text-ink">{survey.survey_id}</code>
        <span className="shrink-0 text-xs text-muted">
          {versions.length} version{versions.length === 1 ? '' : 's'} · {totalResponses} response
          {totalResponses === 1 ? '' : 's'}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-5 py-3">
        {editing ? (
          <>
            <label htmlFor={`code-${survey.survey_id}`} className="text-xs font-medium text-muted">
              Short code
            </label>
            <input
              id={`code-${survey.survey_id}`}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="e.g. climate-2026"
              disabled={busy}
              className={`${INPUT_CLASSES} max-w-[16rem]`}
            />
            <Button type="button" size="sm" onClick={handleSave} disabled={busy || !draft.trim()}>
              Save
            </Button>
            {code ? (
              <Button
                type="button"
                size="sm"
                variant="danger"
                onClick={handleClear}
                disabled={busy}
              >
                Remove
              </Button>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setDraft(code ?? '');
                setError(null);
              }}
              disabled={busy}
            >
              Cancel
            </Button>
          </>
        ) : (
          <>
            <span className="text-xs font-medium text-muted">Short code</span>
            {code ? (
              <code className="text-sm text-ink">{code}</code>
            ) : (
              <span className="text-sm text-faint">none</span>
            )}
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => {
                setEditing(true);
                setDraft(code ?? '');
              }}
            >
              {code ? 'Edit' : 'Add short code'}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => void handleCopy()}
              disabled={!link}
              title={link ? link : 'Publish a version to get a shareable link'}
            >
              {copied ? 'Copied!' : 'Copy link'}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={handleExport}
              disabled={exporting}
              title="Download all responses as CSV (from the last ETL build)"
            >
              {exporting ? 'Exporting…' : 'Export CSV'}
            </Button>
          </>
        )}
      </div>
      {error ? (
        <div className="border-b border-border px-5 py-2">
          <Alert tone="error">{error}</Alert>
        </div>
      ) : null}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-faint">
            <th className="px-5 py-2 font-medium">Version</th>
            <th className="px-5 py-2 font-medium">Status</th>
            <th className="px-5 py-2 font-medium">Responses</th>
            <th className="px-5 py-2 font-medium">Published</th>
            <th className="px-5 py-2" />
          </tr>
        </thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.version} className="border-t border-border">
              <td className="px-5 py-2 font-medium text-ink">v{v.version}</td>
              <td className="px-5 py-2">
                <Badge tone={statusTone(v.status)}>{v.status}</Badge>
              </td>
              <td className="px-5 py-2 text-muted">{v.response_count}</td>
              <td className="px-5 py-2 text-muted">
                {v.published_at ? formatDate(v.published_at) : '—'}
              </td>
              <td className="px-5 py-2 text-right">
                <Link
                  to={`/admin/surveys/${v.survey_id}/versions/${v.version}`}
                  aria-label={`Open ${v.survey_id} v${v.version}`}
                  className="font-medium text-brand-dark hover:underline"
                >
                  Open
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

export function SurveyListView() {
  const navigate = useNavigate();
  const [surveys, setSurveys] = useState<SurveySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let active = true;
    listSurveys()
      .then((rows) => {
        if (active) setSurveys(rows);
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      });
    return () => {
      active = false;
    };
  }, []);

  const handleCreate = (): void => {
    setCreating(true);
    setError(null);
    createSurvey(STARTER_DEFINITION)
      .then((created) =>
        navigate(`/admin/surveys/${created.survey_id}/versions/${created.version}`),
      )
      .catch((err: unknown) => {
        setError(errorMessage(err));
        setCreating(false);
      });
  };

  const newButton = (
    <Button type="button" onClick={handleCreate} disabled={creating}>
      {creating ? 'Creating…' : 'New survey'}
    </Button>
  );

  return (
    <section>
      <PageHeader
        title="Surveys"
        subtitle="Draft, publish, and track responses across versions."
        actions={newButton}
      />
      {error ? <Alert tone="error">Error: {error}</Alert> : null}
      {surveys === null ? (
        // A failed initial load shows the error alone — not a perpetual spinner.
        error ? null : (
          <LoadingState />
        )
      ) : surveys.length === 0 ? (
        <EmptyState>No surveys yet.</EmptyState>
      ) : (
        <div className="flex flex-col gap-4">
          {groupBySurvey(surveys).map((versions) => (
            <SurveyCard key={versions[0]?.survey_id} versions={versions} />
          ))}
        </div>
      )}
    </section>
  );
}
