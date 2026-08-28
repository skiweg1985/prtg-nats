import { Navigate, Route, Routes } from 'react-router-dom'

import { AuthGate } from '@/features/auth/AuthGate'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { JobDetailPage, JobListPage } from '@/features/jobs/JobPages'
import { CredentialsPage } from '@/features/infrastructure/CredentialsPage'
import { IperfEndpointPage } from '@/features/infrastructure/IperfEndpointPage'
import { IperfPage } from '@/features/infrastructure/IperfPage'
import {
  AuditPage,
  CertificatesPage,
  DeploymentListPage,
  NatsPage,
  NotFoundPage,
  SettingsPage,
} from '@/features/misc/Pages'
import { EnrollWizard } from '@/features/probes/EnrollWizard'
import { ProbeDetailPage } from '@/features/probes/ProbeDetailPage'
import { ProbeListPage } from '@/features/probes/ProbeListPage'
import { SensorDetailPage, SensorListPage } from '@/features/sensors/SensorPages'
import { UpdatesPage } from '@/features/updates/UpdatesPage'
import { AppLayout } from '@/layouts/AppLayout'

/**
 * Routes are named after objects, not after the commands they replace. An
 * administrator looking for a probe goes to /probes, whatever the shell called
 * the operation.
 */
export function AppRoutes() {
  return (
    <AuthGate>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<DashboardPage />} />

          <Route path="probes" element={<ProbeListPage />} />
          {/* Before the :probeId route, or "new" would be read as an id. */}
          <Route path="probes/new" element={<EnrollWizard />} />
          <Route path="probes/:probeId" element={<ProbeDetailPage />} />

          <Route path="sensors" element={<SensorListPage />} />
          <Route path="sensors/:name" element={<SensorDetailPage />} />

          <Route path="deployments" element={<DeploymentListPage />} />

          <Route path="jobs" element={<JobListPage />} />
          <Route path="jobs/:jobId" element={<JobDetailPage />} />

          <Route path="infrastructure">
            <Route index element={<Navigate to="nats" replace />} />
            <Route path="nats" element={<NatsPage />} />
            <Route path="certificates" element={<CertificatesPage />} />
            <Route path="iperf" element={<IperfPage />} />
            <Route path="iperf/:name" element={<IperfEndpointPage />} />
            <Route path="credentials" element={<CredentialsPage />} />
          </Route>

          <Route path="audit" element={<AuditPage />} />
        <Route path="updates" element={<UpdatesPage />} />
          <Route path="settings" element={<SettingsPage />} />

          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </AuthGate>
  )
}
