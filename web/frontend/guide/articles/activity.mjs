import { L } from '../lib/text.mjs'

export const activity = {
  id: 'activity',
  illustration: 'activity',
  illustrationAlt: L(
    'Ein Mann verfolgt auf einer Wandanzeige eine Liste von Vorgängen, einer davon läuft gerade.',
    'A man follows a list of tasks on a wall display, one of them is running.',
  ),
  title: L('Jobs, Sensor-Log und Audit', 'Jobs, sensor log and audit'),
  who: L(
    'Für alle, die nachvollziehen wollen, was die Plattform tut, was die Sensoren melden und wer was geändert hat.',
    'For everyone who wants to follow what the platform does, what the sensors report and who changed what.',
  ),
  articles: [
    {
      slug: 'follow-jobs',
      title: L('Jobs verfolgen', 'Following jobs'),
      short: L(
        'Was die Plattform gerade tut, wie ein Rollout auf jeder Probe ausging, und Jobs wiederholen oder abbrechen.',
        'What the platform is doing right now, how a rollout went on each probe, and retrying or cancelling jobs.',
      ),
      lead: L(
        'Alles, was dauert oder scheitern kann, läuft als Job: ein Rollout, das Einbinden einer Probe, ein Update. Unter {ui:nav.jobs} siehst du, was gerade läuft und was gelaufen ist.',
        'Anything that takes time or can fail runs as a job: a rollout, enrolling a probe, an update. Under {ui:nav.jobs} you see what is running and what has run.',
      ),
      role: 'viewer',
      illustration: 'activity',
      illustrationAlt: L(
        'Ein Mann verfolgt auf einer Wandanzeige eine Liste von Vorgängen, einer davon läuft gerade.',
        'A man follows a list of tasks on a wall display, one of them is running.',
      ),
      keywords: L(
        'job rollout protokoll fehler wiederholen abbrechen wartet blockiert',
        'job rollout log error retry cancel waiting blocked',
      ),
      appPaths: ['/jobs'],
      unnumbered: true,
      steps: [
        {
          title: L('Die Jobliste', 'The job list'),
          text: L(
            '<p>{ui:jobs.filters.all} zeigt die letzten fünfzig Jobs mit Status, Fortschritt, Dauer und wer sie gestartet hat. {ui:jobs.filters.running} und {ui:jobs.filters.failed} durchsuchen dagegen die ganze Historie. Ein Klick auf eine Zeile öffnet den Job.</p>',
            '<p>{ui:jobs.filters.all} shows the last fifty jobs with status, progress, duration and who started them. {ui:jobs.filters.running} and {ui:jobs.filters.failed}, by contrast, search the whole history. Clicking a row opens the job.</p>',
          ),
          image: 'activity-jobs-list',
          alt: L(
            'Die Jobliste mit laufenden, erfolgreichen und gescheiterten Jobs',
            'The job list with running, successful and failed jobs',
          ),
        },
        {
          title: L('Rollouts', 'Rollouts'),
          text: L(
            '<p>Der Reiter {ui:jobs.filters.rollouts} zeigt nur die Sensor-Rollouts, jeweils mit Sensor, Version und wie viele Probes erfolgreich waren. Klappst du eine Zeile auf, steht dort das Ergebnis je Probe: der Versionswechsel oder der Fehler mit der Meldung der Probe. Ein {ui:deployments.dryRunBadge} hat nichts installiert.</p>',
            '<p>The {ui:jobs.filters.rollouts} tab shows only sensor rollouts, each with sensor, version and how many probes succeeded. Unfold a row to see the outcome per probe: the version change, or the error with the probe’s own message. A {ui:deployments.dryRunBadge} installed nothing.</p>',
          ),
          image: 'activity-rollouts',
          alt: L(
            'Die Rollouts mit einer aufgeklappten Zeile und dem Ergebnis je Probe',
            'The rollouts with one row unfolded, showing the outcome per probe',
          ),
        },
        {
          title: L('Die Seite eines Jobs', 'A job’s page'),
          text: L(
            '<p>Oben stehen die Schritte des Jobs und welcher gerade läuft, darunter das {ui:jobs.log}. Solange der Job läuft, kommen neue Zeilen von selbst dazu. Unter {ui:jobs.result} steht, wann er begann, wie lange er dauerte und wer ihn gestartet hat; bei einem Rollout zusätzlich, auf welchen Probes er gelang.</p>',
            '<p>At the top are the job’s steps and which one is running, below them the {ui:jobs.log}. While the job runs, new lines appear on their own. {ui:jobs.result} shows when it started, how long it took and who started it; for a rollout also on which probes it succeeded.</p>',
          ),
          image: 'activity-job-detail',
          alt: L(
            'Die Seite eines gescheiterten Rollouts mit Schritten, Fehlerbeschreibung und Protokoll',
            'The page of a failed rollout with steps, error description and log',
          ),
        },
        {
          title: L('Wenn ein Job scheitert', 'When a job fails'),
          text: L(
            '<p>Ein Fehlerfeld beantwortet der Reihe nach {ui:errors.whatFailed}, {ui:errors.affected}, {ui:errors.atStep}, {ui:errors.likelyCause} und {ui:errors.recommendedAction}. Die ungekürzte Ausgabe der Probe steht hinter {ui:common.showTechnicalDetails}.</p>',
            '<p>An error panel answers, in order: {ui:errors.whatFailed}, {ui:errors.affected}, {ui:errors.atStep}, {ui:errors.likelyCause} and {ui:errors.recommendedAction}. The probe’s full output is behind {ui:common.showTechnicalDetails}.</p>',
          ),
        },
        {
          title: L('Wiederholen und abbrechen', 'Retrying and cancelling'),
          text: L(
            '<p>Als Operator startest du einen beendeten Job mit {ui:jobs.retry} neu. Das wird ein neuer Job mit denselben Angaben; der alte bleibt in der Historie. Einen laufenden Job hältst du mit {ui:jobs.cancel} an: Ein wartender Job endet sofort, ein laufender nach der Probe, an der er gerade arbeitet.</p>',
            '<p>As an operator you restart a finished job with {ui:jobs.retry}. That becomes a new job with the same inputs; the old one stays in the history. {ui:jobs.cancel} stops a running job: a queued job ends at once, a running one after the probe it is working on.</p>',
          ),
        },
        {
          title: L('Ein Job wartet', 'A job is waiting'),
          text: L(
            '<p>Ein Pausenzeichen in der Liste heißt, dass der Job auf einen anderen wartet, der am selben Ziel arbeitet, etwa an derselben Probe. Auf seiner Seite steht, welcher Job das ist. Du musst nichts tun: Er beginnt von selbst, sobald der andere fertig ist.</p>',
            '<p>A pause sign in the list means the job is waiting for another one working on the same target, such as the same probe. Its page names that job. You need not do anything: it starts by itself once the other one has finished.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Unter der Überschrift eines Jobs steht sein Ziel als Link, zum Beispiel die Probe, an der er gearbeitet hat.',
          'Below a job’s heading its target is shown as a link, for example the probe it worked on.',
        ),
        L(
          'Nach einem erfolgreichen Rollout erinnert die Seite daran, den Sensor in PRTG anzulegen; {ui:jobs.deploy.toSensor} führt zum Skriptnamen und zur Parameterzeile. Siehe <a href="@deploy-sensors">Einen Sensor ausrollen</a>.',
          'After a successful rollout the page reminds you to set the sensor up in PRTG; {ui:jobs.deploy.toSensor} leads to the script name and the parameter line. See <a href="@deploy-sensors">Deploying a sensor</a>.',
        ),
        L(
          'Reißt die Live-Verbindung ab, verbindet sich die Seite selbst neu. Gelingt das nicht, holst du sie mit {ui:jobs.reconnect} zurück; das Protokoll ist bis dahin vollständig.',
          'If the live connection drops, the page reconnects by itself. If that fails, {ui:jobs.reconnect} brings it back; the log is complete up to that point.',
        ),
      ],
      related: ['deploy-sensors', 'sensor-logs', 'dashboard'],
    },
    {
      slug: 'sensor-logs',
      title: L('Das Sensor-Log lesen', 'Reading the sensor log'),
      short: L(
        'Jeder Lauf eines Sensors mit seiner Ausgabe: filtern, öffnen und als Link weitergeben.',
        'Every run of a sensor with its output: filter, open and pass it on as a link.',
      ),
      lead: L(
        'Das Sensor-Log zeigt jeden Lauf eines Sensors so, wie die Probe ihn gemeldet hat, mit der vollständigen Ausgabe. Hier liest du nach, warum ein Sensor in PRTG rot geworden ist.',
        'The sensor log shows every run of a sensor as the probe reported it, with its full output. This is where you read why a sensor turned red in PRTG.',
      ),
      role: 'viewer',
      keywords: L(
        'sensor-log lauf ausgabe lücke ssid wlan fehlercode rückkanal',
        'sensor log run output gap ssid wifi error code return channel',
      ),
      appPaths: ['/sensor-logs'],
      unnumbered: true,
      steps: [
        {
          title: L('Läufe finden', 'Finding runs'),
          text: L(
            '<p>Unter {ui:nav.sensorLogs} stehen zunächst die Läufe der letzten 24 Stunden. Grenze sie nach {ui:sensorLogs.filters.range}, {ui:sensorLogs.filters.result}, {ui:sensorLogs.filters.probe}, {ui:sensorLogs.filters.sensor} und {ui:sensorLogs.filters.network} ein; das Netz bestätigst du mit der Eingabetaste. {ui:sensorLogs.filters.search} findet Text in der Ausgabe der Läufe.</p>',
            '<p>{ui:nav.sensorLogs} starts with the runs of the last 24 hours. Narrow them by {ui:sensorLogs.filters.range}, {ui:sensorLogs.filters.result}, {ui:sensorLogs.filters.probe}, {ui:sensorLogs.filters.sensor} and {ui:sensorLogs.filters.network}; confirm the network with the Enter key. {ui:sensorLogs.filters.search} finds text in the runs’ output.</p>',
          ),
          image: 'activity-sensor-log',
          alt: L(
            'Das Sensor-Log mit Filtern und Läufen mit den Ergebnissen OK, Fehler und Lücke',
            'The sensor log with filters and runs with the results OK, error and gap',
          ),
        },
        {
          title: L('Das Ergebnis eines Laufs', 'The result of a run'),
          text: L(
            '<p>Jeder Lauf ist {ui:sensorLogs.result.ok}, {ui:sensorLogs.result.error} oder eine {ui:sensorLogs.result.gap}. Die {ui:sensorLogs.columns.summary} nennt die Meldung, die auch PRTG bekommen hat, und den Fehlercode des Sensors. Eine Lücke markiert Läufe, die die Probe verwerfen musste, weil sie die Plattform zu lange nicht erreicht hat.</p>',
            '<p>Every run is {ui:sensorLogs.result.ok}, {ui:sensorLogs.result.error} or a {ui:sensorLogs.result.gap}. The {ui:sensorLogs.columns.summary} gives the message PRTG received as well and the sensor’s error code. A gap marks runs the probe had to drop because it could not reach the platform for too long.</p>',
          ),
        },
        {
          title: L('Einen Lauf öffnen', 'Opening a run'),
          text: L(
            '<p>Der Pfeil am Zeilenanfang klappt die Ausgabe direkt in der Liste auf. Ein Klick auf die Zeile öffnet den Lauf auf einer eigenen Seite: mit dem Fehlercode in Worten, den gemessenen Werten und der rohen Ausgabe, etwa der Anmeldung am WLAN. Die Adresse dieser Seite kannst du weitergeben. Probe und Sensor in der Zeile führen direkt zur Probe und zum Sensor auf dieser Probe.</p><p>Über dem Titel zeigt der Navigationspfad, woher du gekommen bist: Aus dem gefilterten Sensor-Log geöffnet, führt er mit denselben Filtern dorthin zurück; von der Seite des Sensors geöffnet, über Probe und Sensor. Ohne bekannte Herkunft, etwa bei einem weitergegebenen Link, zeigt er Probe und Sensor.</p>',
            '<p>The arrow at the start of a row unfolds the output right in the list. Clicking the row opens the run on a page of its own: with the error code in words, the measured values and the raw output, such as the Wi-Fi sign-in. You can pass on the address of that page. Probe and sensor in the row lead straight to the probe and to the sensor on that probe.</p><p>Above the title, the breadcrumb shows where you came from: opened from the filtered sensor log, it leads back there with the same filters; opened from the sensor’s page, through probe and sensor. Without a known origin, such as a link someone passed on, it shows probe and sensor.</p>',
          ),
          image: 'activity-sensor-log-run',
          alt: L(
            'Die Seite eines fehlgeschlagenen Laufs mit Fehlercode, Werten und Ausgabe',
            'The page of a failed run with error code, values and output',
          ),
        },
      ],
      notes: [
        L(
          'Ins Sensor-Log schreiben Sensoren, die das vorsehen; von den mitgelieferten ist das <code>wlan-auth</code>. Die Probe hält ihre Läufe zurück, solange sie die Plattform nicht erreicht, und liefert sie danach nach.',
          'Sensors that support it write to the sensor log; of those that ship with the platform, that is <code>wlan-auth</code>. A probe holds its runs back while it cannot reach the platform and delivers them afterwards.',
        ),
        L(
          'Das Sensor-Log reicht 14 Tage zurück und zeigt 100 Läufe je Seite; mit {ui:sensorLogs.previous} und {ui:sensorLogs.next} blätterst du. Filter und Seite stehen in der Adresse der Seite, so dass du auch eine gefilterte Ansicht als Link weitergeben kannst.',
          'The sensor log goes back 14 days and shows 100 runs per page; {ui:sensorLogs.previous} and {ui:sensorLogs.next} turn the pages. The filters and the page are part of the page address, so you can pass on a filtered view as a link too.',
        ),
        L(
          'Die Läufe eines Sensors auf einer Probe zeigt seine eigene Seite: Im Reiter {ui:probes.tabs.sensors} der Probe öffnet ein Klick auf die Zeile des Sensors eine Zeitleiste je Netz mit Erfolgsquote, Läufen pro Stunde und letztem Fehler, umschaltbar zwischen {ui:probeSensor.window.24h} und {ui:probeSensor.window.7d}. Ein einzelner Fehler zwischen erfolgreichen Läufen ist dort nur leise markiert, ein anhaltender rot. Ein Klick auf einen Abschnitt öffnet seinen Lauf in einem Fenster, bei einem Fehler den ersten fehlgeschlagenen. Dort stehen die Stufen des Laufs mit Ergebnis und Dauer, die fehlgeschlagene aufgeklappt, darunter die {ui:runViewer.raw}. Mit {ui:runViewer.older} und {ui:runViewer.newer} oder den Pfeiltasten ← und → wechselst du zum Lauf davor oder danach in derselben Zeile, mit {ui:runViewer.failuresOnly} nur zwischen Fehlern. Der geöffnete Lauf steht in der Adresse, ein Link darauf öffnet dasselbe Fenster. Für <code>wlan-auth</code> zeigt dieselbe Seite unter {ui:probeSensor.configurations.title}, welche Konfigurationen die Probe misst; eine, die PRTG nach einer Änderung nicht mehr abfragt, ruht nach drei Intervallen und lässt sich dort sofort entfernen.',
          'The runs of a sensor on a probe are on that sensor’s own page: in the probe’s {ui:probes.tabs.sensors} tab, clicking the sensor’s row opens a timeline per network with success rate, runs per hour and the last failure, switchable between {ui:probeSensor.window.24h} and {ui:probeSensor.window.7d}. A single failure between passing runs is only marked quietly there, a lasting one in red. Clicking a slice opens its run in a dialog, the first failed one where it has a failure. It shows the stages of the run with result and duration, the failed one expanded, and the {ui:runViewer.raw} below. {ui:runViewer.older} and {ui:runViewer.newer} or the arrow keys ← and → step to the run before or after it in the same row, {ui:runViewer.failuresOnly} between failures only. The open run is part of the address, so a link to it opens the same dialog. For <code>wlan-auth</code> the same page shows under {ui:probeSensor.configurations.title} which configurations the probe measures; one PRTG no longer asks for after a change pauses after three intervals and can be removed there at once.',
        ),
        L(
          'Eine Zeile, die nicht auf die Seite gehört, etwa ein versehentlich eingerichtetes Netz, nimmst du mit dem Symbol am Ende ihrer Zeile heraus (Operator). Sie bleibt weg, bis wieder ein Lauf für sie eingeht; die Läufe selbst bleiben im Sensor-Log. Misst die Probe die Konfiguration noch, bietet die Rückfrage an, sie dort gleich mit zu entfernen.',
          'A row that does not belong on the page, such as a network set up by mistake, is taken out with the icon at the end of its row (operator). It stays away until a run for it comes in again; the runs themselves stay in the sensor log. If the probe still measures the configuration, the confirmation offers to remove it there as well.',
        ),
        L(
          'Passwörter entfernt die Probe, bevor sie einen Lauf meldet. Identität, SSID und Adressen bleiben stehen, damit du einen Lauf etwa mit dem RADIUS-Server abgleichen kannst.',
          'The probe removes passwords before it reports a run. Identity, SSID and addresses stay, so you can match a run against the RADIUS server, for example.',
        ),
      ],
      related: ['follow-jobs', 'available-sensors', 'probe-details'],
    },
    {
      slug: 'audit',
      title: L('Das Audit', 'The audit'),
      short: L(
        'Wer wann was geändert hat, und ob es geklappt hat.',
        'Who changed what and when, and whether it worked.',
      ),
      lead: L(
        'Das Audit verzeichnet jede administrative Aktion der Reihe nach: wer was wann an welchem Objekt getan hat, und mit welchem Ergebnis.',
        'The audit records every administrative action in order: who did what, when and to which object, and with what result.',
      ),
      role: 'viewer',
      keywords: L(
        'audit nachvollziehen wer änderung anmeldung abgelehnt',
        'audit trail who change sign-in denied',
      ),
      appPaths: ['/audit'],
      unnumbered: true,
      steps: [
        {
          title: L('Was verzeichnet wird', 'What is recorded'),
          text: L(
            '<p>Unter {ui:nav.audit} stehen unter anderem Anmeldungen, Änderungen an Benutzern, das Einbinden und Ausbinden von Probes, Rollouts und Varianten, eingesehene und gewechselte Passwörter, erneuerte Zertifikate, Backups und Updates. Jede Zeile nennt {ui:audit.columns.time}, {ui:audit.columns.actor}, {ui:audit.columns.action}, {ui:audit.columns.object}, {ui:audit.columns.result} und als {ui:audit.columns.source} die IP-Adresse, von der die Aktion kam.</p>',
            '<p>{ui:nav.audit} lists, among other things, sign-ins, changes to users, enrolling and unenrolling probes, rollouts and variants, passwords viewed and rotated, renewed certificates, backups and updates. Each row gives the {ui:audit.columns.time}, {ui:audit.columns.actor}, {ui:audit.columns.action}, {ui:audit.columns.object}, {ui:audit.columns.result} and, as {ui:audit.columns.source}, the IP address the action came from.</p>',
          ),
          image: 'activity-audit',
          alt: L(
            'Das Audit mit Anmeldungen, Rollouts und einer abgelehnten Aktion',
            'The audit with sign-ins, rollouts and a denied action',
          ),
        },
        {
          title: L('Das Ergebnis', 'The result'),
          text: L(
            '<p>{ui:status.audit.success} und {ui:status.audit.failure} sagen, ob die Aktion gelungen ist. {ui:status.audit.denied} heißt, dass jemand etwas versucht hat, was seine Rolle nicht darf.</p>',
            '<p>{ui:status.audit.success} and {ui:status.audit.failure} tell whether the action worked. {ui:status.audit.denied} means someone tried something their role may not do.</p>',
          ),
        },
        {
          title: L('Suchen und sortieren', 'Searching and sorting'),
          text: L(
            '<p>Das Suchfeld findet Einträge nach Benutzer, Aktion und Objekt. Eine Aktion findest du auch über ihren technischen Namen, den die Spalte zeigt, wenn du mit der Maus darauf zeigst. Ein Klick auf eine Spaltenüberschrift sortiert nach Zeit, Benutzer, Aktion oder Ergebnis.</p>',
            '<p>The search field finds entries by user, action and object. You can also find an action by its technical name, which the column shows when you hover over it. Clicking a column heading sorts by time, user, action or result.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Die Liste zeigt die letzten hundert Einträge; Suche und Sortierung wirken auf diese.',
          'The list shows the last hundred entries; searching and sorting work on those.',
        ),
        L(
          'Einträge im Audit lassen sich weder ändern noch löschen, auch nicht von einem Administrator.',
          'Audit entries cannot be changed or deleted, not even by an administrator.',
        ),
        L(
          'Die jüngsten Einträge stehen auch auf der <a href="@dashboard">Übersicht</a>.',
          'The most recent entries are also on the <a href="@dashboard">dashboard</a>.',
        ),
      ],
      related: ['roles', 'follow-jobs'],
    },
  ],
}
