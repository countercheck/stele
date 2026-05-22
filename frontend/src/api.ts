// Relative URLs; the Vite dev server proxies /surveys to the API (vite.config.ts).
const API_BASE = '';

export interface SurveyDetail {
  survey_id: string;
  version: number;
  status: string;
  definition_hash: string | null;
  definition_json: Record<string, unknown>;
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

export async function fetchSurvey(surveyId: string, version: number): Promise<SurveyDetail> {
  const res = await fetch(`${API_BASE}/surveys/${surveyId}/versions/${version}`);
  if (!res.ok) {
    throw new Error(`Failed to load survey (${res.status})`);
  }
  return (await res.json()) as SurveyDetail;
}

export async function submitResponse(
  surveyId: string,
  version: number,
  body: SubmitBody,
): Promise<SubmitResult> {
  const res = await fetch(`${API_BASE}/surveys/${surveyId}/versions/${version}/responses`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`Failed to submit response (${res.status})`);
  }
  return (await res.json()) as SubmitResult;
}
