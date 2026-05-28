import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DbCredential } from '../api';
import { DbCredentialsView } from './DbCredentialsView';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listDbCredentials: vi.fn(),
}));

vi.mock('../auth/AuthContext', () => ({
  useAuth: vi.fn(),
}));

const { listDbCredentials } = await import('../api');
const { useAuth } = await import('../auth/AuthContext');
const mockedList = vi.mocked(listDbCredentials);
const mockedAuth = vi.mocked(useAuth);

function asRole(role: string): void {
  mockedAuth.mockReturnValue({
    user: { id: 1, email: 'me@example.com', roles: [role], disabled: false, created_at: 't' },
    status: 'ready',
    login: vi.fn(),
    logout: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>);
}

function grant(overrides: Partial<DbCredential> = {}): DbCredential {
  return {
    id: 1,
    subject_label: 'jane.analyst',
    access: 'analyst',
    login_role: 'stele_analyst_jane',
    status: 'active',
    provisioned_by: 1,
    created_at: '2026-01-02T00:00:00Z',
    revoked_at: null,
    rotated_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  asRole('admin');
  mockedList.mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderView() {
  return render(
    <MemoryRouter>
      <DbCredentialsView />
    </MemoryRouter>,
  );
}

describe('DbCredentialsView', () => {
  it('lists the credential registry', async () => {
    mockedList.mockResolvedValue([
      grant(),
      grant({
        id: 2,
        subject_label: 'sam.reviewer',
        access: 'reviewer',
        login_role: 'stele_reviewer_sam',
        status: 'revoked',
        revoked_at: '2026-03-01T00:00:00Z',
      }),
    ]);
    renderView();
    expect(await screen.findByText('jane.analyst')).toBeInTheDocument();
    expect(screen.getByText('stele_analyst_jane')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('sam.reviewer')).toBeInTheDocument();
    expect(screen.getByText('revoked')).toBeInTheDocument();
  });

  it('shows an empty state when no credentials are provisioned', async () => {
    renderView();
    expect(await screen.findByText('No DB credentials provisioned.')).toBeInTheDocument();
  });

  it('refuses non-admin roles', async () => {
    asRole('reviewer');
    renderView();
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Only admins can view DB credentials'),
    );
  });
});
