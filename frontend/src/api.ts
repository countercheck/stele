// All API routes live under /api (the backend mounts the API app there; see
// api/main.py). In dev, Vite proxies /api to FastAPI; in prod the same FastAPI
// process serves both /api and this SPA from one origin, so the auth session
// cookie rides along same-origin with no CORS.
const API_BASE = '/api';

export interface SurveyDetail {
  survey_id: string;
  version: number;
  status: string;
  definition_hash: string | null;
  definition_json: Record<string, unknown>;
}

// Survey/version metadata without definition_json — the shape create/edit/publish
// return.
export interface SurveyMeta {
  survey_id: string;
  version: number;
  status: string;
  definition_hash: string | null;
  published_at: string | null;
  created_at: string;
}

// A row from the admin list endpoint: metadata plus its live response count.
export interface SurveySummary extends SurveyMeta {
  // Live (non-tombstoned) response count for this version.
  response_count: number;
  // The survey's short code, if set. Survey-level, so it repeats across every
  // version row of the same survey; null when unset.
  short_code: string | null;
}

// What a /s/<code> link resolves to: a survey + its latest published version.
export interface ShortCodeResolved {
  survey_id: string;
  version: number;
}

export interface SubmitBody {
  definition_hash: string;
  payload: Record<string, unknown>;
  shown_questions: string[];
}

export interface SubmitResult {
  raw_response_id: number;
  respondent_id: string;
  submitted_at: string;
}

export interface User {
  id: number;
  email: string;
  roles: string[];
  disabled: boolean;
  created_at: string;
}

/** An HTTP error carrying the status code, so callers can branch on 401 etc. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// AuthContext registers a handler here so a 401 from *any* call (e.g. a session
// that expired mid-session) clears the cached user and bounces to login, rather
// than each view having to special-case it.
type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

// FastAPI's HTTPException renders {"detail": "..."}; prefer that human-readable
// reason (the publish gate's 422 messages, 409 conflicts) over a synthetic one.
async function errorDetail(res: Response): Promise<string | null> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === 'string' ? body.detail : null;
  } catch {
    return null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    if (res.status === 401) unauthorizedHandler?.();
    const detail = await errorDetail(res);
    throw new ApiError(
      res.status,
      detail ?? `${init?.method ?? 'GET'} ${path} failed (${res.status})`,
    );
  }
  // 204 (logout) has no body.
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  };
}

// --- Respondent-facing (unchanged behavior) --------------------------------

export async function fetchSurvey(surveyId: string, version: number): Promise<SurveyDetail> {
  return request<SurveyDetail>(`/surveys/${surveyId}/versions/${version}`);
}

export async function submitResponse(
  surveyId: string,
  version: number,
  body: SubmitBody,
): Promise<SubmitResult> {
  return request<SubmitResult>(
    `/surveys/${surveyId}/versions/${version}/responses`,
    jsonInit('POST', body),
  );
}

// Resolve a /s/<code> short link to its survey + latest published version.
// Throws ApiError(404) when the code is unknown or has no published version.
export async function resolveShortCode(code: string): Promise<ShortCodeResolved> {
  return request<ShortCodeResolved>(`/surveys/by-code/${encodeURIComponent(code)}`);
}

// --- Auth ------------------------------------------------------------------

export async function login(email: string, password: string): Promise<User> {
  return request<User>('/auth/login', jsonInit('POST', { email, password }));
}

export async function logout(): Promise<void> {
  await request<void>('/auth/logout', jsonInit('POST'));
}

export async function fetchCurrentUser(): Promise<User> {
  return request<User>('/auth/me');
}

// --- Authoring (admin) -----------------------------------------------------

export async function listSurveys(): Promise<SurveySummary[]> {
  return request<SurveySummary[]>('/surveys');
}

export async function createSurvey(definitionJson: Record<string, unknown>): Promise<SurveyMeta> {
  return request<SurveyMeta>('/surveys', jsonInit('POST', { definition_json: definitionJson }));
}

export async function editSurvey(
  surveyId: string,
  version: number,
  definitionJson: Record<string, unknown>,
): Promise<SurveyMeta> {
  return request<SurveyMeta>(
    `/surveys/${surveyId}/versions/${version}`,
    jsonInit('PUT', { definition_json: definitionJson }),
  );
}

export async function publishSurvey(surveyId: string, version: number): Promise<SurveyMeta> {
  return request<SurveyMeta>(`/surveys/${surveyId}/versions/${version}/publish`, jsonInit('POST'));
}

// Set (or replace) a survey's short code. Throws ApiError(409) if another survey
// owns the code, ApiError(422) if the format is invalid.
export async function setSurveyShortCode(
  surveyId: string,
  shortCode: string,
): Promise<{ survey_id: string; short_code: string }> {
  return request(`/surveys/${surveyId}/short-code`, jsonInit('PUT', { short_code: shortCode }));
}

// Remove a survey's short code (idempotent — 204 even if it had none).
export async function clearSurveyShortCode(surveyId: string): Promise<void> {
  await request<void>(`/surveys/${surveyId}/short-code`, { method: 'DELETE' });
}

// Download a survey's responses as a tidy/long CSV (the marts export). Not routed
// through request() because the body is a file, not JSON: fetch directly, surface
// a 401 through the shared handler, then trigger a browser download via a
// temporary object URL. `excelSafe` requests the spreadsheet-targeted variant,
// which neutralizes formula injection in free-text answers.
export async function downloadSurveyExport(
  surveyId: string,
  opts: { excelSafe?: boolean } = {},
): Promise<void> {
  const query = opts.excelSafe ? '?excel_safe=true' : '';
  const res = await fetch(`${API_BASE}/surveys/${surveyId}/export${query}`);
  if (!res.ok) {
    if (res.status === 401) unauthorizedHandler?.();
    const detail = await errorDetail(res);
    throw new ApiError(res.status, detail ?? `export failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `survey-${surveyId}-responses${opts.excelSafe ? '-excel' : ''}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    // Defer revocation past the current tick: revoking the blob URL synchronously
    // after click() can cancel the download before the browser (notably Safari)
    // has started reading it.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

// --- GDPR / erasure (admin) ------------------------------------------------

// A row from the pii.withdrawals erasure audit.
export interface WithdrawalAudit {
  id: number;
  respondent_id: string;
  requested_at: string;
  reason: string | null;
}

// Outcome of a withdrawal trigger; counts are zero on the idempotent repeat path.
export interface WithdrawalResult {
  respondent_id: string;
  requested_at: string;
  already_withdrawn: boolean;
  raw_rows_tombstoned: number;
  responses_purged: number;
  pii_rows_deleted: number;
}

export async function listWithdrawals(): Promise<WithdrawalAudit[]> {
  return request<WithdrawalAudit[]>('/admin/withdrawals');
}

export async function triggerWithdrawal(
  respondentId: string,
  reason?: string,
): Promise<WithdrawalResult> {
  return request<WithdrawalResult>(
    `/respondents/${respondentId}/withdrawal`,
    jsonInit('POST', { reason: reason ?? null }),
  );
}

// --- PII free-text review (reviewer) ---------------------------------------

// 'scrubbed' is terminal — the answer's PII has been destroyed in place (§3.8).
export type ReviewStatus = 'pending' | 'promoted' | 'rejected' | 'scrubbed';
// A recorded decision is only ever one of these (pending = no decision row).
export type DecisionStatus = 'promoted' | 'rejected';

// A high-risk free-text answer in the screening queue. value_text is the PII the
// reviewer screens (null once scrubbed); status is null while pending, else a
// recorded decision or 'scrubbed'.
export interface FreeTextReviewItem {
  id: number;
  raw_response_id: number;
  respondent_id: string;
  survey_id: string;
  survey_version: number;
  question_name: string;
  value_text: string | null;
  created_at: string;
  status: DecisionStatus | 'scrubbed' | null;
}

export interface FreeTextDecision {
  free_text_id: number;
  raw_response_id: number;
  question_name: string;
  status: DecisionStatus;
  reviewed_at: string;
}

export interface FreeTextScrub {
  free_text_id: number;
  raw_response_id: number;
  question_name: string;
  occurrence: number;
  scrubbed_at: string;
  already_scrubbed: boolean;
  raw_payload_scrubbed: boolean;
  read_model_items_scrubbed: number;
  pii_value_cleared: boolean;
}

export async function listFreeTextForReview(
  status: ReviewStatus = 'pending',
): Promise<FreeTextReviewItem[]> {
  return request<FreeTextReviewItem[]>(`/admin/pii/free-text?status=${status}`);
}

export async function promoteFreeText(id: number, note?: string): Promise<FreeTextDecision> {
  return request<FreeTextDecision>(
    `/admin/pii/free-text/${id}/promote`,
    jsonInit('POST', { note: note ?? null }),
  );
}

export async function rejectFreeText(id: number, note?: string): Promise<FreeTextDecision> {
  return request<FreeTextDecision>(
    `/admin/pii/free-text/${id}/reject`,
    jsonInit('POST', { note: note ?? null }),
  );
}

// Destroy this answer's PII in place (raw payload, read-model, PII copy). §3.8.
export async function scrubFreeText(id: number, reason?: string): Promise<FreeTextScrub> {
  return request<FreeTextScrub>(
    `/admin/pii/free-text/${id}/scrub`,
    jsonInit('POST', { reason: reason ?? null }),
  );
}

// --- Operator accounts (admin) ---------------------------------------------

// The three app-layer roles a user can hold (mirrors service.VALID_ROLES). A user
// can hold any non-empty combination — e.g. researcher + reviewer.
export const ROLES = ['admin', 'researcher', 'reviewer'] as const;
export type Role = (typeof ROLES)[number];

export async function listUsers(): Promise<User[]> {
  return request<User[]>('/admin/users');
}

// Create an operator account with an admin-set initial password (no invite flow).
// Throws ApiError(409) if the email is already registered, 422 on an invalid/empty
// role set.
export async function createUser(email: string, password: string, roles: string[]): Promise<User> {
  return request<User>('/admin/users', jsonInit('POST', { email, password, roles }));
}

// Wholesale-replace a user's roles. Throws ApiError(409) if it would strip the
// admin role from the last enabled admin, 422 on an invalid/empty set, 404 if gone.
export async function setUserRoles(userId: number, roles: string[]): Promise<User> {
  return request<User>(`/admin/users/${userId}/roles`, jsonInit('PUT', { roles }));
}

// Disable a user; their live sessions stop resolving immediately. Throws
// ApiError(409) if it would disable the last enabled admin, 404 if gone.
export async function disableUser(userId: number): Promise<User> {
  return request<User>(`/admin/users/${userId}/disable`, jsonInit('POST'));
}

export async function enableUser(userId: number): Promise<User> {
  return request<User>(`/admin/users/${userId}/enable`, jsonInit('POST'));
}

// Set a new password; revokes the user's existing sessions. 204, no body.
export async function resetUserPassword(userId: number, password: string): Promise<void> {
  await request<void>(`/admin/users/${userId}/reset-password`, jsonInit('POST', { password }));
}

// --- DB-credential registry (admin, read-only) -----------------------------

// A row of the analyst/reviewer credential registry (metadata only — no password
// is ever stored). Provisioning/rotation/revocation is the out-of-band CLI's job;
// the UI only surfaces the audit trail.
export interface DbCredential {
  id: number;
  subject_label: string;
  access: string;
  login_role: string;
  status: string;
  provisioned_by: number | null;
  created_at: string;
  revoked_at: string | null;
  rotated_at: string | null;
}

export async function listDbCredentials(): Promise<DbCredential[]> {
  return request<DbCredential[]>('/admin/db-credentials');
}

// The two data-access tiers a credential can grant (mirrors provisioning.VALID_ACCESS).
export const DB_ACCESS_TIERS = ['analyst', 'reviewer'] as const;
export type DbAccessTier = (typeof DB_ACCESS_TIERS)[number];

// A queued provision/rotate/revoke request. status is 'pending' until the
// privileged worker processes it, then 'done' | 'failed' (error_detail set).
export interface ProvisionRequest {
  id: number;
  action: string;
  access: string | null;
  subject_label: string | null;
  login_role: string | null;
  status: string;
  error_detail: string | null;
  created_at: string;
  processed_at: string | null;
}

// Grant a person DB access at a tier. A brand-new recipient needs initialPassword
// (their first login); the reviewer (PII) tier needs confirmPassword — the admin's
// own password, re-entered as a step-up. Throws ApiError(403) if that's missing or
// wrong, 409 if the subject already holds/awaits a credential for the tier, 422 on
// a bad tier or a new account with no initial password.
export async function grantDbAccess(
  email: string,
  access: DbAccessTier,
  opts: { initialPassword?: string; confirmPassword?: string } = {},
): Promise<ProvisionRequest> {
  return request<ProvisionRequest>(
    '/admin/db-credentials/grant',
    jsonInit('POST', {
      email,
      access,
      initial_password: opts.initialPassword ?? null,
      confirm_password: opts.confirmPassword ?? null,
    }),
  );
}

// Enqueue a revoke of an active credential. Throws ApiError(404) if unknown, 409 if
// it isn't active.
export async function revokeDbCredential(loginRole: string): Promise<ProvisionRequest> {
  return request<ProvisionRequest>(
    `/admin/db-credentials/${encodeURIComponent(loginRole)}/revoke`,
    jsonInit('POST'),
  );
}

// Recent provision/rotate/revoke requests, newest first — the admin's view of the
// async queue (a request stays 'pending' until the worker processes it).
export async function listProvisionRequests(): Promise<ProvisionRequest[]> {
  return request<ProvisionRequest[]>('/admin/db-credentials/requests');
}

// --- Self-service DB credentials (the signed-in recipient) -----------------

// A credential the signed-in user holds, plus whether its one-time password is
// still waiting to be revealed.
export interface MyCredential {
  login_role: string;
  access: string;
  status: string;
  created_at: string;
  has_pending_secret: boolean;
}

// The one-time reveal of a freshly-minted password. Returned once; the stored copy
// is wiped on read, so the client must capture it now.
export interface RevealedSecret {
  login_role: string;
  access: string;
  group_role: string;
  password: string;
  set_role_sql: string;
}

export async function listMyCredentials(): Promise<MyCredential[]> {
  return request<MyCredential[]>('/me/db-credentials');
}

// Reveal a credential's password exactly once. Throws ApiError(410) when there's
// nothing to reveal (already revealed, expired, or not minted yet), 404 if the
// credential isn't the caller's.
export async function revealMyCredential(loginRole: string): Promise<RevealedSecret> {
  return request<RevealedSecret>(
    `/me/db-credentials/${encodeURIComponent(loginRole)}/reveal`,
    jsonInit('POST'),
  );
}

// Regenerate (rotate) the caller's own credential — enqueues a rotate; once the
// worker finishes, a fresh one-time password is waiting to be revealed.
export async function regenerateMyCredential(loginRole: string): Promise<ProvisionRequest> {
  return request<ProvisionRequest>(
    `/me/db-credentials/${encodeURIComponent(loginRole)}/regenerate`,
    jsonInit('POST'),
  );
}

// --- ETL runs (admin) ------------------------------------------------------

// A dbt node that didn't pass, surfaced to explain a failed run.
export interface EtlNodeFailure {
  unique_id: string | null;
  status: string | null;
  message: string | null;
}

// One ops.etl_runs row. status is 'running' | 'success' | 'failed'. Row counts and
// dbt_version/git_sha are null until/unless the run records them; failures is empty
// unless a node errored.
export interface EtlRun {
  run_id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  source_row_counts: Record<string, number | null> | null;
  mart_row_counts: Record<string, number> | null;
  dbt_version: string | null;
  git_sha: string | null;
  // A 'running' run past the stale window — almost certainly orphaned by a restart.
  // Surfaced so the console can offer to clear it rather than wedge the trigger.
  interrupted: boolean;
  failures: EtlNodeFailure[];
}

export async function listEtlRuns(limit = 20): Promise<EtlRun[]> {
  return request<EtlRun[]>(`/admin/etl/runs?limit=${limit}`);
}

// Start a full-refresh ETL run. Resolves with the freshly-created 'running' row, or
// throws ApiError(409) when a run is already in progress (a prior trigger or the
// daily cron).
export async function triggerEtlRun(): Promise<EtlRun> {
  return request<EtlRun>('/admin/etl/runs', jsonInit('POST'));
}

export async function getEtlRun(runId: string): Promise<EtlRun> {
  return request<EtlRun>(`/admin/etl/runs/${runId}`);
}

// Resolve an interrupted (stale 'running') run to 'failed' so it stops wedging the
// trigger. Throws ApiError(409) for a live/finished run, 404 if unknown.
export async function clearEtlRun(runId: string): Promise<EtlRun> {
  return request<EtlRun>(`/admin/etl/runs/${runId}/clear`, jsonInit('POST'));
}
