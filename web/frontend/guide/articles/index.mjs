/**
 * The table of contents. Groups appear on the overview and in the sidebar in
 * this order, and the articles inside them in the order of their files.
 */

import { L } from '../lib/text.mjs'
import { activity } from './activity.mjs'
import { administration } from './administration.mjs'
import { endpoints } from './endpoints.mjs'
import { infrastructure } from './infrastructure.mjs'
import { probes } from './probes.mjs'
import { sensors } from './sensors.mjs'
import { server } from './server.mjs'
import { start } from './start.mjs'

export const home = {
  title: L('Anleitung', 'Guide'),
  heading: L('Anleitung für PRTG-NATS', 'PRTG-NATS guide'),
  intro: L(
    '<p>Was du mit dieser Installation tun kannst, Schritt für Schritt: Probes einbinden, Sensoren ausrollen, Messungen nachvollziehen und den Server betreiben. An jedem Artikel steht, welche Rolle du dafür brauchst.</p>',
    '<p>What you can do with this installation, step by step: enroll probes, deploy sensors, follow measurements and run the server. Each article says which role it needs.</p>',
  ),
}

export const groups = [start, probes, sensors, activity, endpoints, infrastructure, administration, server].filter(
  (group) => group.articles.length > 0,
)
