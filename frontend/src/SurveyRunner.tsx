import { useEffect, useMemo, useState } from 'react';
import { Model } from 'survey-core';
import { Survey } from 'survey-react-ui';

import { fetchSurvey, submitResponse, type SurveyDetail } from './api';

interface SurveyRunnerProps {
  surveyId: string;
  version: number;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function SurveyRunner({ surveyId, version }: SurveyRunnerProps) {
  const [detail, setDetail] = useState<SurveyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    let active = true;
    fetchSurvey(surveyId, version)
      .then((loaded) => {
        if (active) setDetail(loaded);
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      });
    return () => {
      active = false;
    };
  }, [surveyId, version]);

  const model = useMemo(() => (detail ? new Model(detail.definition_json) : null), [detail]);

  useEffect(() => {
    if (!model || !detail) return;
    const handleComplete = (sender: Model): void => {
      // Capture the shown-set from the engine at submit time (invariant 3).
      const shownQuestions = sender
        .getAllQuestions()
        .filter((question) => question.isVisible)
        .map((question) => question.name);
      void submitResponse(surveyId, version, {
        definition_hash: detail.definition_hash ?? '',
        payload: sender.data as Record<string, unknown>,
        shown_questions: shownQuestions,
      })
        .then(() => setSubmitted(true))
        .catch((err: unknown) => setError(errorMessage(err)));
    };
    model.onComplete.add(handleComplete);
    return () => {
      model.onComplete.remove(handleComplete);
    };
  }, [model, detail, surveyId, version]);

  if (error) return <div role="alert">Error: {error}</div>;
  if (submitted) return <div role="status">Thank you — your response was recorded.</div>;
  if (!model) return <div role="status">Loading…</div>;
  return <Survey model={model} />;
}
