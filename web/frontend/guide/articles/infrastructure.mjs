import { L } from '../lib/text.mjs'

export const infrastructure = {
  id: 'infrastructure',
  illustration: 'infrastructure',
  illustrationAlt: L(
    'Ein Mann prüft einen kleinen Serverschrank, ein Tunnel verbindet ihn mit zwei entfernten Probes.',
    'A man checks a small server rack; a tunnel connects it with two distant probes.',
  ),
  title: L('Infrastruktur', 'Infrastructure'),
  who: L(
    'Für alle, die den Server im Blick behalten, und für Administratoren, die ihn betreuen: NATS, Zertifikate, Overlay, Konten und Wartung.',
    'For everyone keeping an eye on the server, and for the administrators looking after it: NATS, certificates, the overlay, accounts and maintenance.',
  ),
  articles: [
    {
      slug: 'nats-status',
      title: L('Den NATS-Server im Blick behalten', 'Keeping an eye on the NATS server'),
      short: L(
        'Was die Seite NATS-Server zeigt und wann du handeln solltest.',
        'What the NATS server page shows and when to act.',
      ),
      lead: L(
        'Unter {ui:nav.nats} siehst du, ob der NATS-Server läuft, wie viele Clients mit ihm verbunden sind und ob die Container des Stacks laufen.',
        'Under {ui:nav.nats} you see whether the NATS server is running, how many clients are connected to it and whether the stack’s containers are running.',
      ),
      role: 'viewer',
      illustration: 'infrastructure',
      illustrationAlt: L(
        'Ein Mann prüft einen kleinen Serverschrank, ein Tunnel verbindet ihn mit zwei entfernten Probes.',
        'A man checks a small server rack; a tunnel connects it with two distant probes.',
      ),
      keywords: L(
        'nats server verbindungen jetstream container status version',
        'nats server connections jetstream containers status version',
      ),
      appPaths: ['/infrastructure/nats'],
      unnumbered: true,
      steps: [
        {
          title: L('NATS-Server', 'NATS server'),
          text: L(
            '<p>{ui:dashboard.endpoint} ist die Adresse, unter der Probes und PRTG-Core den Server erreichen, etwa <code>nats.example.test:23561</code>. Darunter stehen Servername, Version, {ui:infrastructure.uptime}, die Zahl der {ui:dashboard.connections} und unter {ui:dashboard.coreAddress} die eingetragene Adresse des PRTG-Core.</p>',
            '<p>{ui:dashboard.endpoint} is the address probes and the PRTG core reach the server at, for example <code>nats.example.test:23561</code>. Below it are the server name, version, {ui:infrastructure.uptime}, the number of {ui:dashboard.connections} and, under {ui:dashboard.coreAddress}, the PRTG core address on record.</p>',
          ),
          image: 'infrastructure-nats',
          alt: L(
            'Die Seite NATS-Server mit Server, JetStream und Containern',
            'The NATS server page with the server, JetStream and containers',
          ),
        },
        {
          title: L('JetStream', 'JetStream'),
          text: L(
            '<p>Streams, Consumer, Nachrichten und der belegte Speicher. Steht dort nur {ui:common.none}, ist JetStream nicht aktiv.</p>',
            '<p>Streams, consumers, messages and the storage in use. If it only says {ui:common.none}, JetStream is not active.</p>',
          ),
        },
        {
          title: L('Container', 'Containers'),
          text: L(
            '<p>Jeder Container des Stacks mit seinem Zustand: {ui:infrastructure.containerState.running}, {ui:infrastructure.containerState.stopped} oder {ui:infrastructure.containerState.absent}. Kann die Plattform Docker nicht fragen, steht dort {ui:common.unknown}. Das ist kein Fehler, sie kann nur nicht nachsehen.</p>',
            '<p>Every container of the stack with its state: {ui:infrastructure.containerState.running}, {ui:infrastructure.containerState.stopped} or {ui:infrastructure.containerState.absent}. If the platform cannot ask Docker, it says {ui:common.unknown}. That is not a fault; it simply cannot look.</p>',
          ),
        },
      ],
      after: L(
        `<h2>Wann du handeln solltest</h2>
<ul>
<li>Ein Container ist rot markiert, also gestoppt oder nicht vorhanden. Den NATS-Server startet ein Administrator unter <a href="@maintenance">{ui:nav.system}</a> neu; bei anderen Containern hilft die <a href="@troubleshooting">Fehlersuche</a>.</li>
<li>Bei Version und {ui:infrastructure.uptime} stehen nur Striche: Der NATS-Server hat nicht geantwortet. Die Übersicht meldet das ebenfalls.</li>
<li>Die Zahl der Verbindungen fällt plötzlich: Clients haben die Verbindung verloren. Welche Probes betroffen sind, zeigt die <a href="@probe-status">Liste der Probes</a>.</li>
<li>{ui:infrastructure.uptime} ist kürzer als erwartet: Der NATS-Server wurde neu gestartet, etwa nach dem Erneuern des Serverzertifikats.</li>
</ul>`,
        `<h2>When to act</h2>
<ul>
<li>A container is marked red, meaning stopped or not present. An administrator restarts the NATS server under <a href="@maintenance">{ui:nav.system}</a>; for other containers, see <a href="@troubleshooting">troubleshooting</a>.</li>
<li>Version and {ui:infrastructure.uptime} show only dashes: the NATS server did not answer. The dashboard reports this as well.</li>
<li>The number of connections drops suddenly: clients have lost their connection. The <a href="@probe-status">probe list</a> shows which probes are affected.</li>
<li>{ui:infrastructure.uptime} is shorter than expected: the NATS server was restarted, for example after renewing the server certificate.</li>
</ul>`,
      ),
      notes: [
        L(
          'Die Seite aktualisiert sich selbst; du musst sie nicht neu laden.',
          'The page refreshes itself; you do not need to reload it.',
        ),
        L(
          'Der Knopf {ui:credentials.title} oben rechts führt zu den <a href="@nats-accounts">NATS-Konten, die zu keiner Probe gehören</a>.',
          'The {ui:credentials.title} button at the top right leads to the <a href="@nats-accounts">NATS accounts that belong to no probe</a>.',
        ),
      ],
      related: ['dashboard', 'maintenance', 'certificates'],
    },
    {
      slug: 'certificates',
      title: L('Zertifikate prüfen und erneuern', 'Checking and renewing certificates'),
      short: L(
        'CA und Serverzertifikat von NATS: Ablauf im Blick behalten und das Serverzertifikat erneuern.',
        'The NATS CA and server certificate: keep an eye on expiry and renew the server certificate.',
      ),
      lead: L(
        'NATS verschlüsselt mit einer eigenen CA und einem Serverzertifikat, das diese CA ausgestellt hat. Unter {ui:nav.certificates} siehst du, wie lange beide gelten, und erneuerst das Serverzertifikat.',
        'NATS encrypts with a CA of its own and a server certificate issued by that CA. Under {ui:nav.certificates} you see how long both are valid, and you renew the server certificate.',
      ),
      role: 'administrator',
      keywords: L(
        'zertifikat ca tls ablauf gültig erneuern san fingerabdruck',
        'certificate ca tls expiry valid renew san fingerprint',
      ),
      appPaths: ['/infrastructure/certificates'],
      steps: [
        {
          title: L('Die Seite lesen', 'Read the page'),
          text: L(
            '<p>Jede Karte zeigt {ui:infrastructure.subject}, {ui:infrastructure.issuer}, {ui:infrastructure.notAfter} mit den verbleibenden Tagen, den {ui:infrastructure.fingerprint} und, falls vorhanden, die {ui:infrastructure.subjectAltNames}. Oben rechts steht der Zustand, zum Beispiel {ui:status.certificate.valid} oder {ui:status.certificate.expiring_soon}.</p>',
            '<p>Each card shows {ui:infrastructure.subject}, {ui:infrastructure.issuer}, {ui:infrastructure.notAfter} with the days remaining, the {ui:infrastructure.fingerprint} and, where present, the {ui:infrastructure.subjectAltNames}. The state is at the top right, for example {ui:status.certificate.valid} or {ui:status.certificate.expiring_soon}.</p>',
          ),
          image: 'infrastructure-certificates',
          alt: L(
            'Die Seite Zertifikate mit den Karten für CA und Serverzertifikat',
            'The certificates page with the cards for the CA and the server certificate',
          ),
        },
        {
          title: L('Wann erneuern', 'When to renew'),
          text: L(
            '<p>Die Übersicht warnt in der Grundeinstellung 30 Tage vor dem Ablauf. Erneuere spätestens dann – und ebenso, wenn unter {ui:infrastructure.subjectAltNames} ein Name oder eine Adresse fehlt, unter der Probes den Server ansprechen.</p>',
            '<p>By default, the dashboard warns 30 days before expiry. Renew by then at the latest – and also when a name or address the probes use for the server is missing from {ui:infrastructure.subjectAltNames}.</p>',
          ),
        },
        {
          title: L('Serverzertifikat erneuern', 'Renew the server certificate'),
          text: L(
            '<p>Wähle beim {ui:infrastructure.certificateKind.server} {ui:infrastructure.renewServer} und bestätige. Der Job stellt ein neues Zertifikat aus und aktiviert es. Der NATS-Server startet dabei neu, und alle Probes verbinden sich neu.</p>',
            '<p>On the {ui:infrastructure.certificateKind.server} card, choose {ui:infrastructure.renewServer} and confirm. The job issues a new certificate and activates it. The NATS server restarts in the process, and every probe reconnects.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Die Seite sehen alle Rollen. Erneuern darf nur ein Administrator.',
          'Every role can see the page. Only an administrator may renew.',
        ),
        L(
          'Die Probes vertrauen der CA, nicht dem einzelnen Serverzertifikat. Nach dem Erneuern musst du auf den Probes nichts ändern.',
          'The probes trust the CA, not the individual server certificate. After renewing, nothing needs changing on the probes.',
        ),
        L(
          'Erneuern lässt sich hier nur das Serverzertifikat, nicht die CA.',
          'Only the server certificate can be renewed here, not the CA.',
        ),
        L(
          'Steht dort {ui:status.certificate.missing}, ist die Installation noch nicht initialisiert; {ui:infrastructure.toSetup} führt zur Übersicht. Bei {ui:status.certificate.mismatched} gehören Zertifikat und privater Schlüssel nicht zusammen – dann hilft die <a href="@troubleshooting">Fehlersuche</a>.',
          'If it says {ui:status.certificate.missing}, the installation has not been initialised yet; {ui:infrastructure.toSetup} leads to the dashboard. With {ui:status.certificate.mismatched}, the certificate and its private key do not belong together – see <a href="@troubleshooting">troubleshooting</a>.',
        ),
        L(
          'Auf dem Server tut <code>./prtg-nats renew-certificate</code> dasselbe, siehe <a href="@command-line">Kommandozeile</a>.',
          'On the server, <code>./prtg-nats renew-certificate</code> does the same, see <a href="@command-line">the command line</a>.',
        ),
      ],
      related: ['nats-status', 'overlay', 'dashboard'],
    },
    {
      slug: 'overlay',
      title: L('Probes über das Overlay anbinden', 'Connecting probes over the overlay'),
      short: L(
        'Ein WireGuard-Tunnel zu den Probes: einschalten, Probes ins Overlay holen, Modus wählen, wieder herausnehmen.',
        'A WireGuard tunnel to the probes: turn it on, add probes, choose the mode, take them off again.',
      ),
      lead: L(
        'Das Overlay ist ein WireGuard-Tunnel zwischen diesem Server und den Probes. Die Probe baut ihn selbst auf. So bleibt sie erreichbar, wenn ihr Standort keinen Weg herein hat oder ihr gewöhnlicher Weg zum NATS-Server ausfällt.',
        'The overlay is a WireGuard tunnel between this server and the probes. The probe builds it itself. That keeps it reachable when its site has no way in, or when its ordinary path to the NATS server fails.',
      ),
      role: 'operator',
      keywords: L(
        'overlay wireguard tunnel vpn nat hub modus auto direkt standortadresse',
        'overlay wireguard tunnel vpn nat hub mode auto direct site address',
      ),
      appPaths: ['/infrastructure/overlay'],
      steps: [
        {
          title: L('Overlay einschalten', 'Turn the overlay on'),
          text: L(
            '<p>Solange das Overlay aus ist, steht oben {ui:infrastructure.overlay.disabled.title}. Ein Administrator wählt {ui:infrastructure.overlay.enable.action} und gibt die {ui:infrastructure.overlay.endpoint} an, die die Probes anwählen. Sie muss erreichbar sein, wenn der gewöhnliche Weg es nicht ist – bei einer internen NATS-Adresse also die öffentliche. Die NATS-Adresse selbst nimmt der Dialog nicht an.</p>',
            '<p>While the overlay is off, the page says {ui:infrastructure.overlay.disabled.title}. An administrator chooses {ui:infrastructure.overlay.enable.action} and enters the {ui:infrastructure.overlay.endpoint} the probes dial. It has to be reachable when the ordinary path is not – so on a site with an internal NATS address, the public one. The dialog does not accept the NATS address itself.</p>',
          ),
        },
        {
          title: L('Probes ins Overlay holen', 'Add probes to the overlay'),
          text: L(
            '<p>Unter {ui:infrastructure.overlay.add.title} öffnet {ui:infrastructure.overlay.add.action} die Probes, die noch nicht im Overlay sind. Hake sie an, wähle einen Modus und bestätige mit {ui:infrastructure.overlay.add.confirm}. Jede Probe erzeugt dabei ihren eigenen Schlüssel; ihre Standortadresse bleibt, wie sie ist.</p>',
            '<p>Under {ui:infrastructure.overlay.add.title}, {ui:infrastructure.overlay.add.action} lists the probes that are not on the overlay yet. Tick them, choose a mode and confirm with {ui:infrastructure.overlay.add.confirm}. Each probe generates its own key; its site address stays as it is.</p>',
          ),
          image: 'infrastructure-overlay-add',
          alt: L(
            'Der Dialog „Probes hinzufügen“ mit Probes zur Auswahl und den drei Modi',
            'The “Add probes” dialog with probes to pick and the three modes',
          ),
          narrow: true,
        },
        {
          title: L('Was der Tunnel trägt', 'What the tunnel carries'),
          text: L(
            `<p>Ist eine Probe im Overlay, erreicht das Management sie über den Tunnel und fällt auf die Standortadresse zurück, wenn der Tunnel nicht antwortet. Ob auch der NATS-Verkehr den Tunnel nimmt, legt der Modus fest:</p>
<table>
<thead><tr><th>Modus</th><th>NATS-Verkehr</th></tr></thead>
<tbody>
<tr><td>{ui:infrastructure.overlay.modes.off.name}</td><td>Kein Tunnel, NATS läuft immer direkt.</td></tr>
<tr><td>{ui:infrastructure.overlay.modes.auto.name}</td><td>Tunnel steht; NATS nimmt ihn nur, solange der direkte Weg gestört ist. Der übliche Fall.</td></tr>
<tr><td>{ui:infrastructure.overlay.modes.on.name}</td><td>Tunnel steht; NATS nimmt immer ihn.</td></tr>
</tbody>
</table>
<p>Den Modus änderst du jederzeit: Probes in der Liste auswählen und {ui:infrastructure.overlay.actions.mode}.</p>`,
            `<p>Once a probe is on the overlay, management reaches it through the tunnel and falls back to the site address when the tunnel does not answer. Whether NATS traffic takes the tunnel as well is set by the mode:</p>
<table>
<thead><tr><th>Mode</th><th>NATS traffic</th></tr></thead>
<tbody>
<tr><td>{ui:infrastructure.overlay.modes.off.name}</td><td>No tunnel, NATS always goes direct.</td></tr>
<tr><td>{ui:infrastructure.overlay.modes.auto.name}</td><td>Tunnel up; NATS takes it only while the direct path is down. The usual case.</td></tr>
<tr><td>{ui:infrastructure.overlay.modes.on.name}</td><td>Tunnel up; NATS always takes it.</td></tr>
</tbody>
</table>
<p>You can change the mode at any time: select probes in the list and choose {ui:infrastructure.overlay.actions.mode}.</p>`,
          ),
        },
        {
          title: L('Den aktuellen Weg lesen', 'Read the current path'),
          text: L(
            '<p>Die Liste zeigt je Probe {ui:infrastructure.overlay.columns.mode} und {ui:infrastructure.overlay.columns.path}. Farbig markiert ist nur, was nicht stimmt: {ui:infrastructure.overlay.paths.tunnelFallback} heißt, die Probe liefert weiter, aber ihr gewöhnlicher Weg ist ausgefallen. {ui:infrastructure.overlay.paths.no_handshake} und {ui:infrastructure.overlay.paths.down} heißen, der Tunnel steht nicht.</p>',
            '<p>The list shows {ui:infrastructure.overlay.columns.mode} and {ui:infrastructure.overlay.columns.path} for each probe. Only what is wrong is marked in colour: {ui:infrastructure.overlay.paths.tunnelFallback} means the probe keeps delivering, but its ordinary path has failed. {ui:infrastructure.overlay.paths.no_handshake} and {ui:infrastructure.overlay.paths.down} mean the tunnel is not up.</p>',
          ),
          image: 'infrastructure-overlay',
          alt: L(
            'Die Seite Overlay mit Hub und der Liste der Probes mit Modus und aktuellem Weg',
            'The overlay page with the hub and the list of probes with their mode and current path',
          ),
        },
        {
          title: L('Probe aus dem Overlay nehmen', 'Take a probe off the overlay'),
          text: L(
            '<p>Wähle die Probes in der Liste aus und unter {ui:common.moreActions} {ui:infrastructure.overlay.actions.remove}. Das läuft über die Standortadresse der Probe. Antwortet sie dort nicht, wird es abgelehnt, statt die Probe unerreichbar zu machen.</p>',
            '<p>Select the probes in the list and choose {ui:infrastructure.overlay.actions.remove} under {ui:common.moreActions}. This goes over the probe’s site address. If the probe does not answer there, the request is refused rather than leaving the probe unreachable.</p>',
          ),
        },
        {
          title: L('Overlay ausschalten', 'Turn the overlay off'),
          text: L(
            '<p>In der Karte {ui:infrastructure.overlay.hub} stoppt {ui:infrastructure.overlay.disable.action} den Hub; das darf nur ein Administrator. Jede Probe behält Adresse und Schlüssel. Stell Probes im Modus {ui:infrastructure.overlay.modes.on.name} vorher auf {ui:infrastructure.overlay.modes.auto.name}, denn ihr Tunnel ist danach weg.</p>',
            '<p>In the {ui:infrastructure.overlay.hub} card, {ui:infrastructure.overlay.disable.action} stops the hub; only an administrator may do this. Every probe keeps its address and key. Switch probes in mode {ui:infrastructure.overlay.modes.on.name} to {ui:infrastructure.overlay.modes.auto.name} first, because their tunnel is gone afterwards.</p>',
          ),
        },
      ],
      after: L(
        `<h2>Ein Standort ohne Weg herein</h2>
<p>Erreicht eine Probe die Plattform gar nicht direkt, etwa an einem Standort ohne Site-to-Site-VPN, hakst du beim Erstellen der Einladung {ui:probes.enroll.fields.overlayBootstrap} an. Die Option gibt es nur bei eingeschaltetem Overlay. Du bekommst dann drei Befehle, die du der Reihe nach auf einer Konsole der Probe ausführst: {ui:probes.enroll.step2.steps.install_wireguard.title}, {ui:probes.enroll.step2.steps.build_tunnel.title} und {ui:probes.enroll.step2.steps.enroll.title}.</p>
<p>Das Skript enthält den privaten WireGuard-Schlüssel der Probe. Gib es weiter wie ein Passwort, nicht über ein Ticket oder einen Chat. Eine solche Probe steht immer im Modus {ui:infrastructure.overlay.modes.on.name} und spricht den Server über seine IP-Adresse an. Fehlt diese Adresse im Serverzertifikat, <a href="@certificates">erneuere es</a>.</p>`,
        `<h2>A site with no way in</h2>
<p>If a probe cannot reach the platform directly at all, for example at a site with no site-to-site VPN, tick {ui:probes.enroll.fields.overlayBootstrap} when you create the invitation. The option is there only while the overlay is on. You then get three commands to run in order on a console of the probe: {ui:probes.enroll.step2.steps.install_wireguard.title}, {ui:probes.enroll.step2.steps.build_tunnel.title} and {ui:probes.enroll.step2.steps.enroll.title}.</p>
<p>The script carries the probe’s private WireGuard key. Hand it over the way you would a password, not in a ticket or a chat. Such a probe is always in mode {ui:infrastructure.overlay.modes.on.name} and addresses the server by its IP address. If that address is missing from the server certificate, <a href="@certificates">renew it</a>.</p>`,
      ),
      notes: [
        L(
          'Ein- und Ausschalten darf nur ein Administrator. Probes ins Overlay holen, ihren Modus ändern und sie herausnehmen darf auch ein Operator.',
          'Only an administrator may turn the overlay on and off. An operator may also add probes, change their mode and take them off.',
        ),
        L(
          'Die Probes müssen den Server über UDP erreichen, standardmäßig auf Port 51820, siehe <a href="@firewall-and-ports">Firewall und Ports</a>. Auf den Probes muss sich wireguard-tools installieren lassen; auf RHEL 9 kommt es aus EPEL.',
          'The probes have to reach the server over UDP, on port 51820 by default, see <a href="@firewall-and-ports">firewall and ports</a>. wireguard-tools must be installable on the probes; on RHEL 9 it comes from EPEL.',
        ),
        L(
          'Eine einzelne Probe holst du auch auf ihrer eigenen Seite mit {ui:probes.overlayAdd} ins Overlay, und dort änderst du ebenso ihren Modus.',
          'A single probe can also be added on its own page with {ui:probes.overlayAdd}, and its mode can be changed there too.',
        ),
        L(
          'Wer nur den Tunnel abbauen, die Overlay-Adresse aber behalten will, stellt den Modus auf {ui:infrastructure.overlay.modes.off.name}, statt die Probe herauszunehmen.',
          'To take down the tunnel but keep the overlay address, set the mode to {ui:infrastructure.overlay.modes.off.name} instead of taking the probe off.',
        ),
        L(
          'Wird eine Probe ausgebunden, verliert sie auch ihren Platz im Overlay.',
          'When a probe is unenrolled, it loses its place on the overlay as well.',
        ),
      ],
      related: ['enroll-a-probe', 'probe-details', 'firewall-and-ports'],
    },
    {
      slug: 'nats-accounts',
      title: L('NATS-Konten ohne Probe', 'NATS accounts without a probe'),
      short: L(
        'Das gemeinsame Konto des PRTG-Core und NATS-Konten, die zu keiner eingebundenen Probe gehören.',
        'The PRTG core’s shared account and NATS accounts that belong to no enrolled probe.',
      ),
      lead: L(
        'Jede eingebundene Probe hat ihr eigenes NATS-Konto, das auf ihrer Seite steht. Unter {ui:nav.credentials} stehen die übrigen: das gemeinsame Konto des PRTG-Core, von Hand angelegte Konten und Reste ausgebundener Probes.',
        'Every enrolled probe has its own NATS account, shown on its page. Under {ui:nav.credentials} you find the rest: the PRTG core’s shared account, accounts created by hand and leftovers of unenrolled probes.',
      ),
      role: 'administrator',
      keywords: L(
        'nats konto benutzer passwort anzeigen wechseln löschen gemeinsam prtg-core',
        'nats account user password reveal change delete shared prtg core',
      ),
      appPaths: ['/infrastructure/credentials'],
      unnumbered: true,
      steps: [
        {
          title: L('Die Liste', 'The list'),
          text: L(
            '<p>{ui:credentials.columns.account} nennt das Konto, {ui:credentials.columns.usage} wofür es dient. Das gemeinsame Konto trägt die Markierung {ui:credentials.shared}, dort steht {ui:credentials.usedByCore}. Bei allen anderen steht {ui:credentials.unused} – auch wenn eine von Hand installierte Probe das Konto benutzt.</p>',
            '<p>{ui:credentials.columns.account} names the account, {ui:credentials.columns.usage} what it serves. The shared account is marked {ui:credentials.shared} and shows {ui:credentials.usedByCore}. Every other account shows {ui:credentials.unused} – even when a probe installed by hand uses it.</p>',
          ),
          image: 'infrastructure-nats-accounts',
          alt: L(
            'Die Seite Konten ohne Probe mit dem gemeinsamen Konto und einem von Hand angelegten Konto',
            'The accounts without a probe page with the shared account and an account created by hand',
          ),
        },
        {
          title: L('Konto anlegen', 'Create an account'),
          text: L(
            '<p>Unter {ui:credentials.createTitle} gibst du einen Namen ein, etwa <code>probe-kiel-01</code>, und wählst {ui:credentials.create}. Das Passwort erzeugt die Plattform. So ein Konto brauchst du für eine Probe, die du <a href="@manual-probe-install">von Hand installierst</a>.</p>',
            '<p>Under {ui:credentials.createTitle}, enter a name such as <code>probe-kiel-01</code> and choose {ui:credentials.create}. The platform generates the password. You need such an account for a probe you <a href="@manual-probe-install">install by hand</a>.</p>',
          ),
        },
        {
          title: L('Passwort anzeigen', 'Reveal the password'),
          text: L(
            '<p>{ui:common.reveal} zeigt das Passwort zum Kopieren. Jede Anzeige wird im <a href="@audit">Audit</a> festgehalten; zeig es nur, wenn es irgendwo eingetippt werden muss.</p>',
            '<p>{ui:common.reveal} shows the password for copying. Every disclosure is recorded in the <a href="@audit">audit</a>; reveal it only when it has to be typed in somewhere.</p>',
          ),
        },
        {
          title: L('Passwort wechseln', 'Change the password'),
          text: L(
            '<p>Unter {ui:common.moreActions} erzeugt {ui:credentials.rotate} ein neues Passwort; das bisherige gilt sofort nicht mehr. Wer das Konto benutzt, braucht danach das neue: beim gemeinsamen Konto der PRTG-Core, bei einer von Hand installierten Probe ihre Konfiguration.</p>',
            '<p>Under {ui:common.moreActions}, {ui:credentials.rotate} generates a new password; the current one stops working immediately. Whoever uses the account needs the new one afterwards: for the shared account the PRTG core, for a probe installed by hand its configuration.</p>',
          ),
        },
        {
          title: L('Konto löschen', 'Delete an account'),
          text: L(
            '<p>{ui:credentials.delete} entfernt das Konto und seine Zugangsdaten vom Server. Ein Client, der es noch benutzt, verliert beim nächsten Neuverbinden die Verbindung. Das gemeinsame Konto lässt sich nicht löschen.</p>',
            '<p>{ui:credentials.delete} removes the account and its credentials from the server. A client still using it loses its connection on the next reconnect. The shared account cannot be deleted.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Die Liste sehen alle Rollen. Anzeigen, anlegen, Passwort wechseln und löschen darf nur ein Administrator.',
          'Every role can see the list. Only an administrator may reveal, create, change passwords and delete.',
        ),
        L(
          'Das Konto einer eingebundenen Probe findest du auf der Seite der Probe. Wechselst du dort sein Passwort, bekommt die Probe das neue im selben Job.',
          'An enrolled probe’s account is on the probe’s page. If you change its password there, the probe receives the new one in the same job.',
        ),
        L(
          'Auch der Knopf {ui:credentials.title} auf der Seite {ui:nav.nats} führt hierher.',
          'The {ui:credentials.title} button on the {ui:nav.nats} page leads here as well.',
        ),
      ],
      related: ['connect-prtg-core', 'manual-probe-install', 'probe-details'],
    },
    {
      slug: 'maintenance',
      title: L('Wartung: prüfen, sichern, neu starten', 'Maintenance: verify, back up, restart'),
      short: L(
        'Die Installation prüfen, NATS neu starten, Sicherungen anlegen und herunterladen.',
        'Verify the installation, restart NATS, create and download backups.',
      ),
      lead: L(
        'Unter {ui:nav.system} liegen die Handgriffe am Server selbst. Jede Aktion läuft als Job, und die Seite öffnet ihn für dich.',
        'Under {ui:nav.system} you find the hands-on work on the server itself. Every action runs as a job, and the page opens it for you.',
      ),
      role: 'administrator',
      keywords: L(
        'wartung prüfen verify neustart restart sicherung backup export jetstream archiv herunterladen',
        'maintenance verify restart backup export jetstream archive download',
      ),
      appPaths: ['/infrastructure/maintenance'],
      unnumbered: true,
      steps: [
        {
          title: L('Installation prüfen', 'Verify the installation'),
          text: L(
            '<p>{ui:system.verify.run} prüft Container, Zertifikate, Laufzeitverzeichnis und ob NATS erreichbar ist. Das Ergebnis steht im Job. Diese Prüfung darf jede Rolle starten.</p>',
            '<p>{ui:system.verify.run} checks the containers, certificates, runtime directory and whether NATS can be reached. The result is in the job. Every role may start this check.</p>',
          ),
          image: 'infrastructure-maintenance',
          alt: L(
            'Die Seite Wartung mit Prüfen, Neustart, Sicherungen und vorhandenen Archiven',
            'The maintenance page with verify, restart, backups and existing archives',
          ),
        },
        {
          title: L('NATS neu starten', 'Restart NATS'),
          text: L(
            '<p>{ui:system.restart.run} startet den NATS-Container nach einer Rückfrage neu. Alle Probes und der PRTG-Core verlieren kurz die Verbindung; Messungen aus diesem Moment gehen verloren. Ohne Zugriff auf Docker ist der Knopf gesperrt, dann geht es nur auf dem Server mit <code>./prtg-nats restart</code>.</p>',
            '<p>{ui:system.restart.run} restarts the NATS container after asking for confirmation. All probes and the PRTG core briefly lose their connection; measurements from that moment are lost. Without access to Docker the button is disabled, and it only works on the server with <code>./prtg-nats restart</code>.</p>',
          ),
        },
        {
          title: L('Sicherungen anlegen', 'Create backups'),
          text: L(
            '<p>{ui:system.backup.jetstream} sichert den Nachrichtenbestand von JetStream. {ui:system.backup.export} archiviert nach einer Rückfrage das gesamte Laufzeitverzeichnis für einen Umzug oder eine Wiederherstellung. Dieses Archiv enthält den CA-Schlüssel, alle Konten und alle Passwörter im Klartext.</p>',
            '<p>{ui:system.backup.jetstream} backs up the JetStream message store. {ui:system.backup.export} archives the whole runtime directory, after asking for confirmation, for a move or a recovery. That archive contains the CA key, every account and every password in the clear.</p>',
          ),
        },
        {
          title: L('Vorhandene Archive', 'Existing archives'),
          text: L(
            '<p>Hier stehen die Archive auf dem Server mit Art, Größe, Alter und Fingerabdruck. {ui:system.backups.download} lädt eines herunter. Wie lange du sie aufbewahrst und wann du alte entfernst, entscheidest du selbst.</p>',
            '<p>This lists the archives on the server with their kind, size, age and fingerprint. {ui:system.backups.download} downloads one. How long you keep them and when you remove old ones is up to you.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Prüfen darf jede Rolle, und die Liste der Archive sehen alle. Neu starten, Sicherungen anlegen und Archive herunterladen darf nur ein Administrator.',
          'Every role may verify, and everyone sees the list of archives. Only an administrator may restart, create backups and download archives.',
        ),
        L(
          'Ein Export des Laufzeitverzeichnisses gehört an einen sicheren Ort, nicht in ein Ticket oder einen Chat.',
          'An export of the runtime directory belongs in a safe place, not in a ticket or a chat.',
        ),
        L(
          'Wie du aus einem Export wiederherstellst oder auf einen anderen Server umziehst, steht unter <a href="@backup-and-move">Sichern, wiederherstellen und umziehen</a>.',
          'How to restore from an export or move to another server is described under <a href="@backup-and-move">backing up, restoring and moving</a>.',
        ),
      ],
      related: ['nats-status', 'backup-and-move', 'command-line'],
    },
  ],
}
