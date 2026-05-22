import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SurveyRunner } from './SurveyRunner';

const DEFINITION = {
  pages: [
    {
      name: 'p1',
      elements: [{ type: 'radiogroup', name: 'q1', title: 'Pick one', choices: ['a', 'b'] }],
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockApi(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ raw_response_id: 1, respondent_id: 'r', submitted_at: 't' }),
      } as unknown as Response);
    }
    return Promise.resolve({
      ok: true,
      json: () =>
        Promise.resolve({
          survey_id: 's',
          version: 1,
          status: 'published',
          definition_hash: 'h',
          definition_json: DEFINITION,
        }),
    } as unknown as Response);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('SurveyRunner', () => {
  it('renders a fetched survey and submits the shown-set + payload on completion', async () => {
    const fetchMock = mockApi();
    render(<SurveyRunner surveyId="s" version={1} />);

    expect(await screen.findByText('Pick one')).toBeInTheDocument();

    await userEvent.click(await screen.findByText('a'));
    await userEvent.click(screen.getByText('Complete'));

    await waitFor(() => {
      expect(screen.getByText(/Thank you/)).toBeInTheDocument();
    });

    const postCall = fetchMock.mock.calls.find(
      (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
    );
    expect(postCall).toBeDefined();
    const sent = JSON.parse((postCall?.[1] as RequestInit).body as string) as {
      definition_hash: string;
      payload: Record<string, unknown>;
      shown_questions: string[];
    };
    expect(sent.definition_hash).toBe('h');
    expect(sent.payload).toEqual({ q1: 'a' });
    expect(sent.shown_questions).toContain('q1');
  });

  it('shows an error when the survey fails to load', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500 } as unknown as Response),
    );
    render(<SurveyRunner surveyId="s" version={1} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/Error/);
  });
});
