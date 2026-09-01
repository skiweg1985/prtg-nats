---
title: Terminology of the web interface
role: developer
updated: 2026-09-01
---

# Terminology of the web interface

One term per concept, per language. Every user-facing string in
`src/i18n/locales/` follows this table; `npm run i18n:check` enforces the
retired column mechanically (`FORBIDDEN_TERMS` in
`scripts/check-translations.mjs`). When a new string needs a word this page
does not settle, settle it here first.

## Concepts

| Concept | DE | EN | Retired |
| --- | --- | --- | --- |
| managed host | Probe | probe | Sonde, MPP (outside package talk) |
| joining a probe | einbinden / Einbindung | enroll / enrollment | anmelden (probe sense), enrollieren, Aufnahme, enrol |
| leaving | ausbinden | unenroll | Außerbetriebnahme, abmelden |
| probe status badge | Eingebunden | Enrolled | — |
| invitation withdrawal | zurückziehen | revoke | cancel |
| NATS credential | NATS-Konto / Konto | NATS account | Benutzer (for accounts) |
| SSH access | Management-Zugang | management access | bare „Zugang" |
| sensor config bundle | Variante | variant | Profil (for this sense) |
| iperf credential bundle | Zugangsdaten(-Profil) | credentials / credential profile | — |
| iperf target | Messpunkt | iperf endpoint / endpoint | Endpunkt, Endpoint (DE), Gegenstelle, counterpart |
| NATS monitoring URL | Adresse / Monitoring-Adresse | address | Endpunkt/endpoint for this sense |
| WireGuard dial-in | Hub-Adresse / Endpoint des Overlays | hub address | — |
| shipping a sensor | ausrollen (Verb), Rollout (Substantiv) | deploy / deployment | Ausrollvorgang, verteilen |
| secret | Passwort | password | Kennwort |
| credentials | Zugangsdaten | credentials | Anmeldedaten |
| async unit of work | Job | job | Vorgang, Auftrag |
| job step log | Verlauf / Protokoll (job page only) | log | — |
| audit trail | Audit | audit | Protokoll (for this sense) |
| PRTG access key | PRTG Access Key | PRTG access key | Zugriffsschlüssel |

The overlay vocabulary is layered on purpose and is the model to follow:
**Overlay** is the feature, **Tunnel** the per-probe link, **Hub** the server
side. `VPN` refers only to the customer's own site-to-site tunnel, never to
ours.

## Register (German)

The interface uses **Du**, consistently. Buttons and menu entries stay in
the infinitive („Ausrollen", „Konto anlegen") - that is standard German UI
style and is not an exception to the Du rule. Impersonal phrasing is fine
where no address is needed at all; what must not happen is „Sie" anywhere,
or both forms on one screen.

## Text budget

Written for an experienced sysadmin. Never explain ssh, systemd, ports,
certificates or WireGuard basics - explain only what is specific to this
platform (what a variant is, where the default alias comes from, which side
opens the iperf connection).

- Body copy: at most two sentences.
- Banners: one sentence plus the action.
- `errors.*.cause`: one sentence. `errors.*.action`: one or two, imperative.
- Anything longer moves into a collapsed `<details>` block or the docs.

## The `--profile` flag

The CLI flag stays `--profile` for compatibility while the UI says
„Variante". Where the flag surfaces, use one fixed sentence: „In PRTG trägt
der Parameter `--profile` den Namen der Variante." Do not paraphrase it
differently per page.
