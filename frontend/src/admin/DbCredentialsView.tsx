import { useEffect, useState } from 'react';

import { listDbCredentials, type DbCredential } from '../api';
import { useAuth } from '../auth/AuthContext';
import { formatDateTime } from '../format';
import { Alert, Badge, Card, EmptyState, LoadingState, PageHeader } from '../ui';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Read-only view of the analyst/reviewer DB-credential registry (design doc §3.10).
 * Provisioning, rotation, and revocation are an out-of-band CLI procedure
 * (`scripts/provision_db_credential.py`) because `stele_api` has no role-DDL
 * privilege — this view only surfaces who holds which data-access credential and
 * its lifecycle. No password is ever shown: the registry doesn't store one.
 */
export function DbCredentialsView() {
  const { user } = useAuth();
  const [grants, setGrants] = useState<DbCredential[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listDbCredentials()
      .then((rows) => {
        if (active) setGrants(rows);
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      });
    return () => {
      active = false;
    };
  }, []);

  if (user && !user.roles.includes('admin')) {
    return <Alert tone="error">Only admins can view DB credentials.</Alert>;
  }

  return (
    <section>
      <PageHeader
        title="DB credentials"
        subtitle="Analyst and reviewer data-access grants. Provisioning is an out-of-band CLI procedure; this view is read-only."
      />

      {error ? <Alert tone="error">Error: {error}</Alert> : null}

      {grants === null ? (
        error ? null : (
          <LoadingState />
        )
      ) : grants.length === 0 ? (
        <EmptyState>No DB credentials provisioned.</EmptyState>
      ) : (
        <Card>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-faint">
                <th className="px-5 py-2 font-medium">Subject</th>
                <th className="px-5 py-2 font-medium">Access</th>
                <th className="px-5 py-2 font-medium">Login role</th>
                <th className="px-5 py-2 font-medium">Status</th>
                <th className="px-5 py-2 font-medium">Created</th>
                <th className="px-5 py-2 font-medium">Revoked</th>
              </tr>
            </thead>
            <tbody>
              {grants.map((g) => (
                <tr key={g.id} className="border-t border-border">
                  <td className="px-5 py-2 text-ink">{g.subject_label}</td>
                  <td className="px-5 py-2 text-muted">{g.access}</td>
                  <td className="px-5 py-2 font-mono text-xs text-muted">{g.login_role}</td>
                  <td className="px-5 py-2">
                    <Badge tone={g.status === 'active' ? 'success' : 'neutral'}>{g.status}</Badge>
                  </td>
                  <td className="px-5 py-2 text-muted">{formatDateTime(g.created_at)}</td>
                  <td className="px-5 py-2 text-muted">
                    {g.revoked_at ? formatDateTime(g.revoked_at) : '—'}
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
