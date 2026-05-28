import { Navigate, Route, Routes } from 'react-router-dom';

import { AuthProvider, useAuth } from '../auth/AuthContext';
import { RequireAuth } from '../auth/RequireAuth';
import { AdminLayout } from './AdminLayout';
import { DbCredentialsView } from './DbCredentialsView';
import { EtlView } from './EtlView';
import { GdprView } from './GdprView';
import { LoginView } from './LoginView';
import { PiiReviewView } from './PiiReviewView';
import { SurveyEditorView } from './SurveyEditorView';
import { SurveyListView } from './SurveyListView';
import { UsersView } from './UsersView';

/**
 * Role-aware landing for "/admin". A reviewer who can't also author surveys (the
 * list endpoint is author-gated → 403) is sent to their PII queue; anyone who
 * holds researcher/admin (including alongside reviewer) lands on the survey list.
 */
function AdminIndex() {
  const { user } = useAuth();
  const canAuthor = user?.roles.includes('researcher') || user?.roles.includes('admin');
  if (user?.roles.includes('reviewer') && !canAuthor) {
    return <Navigate to="/admin/pii-review" replace />;
  }
  return <SurveyListView />;
}

/**
 * The operator area, mounted under "/admin/*". AuthProvider lives here — not at
 * the app root — so the public respondent path never triggers a session probe.
 * The login screen is open; everything else sits behind RequireAuth.
 *
 * This whole subtree (and its heavy deps — CodeMirror, the admin views) is a
 * lazy-loaded chunk (see App.tsx), so the public respondent path never downloads
 * it. Default export so React.lazy can import it.
 */
export default function AdminApp() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="login" element={<LoginView />} />
        <Route element={<RequireAuth />}>
          <Route element={<AdminLayout />}>
            <Route index element={<AdminIndex />} />
            <Route path="surveys/:surveyId/versions/:version" element={<SurveyEditorView />} />
            <Route path="gdpr" element={<GdprView />} />
            <Route path="etl" element={<EtlView />} />
            <Route path="users" element={<UsersView />} />
            <Route path="db-credentials" element={<DbCredentialsView />} />
            <Route path="pii-review" element={<PiiReviewView />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
