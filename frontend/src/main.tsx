import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { SurveyRunner } from './SurveyRunner';

const params = new URLSearchParams(window.location.search);
const surveyId = params.get('survey') ?? '';
const version = Number(params.get('version') ?? '1');

const rootElement = document.getElementById('app');
if (rootElement) {
  createRoot(rootElement).render(
    <StrictMode>
      {surveyId ? (
        <SurveyRunner surveyId={surveyId} version={version} />
      ) : (
        <p>
          Provide ?survey=&lt;id&gt;&amp;version=&lt;n&gt; in the URL to load a published survey.
        </p>
      )}
    </StrictMode>,
  );
}
