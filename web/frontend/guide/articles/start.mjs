import { L } from '../lib/text.mjs'

export const start = {
  id: 'start',
  title: L('Erste Schritte', 'Getting started'),
  who: L(
    'Für alle, die mit der Plattform arbeiten: was sie tut und wie die Oberfläche aufgebaut ist.',
    'For everyone working with the platform: what it does and how the interface is laid out.',
  ),
  illustration: 'start',
  illustrationAlt: L(
    'Zwei Personen an einem Tisch schauen gemeinsam auf einen Bildschirm mit einer Übersicht.',
    'Two people at a desk looking at an overview on a screen together.',
  ),
  articles: [
    {
      slug: 'what-it-does',
      title: L('Was die Plattform macht', 'What the platform does'),
      short: L(
        'Ein NATS-Server für die Multi-Platform-Probes von PRTG und eine Oberfläche, um sie zu verwalten.',
        'A NATS server for PRTG multi-platform probes and an interface to manage them.',
      ),
      lead: L(
        'PRTG-NATS stellt den NATS-Server bereit, über den Multi-Platform-Probes mit dem PRTG-Core sprechen, und verwaltet die Probes, ihre Sensoren und alles, was sie dafür brauchen.',
        'PRTG-NATS provides the NATS server multi-platform probes use to talk to the PRTG core, and manages the probes, their sensors and everything they need for it.',
      ),
      role: 'viewer',
      keywords: L('überblick einführung mpp nats', 'overview introduction mpp nats'),
      illustration: 'start',
      illustrationAlt: L(
        'Zwei Personen an einem Tisch schauen gemeinsam auf einen Bildschirm mit einer Übersicht.',
        'Two people at a desk looking at an overview on a screen together.',
      ),
      body: L(
        `<p>Eine Multi-Platform-Probe (MPP) ist ein Linux-System an einem Standort, das für PRTG misst. Sie meldet ihre Ergebnisse nicht direkt an den PRTG-Core, sondern über einen NATS-Server. Diese Installation ist dieser NATS-Server – und der Ort, von dem aus du die Probes betreust.</p>
<h2>Was du hier tun kannst</h2>
<table>
<thead><tr><th>Aufgabe</th><th>Wo</th></tr></thead>
<tbody>
<tr><td>Sehen, ob Server und Probes in Ordnung sind</td><td>{ui:nav.dashboard}</td></tr>
<tr><td>Eine neue Probe einbinden und mit PRTG verknüpfen</td><td>{ui:nav.probes}</td></tr>
<tr><td>Sensoren auf Probes ausrollen und konfigurieren</td><td>{ui:nav.sensors}</td></tr>
<tr><td>Verfolgen, was die Plattform gerade tut</td><td>{ui:nav.jobs}</td></tr>
<tr><td>Nachlesen, was ein Sensor bei einem Lauf gemeldet hat</td><td>{ui:nav.sensorLogs}</td></tr>
<tr><td>Messpunkte für Durchsatzmessungen betreiben</td><td>{ui:nav.iperf}</td></tr>
<tr><td>Zertifikate, Overlay und Wartung des Servers</td><td>{ui:nav.infrastructure}</td></tr>
<tr><td>Die Installation aktualisieren</td><td>{ui:nav.updates}</td></tr>
</tbody>
</table>
<h2>Was auf den Probes bleibt</h2>
<p>Die Probes bleiben normale Pakete auf ihren eigenen Linux-Systemen. Die Plattform richtet sie ein, rollt Sensoren aus und hält ihre Konfiguration aktuell, aber sie ersetzt sie nicht. In PRTG selbst ändert die Plattform nichts: Neue Probes bestätigst du weiterhin im PRTG-Core.</p>
<h2>Wie du dich zurechtfindest</h2>
<p>Die Artikel dieser Anleitung sind nach Aufgaben geordnet. An jedem steht, welche Rolle du dafür brauchst. Aus der Oberfläche heraus öffnet der Eintrag {ui:nav.guide} den Artikel, der zur gerade offenen Seite passt.</p>`,
        `<p>A multi-platform probe (MPP) is a Linux system at a site that measures for PRTG. It does not report its results to the PRTG core directly but through a NATS server. This installation is that NATS server – and the place you look after the probes from.</p>
<h2>What you can do here</h2>
<table>
<thead><tr><th>Task</th><th>Where</th></tr></thead>
<tbody>
<tr><td>See whether the server and the probes are healthy</td><td>{ui:nav.dashboard}</td></tr>
<tr><td>Enroll a new probe and link it to PRTG</td><td>{ui:nav.probes}</td></tr>
<tr><td>Deploy and configure sensors on probes</td><td>{ui:nav.sensors}</td></tr>
<tr><td>Follow what the platform is doing right now</td><td>{ui:nav.jobs}</td></tr>
<tr><td>Read what a sensor reported in a run</td><td>{ui:nav.sensorLogs}</td></tr>
<tr><td>Run iperf endpoints for throughput measurements</td><td>{ui:nav.iperf}</td></tr>
<tr><td>Certificates, the overlay and server maintenance</td><td>{ui:nav.infrastructure}</td></tr>
<tr><td>Update the installation</td><td>{ui:nav.updates}</td></tr>
</tbody>
</table>
<h2>What stays on the probes</h2>
<p>The probes remain ordinary packages on their own Linux systems. The platform sets them up, deploys sensors and keeps their configuration current, but it does not replace them. It changes nothing in PRTG itself: you still approve new probes in the PRTG core.</p>
<h2>Finding your way</h2>
<p>The articles in this guide are ordered by task. Each one says which role you need for it. From the interface, the {ui:nav.guide} entry opens the article that belongs to the page you are on.</p>`,
      ),
      related: ['dashboard', 'roles', 'enroll-a-probe'],
    },
    {
      slug: 'dashboard',
      title: L('Die Übersicht lesen', 'Reading the dashboard'),
      short: L(
        'Ob die Plattform betriebsbereit ist und wo etwas deine Aufmerksamkeit braucht.',
        'Whether the platform is ready and where something needs your attention.',
      ),
      lead: L(
        'Die Übersicht zeigt auf einen Blick, ob die Plattform betriebsbereit ist und ob gerade etwas zu tun ist. Steht dort {ui:dashboard.allClear}, ist alles in Ordnung.',
        'The dashboard answers one question: is the platform ready, and does anything need action? When it says {ui:dashboard.allClear}, all is well.',
      ),
      role: 'viewer',
      keywords: L('dashboard status warnungen start', 'status warnings home'),
      appPaths: ['/'],
      steps: [
        {
          title: L('Was gerade ansteht', 'What needs attention'),
          text: L(
            '<p>Gibt es etwas zu tun, steht oben die Liste {ui:dashboard.attention.title}. Jede Zeile ist eine Art von Problem, etwa Probes, die nicht antworten, fehlgeschlagene Jobs, bald ablaufende Zertifikate oder Probes, die in PRTG noch nicht eingetragen sind. Betrifft es mehrere Probes, stehen sie in einer Zeile mit ihren Namen darunter. Ein Klick führt dorthin, wo du es erledigst: zur Probe selbst oder zur passend gefilterten Liste.</p>',
            '<p>When there is something to do, the {ui:dashboard.attention.title} list is at the top. Each row is one kind of problem, for example probes that are not answering, failed jobs, certificates about to expire or probes not yet registered in PRTG. When several probes are affected they share one row, with their names below it. A click takes you to where you deal with it: the probe itself, or the list filtered accordingly.</p>',
          ),
          image: 'dashboard',
          alt: L('Die Übersicht mit der Liste der offenen Punkte, NATS-Server und Probes', 'The dashboard with the attention list, NATS server and probes'),
        },
        {
          title: L('NATS-Server, Probes und Jobs', 'NATS server, probes and jobs'),
          text: L(
            '<p>Darunter stehen der Zustand des NATS-Servers mit seinen Verbindungen und dem Speicher von JetStream, die Probes nach Zustand und die Jobs. Die zuletzt gelaufenen Jobs öffnest du direkt aus der Liste.</p>',
            '<p>Below it are the state of the NATS server with its connections and JetStream storage, the probes by state, and the jobs. The most recent jobs open straight from their list.</p>',
          ),
        },
        {
          title: L('Letzte administrative Aktionen', 'Recent administrative actions'),
          text: L(
            '<p>Die letzten Einträge aus dem Audit zeigen, wer zuletzt etwas geändert hat. Die vollständige Liste steht unter {ui:nav.audit}.</p>',
            '<p>The latest audit entries show who changed something last. The full list is under {ui:nav.audit}.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Die Übersicht aktualisiert sich selbst. Du musst die Seite nicht neu laden, um einen neuen Zustand zu sehen.',
          'The dashboard refreshes itself. You do not need to reload the page to see a new state.',
        ),
        L(
          'Meldet die Übersicht, dass die Installation noch nicht eingerichtet ist, führt ein Administrator zuerst die <a href="@install-the-server">Einrichtung auf dem Server</a> aus.',
          'If the dashboard reports that the installation has not been set up yet, an administrator runs the <a href="@install-the-server">setup on the server</a> first.',
        ),
      ],
      related: ['probe-status', 'follow-jobs'],
    },
    {
      slug: 'roles',
      title: L('Rollen: wer was darf', 'Roles: who may do what'),
      short: L(
        'Betrachter, Operator und Administrator – und warum ein Knopf manchmal fehlt.',
        'Viewer, operator and administrator – and why a button is sometimes missing.',
      ),
      lead: L(
        'Jedes Konto hat eine von drei Rollen. Jede Rolle darf alles, was die Rolle davor darf, und etwas mehr.',
        'Every account has one of three roles. Each role may do everything the one before it may, and more.',
      ),
      role: 'viewer',
      keywords: L('berechtigung rechte zugriff', 'permission rights access'),
      body: L(
        `<table>
<thead><tr><th>Rolle</th><th>Darf</th></tr></thead>
<tbody>
<tr><td><b>Betrachter</b></td><td>alles lesen: Status, Probes, Sensoren, Jobs, Sensor-Logs und das Audit; dazu den Zustand einer Probe auffrischen und eine Prüfung starten</td></tr>
<tr><td><b>Operator</b></td><td>zusätzlich Sensoren ausrollen, Konfigurationen anwenden, Abweichungen beheben, Jobs wiederholen und abbrechen, Messpunkte verwalten</td></tr>
<tr><td><b>Administrator</b></td><td>zusätzlich Benutzer verwalten, Zugangsdaten rotieren, Zertifikate erneuern, Probes einbinden und ausbinden, Systemeinstellungen ändern, Dienste neu starten und die Installation aktualisieren</td></tr>
</tbody>
</table>
<p>Die Grenze zwischen Operator und Administrator ist bewusst gezogen: Ein Operator hält die Probes am Laufen, ein Administrator ändert, welche Probes es gibt und wie der Server arbeitet.</p>
<h2>Wenn ein Knopf fehlt</h2>
<p>Die Oberfläche blendet aus, was deine Rolle nicht darf. Fehlt dir eine Aktion, die in dieser Anleitung beschrieben ist, steht oben am Artikel, welche Rolle du dafür brauchst. Welche Rolle du hast, siehst du unter {ui:nav.settings} bei {ui:settings.roles}.</p>
<h2>Anmeldung und Sitzung</h2>
<ul>
<li>Eine Sitzung endet nach zwölf Stunden oder nach sechzig Minuten ohne Aktivität.</li>
<li>Wer sein Passwort ändert, beendet damit alle anderen Sitzungen seines Kontos.</li>
<li>Nach mehreren falschen Passwörtern wartet die Anmeldung zunehmend länger, höchstens fünfzehn Minuten. Gesperrt wird ein Konto dadurch nie.</li>
</ul>`,
        `<table>
<thead><tr><th>Role</th><th>May</th></tr></thead>
<tbody>
<tr><td><b>Viewer</b></td><td>read everything: status, probes, sensors, jobs, sensor logs and the audit; also refresh a probe's state and run a check</td></tr>
<tr><td><b>Operator</b></td><td>also deploy sensors, apply configuration, resolve deviations, retry and cancel jobs, manage iperf endpoints</td></tr>
<tr><td><b>Administrator</b></td><td>also manage users, rotate credentials, renew certificates, enroll and unenroll probes, change system settings, restart services and update the installation</td></tr>
</tbody>
</table>
<p>The line between operator and administrator is deliberate: an operator keeps the probes running, an administrator changes which probes exist and how the server works.</p>
<h2>When a button is missing</h2>
<p>The interface hides what your role may not do. If an action described in this guide is missing for you, the top of the article says which role it needs. Your own role is shown under {ui:nav.settings} in {ui:settings.roles}.</p>
<h2>Signing in and sessions</h2>
<ul>
<li>A session ends after twelve hours, or after sixty minutes without activity.</li>
<li>Changing your password ends every other session of your account.</li>
<li>After several wrong passwords, signing in waits a little longer each time, at most fifteen minutes. An account is never locked by this.</li>
</ul>`,
      ),
      related: ['your-settings', 'manage-users'],
    },
    {
      slug: 'your-settings',
      title: L('Sprache, Darstellung und Passwort', 'Language, appearance and password'),
      short: L(
        'Die Oberfläche auf Deutsch oder Englisch, hell oder dunkel, und das eigene Passwort ändern.',
        'The interface in German or English, light or dark, and changing your own password.',
      ),
      lead: L(
        'Unter {ui:nav.settings} stellst du ein, wie die Oberfläche für dich aussieht, und änderst dein Passwort.',
        'Under {ui:nav.settings} you choose how the interface looks for you and change your password.',
      ),
      role: 'viewer',
      keywords: L('englisch deutsch dunkel hell theme passwort ändern', 'english german dark light theme change password'),
      appPaths: ['/settings'],
      steps: [
        {
          text: L(
            '<p>Öffne {ui:nav.settings}. Unter {ui:settings.general} wählst du {ui:settings.language} und {ui:settings.theme}. Beides gilt nur für den Browser, in dem du es einstellst, und auch für diese Anleitung.</p>',
            '<p>Open {ui:nav.settings}. Under {ui:settings.general} you choose {ui:settings.language} and {ui:settings.theme}. Both apply only to the browser you set them in, and to this guide as well.</p>',
          ),
          image: 'settings-general',
          alt: L('Einstellungen mit Sprache und Darstellung', 'Settings with language and appearance'),
        },
        {
          text: L(
            '<p>Mit {ui:auth.changePassword} vergibst du ein neues Passwort. Es braucht mindestens zwölf Zeichen; Länge zählt mehr als Sonderzeichen. Danach sind alle anderen Sitzungen deines Kontos beendet.</p>',
            '<p>{ui:auth.changePassword} sets a new password. It needs at least twelve characters; length counts for more than special characters. Afterwards every other session of your account has ended.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Wer ein Konto mit einem Startpasswort bekommt, muss es bei der ersten Anmeldung ändern, bevor es weitergeht.',
          'Anyone given an account with an initial password has to change it at the first sign-in before going any further.',
        ),
      ],
      related: ['roles'],
    },
  ],
}
