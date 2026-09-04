import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AppLayout } from './components/AppLayout';
import { RequireAuth, RequirePermission } from './auth/AuthProvider';
import { LoginPage } from './pages/login/LoginPage';
import { DashboardPage } from './pages/dashboard/DashboardPage';
import { MapPage } from './pages/map/MapPage';
import { WallPage } from './pages/wall/WallPage';
import { CamerasPage } from './pages/cameras/CamerasPage';
import { CameraDetailPage } from './pages/cameras/CameraDetailPage';
import { ImportPage } from './pages/import/ImportPage';
import { HealthPage } from './pages/health/HealthPage';
import { GapAnalysisPage } from './pages/gap/GapAnalysisPage';
import { DetectionsPage } from './pages/detections/DetectionsPage';
import { VehicleSearchPage } from './pages/vehicles/VehicleSearchPage';
import { RoutePage } from './pages/vehicles/RoutePage';
import { WatchlistPage } from './pages/watchlist/WatchlistPage';
import { AlertsPage } from './pages/alerts/AlertsPage';
import { EventsPage } from './pages/events/EventsPage';
import { ReportsPage } from './pages/reports/ReportsPage';
import { AuditPage } from './pages/audit/AuditPage';
import { SettingsPage } from './pages/settings/SettingsPage';
import { UsersPage } from './pages/users/UsersPage';
import { AboutPage } from './pages/misc/AboutPage';
import { NotFoundPage } from './pages/misc/NotFoundPage';

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { path: '/', element: <Navigate to="/dashboard" replace /> },
          { path: '/dashboard', element: <DashboardPage /> },
          { path: '/map', element: <MapPage /> },
          { path: '/wall', element: <WallPage /> },
          { path: '/cameras', element: <CamerasPage /> },
          {
            path: '/cameras/import',
            element: (
              <RequirePermission permission="cameras.write">
                <ImportPage />
              </RequirePermission>
            ),
          },
          { path: '/cameras/:id', element: <CameraDetailPage /> },
          { path: '/health', element: <HealthPage /> },
          { path: '/gap-analysis', element: <GapAnalysisPage /> },
          { path: '/detections', element: <DetectionsPage /> },
          { path: '/vehicles', element: <VehicleSearchPage /> },
          { path: '/vehicles/:plate/route', element: <RoutePage /> },
          { path: '/watchlist', element: <WatchlistPage /> },
          { path: '/alerts', element: <AlertsPage /> },
          { path: '/events', element: <EventsPage /> },
          { path: '/reports', element: <ReportsPage /> },
          {
            path: '/audit',
            element: (
              <RequirePermission permission="admin.audit">
                <AuditPage />
              </RequirePermission>
            ),
          },
          {
            path: '/settings',
            element: (
              <RequirePermission permission="admin.settings">
                <SettingsPage />
              </RequirePermission>
            ),
          },
          { path: '/settings/users', element: <Navigate to="/users" replace /> },
          {
            path: '/settings/:tab',
            element: (
              <RequirePermission permission="admin.settings">
                <SettingsPage />
              </RequirePermission>
            ),
          },
          {
            path: '/users',
            element: (
              <RequirePermission permission="admin.users">
                <UsersPage />
              </RequirePermission>
            ),
          },
          { path: '/about', element: <AboutPage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
