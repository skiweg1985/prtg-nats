import {
  Activity,
  BookOpen,
  CircleArrowUp,
  Cpu,
  FileText,
  Gauge,
  History,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  LogIn,
  Network,
  ScrollText,
  Server,
  Settings,
  ShieldCheck,
  Ticket,
  User,
  Wrench,
  type LucideIcon,
} from 'lucide-react'

/**
 * One icon per kind of object, wherever that kind appears.
 *
 * The sidebar entry for certificates and a certificate line in the audit
 * feed draw the same shield, so the icon comes to mean the object rather
 * than the place it was drawn. A new kind gets its icon here, not at the
 * call site.
 */
export const OBJECT_ICONS = {
  dashboard: LayoutDashboard,
  probe: Cpu,
  sensor: Activity,
  job: ListChecks,
  sensorLog: ScrollText,
  nats: Server,
  certificate: ShieldCheck,
  iperf: Gauge,
  overlay: Network,
  credential: KeyRound,
  system: Wrench,
  audit: History,
  update: CircleArrowUp,
  settings: Settings,
  guide: BookOpen,
  auth: LogIn,
  enrollment: Ticket,
  user: User,
} satisfies Record<string, LucideIcon>

export type ObjectKind = keyof typeof OBJECT_ICONS

/** The icon of an audit event, by the area its action belongs to
    ("sensor.profile.write" is a sensor's) or, failing that, by its object
    type. The action first: a sign-in is recorded against a web user, and a
    rollout against a deployment, neither of which says what happened. */
export function auditIcon(objectType: string, action: string): LucideIcon {
  const icons: Record<string, LucideIcon> = OBJECT_ICONS
  return icons[action.split('.')[0]] ?? icons[objectType] ?? FileText
}
