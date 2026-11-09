import { L } from '../lib/text.mjs'

export const probes = {
  id: 'probes',
  illustration: 'probes',
  illustrationAlt: L(
    'Eine Technikerin schließt an einem Standort eine kleine Probe an; eine Leitung führt zum zentralen Server.',
    'A technician connects a small probe at a site; a line leads to the central server.',
  ),
  title: L('Probes', 'Probes'),
  who: L(
    'Für Operatoren und Administratoren, die Probes betreuen: Zustand lesen, Probes einbinden, mit PRTG verknüpfen und wieder ausbinden.',
    'For operators and administrators looking after probes: reading their state, enrolling them, linking them to PRTG and unenrolling them again.',
  ),
  articles: [
    {
      slug: 'probe-status',
      title: L('Die Liste der Probes lesen', 'Reading the probe list'),
      short: L(
        'Welche Probe in Ordnung ist, welche nicht, und was du für mehrere Probes auf einmal tun kannst.',
        'Which probe is healthy, which is not, and what you can do for several probes at once.',
      ),
      lead: L(
        'Unter {ui:nav.probes} steht jede eingebundene Probe in einer Zeile; Probes, um die du dich kümmern musst, stehen oben. Von hier öffnest du eine Probe oder startest eine Aktion für mehrere Probes gleichzeitig.',
        'Under {ui:nav.probes} every enrolled probe has one row, the ones that need action first. From here you open a probe or start an action for several probes at once.',
      ),
      role: 'viewer',
      illustration: 'probes',
      illustrationAlt: L(
        'Eine Technikerin schließt an einem Standort eine kleine Probe an; eine Leitung führt zum zentralen Server.',
        'A technician connects a small probe at a site; a line leads to the central server.',
      ),
      keywords: L(
        'liste status zustand filter beeinträchtigt nicht erreichbar abweichungen helper mehrere auswahl',
        'list status state filter degraded unreachable deviations helper several selection bulk',
      ),
      appPaths: ['/probes'],
      body: L(
        `<h2>Spalten</h2>
<table>
<thead><tr><th>Spalte</th><th>Was dort steht</th></tr></thead>
<tbody>
<tr><td>{ui:probes.columns.name}</td><td>Der Name der Probe, darunter ihr NATS-Konto, wenn es anders heißt. {ui:probes.routes.tunnel} heißt: Management erreicht die Probe über den Tunnel.</td></tr>
<tr><td>{ui:probes.columns.host}</td><td>Die Adresse, unter der die Probe zuletzt erreicht wurde.</td></tr>
<tr><td>{ui:probes.columns.status}</td><td>Der Zustand und Kennzeichen wie {ui:probes.prtg.missingBadge}, {ui:probes.helperOutdated} für einen alten Probe-Helper oder {ui:status.job.running}, solange ein Job auf der Probe arbeitet.</td></tr>
<tr><td>{ui:probes.columns.version}</td><td>Die installierte Version von <code>prtgmpprobe</code>.</td></tr>
<tr><td>{ui:probes.columns.sensors}</td><td>Wie viele Sensoren auf der Probe ausgerollt sind.</td></tr>
<tr><td>{ui:probes.columns.deviations}</td><td>Wie oft die Probe vom gewünschten Zustand abweicht.</td></tr>
<tr><td>{ui:probes.columns.observed}</td><td>Wann die Plattform die Probe zuletzt abgefragt hat. Ein grauer Wert ist älter als ein paar Minuten.</td></tr>
</tbody>
</table>
<h2>Zustände</h2>
<table>
<thead><tr><th>Zustand</th><th>Bedeutung</th></tr></thead>
<tbody>
<tr><td>{ui:status.probe.healthy}</td><td>Dienst läuft, CA stimmt, NATS ist verbunden, keine Abweichung braucht Aufmerksamkeit.</td></tr>
<tr><td>{ui:status.probe.degraded}</td><td>Die Probe antwortet, aber etwas stimmt nicht: Dienst gestoppt, CA fehlt oder weicht ab, keine NATS-Verbindung oder Abweichungen.</td></tr>
<tr><td>{ui:status.probe.unreachable}</td><td>Die Verwaltung bekommt keine Antwort von der Probe. Ob sie trotzdem noch misst, zeigt ihre Seite.</td></tr>
<tr><td>{ui:status.probe.pending}, {ui:status.probe.enrolled}</td><td>Die Einbindung ist noch nicht fertig: Die Probe hat noch nicht geantwortet oder meldet noch keine Probe-Id.</td></tr>
</tbody>
</table>
<p>{ui:probes.prtg.missingBadge} steht neben einer Probe, die hier läuft, aber noch nicht als in PRTG eingetragen markiert ist. Solange das so ist, misst sie für PRTG womöglich nichts, obwohl sie hier grün ist.</p>`,
        `<h2>Columns</h2>
<table>
<thead><tr><th>Column</th><th>What it shows</th></tr></thead>
<tbody>
<tr><td>{ui:probes.columns.name}</td><td>The probe's name, with its NATS account below when that is different. {ui:probes.routes.tunnel} means management reaches the probe via the tunnel.</td></tr>
<tr><td>{ui:probes.columns.host}</td><td>The address the probe was last reached at.</td></tr>
<tr><td>{ui:probes.columns.status}</td><td>The state, plus marks such as {ui:probes.prtg.missingBadge}, {ui:probes.helperOutdated} for an old probe helper, or {ui:status.job.running} while a job is working on the probe.</td></tr>
<tr><td>{ui:probes.columns.version}</td><td>The installed version of <code>prtgmpprobe</code>.</td></tr>
<tr><td>{ui:probes.columns.sensors}</td><td>How many sensors are deployed on the probe.</td></tr>
<tr><td>{ui:probes.columns.deviations}</td><td>How many ways the probe differs from its desired state.</td></tr>
<tr><td>{ui:probes.columns.observed}</td><td>When the platform last asked the probe. A grey value is older than a few minutes.</td></tr>
</tbody>
</table>
<h2>States</h2>
<table>
<thead><tr><th>State</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td>{ui:status.probe.healthy}</td><td>The service runs, the CA matches, NATS is connected, and no deviation needs attention.</td></tr>
<tr><td>{ui:status.probe.degraded}</td><td>The probe answers, but something is wrong: the service is stopped, the CA is missing or different, NATS is not connected, or there are deviations.</td></tr>
<tr><td>{ui:status.probe.unreachable}</td><td>Management gets no answer from the probe. Its page shows whether it is still measuring.</td></tr>
<tr><td>{ui:status.probe.pending}, {ui:status.probe.enrolled}</td><td>The enrollment has not finished: the probe has not answered yet, or reports no probe id yet.</td></tr>
</tbody>
</table>
<p>{ui:probes.prtg.missingBadge} marks a probe that runs here but has not been marked as registered in PRTG. Until it is, it may measure nothing for PRTG even though it is green here.</p>`,
      ),
      steps: [
        {
          title: L('Filtern und suchen', 'Filtering and searching'),
          text: L(
            '<p>Die Knöpfe über der Tabelle filtern nach Zustand, {ui:probes.filters.helperOutdated} oder {ui:probes.filters.deviations}; die Zahl daneben zählt die ganze Liste. Das Suchfeld findet eine Probe über ihren Namen, ihr NATS-Konto oder ihre Adresse. Filter und Suche stehen in der Adresse der Seite, du kannst die gefilterte Liste also als Link weitergeben.</p>',
            '<p>The buttons above the table filter by state, {ui:probes.filters.helperOutdated} or {ui:probes.filters.deviations}; the number beside each counts the whole list. The search field finds a probe by its name, its NATS account or its address. Filters and search are part of the page address, so you can pass the filtered list on as a link.</p>',
          ),
          image: 'probes-list',
          alt: L(
            'Die Liste der Probes mit Zustandsfiltern und einer ausgewählten Probe',
            'The probe list with state filters and one selected probe',
          ),
        },
        {
          title: L('Mehrere Probes auf einmal', 'Several probes at once'),
          text: L(
            '<p>Wähle Probes über die Kästchen am Zeilenanfang aus. Über der Tabelle erscheinen dann {ui:probes.refreshState}, {ui:sensors.deploy}, {ui:probes.updateHelper}, {ui:probes.configure} und {ui:probes.fixDeviations}, unter {ui:common.moreActions} außerdem {ui:probes.validate} und {ui:probes.installCa}. Eine Aktion wird nur angeboten, wenn sie auf mindestens eine ausgewählte Probe zutrifft.</p>',
            '<p>Select probes with the boxes at the start of each row. Above the table you then get {ui:probes.refreshState}, {ui:sensors.deploy}, {ui:probes.updateHelper}, {ui:probes.configure} and {ui:probes.fixDeviations}, and under {ui:common.moreActions} also {ui:probes.validate} and {ui:probes.installCa}. An action is only offered when it applies to at least one selected probe.</p>',
          ),
        },
        {
          title: L('Bestätigen und verfolgen', 'Confirming and following'),
          text: L(
            '<p>Bei mehr als einer Probe zeigt ein Dialog, welche Probes die Aktion trifft und welche außen vor bleiben; mit {ui:common.confirm} startet sie. Alles läuft als ein Job, eine Probe nach der anderen, und die Seite des Jobs öffnet sich.</p>',
            '<p>For more than one probe, a dialog shows which probes the action affects and which are left out; {ui:common.confirm} starts it. Everything runs as one job, one probe after another, and the job page opens.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Als Betrachter kannst du den Zustand aktualisieren und eine Prüfung starten. Alles andere in der Auswahl braucht mindestens die Rolle Operator.',
          'As a viewer you can refresh the state and run a check. Everything else in the selection needs at least the operator role.',
        ),
        L(
          'Gibt es noch offene Einladungen, steht oben neben {ui:probes.enroll.action} ein Hinweis darauf. Er führt zum Assistenten, in dem du sie zurückziehen kannst.',
          'If invitations are still open, a note next to {ui:probes.enroll.action} at the top says so. It leads to the wizard, where you can revoke them.',
        ),
        L(
          'Kann die Plattform die Namen gerade nicht aus PRTG lesen, sagt eine Zeile über der Tabelle warum. Die Probes stehen dann unter dem Namen bei der Einbindung in der Liste.',
          'If the platform cannot read the names from PRTG right now, a line above the table says why. The probes are then listed under their enrollment name.',
        ),
      ],
      related: ['probe-details', 'enroll-a-probe', 'deploy-sensors', 'follow-jobs'],
    },
    {
      slug: 'enroll-a-probe',
      title: L('Eine Probe einbinden', 'Enrolling a probe'),
      short: L(
        'Eine Einladung erzeugen, einen Befehl auf der Probe ausführen und die Probe in PRTG genehmigen.',
        'Create an invitation, run one command on the probe and approve the probe in PRTG.',
      ),
      lead: L(
        'Die Probe bindet sich selbst ein: Du erzeugst hier einen Befehl mit einer einmaligen Einladung und führst ihn auf dem Linux-System aus. Den Rest übernimmt die Plattform, bis auf zwei Schritte in PRTG.',
        'The probe enrolls itself: you create a command carrying a single-use invitation here and run it on the Linux system. The platform does the rest, except for two steps in PRTG.',
      ),
      role: 'administrator',
      keywords: L(
        'hinzufügen neu einladung befehl access key genehmigen wireguard',
        'add new invitation command access key approve wireguard',
      ),
      appPaths: ['/probes/new'],
      steps: [
        {
          title: L('Die Probe beschreiben', 'Describe the probe'),
          text: L(
            '<p>Öffne {ui:nav.probes} und wähle {ui:probes.enroll.action}. Trag bei {ui:probes.enroll.fields.account} einen Namen wie <code>probe-berlin-01</code> ein: Unter ihm erscheint die Probe hier und in PRTG, und so heißt auch ihr NATS-Konto.</p>',
            '<p>Open {ui:nav.probes} and choose {ui:probes.enroll.action}. Enter a name such as <code>probe-berlin-01</code> under {ui:probes.enroll.fields.account}: the probe appears under it here and in PRTG, and its NATS account gets the same name.</p>',
          ),
        },
        {
          title: L('Weitere Optionen', 'More options'),
          text: L(
            '<p>Unter {ui:probes.enroll.moreOptions} stehen {ui:probes.enroll.fields.probeName}, {ui:probes.enroll.fields.ttl} (15 Minuten bis 24 Stunden) und {ui:probes.enroll.fields.installPackage}. Ist das Overlay eingeschaltet, gibt es zusätzlich {ui:probes.enroll.fields.overlayBootstrap} für einen Standort ohne eigenes VPN. Mit {ui:probes.enroll.step1.submit} entsteht die Einladung; auf der Probe passiert dabei noch nichts.</p>',
            '<p>{ui:probes.enroll.moreOptions} holds {ui:probes.enroll.fields.probeName}, {ui:probes.enroll.fields.ttl} (15 minutes up to 24 hours) and {ui:probes.enroll.fields.installPackage}. When the overlay is on, there is also {ui:probes.enroll.fields.overlayBootstrap} for a site without its own VPN. {ui:probes.enroll.step1.submit} creates the invitation; nothing happens on the probe yet.</p>',
          ),
        },
        {
          title: L('Den Befehl auf der Probe ausführen', 'Run the command on the probe'),
          text: L(
            '<p>Kopiere den Befehl und führe ihn auf der Probe als Benutzer mit sudo-Recht aus. Er prüft die CA gegen den angezeigten {ui:probes.enroll.step2.caFingerprint}, richtet den Management-Zugang ein und meldet sich zurück. Hast du {ui:probes.enroll.fields.overlayBootstrap} gewählt, stehen davor zwei weitere Befehle, die du der Reihe nach ausführst: {ui:probes.enroll.step2.steps.install_wireguard.title} und {ui:probes.enroll.step2.steps.build_tunnel.title}.</p>',
            '<p>Copy the command and run it on the probe as a user who can sudo. It checks the CA against the {ui:probes.enroll.step2.caFingerprint} shown, sets up the management access and reports back. If you chose {ui:probes.enroll.fields.overlayBootstrap}, two more commands come first, run in order: {ui:probes.enroll.step2.steps.install_wireguard.title} and {ui:probes.enroll.step2.steps.build_tunnel.title}.</p>',
          ),
          image: 'probes-enroll-command',
          alt: L(
            'Der zweite Schritt des Assistenten mit dem Befehl, dem CA-Fingerabdruck und der Restlaufzeit',
            'The second step of the wizard with the command, the CA fingerprint and the remaining validity',
          ),
        },
        {
          title: L('Zusehen, wie die Plattform übernimmt', 'Watch the platform take over'),
          text: L(
            '<p>Sobald sich die Probe meldet, geht die Seite von selbst zu {ui:probes.enroll.step3.title} weiter und zeigt den Job mit seinem Protokoll. Steht dort {ui:probes.enroll.step3.doneTitle}, verbindet sich die Probe mit NATS; {ui:probes.enroll.step3.toProbe} öffnet ihre Seite.</p>',
            '<p>As soon as the probe reports in, the page moves on to {ui:probes.enroll.step3.title} by itself and shows the job with its log. When it says {ui:probes.enroll.step3.doneTitle}, the probe connects to NATS; {ui:probes.enroll.step3.toProbe} opens its page.</p>',
          ),
        },
        {
          title: L('Den Access Key in PRTG eintragen', 'Add the access key in PRTG'),
          text: L(
            '<p>Auf der Seite der Probe zeigt {ui:common.reveal} im Reiter {ui:probes.tabs.overview} den {ui:probes.accessKey}; das wird im Audit festgehalten. Trag ihn im PRTG-Core unter <i>Setup › System Administration › Probes › Probe Connection Settings › Access Keys</i> als neue Zeile ein, ohne vorhandene Keys zu ersetzen.</p>',
            '<p>On the probe\'s page, {ui:common.reveal} on the {ui:probes.tabs.overview} tab shows the {ui:probes.accessKey}; this is recorded in the audit. In the PRTG core, add it as a new line under <i>Setup › System Administration › Probes › Probe Connection Settings › Access Keys</i>, without replacing existing keys.</p>',
          ),
        },
        {
          title: L('Die Probe in PRTG genehmigen', 'Approve the probe in PRTG'),
          text: L(
            '<p>Genehmige die Verbindungsanfrage der Probe in PRTG mit <i>Approve</i>, nicht mit <i>Approve and auto-discover</i>. Markiere die Probe danach hier mit {ui:probes.accessKeyMarkDone} im Dialog des Access Keys oder mit {ui:probes.prtg.markDone} auf ihrer Seite.</p>',
            '<p>Approve the probe\'s connection request in PRTG with <i>Approve</i>, not with <i>Approve and auto-discover</i>. Then mark the probe here with {ui:probes.accessKeyMarkDone} in the access key dialog, or with {ui:probes.prtg.markDone} on its page.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Behandle den Befehl wie ein Passwort: Bis er benutzt ist oder abläuft, kann jeder damit einen Host unter diesem Konto einbinden. Mit {ui:probes.enroll.step2.revoke} machst du ihn sofort unbrauchbar.',
          'Treat the command like a password: until it is used or expires, anyone can enroll a host under this account with it. {ui:probes.enroll.step2.revoke} makes it useless at once.',
        ),
        L(
          'Der Befehl ist nur beim Erzeugen sichtbar. Offene Einladungen stehen oben im Assistenten unter {ui:probes.enroll.open.title}; dort kannst du sie mit {ui:probes.enroll.open.revoke} zurückziehen oder mit {ui:probes.enroll.open.reissue} durch einen neuen Befehl ersetzen.',
          'The command is only visible when it is created. Open invitations are listed at the top of the wizard under {ui:probes.enroll.open.title}; there you can {ui:probes.enroll.open.revoke} them or replace them with a new command using {ui:probes.enroll.open.reissue}.',
        ),
        L(
          'Gehört der Name zu einer schon eingebundenen Probe, bindest du sie erneut ein. Probe-Id und Access Key bleiben dabei erhalten, PRTG sieht dieselbe Probe.',
          'If the name belongs to a probe that is already enrolled, you enroll it again. Its probe id and access key stay the same, and PRTG sees the same probe.',
        ),
        L(
          'Bricht die Einbindung ab, zeigt das Protokoll, wo. War die Ursache vorübergehend, wiederhole den Job; muss der Befehl erneut laufen, erzeuge eine neue Einladung.',
          'If the enrollment stops, the log shows where. If the cause was temporary, retry the job; if the command has to run again, create a new invitation.',
        ),
        L(
          'Kommt die Einbindung über die Oberfläche nicht in Frage, geht sie auch <a href="@command-line">auf der Kommandozeile</a> oder <a href="@manual-probe-install">von Hand</a>.',
          'If enrolling from the interface is not an option, it also works <a href="@command-line">on the command line</a> or <a href="@manual-probe-install">by hand</a>.',
        ),
      ],
      related: ['link-to-prtg', 'probe-details', 'deploy-sensors', 'overlay'],
    },
    {
      slug: 'probe-details',
      title: L('Die Seite einer Probe', 'A probe\'s page'),
      short: L(
        'Wie die Plattform die Probe erreicht, was auf ihr läuft, wo sie abweicht, und welche Aktionen es gibt.',
        'How the platform reaches the probe, what runs on it, where it differs, and which actions there are.',
      ),
      lead: L(
        'Ein Klick auf eine Zeile in {ui:nav.probes} öffnet die Seite der Probe. Oben stehen ihr Name, ihr Zustand und die Aktionen, darunter vier Reiter.',
        'Clicking a row in {ui:nav.probes} opens the probe\'s page. At the top are its name, its state and the actions, with four tabs below.',
      ),
      role: 'viewer',
      keywords: L(
        'detail tunnel standortadresse rückkanal abweichungen diagnose helper konfiguration ca access key',
        'detail tunnel site address return channel deviations diagnostics helper configuration ca access key',
      ),
      appPaths: ['/probes/*'],
      unnumbered: true,
      steps: [
        {
          title: L('Kopf und Aktionen', 'Header and actions'),
          text: L(
            `<p>Unter dem Namen steht das NATS-Konto, wenn es anders heißt. Die Aktionen:</p>
<table>
<thead><tr><th>Aktion</th><th>Was sie tut</th><th>Rolle</th></tr></thead>
<tbody>
<tr><td>{ui:probes.refreshState}</td><td>fragt die Probe jetzt ab</td><td>Betrachter</td></tr>
<tr><td>{ui:probes.validate}</td><td>sammelt alles, was die Probe über sich sagen kann, in einem Job</td><td>Betrachter</td></tr>
<tr><td>{ui:probes.configure}</td><td>schreibt die Konfiguration der Probe neu und aktiviert sie</td><td>Operator</td></tr>
<tr><td>{ui:probes.installCa}</td><td>installiert die CA des NATS-Servers erneut</td><td>Operator</td></tr>
<tr><td>{ui:probes.updateHelper}</td><td>ersetzt den Probe-Helper durch die Version dieser Plattform</td><td>Operator</td></tr>
<tr><td>{ui:probes.unenroll}</td><td>siehe <a href="@unenroll-a-probe">Eine Probe ausbinden</a></td><td>Administrator</td></tr>
</tbody>
</table>
<p>Oben stehen immer {ui:probes.refreshState} und {ui:probes.configure}, alles andere unter {ui:common.moreActions}. Ist der Helper veraltet, steht {ui:probes.updateHelper} zusätzlich im Hinweis darüber. Alles außer {ui:probes.refreshState} läuft als Job, dessen Seite sich danach öffnet.</p>`,
            `<p>The NATS account is shown below the name when it is different. The actions:</p>
<table>
<thead><tr><th>Action</th><th>What it does</th><th>Role</th></tr></thead>
<tbody>
<tr><td>{ui:probes.refreshState}</td><td>asks the probe now</td><td>Viewer</td></tr>
<tr><td>{ui:probes.validate}</td><td>collects everything the probe can say about itself in one job</td><td>Viewer</td></tr>
<tr><td>{ui:probes.configure}</td><td>writes the probe's configuration again and activates it</td><td>Operator</td></tr>
<tr><td>{ui:probes.installCa}</td><td>installs the NATS server's CA again</td><td>Operator</td></tr>
<tr><td>{ui:probes.updateHelper}</td><td>replaces the probe helper with this platform's version</td><td>Operator</td></tr>
<tr><td>{ui:probes.unenroll}</td><td>see <a href="@unenroll-a-probe">Unenrolling a probe</a></td><td>Administrator</td></tr>
</tbody>
</table>
<p>{ui:probes.refreshState} and {ui:probes.configure} are always at the top, everything else is under {ui:common.moreActions}. When the helper is outdated, {ui:probes.updateHelper} also sits in the notice about it. Everything except {ui:probes.refreshState} runs as a job, whose page opens afterwards.</p>`,
          ),
        },
        {
          title: L('Hinweise über den Reitern', 'Notices above the tabs'),
          text: L(
            '<p>{ui:probes.fault.unreachableTitle} heißt, die Probe ist ganz weg. {ui:probes.fault.managementOnlyTitle} heißt, sie ist über NATS noch verbunden und misst weiter. {ui:probes.fault.datedTitle} steht über Werten, die älter als ein paar Minuten sind, {ui:probes.helperOutdatedTitle} vor dem nächsten Job, der daran scheitern würde. {ui:probes.prtg.bannerTitle} bleibt stehen, bis jemand {ui:probes.prtg.markDone} wählt; {ui:probes.prtg.markUndone} nimmt das zurück.</p>',
            '<p>{ui:probes.fault.unreachableTitle} means the probe is gone entirely. {ui:probes.fault.managementOnlyTitle} means it is still connected over NATS and keeps measuring. {ui:probes.fault.datedTitle} sits above values older than a few minutes, {ui:probes.helperOutdatedTitle} ahead of the next job that would fail on it. {ui:probes.prtg.bannerTitle} stays until someone chooses {ui:probes.prtg.markDone}; {ui:probes.prtg.markUndone} takes that back.</p>',
          ),
        },
        {
          title: L('Übersicht: Identität und Wege', 'Overview: identity and routes'),
          text: L(
            `<p>Die Karte {ui:probes.identity} sagt, wer die Probe ist und wie sie erreicht wird:</p>
<ul>
<li>{ui:probes.natsUser}: das Konto, mit dem die Probe sich mit NATS verbindet. Als Administrator zeigst du mit {ui:probes.account.showPassword} sein Passwort an oder wechselst es mit {ui:credentials.rotate} unter {ui:common.moreActions}.</li>
<li>{ui:probes.prtgName.label}: das PRTG-Objekt, mit dem die Probe <a href="@link-to-prtg">verknüpft</a> ist.</li>
<li>{ui:probes.probeId}: die feste Kennung der Probe. Sie ändert sich nie, auch nicht bei einer erneuten Einbindung.</li>
<li>{ui:probes.routes.management}: {ui:probes.routes.tunnel} oder {ui:probes.routes.site}, mit Adresse und Zeitpunkt der letzten Antwort. Antwortet der Tunnel, steht die Standortadresse als Rückfall darunter.</li>
<li>{ui:probes.routes.nats}: {ui:probes.routes.tunnel} oder {ui:probes.routes.direct}.</li>
<li>{ui:probes.overlay}: der Modus der Probe im Overlay, falls es eingeschaltet ist; {ui:probes.overlayAdd} und {ui:infrastructure.overlay.actions.mode} brauchen die Rolle Operator.</li>
<li>{ui:probes.accessKey}: {ui:common.reveal} zeigt den Key für PRTG und hält das im Audit fest.</li>
</ul>`,
            `<p>The {ui:probes.identity} card says who the probe is and how it is reached:</p>
<ul>
<li>{ui:probes.natsUser}: the account the probe connects to NATS with. As an administrator, {ui:probes.account.showPassword} shows its password and {ui:credentials.rotate} under {ui:common.moreActions} changes it.</li>
<li>{ui:probes.prtgName.label}: the PRTG object the probe is <a href="@link-to-prtg">linked</a> to.</li>
<li>{ui:probes.probeId}: the probe's fixed identifier. It never changes, not even when the probe is enrolled again.</li>
<li>{ui:probes.routes.management}: {ui:probes.routes.tunnel} or {ui:probes.routes.site}, with the address and the time of the last answer. When the tunnel answers, the site address is shown below as the fallback.</li>
<li>{ui:probes.routes.nats}: {ui:probes.routes.tunnel} or {ui:probes.routes.direct}.</li>
<li>{ui:probes.overlay}: the probe's mode on the overlay, if the overlay is on; {ui:probes.overlayAdd} and {ui:infrastructure.overlay.actions.mode} need the operator role.</li>
<li>{ui:probes.accessKey}: {ui:common.reveal} shows the key for PRTG and records that in the audit.</li>
</ul>`,
          ),
          image: 'probes-detail-overview',
          alt: L(
            'Der Reiter Übersicht mit den Karten Identität und Tatsächlicher Zustand',
            'The Overview tab with the Identity and Actual state cards',
          ),
        },
        {
          title: L('Übersicht: Zustand und Rückkanal', 'Overview: state and return channel'),
          text: L(
            '<p>{ui:probes.actualState} zeigt, was die Probe zuletzt gemeldet hat: {ui:probes.columns.service}, Paket, {ui:probes.columns.nats}, CA und {ui:probes.helper}. Der {ui:probes.returnChannel.label} zeigt, wann die Probe zuletzt Sensor-Logs geliefert hat, wie voll ihr Puffer ist und ob es Lücken gab. Ist er {ui:probes.returnChannel.silent}, sein Zertifikat abgelaufen oder gab es in den letzten 24 Stunden eine Lücke, startet {ui:probes.returnChannel.repair} (Operator) die Reparatur.</p>',
            '<p>{ui:probes.actualState} shows what the probe last reported: {ui:probes.columns.service}, package, {ui:probes.columns.nats}, CA and {ui:probes.helper}. The {ui:probes.returnChannel.label} shows when the probe last delivered sensor logs, how full its buffer is and whether there were gaps. When it is {ui:probes.returnChannel.silent}, its certificate has expired, or there was a gap in the last 24 hours, {ui:probes.returnChannel.repair} (operator) starts the repair.</p>',
          ),
        },
        {
          title: L('Sensoren', 'Sensors'),
          text: L(
            '<p>Der Reiter {ui:probes.tabs.sensors} listet jeden Sensor mit gewünschter und installierter Version und unter {ui:probeSensor.last24h} einem kleinen Balken mit der Erfolgsquote; ein Klick auf die Zeile öffnet die Seite des Sensors mit seiner Zeitleiste. {ui:probes.deploySensor} und {ui:sensors.updateOnProbe} rollen aus, {ui:sensors.remove} unter {ui:common.moreActions} nimmt einen Sensor von der Probe (beides Operator). Darunter stehen die {ui:probes.variants.title}, die {ui:probes.iperf.title} und, für Funk-Sensoren, die {ui:probes.interfaces.title} mit {ui:probes.interfaces.reserve} und {ui:probes.interfaces.release}.</p>',
            '<p>The {ui:probes.tabs.sensors} tab lists every sensor with its desired and installed version and, under {ui:probeSensor.last24h}, a small bar with its success rate; clicking the row opens the sensor’s page with its timeline. {ui:probes.deploySensor} and {ui:sensors.updateOnProbe} deploy, {ui:sensors.remove} under {ui:common.moreActions} takes a sensor off the probe (both operator). Below are the {ui:probes.variants.title}, the {ui:probes.iperf.title} and, for wireless sensors, the {ui:probes.interfaces.title} with {ui:probes.interfaces.reserve} and {ui:probes.interfaces.release}.</p>',
          ),
        },
        {
          title: L('Übersicht: Abweichungen', 'Overview: deviations'),
          text: L(
            '<p>Weicht die Probe vom gewünschten Zustand ab, steht oben in der {ui:probes.tabs.overview} der Abschnitt {ui:probes.deviationsTitle}. Er nennt jede Abweichung und jeweils, was dagegen hilft. Passt alles, fehlt der Abschnitt. {ui:probes.fixDeviations} zeigt zuerst unter {ui:probes.planPreview}, was passieren würde; erst {ui:probes.executePlan} startet den Job (Operator). Grau markierte Abweichungen sind nur zur Information und verschwinden nicht von selbst.</p>',
            '<p>When the probe differs from its desired state, the {ui:probes.deviationsTitle} section sits at the top of the {ui:probes.tabs.overview}. It names each deviation and what fixes it. When everything matches, the section is not there. {ui:probes.fixDeviations} first shows what would happen under {ui:probes.planPreview}; only {ui:probes.executePlan} starts the job (operator). Deviations marked in grey are for information only and do not clear by themselves.</p>',
          ),
        },
        {
          title: L('Diagnose', 'Diagnostics'),
          text: L(
            '<p>Der Reiter {ui:probes.tabs.diagnostics} zeigt Hostname, Pfad der Konfigurationsdatei, den vollständigen CA-Fingerabdruck und unter {ui:probes.diag.errorDetails} die Meldung, mit der die letzte Abfrage gescheitert ist.</p>',
            '<p>The {ui:probes.tabs.diagnostics} tab shows the host name, the path of the configuration file, the full CA fingerprint and, under {ui:probes.diag.errorDetails}, the message the last query failed with.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Der gewählte Reiter steht in der Adresse der Seite. Nach einem Job landest du wieder dort, wo du warst, und {ui:probes.title} oben links führt zur Liste mit denselben Filtern zurück.',
          'The selected tab is part of the page address. After a job you land back where you were, and {ui:probes.title} at the top left returns to the list with the same filters.',
        ),
      ],
      related: ['probe-status', 'link-to-prtg', 'deploy-sensors', 'sensor-logs', 'overlay'],
    },
    {
      slug: 'link-to-prtg',
      title: L('Eine Probe mit PRTG verknüpfen', 'Linking a probe to PRTG'),
      short: L(
        'Die Probe mit ihrem Objekt in PRTG verbinden, damit sie hier so heißt wie dort.',
        'Connect the probe to its object in PRTG, so it is called the same here as there.',
      ),
      lead: L(
        'Eine Probe kann in PRTG jederzeit umbenannt werden, ohne dass die Plattform davon erfährt. Verknüpfst du sie einmal mit ihrem PRTG-Objekt, liest die Plattform den Namen in PRTG über dessen Objekt-Id und zeigt die Probe darunter an.',
        'A probe can be renamed in PRTG at any time without the platform hearing about it. Once you link it to its PRTG object, the platform reads the name in PRTG by that object id and shows the probe under it.',
      ),
      role: 'operator',
      keywords: L(
        'prtg name objekt-id umbenennen verknüpfung anzeigename',
        'prtg name object id rename link display name',
      ),
      body: L(
        `<h2>Welcher Name wo steht</h2>
<table>
<thead><tr><th>Begriff</th><th>Was er ist</th></tr></thead>
<tbody>
<tr><td>Probe-Id</td><td>Die Kennung der Probe. Sie ändert sich nie und identifiziert die Probe.</td></tr>
<tr><td>NATS-Konto</td><td>Der Name, den du bei der Einbindung eingetragen hast. Unter ihm kennen Shell, Jobs und NATS die Probe.</td></tr>
<tr><td>Name bei der Einbindung</td><td>Der Name, mit dem sich die Probe bei PRTG zum ersten Mal gemeldet hat. Er ist gleich dem NATS-Konto, außer bei der Einbindung war {ui:probes.enroll.fields.probeName} ausgefüllt.</td></tr>
<tr><td>Name in PRTG</td><td>Wie PRTG die Probe heute nennt, gelesen über die Verknüpfung.</td></tr>
</tbody>
</table>
<p>Die Oberfläche zeigt eine Probe immer unter genau einem Namen: verknüpft unter ihrem Namen in PRTG, sonst unter dem Namen bei der Einbindung.</p>`,
        `<h2>Which name appears where</h2>
<table>
<thead><tr><th>Term</th><th>What it is</th></tr></thead>
<tbody>
<tr><td>Probe id</td><td>The probe's identifier. It never changes and identifies the probe.</td></tr>
<tr><td>NATS account</td><td>The name you entered at enrollment. Shell, jobs and NATS know the probe by it.</td></tr>
<tr><td>Enrollment name</td><td>The name the probe first announced to PRTG. It equals the NATS account unless {ui:probes.enroll.fields.probeName} was filled in at enrollment.</td></tr>
<tr><td>Name in PRTG</td><td>What PRTG calls the probe today, read through the link.</td></tr>
</tbody>
</table>
<p>The interface always shows a probe under exactly one name: under its name in PRTG once it is linked, otherwise under its enrollment name.</p>`,
      ),
      steps: [
        {
          text: L(
            '<p>Öffne die Probe. Im Reiter {ui:probes.tabs.overview} wählst du in der Zeile {ui:probes.prtgName.label} den Knopf {ui:probes.prtgName.link}.</p>',
            '<p>Open the probe. On the {ui:probes.tabs.overview} tab, choose {ui:probes.prtgName.link} in the {ui:probes.prtgName.label} row.</p>',
          ),
        },
        {
          text: L(
            '<p>Der Dialog {ui:probes.prtgName.dialogTitle} listet die Probes, die PRTG führt und die noch mit keiner anderen Probe hier verknüpft sind. Eine Probe mit {ui:probes.prtgName.suggested} ist vorgewählt; prüf trotzdem, ob es die richtige ist.</p>',
            '<p>The {ui:probes.prtgName.dialogTitle} dialog lists the probes PRTG has that are not yet linked to another probe here. A probe marked {ui:probes.prtgName.suggested} is preselected; check that it is the right one anyway.</p>',
          ),
          image: 'probes-link-dialog',
          alt: L(
            'Der Dialog zum Verknüpfen mit einer vorgewählten Probe aus PRTG',
            'The link dialog with a preselected probe from PRTG',
          ),
          narrow: true,
        },
        {
          text: L(
            '<p>Steht die Probe nicht in der Liste, trag bei {ui:probes.prtgName.manual} die Zahl ein: Öffne die Probe in PRTG, ihre Adresse endet auf <code>id=</code> und diese Zahl. Bestätige mit {ui:probes.prtgName.link}.</p>',
            '<p>If the probe is not in the list, enter the number under {ui:probes.prtgName.manual}: open the probe in PRTG, its address ends in <code>id=</code> followed by that number. Confirm with {ui:probes.prtgName.link}.</p>',
          ),
        },
      ],
      after: L(
        '<p>Die Zeile {ui:probes.prtgName.label} zeigt danach die Objekt-Id, und Seitentitel und Liste zeigen den Namen in PRTG. Benennt jemand die Probe in PRTG um, übernimmt die Plattform das beim nächsten Lesen.</p>',
        '<p>The {ui:probes.prtgName.label} row then shows the object id, and the page title and the list show the name in PRTG. If someone renames the probe in PRTG, the platform picks that up the next time it reads.</p>',
      ),
      notes: [
        L(
          'Die Verknüpfung braucht einen <a href="@connect-prtg-core">angebundenen PRTG-Core</a> mit Adresse und API-Token. Ohne ihn fehlt der Knopf {ui:probes.prtgName.link}.',
          'The link needs a <a href="@connect-prtg-core">connected PRTG core</a> with a URL and an API token. Without it, the {ui:probes.prtgName.link} button is missing.',
        ),
        L(
          'In PRTG ändert die Verknüpfung nichts. Die Plattform liest dort nur.',
          'The link changes nothing in PRTG. The platform only reads there.',
        ),
        L(
          'Mit {ui:probes.prtgName.relink} wählst du ein anderes Objekt, mit {ui:probes.prtgName.unlink} unter {ui:common.moreActions} löst du die Verknüpfung. Danach heißt die Probe hier wieder wie bei der Einbindung.',
          '{ui:probes.prtgName.relink} picks a different object, {ui:probes.prtgName.unlink} under {ui:common.moreActions} removes the link. The probe is then shown under its enrollment name again.',
        ),
        L(
          'Ist PRTG nicht erreichbar oder lehnt das Token ab, sagt die Zeile {ui:probes.prtgName.label} das, und die Probe steht so lange unter dem Namen bei der Einbindung da. Wurde das Objekt in PRTG gelöscht, steht das ebenfalls dort.',
          'If PRTG cannot be reached or refuses the token, the {ui:probes.prtgName.label} row says so, and the probe is shown under its enrollment name in the meantime. If the object was deleted in PRTG, the row says that too.',
        ),
      ],
      related: ['connect-prtg-core', 'enroll-a-probe', 'probe-details'],
    },
    {
      slug: 'unenroll-a-probe',
      title: L('Eine Probe ausbinden', 'Unenrolling a probe'),
      short: L(
        'Eine Probe aus der Verwaltung nehmen und auf Wunsch Sensoren und Probe-Software mit entfernen.',
        'Take a probe out of management and, if you choose, remove its sensors and the probe software as well.',
      ),
      lead: L(
        'Wird eine Probe nicht mehr gebraucht, bindest du sie aus. Die Plattform nimmt ihr den Management-Zugang und das NATS-Konto; was sonst auf dem Host bleibt, entscheidest du im Dialog.',
        'When a probe is no longer needed, you unenroll it. The platform takes away its management access and its NATS account; what else stays on the host is up to you in the dialog.',
      ),
      role: 'administrator',
      keywords: L(
        'ausbinden entfernen löschen stilllegen deinstallieren',
        'unenroll remove delete retire uninstall',
      ),
      steps: [
        {
          text: L(
            '<p>Öffne die Probe und wähle oben unter {ui:common.moreActions} den Eintrag {ui:probes.unenroll}.</p>',
            '<p>Open the probe and choose {ui:probes.unenroll} under {ui:common.moreActions} at the top.</p>',
          ),
        },
        {
          text: L(
            '<p>Entscheide, was zusätzlich geht. {ui:probes.cleanup.removeSensors} nimmt alle Sensoren samt Diensten, sudo-Regeln, Konfiguration und Zugangsdaten von der Probe. {ui:probes.cleanup.uninstallMpp} entfernt <code>prtgmpprobe</code>, seine Konfiguration mit der NATS-CA und die Paketquelle. Beides ist anfangs aus.</p>',
            '<p>Decide what goes as well. {ui:probes.cleanup.removeSensors} takes every sensor off the probe, with its services, sudo rules, configuration and credentials. {ui:probes.cleanup.uninstallMpp} removes <code>prtgmpprobe</code>, its configuration with the NATS CA, and the package source. Both start off.</p>',
          ),
          image: 'probes-unenroll-dialog',
          alt: L(
            'Der Dialog zum Ausbinden mit den beiden Optionen zum Aufräumen',
            'The unenroll dialog with the two cleanup options',
          ),
          narrow: true,
        },
        {
          text: L(
            '<p>Bestätige mit {ui:probes.unenroll}. Die Seite des Jobs öffnet sich und zeigt jeden Schritt.</p>',
            '<p>Confirm with {ui:probes.unenroll}. The job page opens and shows every step.</p>',
          ),
        },
      ],
      after: L(
        `<h2>Was dabei passiert</h2>
<ul>
<li>Zuerst laufen die gewählten Aufräumschritte, solange die Probe noch erreichbar ist. Scheitert einer, bricht der Job ab und die Probe bleibt eingebunden.</li>
<li>Dann entfernt die Plattform den Management-Zugang auf der Probe und die Probe aus ihrer Verwaltung. Ist die Probe nicht erreichbar, bleibt der Zugang auf dem Host zurück, und das Protokoll sagt das.</li>
<li>Eine Probe im Overlay verliert ihren Tunnel.</li>
<li>Zuletzt wird das NATS-Konto der Probe gelöscht. Sie verliert damit ihre Verbindung und misst nicht mehr für PRTG. Nur das letzte verbliebene Konto bleibt bestehen, weil NATS eins braucht; das Protokoll warnt dann.</li>
</ul>`,
        `<h2>What happens</h2>
<ul>
<li>The cleanup steps you chose run first, while the probe can still be reached. If one fails, the job stops and the probe stays enrolled.</li>
<li>Then the platform removes the management access on the probe and the probe from its management. If the probe cannot be reached, the access stays behind on the host, and the log says so.</li>
<li>A probe on the overlay loses its tunnel.</li>
<li>Finally the probe's NATS account is deleted. The probe loses its connection with it and no longer measures for PRTG. Only the last remaining account is kept, because NATS needs one; the log then warns.</li>
</ul>`,
      ),
      notes: [
        L(
          'In PRTG ändert die Plattform nichts. Das Objekt der Probe bleibt dort stehen, bis du es selbst löschst.',
          'The platform changes nothing in PRTG. The probe\'s object stays there until you delete it yourself.',
        ),
        L(
          'Bindest du denselben Host später wieder ein, bekommt er einen neuen PRTG Access Key, den du wie bei einer neuen Probe in PRTG einträgst und genehmigst.',
          'If you enroll the same host again later, it gets a new PRTG access key, which you add and approve in PRTG as for a new probe.',
        ),
        L(
          'Wählst du nur {ui:probes.cleanup.uninstallMpp}, bleiben die Sensoren als Dienste zurück, die niemand mehr aufruft.',
          'If you choose only {ui:probes.cleanup.uninstallMpp}, the sensors stay behind as services nothing calls any more.',
        ),
      ],
      related: ['enroll-a-probe', 'remove-sensors', 'follow-jobs'],
    },
  ],
}
