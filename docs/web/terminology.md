---
title: Terminology of the web interface
role: developer
updated: 2026-11-08
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
| credential bundle (iperf, wlan-auth run field `profile`) | Zugangsdaten(-Profil) | credentials / credential profile | Profil (bare) |
| iperf target | Messpunkt | iperf endpoint / endpoint | Endpunkt, Endpoint (DE), Gegenstelle, counterpart |
| iperf target not run by this platform | externer Messpunkt / eintragen | external endpoint / add | Fremder Messpunkt, registrieren, foreign endpoint |
| NATS monitoring URL | Adresse / Monitoring-Adresse | address | Endpunkt/endpoint for this sense |
| WireGuard dial-in | Hub-Adresse / Endpoint des Overlays | hub address | — |
| shipping a sensor | ausrollen (Verb), Rollout (Substantiv) | deploy / deployment | Ausrollvorgang, verteilen |
| secret | Passwort | password | Kennwort |
| credentials | Zugangsdaten | credentials | Anmeldedaten |
| async unit of work | Job | job | Vorgang, Auftrag |
| job step log | Verlauf / Protokoll (job page only) | log | — |
| audit trail | Audit | audit | Protokoll (for this sense) |
| PRTG access key | PRTG Access Key | PRTG access key | Zugriffsschlüssel |
| PRTG's server | PRTG-Core | PRTG core | PRTG Core (DE, without hyphen), Core (bare), Core-Adresse |
| its URL in the settings | Adresse des PRTG-Core | PRTG core URL | Core-Adresse, core URL (bare) |
| a person confirming the PRTG steps | bestätigt von | confirmed by | abgehakt, ticked |
| name a probe announced at enrollment | Name bei der Einbindung | enrollment name | Probe-Name in PRTG, probe name in PRTG (for this value) |
| PRTG's number for a probe object | Objekt-Id | object id | GID, GUID, PRTG-Objekt-Id, PRTG object id |
| connecting a probe to its PRTG object | verknüpfen / Verknüpfung | link | — |
| the way management reaches a probe | Management über | management via | — |
| the way NATS traffic reaches the server | NATS über | NATS via | — |
| route over the overlay | Tunnel | tunnel | — |
| putting a probe on the overlay | ins Overlay holen | add to the overlay | Aufnehmen |
| route over the probe's own network | Standortadresse | site address (`site` in the API and the shell) | — |
| NATS traffic outside the overlay | direkt | direct | — |
| the probe's own connection back to the platform | Rückkanal | return channel | — |
| what sensors report over it, per run | Sensor-Log | sensor log | Protokoll (for this sense) |
| one sensor execution in the sensor log | Lauf | run | — |
| a stretch the probe could not keep | Lücke | gap | — |
| the runtime volume's directory | Laufzeitverzeichnis | runtime directory | runtime-Verzeichnis, Runtime-Verzeichnis |
| the stack's git working tree on the host | Installationsverzeichnis | installation directory | Checkout (in the interface) |
| the branch updates come from | Update-Kanal | update channel | Branch (in the interface) |

The overlay vocabulary is layered on purpose and is the model to follow:
**Overlay** is the feature, **Tunnel** the per-probe link, **Hub** the server
side. `VPN` refers only to the customer's own site-to-site tunnel, never to
ours.

The enrollment name is only what the probe announced when it was first
approved. Anyone can rename the probe in PRTG afterwards, and PRTG does not
tell us, so the stored value is never labelled as the name PRTG uses. What
identifies a probe is its **Probe-Id** / **probe id**, which never changes.
The name PRTG currently shows, once it is read from PRTG, is the **Name in
PRTG** / **name in PRTG**. The interface shows a probe under one name only:
a display name chosen here, otherwise the name in PRTG for a linked probe,
otherwise the enrollment name - never two of them side by side. The
enrollment name stays in the shell's `probe show`, where it describes the
probe's configuration. The enrollment wizard's „Anderer Name in PRTG" /
"Different name in PRTG" sets the enrollment name - at that moment the two
are the same.

PRTG has no GID to look the probe id up by, so the name in PRTG is read by the
**Objekt-Id** / **object id**, the number PRTG shows in the probe's address.
A probe is **verknüpft** / **linked** to its PRTG object once, by a person;
the **PRTG-Verknüpfung** / **PRTG link** row shows that object id.

A probe has two routes, and the interface names the one in use. Management
goes **über den Tunnel** / **via the tunnel** - the overlay address - or
**über die Standortadresse** / **via the site address**, the probe's address
on its own network. The API and the shell spell the second one `site`.
NATS traffic goes through the tunnel or **direkt** / **direct**; "site"
is not used for NATS, because NATS does not connect to the probe.

The **Rückkanal** / **return channel** is how a probe reports back on its own
initiative, separate from the management access and from NATS. What arrives
over it is the **Sensor-Log** / **sensor log**: one entry per **Lauf** /
**run** of a sensor, with its raw output. „Protokoll" stays with the job page,
so the two are never confused on screen; the audit trail is „Audit", never
„Audit-Protokoll". A **Lücke** / **gap** marks runs the probe had to drop
while the platform was out of reach.

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

The `wlan-auth` run detail shows a `profile` field: the name of the
credentials the run signed in with. It is labelled „Zugangsdaten-Profil" /
"credential profile", like the iperf credentials, because the reader of a
run wants to know which credentials were used, not how they reached the
probe. Bare „Profil" stays retired.
