import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError,
  clearEtlRun,
  downloadSurveyExport,
  fetchSurvey,
  getEtlRun,
  listEtlRuns,
  listSurveys,
  login,
  publishSurvey,
  setUnauthorizedHandler,
  submitResponse,
  triggerEtlRun,
} from './api';

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
});

function okJson(value: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(value) } as unknown as Response;
}

describe('fetchSurvey', () => {
  it('requests the version endpoint and returns the detail', async () => {
    const detail = {
      survey_id: 's',
      version: 1,
      status: 'published',
      definition_hash: 'h',
      definition_json: { pages: [] },
    };
    const fetchMock = vi.fn().mockResolvedValue(okJson(detail));
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchSurvey('s', 1);

    expect(fetchMock).toHaveBeenCalledWith('/api/surveys/s/versions/1', undefined);
    expect(result).toEqual(detail);
  });

  it('throws an ApiError carrying the status on a non-ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 404 } as unknown as Response),
    );
    await expect(fetchSurvey('s', 1)).rejects.toMatchObject({ status: 404 });
    await expect(fetchSurvey('s', 1)).rejects.toBeInstanceOf(ApiError);
  });
});

describe('submitResponse', () => {
  it('posts the JSON body to the responses endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(okJson({ raw_response_id: 1, respondent_id: 'r', submitted_at: 't' }));
    vi.stubGlobal('fetch', fetchMock);

    const body = { definition_hash: 'h', payload: { q1: 'a' }, shown_questions: ['q1'] };
    const result = await submitResponse('s', 1, body);

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/surveys/s/versions/1/responses',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(body) }),
    );
    expect(result.raw_response_id).toBe(1);
  });
});

describe('auth + authoring calls', () => {
  it('login posts credentials and returns the user', async () => {
    const user = { id: 1, email: 'a@b.c', roles: ['admin'], disabled: false, created_at: 't' };
    const fetchMock = vi.fn().mockResolvedValue(okJson(user));
    vi.stubGlobal('fetch', fetchMock);

    const result = await login('a@b.c', 'pw');

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/login',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ email: 'a@b.c', password: 'pw' }),
      }),
    );
    expect(result).toEqual(user);
  });

  it('listSurveys requests the collection endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson([]));
    vi.stubGlobal('fetch', fetchMock);

    await listSurveys();

    expect(fetchMock).toHaveBeenCalledWith('/api/surveys', undefined);
  });

  it('publishSurvey posts to the publish endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(okJson({ survey_id: 's', version: 1, status: 'published' }));
    vi.stubGlobal('fetch', fetchMock);

    await publishSurvey('s', 1);

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/surveys/s/versions/1/publish',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});

describe('downloadSurveyExport', () => {
  it('fetches the export and triggers a browser download', async () => {
    vi.useFakeTimers();
    const blob = new Blob(['csv'], { type: 'text/csv' });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: () => Promise.resolve(blob),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);
    const createObjectURL = vi.fn().mockReturnValue('blob:mock');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    try {
      await downloadSurveyExport('abc');

      expect(fetchMock).toHaveBeenCalledWith('/api/surveys/abc/export');
      expect(createObjectURL).toHaveBeenCalledWith(blob);
      expect(clickSpy).toHaveBeenCalledOnce();
      // Revocation is deferred to the next tick so the download can start first.
      expect(revokeObjectURL).not.toHaveBeenCalled();
      vi.runAllTimers();
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock');
    } finally {
      clickSpy.mockRestore();
      vi.useRealTimers();
    }
  });

  it('requests the excel-safe variant when asked', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: () => Promise.resolve(new Blob(['csv'])),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('URL', { createObjectURL: vi.fn(), revokeObjectURL: vi.fn() });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    try {
      await downloadSurveyExport('abc', { excelSafe: true });
      expect(fetchMock).toHaveBeenCalledWith('/api/surveys/abc/export?excel_safe=true');
      // Flush the deferred revoke while URL is still stubbed (else it fires post-teardown).
      vi.runAllTimers();
    } finally {
      clickSpy.mockRestore();
      vi.useRealTimers();
    }
  });

  it('fires the unauthorized handler and throws on a 401', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: () => Promise.resolve({}),
      } as unknown as Response),
    );

    await expect(downloadSurveyExport('abc')).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });
});

describe('unauthorized handler', () => {
  it('fires on a 401 so the session-expiry path can clear the user', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 401 } as unknown as Response),
    );

    await expect(listSurveys()).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it('does not fire on a non-401 error', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500 } as unknown as Response),
    );

    await expect(listSurveys()).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});

describe('ETL runs', () => {
  it('lists runs with the limit query param', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson([]));
    vi.stubGlobal('fetch', fetchMock);

    await listEtlRuns(20);

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/etl/runs?limit=20', undefined);
  });

  it('triggers a run with a POST and returns the running row', async () => {
    const row = { run_id: 'r1', status: 'running' };
    const fetchMock = vi.fn().mockResolvedValue(okJson(row));
    vi.stubGlobal('fetch', fetchMock);

    const result = await triggerEtlRun();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/etl/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    expect(result).toEqual(row);
  });

  it('surfaces a 409 (run already in progress) as an ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 409 } as unknown as Response),
    );
    await expect(triggerEtlRun()).rejects.toMatchObject({ status: 409 });
  });

  it('fetches a single run by id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson({ run_id: 'r1', status: 'success' }));
    vi.stubGlobal('fetch', fetchMock);

    await getEtlRun('r1');

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/etl/runs/r1', undefined);
  });

  it('clears an interrupted run with a POST', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson({ run_id: 'r1', status: 'failed' }));
    vi.stubGlobal('fetch', fetchMock);

    await clearEtlRun('r1');

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/etl/runs/r1/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
  });
});
