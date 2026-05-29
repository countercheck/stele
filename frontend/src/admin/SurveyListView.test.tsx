import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SurveySummary } from '../api';
import { SurveyListView } from './SurveyListView';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listSurveys: vi.fn(),
  createSurvey: vi.fn(),
  setSurveyShortCode: vi.fn(),
  clearSurveyShortCode: vi.fn(),
  downloadSurveyExport: vi.fn(),
}));

const {
  listSurveys,
  createSurvey,
  setSurveyShortCode,
  clearSurveyShortCode,
  downloadSurveyExport,
} = await import('../api');
const mockedList = vi.mocked(listSurveys);
const mockedCreate = vi.mocked(createSurvey);
const mockedSetCode = vi.mocked(setSurveyShortCode);
const mockedClearCode = vi.mocked(clearSurveyShortCode);
const mockedExport = vi.mocked(downloadSurveyExport);

// A SurveySummary row with sensible defaults; override per test.
function row(overrides: Partial<SurveySummary> = {}): SurveySummary {
  return {
    survey_id: 'aaa',
    version: 1,
    status: 'published',
    definition_hash: 'h',
    published_at: '2026-01-01T00:00:00Z',
    created_at: 't1',
    response_count: 0,
    short_code: null,
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

function renderList() {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <Routes>
        <Route path="/admin" element={<SurveyListView />} />
        <Route
          path="/admin/surveys/:surveyId/versions/:version"
          element={<div>Editor opened</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('SurveyListView', () => {
  it('lists every survey/version with status badges and response counts', async () => {
    mockedList.mockResolvedValue([
      row({
        version: 2,
        status: 'draft',
        definition_hash: null,
        published_at: null,
        created_at: 't2',
      }),
      row({ version: 1, status: 'published', response_count: 5 }),
    ]);
    renderList();

    expect(await screen.findByText('draft')).toBeInTheDocument();
    expect(screen.getByText('published')).toBeInTheDocument();
    // Both versions of the one survey are grouped, each with an Open link.
    expect(screen.getAllByRole('link', { name: /Open aaa/ })).toHaveLength(2);
    // The version summary line reports the survey's total live responses.
    expect(screen.getByText(/5 responses/)).toBeInTheDocument();
  });

  it('shows an empty state when there are no surveys', async () => {
    mockedList.mockResolvedValue([]);
    renderList();
    expect(await screen.findByText('No surveys yet.')).toBeInTheDocument();
  });

  it('shows the error alone (not a perpetual spinner) when the load fails', async () => {
    mockedList.mockRejectedValue(new Error('boom'));
    renderList();
    expect(await screen.findByRole('alert')).toHaveTextContent('boom');
    // Regression: the error and loading states are mutually exclusive.
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('creates a draft and navigates to its editor', async () => {
    mockedList.mockResolvedValue([]);
    mockedCreate.mockResolvedValue({
      survey_id: 'new-id',
      version: 1,
      status: 'draft',
      definition_hash: null,
      published_at: null,
      created_at: 't',
    });
    renderList();
    await screen.findByText('No surveys yet.');

    await userEvent.click(screen.getByRole('button', { name: 'New survey' }));

    expect(mockedCreate).toHaveBeenCalledOnce();
    expect(await screen.findByText('Editor opened')).toBeInTheDocument();
  });
});

describe('SurveyListView short codes', () => {
  const writeText = vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined);

  beforeEach(() => {
    writeText.mockClear();
    Object.assign(navigator, { clipboard: { writeText } });
  });

  it('copies the short-code link when a code is set', async () => {
    mockedList.mockResolvedValue([row({ short_code: 'climate-2026' })]);
    renderList();

    const copy = await screen.findByRole('button', { name: 'Copy link' });
    // The link points at the /s/<code> path on the current origin.
    expect(copy).toHaveAttribute('title', `${window.location.origin}/s/climate-2026`);
    await userEvent.click(copy);
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/s/climate-2026`);
    expect(await screen.findByRole('button', { name: 'Copied!' })).toBeInTheDocument();
  });

  it('falls back to the survey/version link when no code is set', async () => {
    mockedList.mockResolvedValue([row({ survey_id: 'sid', version: 3, short_code: null })]);
    renderList();

    const copy = await screen.findByRole('button', { name: 'Copy link' });
    await userEvent.click(copy);
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/?survey=sid&version=3`);
  });

  it('disables copy when nothing is published', async () => {
    mockedList.mockResolvedValue([
      row({ status: 'draft', definition_hash: null, published_at: null }),
    ]);
    renderList();

    const copy = await screen.findByRole('button', { name: 'Copy link' });
    expect(copy).toBeDisabled();
  });

  it('sets a short code and shows it without a reload', async () => {
    mockedList.mockResolvedValue([row({ short_code: null })]);
    mockedSetCode.mockResolvedValue({ survey_id: 'aaa', short_code: 'my-code' });
    renderList();

    await userEvent.click(await screen.findByRole('button', { name: 'Add short code' }));
    await userEvent.type(screen.getByLabelText('Short code'), 'my-code');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(mockedSetCode).toHaveBeenCalledWith('aaa', 'my-code');
    // The saved code is shown and the copy link now targets it.
    await screen.findByText('my-code');
    expect(screen.getByRole('button', { name: 'Copy link' })).toHaveAttribute(
      'title',
      `${window.location.origin}/s/my-code`,
    );
  });

  it('surfaces a taken-code error and keeps the input open', async () => {
    mockedList.mockResolvedValue([row({ short_code: null })]);
    mockedSetCode.mockRejectedValue(new Error('that short code is already in use'));
    renderList();

    await userEvent.click(await screen.findByRole('button', { name: 'Add short code' }));
    await userEvent.type(screen.getByLabelText('Short code'), 'taken');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('already in use');
    // Still editing, so the operator can correct it.
    expect(screen.getByLabelText('Short code')).toBeInTheDocument();
  });

  it('removes a short code', async () => {
    mockedList.mockResolvedValue([row({ short_code: 'to-remove' })]);
    mockedClearCode.mockResolvedValue(undefined);
    renderList();

    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }));

    expect(mockedClearCode).toHaveBeenCalledWith('aaa');
    await waitFor(() => expect(screen.getByText('none')).toBeInTheDocument());
  });
});

describe('SurveyListView export', () => {
  it('downloads the faithful CSV when Export CSV is clicked', async () => {
    mockedList.mockResolvedValue([row({ survey_id: 'sid' })]);
    mockedExport.mockResolvedValue(undefined);
    renderList();

    await userEvent.click(await screen.findByRole('button', { name: 'Export CSV' }));

    expect(mockedExport).toHaveBeenCalledWith('sid', { excelSafe: false });
  });

  it('requests the excel-safe variant from the Excel-safe button', async () => {
    mockedList.mockResolvedValue([row({ survey_id: 'sid' })]);
    mockedExport.mockResolvedValue(undefined);
    renderList();

    await userEvent.click(await screen.findByRole('button', { name: 'Excel-safe CSV' }));

    expect(mockedExport).toHaveBeenCalledWith('sid', { excelSafe: true });
  });

  it('surfaces an error when the export fails', async () => {
    mockedList.mockResolvedValue([row()]);
    mockedExport.mockRejectedValue(new Error('export failed (500)'));
    renderList();

    await userEvent.click(await screen.findByRole('button', { name: 'Export CSV' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('export failed (500)');
  });
});
