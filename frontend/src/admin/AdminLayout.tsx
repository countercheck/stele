import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';
import { Button } from '../ui';

/** Shell for the authenticated admin area: a branded header with role-aware
 * navigation, the current user, and a logout control, plus the routed view. */
export function AdminLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = (): void => {
    void logout().then(() => navigate('/admin/login', { replace: true }));
  };

  const navLinkClass = ({ isActive }: { isActive: boolean }): string =>
    [
      'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
      isActive ? 'bg-brand-light text-brand-dark' : 'text-muted hover:bg-canvas hover:text-ink',
    ].join(' ');

  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between border-b border-border bg-surface px-6 py-3">
        <div className="flex items-center gap-6">
          <Link to="/admin" className="text-base font-semibold text-brand-dark">
            Stele
          </Link>
          <nav className="flex items-center gap-1">
            {/* Authors see the survey workspace; admins the GDPR console; reviewers
                the PII screening queue. Role drives which links appear (design §3.10). */}
            {user && (user.roles.includes('researcher') || user.roles.includes('admin')) ? (
              <NavLink to="/admin" end className={navLinkClass}>
                Surveys
              </NavLink>
            ) : null}
            {user?.roles.includes('admin') ? (
              <NavLink to="/admin/etl" className={navLinkClass}>
                ETL
              </NavLink>
            ) : null}
            {user?.roles.includes('admin') ? (
              <NavLink to="/admin/gdpr" className={navLinkClass}>
                GDPR
              </NavLink>
            ) : null}
            {user?.roles.includes('admin') ? (
              <NavLink to="/admin/users" className={navLinkClass}>
                Users
              </NavLink>
            ) : null}
            {user?.roles.includes('admin') ? (
              <NavLink to="/admin/db-credentials" className={navLinkClass}>
                DB Credentials
              </NavLink>
            ) : null}
            {user?.roles.includes('reviewer') ? (
              <NavLink to="/admin/pii-review" className={navLinkClass}>
                PII Review
              </NavLink>
            ) : null}
          </nav>
        </div>
        {user ? (
          <div className="flex items-center gap-3">
            <span data-testid="current-user" className="text-sm text-muted">
              {user.email} ({user.roles.join(', ')})
            </span>
            <Button type="button" variant="secondary" size="sm" onClick={handleLogout}>
              Log out
            </Button>
          </div>
        ) : null}
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}
