import { useEffect, useState } from 'react';

import {
  createUser,
  disableUser,
  enableUser,
  listUsers,
  resetUserPassword,
  ROLES,
  setUserRoles,
  type User,
} from '../api';
import { useAuth } from '../auth/AuthContext';
import { formatDateTime } from '../format';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  EmptyState,
  Field,
  LoadingState,
  PageHeader,
} from '../ui';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** A set of role checkboxes over the three app-layer roles. Reused by the create
 * form and the inline per-row role editor. */
function RoleCheckboxes({
  selected,
  onToggle,
  idPrefix,
}: {
  selected: ReadonlySet<string>;
  onToggle: (role: string) => void;
  idPrefix: string;
}) {
  return (
    <div className="flex flex-wrap gap-3">
      {ROLES.map((role) => {
        const id = `${idPrefix}-${role}`;
        return (
          <label key={role} htmlFor={id} className="flex items-center gap-1.5 text-sm text-ink">
            <input
              id={id}
              type="checkbox"
              checked={selected.has(role)}
              onChange={() => onToggle(role)}
            />
            {role}
          </label>
        );
      })}
    </div>
  );
}

/**
 * Admin operator-account console: create users, edit their roles, disable/enable,
 * and reset passwords. Every mutation is admin-only server-side; this view also
 * hides itself from non-admins. The destructive safety rules (last-admin
 * protection, session revocation on reset) live in the API — the UI surfaces the
 * 409 reasons inline and confirms before the irreversible-feeling actions.
 */
export function UsersView() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Create form.
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [newRoles, setNewRoles] = useState<Set<string>>(new Set());

  // Which user's roles are being edited inline, and the working selection.
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editRoles, setEditRoles] = useState<Set<string>>(new Set());

  const refresh = (): void => {
    listUsers()
      .then((rows) => {
        setUsers(rows);
        setError(null);
      })
      .catch((err: unknown) => setError(errorMessage(err)));
  };

  useEffect(() => {
    let active = true;
    listUsers()
      .then((rows) => {
        if (active) setUsers(rows);
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      });
    return () => {
      active = false;
    };
  }, []);

  if (currentUser && !currentUser.roles.includes('admin')) {
    return <Alert tone="error">Only admins can manage operator accounts.</Alert>;
  }

  // Run a mutation, clearing prior banners and refreshing the table on success.
  // The error path keeps the 409/422 reason visible (e.g. last-admin protection).
  const run = (action: () => Promise<unknown>, onSuccess: () => void): void => {
    setBusy(true);
    setError(null);
    setNotice(null);
    action()
      .then(() => {
        onSuccess();
        refresh();
      })
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, role: string): void => {
    const next = new Set(set);
    if (next.has(role)) next.delete(role);
    else next.add(role);
    setter(next);
  };

  const handleCreate = (): void => {
    const addr = email.trim();
    if (!addr || !password || newRoles.size === 0) return;
    run(
      () => createUser(addr, password, [...newRoles]),
      () => {
        setNotice(`Created ${addr}.`);
        setEmail('');
        setPassword('');
        setNewRoles(new Set());
      },
    );
  };

  const startEdit = (u: User): void => {
    setEditingId(u.id);
    setEditRoles(new Set(u.roles));
  };

  const handleSaveRoles = (u: User): void => {
    if (editRoles.size === 0) return;
    run(
      () => setUserRoles(u.id, [...editRoles]),
      () => {
        setNotice(`Updated roles for ${u.email}.`);
        setEditingId(null);
      },
    );
  };

  const handleToggleDisabled = (u: User): void => {
    if (u.disabled) {
      run(
        () => enableUser(u.id),
        () => setNotice(`Enabled ${u.email}.`),
      );
      return;
    }
    if (!window.confirm(`Disable ${u.email}? Their active sessions end immediately.`)) return;
    run(
      () => disableUser(u.id),
      () => setNotice(`Disabled ${u.email}.`),
    );
  };

  const handleReset = (u: User): void => {
    const next = window.prompt(`New password for ${u.email} (this ends their active sessions):`);
    if (next === null) return; // cancelled
    if (!next) {
      setError('Password cannot be empty.');
      return;
    }
    run(
      () => resetUserPassword(u.id, next),
      () => setNotice(`Reset password for ${u.email}.`),
    );
  };

  return (
    <section>
      <PageHeader
        title="Operator accounts"
        subtitle="Create and manage the people who can sign in to Stele."
      />

      <Card className="mb-6">
        <CardBody className="flex flex-col gap-4">
          <h2 className="text-sm font-semibold text-ink">Create an operator</h2>
          <div className="flex flex-wrap items-end gap-3">
            <Field
              label="Email"
              type="email"
              className="min-w-56 flex-1"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="operator@example.com"
            />
            <Field
              label="Initial password"
              type="password"
              className="min-w-56 flex-1"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="set a password"
            />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium text-ink">Roles</span>
            <RoleCheckboxes
              selected={newRoles}
              onToggle={(r) => toggle(newRoles, setNewRoles, r)}
              idPrefix="create-role"
            />
          </div>
          <div>
            <Button
              type="button"
              onClick={handleCreate}
              disabled={busy || !email.trim() || !password || newRoles.size === 0}
            >
              {busy ? 'Working…' : 'Create operator'}
            </Button>
          </div>
        </CardBody>
      </Card>

      {error ? <Alert tone="error">Error: {error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}

      <h2 className="mb-2 mt-6 text-sm font-semibold text-ink">Operators</h2>
      {users === null ? (
        error ? null : (
          <LoadingState />
        )
      ) : users.length === 0 ? (
        <EmptyState>No operators yet.</EmptyState>
      ) : (
        <Card>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-faint">
                <th className="px-5 py-2 font-medium">Email</th>
                <th className="px-5 py-2 font-medium">Roles</th>
                <th className="px-5 py-2 font-medium">Status</th>
                <th className="px-5 py-2 font-medium">Created</th>
                <th className="px-5 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-border align-top">
                  <td className="px-5 py-3 text-ink">{u.email}</td>
                  <td className="px-5 py-3">
                    {editingId === u.id ? (
                      <RoleCheckboxes
                        selected={editRoles}
                        onToggle={(r) => toggle(editRoles, setEditRoles, r)}
                        idPrefix={`edit-role-${u.id}`}
                      />
                    ) : (
                      <span className="flex flex-wrap gap-1">
                        {u.roles.map((r) => (
                          <Badge key={r} tone="brand">
                            {r}
                          </Badge>
                        ))}
                      </span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    <Badge tone={u.disabled ? 'neutral' : 'success'}>
                      {u.disabled ? 'disabled' : 'active'}
                    </Badge>
                  </td>
                  <td className="px-5 py-3 text-muted">{formatDateTime(u.created_at)}</td>
                  <td className="px-5 py-3">
                    <div className="flex flex-wrap gap-2">
                      {editingId === u.id ? (
                        <>
                          <Button
                            type="button"
                            size="sm"
                            onClick={() => handleSaveRoles(u)}
                            disabled={busy || editRoles.size === 0}
                          >
                            Save roles
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            onClick={() => setEditingId(null)}
                            disabled={busy}
                          >
                            Cancel
                          </Button>
                        </>
                      ) : (
                        <>
                          <Button
                            type="button"
                            size="sm"
                            variant="secondary"
                            onClick={() => startEdit(u)}
                            disabled={busy}
                          >
                            Edit roles
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="secondary"
                            onClick={() => handleReset(u)}
                            disabled={busy}
                          >
                            Reset password
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant={u.disabled ? 'secondary' : 'danger'}
                            onClick={() => handleToggleDisabled(u)}
                            disabled={busy}
                          >
                            {u.disabled ? 'Enable' : 'Disable'}
                          </Button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </section>
  );
}
