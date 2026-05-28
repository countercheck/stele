import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { User } from '../api';
import { UsersView } from './UsersView';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listUsers: vi.fn(),
  createUser: vi.fn(),
  setUserRoles: vi.fn(),
  disableUser: vi.fn(),
  enableUser: vi.fn(),
  resetUserPassword: vi.fn(),
}));

vi.mock('../auth/AuthContext', () => ({
  useAuth: vi.fn(),
}));

const { listUsers, createUser, setUserRoles, disableUser, enableUser, resetUserPassword } =
  await import('../api');
const { useAuth } = await import('../auth/AuthContext');
const mockedList = vi.mocked(listUsers);
const mockedCreate = vi.mocked(createUser);
const mockedSetRoles = vi.mocked(setUserRoles);
const mockedDisable = vi.mocked(disableUser);
const mockedEnable = vi.mocked(enableUser);
const mockedReset = vi.mocked(resetUserPassword);
const mockedAuth = vi.mocked(useAuth);

function asRole(role: string): void {
  mockedAuth.mockReturnValue({
    user: { id: 1, email: 'me@example.com', roles: [role], disabled: false, created_at: 't' },
    status: 'ready',
    login: vi.fn(),
    logout: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>);
}

function user(overrides: Partial<User> = {}): User {
  return {
    id: 2,
    email: 'op@example.com',
    roles: ['researcher'],
    disabled: false,
    created_at: '2026-01-02T00:00:00Z',
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
      <UsersView />
    </MemoryRouter>,
  );
}

describe('UsersView', () => {
  it('lists operators with their roles and status', async () => {
    mockedList.mockResolvedValue([user({ roles: ['researcher', 'reviewer'], disabled: true })]);
    renderView();
    expect(await screen.findByText('op@example.com')).toBeInTheDocument();
    // Scope to the table: "researcher"/"reviewer" also label the create-form checkboxes.
    const table = within(screen.getByRole('table'));
    expect(table.getByText('researcher')).toBeInTheDocument();
    expect(table.getByText('reviewer')).toBeInTheDocument();
    expect(table.getByText('disabled')).toBeInTheDocument();
  });

  it('creates an operator with the chosen roles and refreshes', async () => {
    mockedCreate.mockResolvedValue(user());
    renderView();
    await screen.findByText('No operators yet.');

    await userEvent.type(screen.getByLabelText('Email'), 'new@example.com');
    await userEvent.type(screen.getByLabelText('Initial password'), 'sekret123');
    await userEvent.click(screen.getByLabelText('researcher'));
    await userEvent.click(screen.getByRole('button', { name: 'Create operator' }));

    expect(mockedCreate).toHaveBeenCalledWith('new@example.com', 'sekret123', ['researcher']);
    expect(await screen.findByRole('status')).toHaveTextContent('Created new@example.com.');
    expect(mockedList).toHaveBeenCalledTimes(2); // mount + post-create refresh
  });

  it('edits a user’s roles inline', async () => {
    mockedList.mockResolvedValue([user()]);
    mockedSetRoles.mockResolvedValue(user({ roles: ['researcher', 'admin'] }));
    renderView();
    await screen.findByText('op@example.com');

    await userEvent.click(screen.getByRole('button', { name: 'Edit roles' }));
    // The inline editor exposes a checkbox per role for this row; scope to the
    // table so it isn't confused with the create form's identical "admin" box.
    await userEvent.click(within(screen.getByRole('table')).getByLabelText('admin'));
    await userEvent.click(screen.getByRole('button', { name: 'Save roles' }));

    expect(mockedSetRoles).toHaveBeenCalledWith(2, ['researcher', 'admin']);
    expect(await screen.findByRole('status')).toHaveTextContent('Updated roles');
  });

  it('confirms before disabling and surfaces the last-admin 409', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    mockedList.mockResolvedValue([user({ id: 1, email: 'me@example.com', roles: ['admin'] })]);
    mockedDisable.mockRejectedValue(new Error('cannot disable the last enabled admin'));
    renderView();
    await screen.findByText('me@example.com');

    await userEvent.click(screen.getByRole('button', { name: 'Disable' }));

    expect(confirmSpy).toHaveBeenCalledOnce();
    expect(mockedDisable).toHaveBeenCalledWith(1);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'cannot disable the last enabled admin',
    );
    confirmSpy.mockRestore();
  });

  it('does not disable when the confirm is dismissed', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    mockedList.mockResolvedValue([user()]);
    renderView();
    await screen.findByText('op@example.com');

    await userEvent.click(screen.getByRole('button', { name: 'Disable' }));

    expect(mockedDisable).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('enables a disabled user without a confirm', async () => {
    mockedList.mockResolvedValue([user({ disabled: true })]);
    mockedEnable.mockResolvedValue(user({ disabled: false }));
    const confirmSpy = vi.spyOn(window, 'confirm');
    renderView();
    await screen.findByText('op@example.com');

    await userEvent.click(screen.getByRole('button', { name: 'Enable' }));

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(mockedEnable).toHaveBeenCalledWith(2);
    expect(await screen.findByRole('status')).toHaveTextContent('Enabled');
    confirmSpy.mockRestore();
  });

  it('prompts for a new password on reset and skips an empty submit', async () => {
    mockedList.mockResolvedValue([user()]);
    mockedReset.mockResolvedValue(undefined);
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('brand-new-pw');
    renderView();
    await screen.findByText('op@example.com');

    await userEvent.click(screen.getByRole('button', { name: 'Reset password' }));

    expect(mockedReset).toHaveBeenCalledWith(2, 'brand-new-pw');
    expect(await screen.findByRole('status')).toHaveTextContent('Reset password');

    // A cancelled prompt is a no-op.
    promptSpy.mockReturnValue(null);
    await userEvent.click(screen.getByRole('button', { name: 'Reset password' }));
    expect(mockedReset).toHaveBeenCalledTimes(1);
    promptSpy.mockRestore();
  });

  it('refuses non-admin roles', async () => {
    asRole('reviewer');
    renderView();
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Only admins can manage operator accounts',
      ),
    );
    expect(mockedCreate).not.toHaveBeenCalled();
  });
});
