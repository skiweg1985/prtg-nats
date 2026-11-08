#!/usr/bin/env node
/**
 * Take the guide's screenshots from the demo installation (guide/demo/).
 *
 *   node guide/shots/take.mjs                   every image, German and English
 *   node guide/shots/take.mjs de                one language
 *   node guide/shots/take.mjs de probes-list    single images
 *
 * Needs the demo running (guide/demo/run.sh and frontend.sh) and Google Chrome;
 * playwright-core drives the installed browser rather than downloading one.
 * GUIDE_APP_URL overrides where the interface is (default: the port the demo
 * recorded). The images land in guide/images/<lang>/<name>.webp via cwebp.
 */

import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'

import { chromium } from 'playwright-core'

import { label } from '../lib/text.mjs'
import { ACCOUNTS, shots } from './shots.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const imagesDir = join(here, '..', 'images')

function appUrl() {
  if (process.env.GUIDE_APP_URL) return process.env.GUIDE_APP_URL.replace(/\/$/, '')
  const state = process.env.PRTG_NATS_DEMO_STATE ?? '/tmp/prtg-nats-guide-demo'
  const ports = join(state, 'ports.json')
  if (existsSync(ports)) {
    const recorded = JSON.parse(readFileSync(ports, 'utf8'))
    const port = recorded.frontend ?? recorded.ui ?? recorded.vite
    if (port) return `http://127.0.0.1:${port}`
  }
  return 'http://127.0.0.1:15173'
}

const args = process.argv.slice(2)
const langs = args.filter((arg) => arg === 'de' || arg === 'en')
const names = args.filter((arg) => arg !== 'de' && arg !== 'en')
const base = appUrl()
const scratch = join(tmpdir(), 'prtg-nats-guide-shots')
rmSync(scratch, { recursive: true, force: true })
mkdirSync(scratch, { recursive: true })

const selected = shots.filter((shot) => names.length === 0 || names.includes(shot.name))
for (const name of names) {
  if (!shots.some((shot) => shot.name === name)) throw new Error(`no screenshot called ${name}`)
}

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const failed = []

for (const lang of langs.length ? langs : ['de', 'en']) {
  const byUser = Map.groupBy(selected, (shot) => shot.user)
  for (const [user, list] of byUser) {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 800 },
      deviceScaleFactor: 2,
      colorScheme: 'light',
      locale: lang === 'de' ? 'de-DE' : 'en-US',
      timezoneId: 'Europe/Berlin',
    })
    // The interface reads both from storage before it renders anything.
    await context.addInitScript(
      ([language]) => {
        localStorage.setItem('prtg-nats-language', language)
        localStorage.setItem('prtg-nats-theme', 'light')
      },
      [lang],
    )
    const login = await context.request.post(`${base}/api/v1/auth/login`, {
      data: { username: user, password: ACCOUNTS[user] },
    })
    if (!login.ok()) throw new Error(`signing in as ${user} failed: ${login.status()}`)

    for (const shot of list) {
      const page = await context.newPage()
      const ui = (key) => label(key, lang)
      try {
        await page.goto(`${base}${shot.path}`)
        await page.waitForLoadState('domcontentloaded')
        await settle(page)
        if (shot.prepare) await shot.prepare(page, { ui, lang, base })
        await settle(page)
        // A click can leave the page scrolled, and the job page follows its
        // log down once it has loaded; a picture of a page starts at its top,
        // like the reader's first look.
        if (!shot.target) {
          await page.evaluate(() => window.scrollTo(0, 0))
          await page.waitForTimeout(250)
        }
        const png = join(scratch, `${lang}-${shot.name}.png`)
        const target = shot.target ? await shot.target(page, { ui, lang }) : null
        if (target) await target.screenshot({ path: png, animations: 'disabled' })
        else await page.screenshot({ path: png, animations: 'disabled' })
        const out = join(imagesDir, lang, `${shot.name}.webp`)
        mkdirSync(dirname(out), { recursive: true })
        // Wide enough for a sharp image in the article column, small enough
        // that the guide stays light: 1600 px for whole pages, less for details.
        const width = shot.narrow ? 1100 : 1600
        execFileSync('cwebp', ['-quiet', '-q', '82', '-resize', String(width), '0', png, '-o', out])
        console.log(`ok   ${lang} ${shot.name}`)
      } catch (error) {
        failed.push(`${lang} ${shot.name}: ${error.message.split('\n')[0]}`)
        console.log(`FAIL ${lang} ${shot.name}: ${error.message.split('\n')[0]}`)
      } finally {
        await page.close()
      }
    }
    await context.close()
  }
}

await browser.close()
if (failed.length) {
  console.error(`\n${failed.length} screenshot(s) failed:\n  ${failed.join('\n  ')}`)
  process.exit(1)
}

/** Live data and fonts settle within a moment; the event stream never idles. */
async function settle(page) {
  await page.evaluate(() => document.fonts.ready)
  await page.waitForTimeout(900)
}
