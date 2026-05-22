import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchSurvey, submitResponse } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fetchSurvey', () => {
  it('requests the version endpoint and returns the detail', async () => {
    const detail = {
      survey_id: 's',
      version: 1,
      status: 'published',
      definition_hash: 'h',
      definition_json: { pages: [] },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(detail),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchSurvey('s', 1);

    expect(fetchMock).toHaveBeenCalledWith('/surveys/s/versions/1');
    expect(result).toEqual(detail);
  });

  it('throws on a non-ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 404 } as unknown as Response),
    );
    await expect(fetchSurvey('s', 1)).rejects.toThrow('404');
  });
});

describe('submitResponse', () => {
  it('posts the JSON body to the responses endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ raw_response_id: 1, respondent_id: 'r', submitted_at: 't' }),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    const body = { definition_hash: 'h', payload: { q1: 'a' }, shown_questions: ['q1'] };
    const result = await submitResponse('s', 1, body);

    expect(fetchMock).toHaveBeenCalledWith(
      '/surveys/s/versions/1/responses',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(body) }),
    );
    expect(result.raw_response_id).toBe(1);
  });
});
