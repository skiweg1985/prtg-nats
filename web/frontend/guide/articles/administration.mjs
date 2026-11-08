import { L } from '../lib/text.mjs'

export const administration = {
  id: 'administration',
  illustration: 'administration',
  illustrationAlt: L(
    'Eine Frau ordnet am Bildschirm Personenkarten, daneben liegen ein Schlüssel und ein Update-Symbol.',
    'A woman arranges person cards on a screen, next to a key and an update symbol.',
  ),
  title: L('Verwaltung', 'Administration'),
  who: L(
    'Für Administratoren: Benutzer und Rollen, die Anbindung an den PRTG-Core und Updates der Installation.',
    'For administrators: users and roles, the connection to the PRTG core and updates of the installation.',
  ),
  articles: [
    {
      slug: 'manage-users',
      title: L('Benutzer verwalten', 'Managing users'),
      short: L(
        'Konten anlegen, Rollen vergeben, Konten deaktivieren und löschen.',
        'Create accounts, assign roles, deactivate and delete accounts.',
      ),
      lead: L(
        'Unter {ui:nav.settings} im Bereich {ui:settings.users} legst du Konten an und bestimmst, welche Rolle jemand hat.',
        'Under {ui:nav.settings}, in {ui:settings.users}, you create accounts and decide which role each person has.',
      ),
      role: 'administrator',
      illustration: 'administration',
      illustrationAlt: L(
        'Eine Frau ordnet am Bildschirm Personenkarten, daneben liegen ein Schlüssel und ein Update-Symbol.',
        'A woman arranges person cards on a screen, next to a key and an update symbol.',
      ),
      keywords: L(
        'benutzer konto rolle anlegen löschen deaktivieren startpasswort',
        'user account role create delete deactivate initial password',
      ),
      steps: [
        {
          title: L('Konto anlegen', 'Create an account'),
          text: L(
            '<p>Klicke auf {ui:settings.addUser} und fülle {ui:auth.username}, {ui:auth.displayName}, {ui:auth.password} und {ui:settings.role} aus. Das Passwort ist ein Startpasswort mit mindestens zwölf Zeichen. Mit {ui:auth.createAccount} ist das Konto angelegt.</p>',
            '<p>Click {ui:settings.addUser} and fill in {ui:auth.username}, {ui:auth.displayName}, {ui:auth.password} and {ui:settings.role}. The password is an initial password of at least twelve characters. {ui:auth.createAccount} creates the account.</p>',
          ),
          image: 'administration-users-add',
          alt: L(
            'Der Bereich Benutzer und Rollen mit dem geöffneten Formular für ein neues Konto',
            'The users and roles card with the form for a new account open',
          ),
        },
        {
          title: L('Startpasswort weitergeben', 'Hand over the initial password'),
          text: L(
            '<p>Gib das Startpasswort auf einem sicheren Weg weiter. Bei der ersten Anmeldung muss die Person es ändern, bevor sie weiterarbeiten kann.</p>',
            '<p>Pass the initial password on through a secure channel. At the first sign-in the person has to change it before going any further.</p>',
          ),
        },
        {
          title: L('Rolle ändern', 'Change a role'),
          text: L(
            '<p>Jede Zeile der Liste hat ein Auswahlfeld für die Rolle. Eine neue Auswahl gilt ohne weiteren Klick. Was jede Rolle darf, steht unter <a href="@roles">Rollen</a>.</p>',
            '<p>Every row of the list has a selector for the role. A new choice takes effect without a further click. What each role may do is described in <a href="@roles">Roles</a>.</p>',
          ),
        },
        {
          title: L('Deaktivieren oder löschen', 'Deactivate or delete'),
          text: L(
            '<p>Über {ui:common.moreActions} am Ende der Zeile erreichst du {ui:settings.deactivate} und {ui:credentials.delete}. Ein deaktiviertes Konto ist sofort abgemeldet und lässt sich mit {ui:settings.activate} wieder freigeben. Löschen fragt vorher nach; die Einträge im Audit bleiben erhalten.</p>',
            '<p>{ui:common.moreActions} at the end of the row offers {ui:settings.deactivate} and {ui:credentials.delete}. A deactivated account is signed out at once and can be enabled again with {ui:settings.activate}. Deleting asks first; the account’s audit entries are kept.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Dein eigenes Konto ist mit {ui:settings.you} markiert. Seine Rolle kannst du nicht ändern, und es lässt sich weder deaktivieren noch löschen – das muss ein anderer Administrator tun.',
          'Your own account is marked {ui:settings.you}. You cannot change its role, deactivate it or delete it – another administrator has to do that.',
        ),
        L(
          'Das letzte Konto mit der Rolle Administrator kann seine Rolle nicht verlieren und lässt sich weder deaktivieren noch löschen. Lege zuerst einen zweiten Administrator an.',
          'The last account with the administrator role cannot lose that role and can be neither deactivated nor deleted. Create a second administrator first.',
        ),
        L(
          'Ein Passwort lässt sich nicht zurücksetzen. Hat jemand sein Passwort vergessen, lösche das Konto und lege es mit einem neuen Startpasswort wieder an.',
          'A password cannot be reset. When somebody has forgotten theirs, delete the account and create it again with a new initial password.',
        ),
        L(
          'Neben jedem Konto steht die {ui:settings.lastLogin}. So erkennst du Konten, die niemand mehr benutzt.',
          'Each account shows its {ui:settings.lastLogin}. That is how you spot accounts nobody uses any more.',
        ),
      ],
      related: ['roles', 'your-settings', 'audit'],
    },
    {
      slug: 'connect-prtg-core',
      title: L('Den PRTG-Core anbinden', 'Connecting the PRTG core'),
      short: L(
        'Den PRTG-Core auf diesen NATS-Server einstellen und die Plattform die Namen der Probes aus PRTG lesen lassen.',
        'Point the PRTG core at this NATS server and let the platform read the probe names from PRTG.',
      ),
      lead: L(
        'Zwei Verbindungen, beide einmalig: Der PRTG-Core spricht über diesen NATS-Server mit den Probes, und die Plattform liest über die API von PRTG, wie jede Probe dort heißt. Die erste ist Pflicht, die zweite optional.',
        'Two connections, each set up once: the PRTG core talks to the probes through this NATS server, and the platform reads through the PRTG API what each probe is called there. The first is required, the second optional.',
      ),
      role: 'administrator',
      keywords: L(
        'prtg core api token schlüssel nats verbindung ca zertifikat 1616',
        'prtg core api token key nats connection ca certificate 1616',
      ),
      steps: [
        {
          title: L('CA auf den PRTG-Core legen', 'Put the CA on the PRTG core'),
          text: L(
            '<p>Lade die öffentliche CA von <code>http://nats.example.test/nats-ca.pem</code> und lege sie als PEM-Datei mit der Endung <code>.crt</code> in das Verzeichnis <code>cert</code> im Programmordner von PRTG, zum Beispiel als <code>nats-ca.crt</code>.</p>',
            '<p>Download the public CA from <code>http://nats.example.test/nats-ca.pem</code> and place it as a PEM file with the extension <code>.crt</code> in the <code>cert</code> directory of the PRTG program folder, for example as <code>nats-ca.crt</code>.</p>',
          ),
        },
        {
          title: L('NATS-Verbindung in PRTG einstellen', 'Set the NATS connection in PRTG'),
          text: L(
            `<p>In PRTG unter <i>Setup › System Administration › Probes › Settings for NATS Connections</i>:</p>
<table>
<thead><tr><th>Feld in PRTG</th><th>Wert</th></tr></thead>
<tbody>
<tr><td>NATS connections</td><td>über einen entfernten NATS-Server erlauben</td></tr>
<tr><td>Connection security</td><td>TLS</td></tr>
<tr><td>Hostname of the NATS server</td><td><code>nats.example.test:23561</code></td></tr>
<tr><td>NATS authentication</td><td>Benutzername und Passwort</td></tr>
<tr><td>User name</td><td><code>prtg-nats</code></td></tr>
<tr><td>Password</td><td>siehe nächster Schritt</td></tr>
<tr><td>CA root certificate</td><td>die Datei aus Schritt 1</td></tr>
</tbody>
</table>`,
            `<p>In PRTG under <i>Setup › System Administration › Probes › Settings for NATS Connections</i>:</p>
<table>
<thead><tr><th>PRTG field</th><th>Value</th></tr></thead>
<tbody>
<tr><td>NATS connections</td><td>allow connections through a remote NATS server</td></tr>
<tr><td>Connection security</td><td>TLS</td></tr>
<tr><td>Hostname of the NATS server</td><td><code>nats.example.test:23561</code></td></tr>
<tr><td>NATS authentication</td><td>user name and password</td></tr>
<tr><td>User name</td><td><code>prtg-nats</code></td></tr>
<tr><td>Password</td><td>see the next step</td></tr>
<tr><td>CA root certificate</td><td>the file from step 1</td></tr>
</tbody>
</table>`,
          ),
        },
        {
          title: L('Passwort des gemeinsamen Kontos holen', 'Get the password of the shared account'),
          text: L(
            '<p>Öffne {ui:nav.infrastructure} › {ui:nav.credentials}. Das Konto <code>prtg-nats</code> ist als {ui:credentials.shared} markiert; {ui:common.reveal} zeigt sein Passwort. Trage es in PRTG ein, speichere und bestätige den Neustart des PRTG-Core.</p>',
            '<p>Open {ui:nav.infrastructure} › {ui:nav.credentials}. The account <code>prtg-nats</code> is marked {ui:credentials.shared}; {ui:common.reveal} shows its password. Enter it in PRTG, save and confirm the restart of the PRTG core.</p>',
          ),
        },
        {
          title: L('API-Schlüssel in PRTG anlegen', 'Create an API key in PRTG'),
          text: L(
            '<p>Für die Namen der Probes braucht die Plattform einen API-Schlüssel aus PRTG. Lesezugriff genügt.</p>',
            '<p>To read the probe names, the platform needs an API key from PRTG. Read access is enough.</p>',
          ),
        },
        {
          title: L('Die Plattform mit PRTG verbinden', 'Connect the platform to PRTG'),
          text: L(
            '<p>Öffne {ui:nav.settings} › {ui:settings.prtg.title}. Trage die {ui:settings.prtg.coreUrl} ein, etwa <code>https://prtg.example.test:1616</code>, und das {ui:settings.prtg.token}. {ui:settings.prtg.caBundle} brauchst du nur, wenn das Zertifikat des PRTG-Core von einer internen CA stammt. Dann {ui:common.save}.</p>',
            '<p>Open {ui:nav.settings} › {ui:settings.prtg.title}. Enter the {ui:settings.prtg.coreUrl}, for example <code>https://prtg.example.test:1616</code>, and the {ui:settings.prtg.token}. You need the {ui:settings.prtg.caBundle} only when the PRTG core’s certificate comes from an internal CA. Then {ui:common.save}.</p>',
          ),
          image: 'administration-prtg-core',
          alt: L(
            'Der Bereich PRTG-Core in den Einstellungen mit Adresse und gespeichertem API-Token',
            'The PRTG core card in the settings with the URL and a stored API token',
          ),
        },
      ],
      notes: [
        L(
          'Die Plattform ändert in PRTG nichts; sie liest nur die Namen der Probes und zeigt jede verknüpfte Probe unter ihrem Namen in PRTG an. Wie eine Probe mit ihrem Objekt in PRTG verknüpft wird, steht unter <a href="@link-to-prtg">Eine Probe mit PRTG verknüpfen</a>.',
          'The platform changes nothing in PRTG. It only reads the probe names and shows every linked probe under its name in PRTG. How a probe is linked to its PRTG object is described in <a href="@link-to-prtg">Linking a probe to PRTG</a>.',
        ),
        L(
          'Ein gespeichertes Token wird nie wieder angezeigt. Lässt du das Feld leer, bleibt es erhalten; {ui:settings.prtg.removeToken} entfernt es.',
          'A stored token is never shown again. Leaving the field empty keeps it; {ui:settings.prtg.removeToken} removes it.',
        ),
        L(
          'Das Zertifikat des PRTG-Core wird immer geprüft. Die Adresse muss deshalb den Namen enthalten, auf den das Zertifikat ausgestellt ist, nicht die IP-Adresse.',
          'The PRTG core’s certificate is always verified. The URL therefore has to name the host the certificate was issued for, not its IP address.',
        ),
        L(
          'Die Umstellung des PRTG-Core auf diesen NATS-Server verlangt einen Neustart des PRTG-Core. Plane sie in einem Wartungsfenster.',
          'Switching the PRTG core to this NATS server needs a restart of the PRTG core. Plan it for a maintenance window.',
        ),
      ],
      related: ['link-to-prtg', 'enroll-a-probe', 'nats-accounts'],
    },
    {
      slug: 'update-the-installation',
      title: L('Die Installation aktualisieren', 'Updating the installation'),
      short: L(
        'Sehen, welche Version läuft und was neu ist, und ein Update aus der Oberfläche einspielen.',
        'See which version runs and what is new, and install an update from the interface.',
      ),
      lead: L(
        'Unter {ui:nav.updates} siehst du, welche Version läuft und was im Update-Kanal neu ist. Ein Update spielst du von dort mit einem Klick ein.',
        'Under {ui:nav.updates} you see which version runs and what is new on the update channel. You install an update from there with one click.',
      ),
      role: 'administrator',
      keywords: L(
        'update aktualisieren version upgrade neu bauen update-kanal',
        'update upgrade version rebuild update channel',
      ),
      appPaths: ['/updates'],
      steps: [
        {
          title: L('Stand ablesen', 'Read the current state'),
          text: L(
            '<p>In der Karte {ui:updates.state.title} steht bei {ui:updates.running} die laufende Version, bei {ui:updates.checkout} der Code im Installationsverzeichnis und bei {ui:updates.branch} der Branch, aus dem Updates kommen. Oben rechts steht der Zustand, etwa {ui:updates.states.current} oder {ui:updates.states.update_available}.</p>',
            '<p>In the {ui:updates.state.title} card, {ui:updates.running} shows the running version, {ui:updates.checkout} the code in the installation directory and {ui:updates.branch} the branch updates come from. The top right corner shows the state, such as {ui:updates.states.current} or {ui:updates.states.update_available}.</p>',
          ),
          image: 'administration-updates',
          alt: L(
            'Die Seite Updates mit verfügbarem Update und der Liste der Änderungen',
            'The updates page with an update available and the list of changes',
          ),
        },
        {
          title: L('Nach Neuem fragen', 'Check for something new'),
          text: L(
            '<p>Die Plattform fragt den Update-Kanal regelmäßig von selbst. {ui:updates.check_now} fragt sofort.</p>',
            '<p>The platform asks the update channel regularly on its own. {ui:updates.check_now} asks right away.</p>',
          ),
        },
        {
          title: L('Update einspielen', 'Install the update'),
          text: L(
            '<p>Unter {ui:updates.changes} stehen die Änderungen seit der laufenden Version. {ui:updates.install} sichert zuerst die Laufzeitdaten, baut dann die Images und erstellt den Stack neu.</p>',
            '<p>{ui:updates.changes} lists the changes since the running version. {ui:updates.install} first archives the runtime data, then builds the images and recreates the stack.</p>',
          ),
        },
        {
          title: L('Während das Update läuft', 'While the update runs'),
          text: L(
            '<p>{ui:updates.progress} zeigt die Schritte und das Protokoll. Sobald die Container ersetzt werden, erscheint {ui:updates.away.title}: Die Oberfläche ist einige Minuten nicht erreichbar, du darfst die Seite verlassen. Ist das Update fertig, lädt die Seite sich selbst neu.</p>',
            '<p>{ui:updates.progress} shows the steps and the log. Once the containers are being replaced, {ui:updates.away.title} appears: the interface is unavailable for a few minutes and you may leave the page. When the update has finished, the page reloads itself.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Ein Update startet nicht, solange ein anderer Job läuft oder wartet, und nicht, solange das Installationsverzeichnis uncommittete Änderungen hat. Im zweiten Fall steht auf der Seite {ui:updates.dirty_blocks.title}.',
          'An update does not start while another job is running or queued, nor while the installation directory has uncommitted changes. In the second case the page says {ui:updates.dirty_blocks.title}.',
        ),
        L(
          'Steht dort {ui:updates.rebuild_pending.title}, wurde auf dem Host neuer Code geholt, aber nicht gebaut. {ui:updates.rebuild} spielt ihn ein, ohne etwas zu holen.',
          'If it says {ui:updates.rebuild_pending.title}, somebody fetched new code on the host without building it. {ui:updates.rebuild} installs it without fetching anything.',
        ),
        L(
          'Meldet die Seite {ui:updates.unavailable.title}, nennt sie darunter den Grund. Meist fehlt nur einmalig das Image für Updates; dann hilft ein <code>./prtg-nats update</code> auf dem Server, siehe <a href="@command-line">Die Kommandozeile</a>.',
          'If the page reports {ui:updates.unavailable.title}, it names the reason below. Usually only the image for updates is missing, once; a <code>./prtg-nats update</code> on the server fixes that, see <a href="@command-line">The command line</a>.',
        ),
        L(
          'Der Update-Kanal ist der Branch, dem das Installationsverzeichnis auf dem Server folgt, standardmäßig <code>dev</code>. Gewechselt wird er dort mit Git, nicht in der Oberfläche.',
          'The update channel is the branch the installation directory on the server follows, <code>dev</code> by default. It is changed there with Git, not in the interface.',
        ),
        L(
          'Kommt die Oberfläche nach einer Viertelstunde nicht zurück, erscheint {ui:updates.away.stuck_title}. Dann geht es auf dem Server weiter, siehe <a href="@troubleshooting">Fehlersuche</a>.',
          'If the interface has not come back after a quarter of an hour, {ui:updates.away.stuck_title} appears. Then continue on the server, see <a href="@troubleshooting">Troubleshooting</a>.',
        ),
      ],
      related: ['command-line', 'backup-and-move', 'follow-jobs'],
    },
  ],
}
