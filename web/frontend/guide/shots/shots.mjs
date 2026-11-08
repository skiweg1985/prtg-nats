/**
 * Every screenshot in the guide: who is signed in, which page, what to click
 * before the picture, and what to cut out. Buttons are found by the label the
 * interface shows in the language being taken, so a renamed button fails here
 * instead of producing a picture of the wrong screen.
 *
 * The state each picture needs - a degraded probe, a failed rollout, an
 * overlay with three probes - comes from guide/demo/world.py.
 */

// Test values of the local demo installation, see guide/demo/world.py.
export const ACCOUNTS = {
  admin: 'demo-admin-2026!',
  olga: 'demo-olga-2026!',
  viktor: 'demo-viktor-2026!',
}

const dialog = (page) => page.getByRole('dialog').last()

// The interface shows a linked probe under its name in PRTG, so a probe is
// found by either name. The names come from guide/demo/world.py.
const PRTG_NAMES = {
  'probe-berlin-01': 'Berlin-Zentrale',
  'probe-hamburg-01': 'Hamburg-Hafen',
  'probe-leipzig-01': 'Leipzig-Lager',
  'probe-bremen-01': 'Bremen-Werk',
  'probe-kiel-01': 'Kiel Außenstelle',
}
const probe = (account) => new RegExp(`${account}|${PRTG_NAMES[account]}`)

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** A field by its label. A hint below the label is part of its name. */
const field = (scope, text) => scope.getByLabel(new RegExp(`^${escape(text)}`)).first()

/** A card of the interface, found by its heading. */
const card = (page, title) =>
  page.locator('section.surface-card').filter({ has: page.getByRole('heading', { name: title, exact: true }) }).first()

const button = (page, name) => page.getByRole('button', { name, exact: true }).first()

/** Open a probe from the list, the way a reader would. */
async function openProbe(page, name) {
  await page.getByRole('link', { name: new RegExp(name) }).first().click()
  await page.waitForURL(/\/probes\/[^/]+/)
  await page.waitForTimeout(800)
}

async function openRow(page, text) {
  await page.getByRole('link', { name: text, exact: true }).first().click()
  await page.waitForTimeout(800)
}

export const shots = [
  // --- Getting started ---------------------------------------------------------
  { name: 'dashboard', user: 'admin', path: '/' },
  {
    name: 'settings-general',
    user: 'viktor',
    path: '/settings',
    target: (page, { ui }) => card(page, ui('settings.general')),
    narrow: true,
  },

  // --- Probes --------------------------------------------------------------------
  {
    name: 'probes-list',
    user: 'olga',
    path: '/probes',
    prepare: async (page) => {
      const row = page.getByRole('row').filter({ hasText: 'probe-hamburg-01' })
      await row.getByRole('checkbox').first().check()
    },
  },
  {
    name: 'probes-enroll-command',
    user: 'admin',
    path: '/probes/new',
    prepare: async (page, { ui }) => {
      await field(page, ui('probes.enroll.fields.account')).fill('probe-rostock-01')
      await button(page, ui('probes.enroll.step1.submit')).click()
      await page.waitForTimeout(1500)
    },
  },
  {
    name: 'probes-detail-overview',
    user: 'olga',
    path: '/probes',
    prepare: (page) => openProbe(page, 'probe-berlin-01'),
  },
  {
    name: 'probes-link-dialog',
    user: 'olga',
    path: '/probes',
    prepare: async (page, { ui }) => {
      await openProbe(page, 'probe-hamburg-01')
      await button(page, ui('probes.prtgName.link')).click()
      await page.waitForTimeout(1200)
    },
    target: dialog,
    narrow: true,
  },
  {
    name: 'probes-unenroll-dialog',
    user: 'admin',
    path: '/probes',
    prepare: async (page, { ui }) => {
      await openProbe(page, 'probe-kiel-01')
      await button(page, ui('common.moreActions')).click()
      await page.getByRole('menuitem', { name: ui('probes.unenroll'), exact: true }).click()
      await page.waitForTimeout(600)
    },
    target: dialog,
    narrow: true,
  },

  // --- Sensors -------------------------------------------------------------------
  { name: 'sensors-list', user: 'olga', path: '/sensors' },
  {
    name: 'sensors-deploy-dialog',
    user: 'olga',
    path: '/sensors/wlan-auth',
    prepare: async (page, { ui }) => {
      await button(page, ui('sensors.deployTo')).click()
      await page.waitForTimeout(800)
      for (const name of ['probe-hamburg-01', 'probe-kiel-01']) {
        await dialog(page).getByRole('checkbox', { name: probe(name) }).first().check()
      }
    },
    target: dialog,
    narrow: true,
  },
  {
    name: 'sensors-prtg-card',
    user: 'olga',
    path: '/sensors/internet-speed',
    target: (page, { ui }) => card(page, ui('sensors.prtg.title')),
    narrow: true,
  },
  {
    name: 'sensors-variant-dialog',
    user: 'olga',
    path: '/sensors/wlan-auth',
    prepare: async (page, { ui }) => {
      await button(page, ui('sensors.variants.add')).click()
      await page.waitForTimeout(800)
      const form = dialog(page)
      await field(form, ui('sensors.variants.name')).fill('standort-nord')
      await field(form, 'SSID').fill('Standort-Nord')
      // The fields below depend on the method; PSK brings the key field.
      await field(form, 'AUTH').selectOption('psk')
      await page.waitForTimeout(300)
      await form.locator('input[type=password]').first().fill('demo-psk-nord-2026')
      await form.getByRole('checkbox', { name: probe('probe-berlin-01') }).first().check()
    },
    target: dialog,
  },
  {
    name: 'sensors-remove-confirm',
    user: 'olga',
    path: '/probes',
    prepare: async (page, { ui }) => {
      await openProbe(page, 'probe-berlin-01')
      await page.getByRole('tab', { name: ui('probes.tabs.sensors') }).click()
      await page.waitForTimeout(600)
      const row = page.getByRole('row').filter({ hasText: 'link-quality' }).first()
      await row.getByRole('button', { name: ui('common.moreActions') }).click()
      await page.getByRole('menuitem', { name: ui('common.remove'), exact: true }).click()
      await page.waitForTimeout(600)
    },
    target: dialog,
    narrow: true,
  },

  // --- Jobs, sensor log and audit ------------------------------------------------
  { name: 'activity-jobs-list', user: 'olga', path: '/jobs' },
  {
    name: 'activity-rollouts',
    user: 'olga',
    path: '/jobs?view=rollouts',
    prepare: async (page, { ui }) => {
      // The row itself opens the job; the chevron unfolds the result per probe.
      const row = page.getByRole('row').filter({ hasText: 'internet-speed' }).first()
      await row.getByRole('button', { name: ui('common.showDetails') }).click()
      await page.waitForTimeout(800)
    },
  },
  {
    name: 'activity-job-detail',
    user: 'olga',
    path: '/jobs',
    prepare: async (page, { ui }) => {
      await button(page, ui('jobs.filters.failed')).click()
      await page.waitForTimeout(1000)
      await page.getByRole('row').filter({ hasText: 'sensor.deploy' }).first().getByRole('link').first().click()
      await page.waitForURL(/\/jobs\/[^/]+/)
    },
  },
  { name: 'activity-sensor-log', user: 'viktor', path: '/sensor-logs' },
  {
    name: 'activity-sensor-log-run',
    user: 'viktor',
    path: '/sensor-logs',
    prepare: async (page, { ui }) => {
      const row = page.getByRole('row').filter({ hasText: ui('sensorLogs.result.error') }).first()
      await row.getByRole('link').first().click()
      await page.waitForURL(/\/sensor-logs\/[^/]+/)
    },
  },
  { name: 'activity-audit', user: 'viktor', path: '/audit' },

  // --- iperf endpoints -----------------------------------------------------------
  {
    name: 'endpoints-setup-dialog',
    user: 'olga',
    path: '/infrastructure/iperf',
    prepare: async (page, { ui }) => {
      await button(page, ui('infrastructure.iperf.add')).click()
      await page.waitForTimeout(600)
      const form = dialog(page)
      await field(form, ui('infrastructure.iperf.columns.name')).fill('iperf-zentrale-2')
      await field(form, ui('infrastructure.iperf.host')).fill('203.0.113.21')
      await button(form, ui('infrastructure.iperf.scan')).click()
      await page.waitForTimeout(1500)
    },
    target: dialog,
    narrow: true,
  },
  {
    name: 'endpoints-detail',
    user: 'olga',
    path: '/infrastructure/iperf',
    prepare: (page) => openRow(page, 'iperf-zentrale'),
  },
  {
    name: 'endpoints-external-dialog',
    user: 'olga',
    path: '/infrastructure/iperf',
    prepare: async (page, { ui }) => {
      await button(page, ui('infrastructure.iperf.register')).click()
      await page.waitForTimeout(600)
      const form = dialog(page)
      await field(form, ui('infrastructure.iperf.columns.name')).fill('iperf-extern-2')
      await field(form, ui('infrastructure.iperf.host')).fill('203.0.113.51')
      await field(form, ui('infrastructure.iperf.measureUser')).fill('prtg-probe')
      const secret = form.locator('input[type=password]').first()
      if (await secret.count()) await secret.fill('demo-iperf-2026')
      await field(form, ui('infrastructure.iperf.publicKey')).fill('-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAexample\n-----END PUBLIC KEY-----')
    },
    target: dialog,
    narrow: true,
  },
  {
    name: 'endpoints-assign-probes',
    user: 'olga',
    path: '/infrastructure/iperf',
    prepare: async (page, { ui }) => {
      await openRow(page, 'iperf-zentrale')
      await button(page, ui('infrastructure.iperf.assign')).click()
      await page.waitForTimeout(800)
      await dialog(page).getByRole('checkbox', { name: probe('probe-leipzig-01') }).first().check()
      await page.waitForTimeout(400)
    },
    target: dialog,
    narrow: true,
  },

  // --- Infrastructure ------------------------------------------------------------
  { name: 'infrastructure-nats', user: 'viktor', path: '/infrastructure/nats' },
  { name: 'infrastructure-certificates', user: 'admin', path: '/infrastructure/certificates' },
  { name: 'infrastructure-overlay', user: 'olga', path: '/infrastructure/overlay' },
  {
    name: 'infrastructure-overlay-add',
    user: 'olga',
    path: '/infrastructure/overlay',
    prepare: async (page, { ui }) => {
      await button(page, ui('infrastructure.overlay.add.action')).click()
      await page.waitForTimeout(800)
      await dialog(page).getByRole('checkbox', { name: probe('probe-kiel-01') }).first().check()
    },
    target: dialog,
    narrow: true,
  },
  { name: 'infrastructure-nats-accounts', user: 'admin', path: '/infrastructure/credentials' },
  { name: 'infrastructure-maintenance', user: 'admin', path: '/infrastructure/maintenance' },

  // --- Administration ------------------------------------------------------------
  {
    name: 'administration-users-add',
    user: 'admin',
    path: '/settings',
    prepare: async (page, { ui }) => {
      await button(page, ui('settings.addUser')).click()
      await page.waitForTimeout(600)
      // The new-user form opens at the top of the card, above the rows of the
      // existing accounts - which carry a role picker each. Only the form's
      // own fields may be touched, or the picture changes a real account.
      const scope = card(page, ui('settings.users')).locator('form').first()
      await field(scope, ui('auth.username')).fill('betrieb-nord')
      await field(scope, ui('auth.displayName')).fill('Betrieb Nord')
      await scope.locator('input[type=password]').first().fill('demo-start-passwort-2026')
      await field(scope, ui('settings.role')).selectOption('operator')
    },
    target: (page, { ui }) => card(page, ui('settings.users')),
  },
  {
    name: 'administration-prtg-core',
    user: 'admin',
    path: '/settings',
    target: (page, { ui }) => card(page, ui('settings.prtg.title')),
    narrow: true,
  },
  { name: 'administration-updates', user: 'admin', path: '/updates' },
]

