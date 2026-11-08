import { L } from '../lib/text.mjs'

export const server = {
  id: 'server',
  illustration: 'server',
  illustrationAlt: L(
    'Ein Mann am Laptop setzt einen Sicherungsblock von einem Server auf einen zweiten.',
    'A man at a laptop moves a backup block from one server to a second one.',
  ),
  title: L('Betrieb auf dem Server', 'Running the server'),
  who: L(
    'Für Administratoren mit Shell-Zugang zum Server: installieren, absichern, sichern und reparieren.',
    'For administrators with shell access to the server: installing, securing, backing up and repairing.',
  ),
  articles: [
    {
      slug: 'install-the-server',
      title: L('Den Server installieren', 'Installing the server'),
      short: L(
        'Voraussetzungen, Code holen, Standortdaten, Stack starten und den ersten Administrator anlegen.',
        'Requirements, getting the code, site settings, starting the stack and creating the first administrator.',
      ),
      lead: L(
        'Die Plattform läuft als Docker-Compose-Stack auf einem Linux-Host. Ein Befehl fragt die Standortdaten ab, startet den Stack und richtet ihn ein; den ersten Administrator legst du danach im Browser an.',
        'The platform runs as a Docker Compose stack on a Linux host. One command asks for the site settings, starts the stack and sets it up; you then create the first administrator in the browser.',
      ),
      role: 'administrator',
      illustration: 'server',
      illustrationAlt: L(
        'Ein Mann am Laptop setzt einen Sicherungsblock von einem Server auf einen zweiten.',
        'A man at a laptop moves a backup block from one server to a second one.',
      ),
      keywords: L(
        'installation setup voraussetzungen docker compose env erster administrator',
        'installation setup requirements docker compose env first administrator',
      ),
      body: L(
        `<h2>Voraussetzungen</h2>
<ul>
<li>Ubuntu x86-64 mit Docker Engine, Docker Compose v2, OpenSSL und Git</li>
<li>mindestens 10 GB freier Speicher</li>
<li>ein DNS-Name für den Server, etwa <code>nats.example.test</code>, der auf seine Adresse zeigt, etwa <code>192.0.2.10</code></li>
<li>die Freigaben aus <a href="@firewall-and-ports">Firewall und Ports</a></li>
</ul>`,
        `<h2>Requirements</h2>
<ul>
<li>Ubuntu x86-64 with Docker Engine, Docker Compose v2, OpenSSL and Git</li>
<li>at least 10 GB of free storage</li>
<li>a DNS name for the server, such as <code>nats.example.test</code>, that resolves to its address, such as <code>192.0.2.10</code></li>
<li>the rules from <a href="@firewall-and-ports">Firewall and ports</a></li>
</ul>`,
      ),
      steps: [
        {
          title: L('Code holen', 'Get the code'),
          text: L(
            `<p>Klone das Repository in das Installationsverzeichnis:</p>
<pre><code>sudo git clone --branch dev &lt;Repository-URL&gt; /opt/prtg-nats
cd /opt/prtg-nats</code></pre>
<p>Der Branch, den du hier wählst, ist später der Update-Kanal.</p>`,
            `<p>Clone the repository into the installation directory:</p>
<pre><code>sudo git clone --branch dev &lt;repository URL&gt; /opt/prtg-nats
cd /opt/prtg-nats</code></pre>
<p>The branch you choose here becomes the update channel.</p>`,
          ),
        },
        {
          title: L('Einrichten', 'Set up'),
          text: L(
            `<pre><code>sudo ./prtg-nats setup</code></pre>
<p>Der Befehl fragt die Standortdaten ab und schreibt sie nach <code>.env</code>, startet dann den Stack und erzeugt CA, Zertifikate und Zugangsdaten. Er lässt sich gefahrlos wiederholen; eine vollständige Einrichtung wird nicht überschrieben.</p>`,
            `<pre><code>sudo ./prtg-nats setup</code></pre>
<p>The command asks for the site settings and writes them to <code>.env</code>, then starts the stack and creates the CA, certificates and credentials. It is safe to repeat; a complete setup is not overwritten.</p>`,
          ),
        },
        {
          title: L('Prüfen', 'Check'),
          text: L(
            `<pre><code>sudo ./prtg-nats status
sudo ./prtg-nats verify</code></pre>
<p><code>verify</code> endet mit einer echten Anmeldung bei NATS über TLS. Schlägt ein Punkt fehl, nennt die Ausgabe ihn.</p>`,
            `<pre><code>sudo ./prtg-nats status
sudo ./prtg-nats verify</code></pre>
<p><code>verify</code> ends with a real TLS sign-in to NATS. If a check fails, the output names it.</p>`,
          ),
        },
        {
          title: L('Ersten Administrator anlegen', 'Create the first administrator'),
          text: L(
            '<p>Öffne <code>https://nats.example.test</code>. Es gibt kein Standardkonto: Die Seite zeigt {ui:auth.setupTitle}. Gib Benutzername und ein Passwort mit mindestens zwölf Zeichen ein und klicke {ui:auth.createAccount}. Dieses Formular gibt es nur für das erste Konto.</p>',
            '<p>Open <code>https://nats.example.test</code>. There is no default account: the page shows {ui:auth.setupTitle}. Enter a user name and a password of at least twelve characters and click {ui:auth.createAccount}. This form exists for the first account only.</p>',
          ),
        },
        {
          title: L('Initialisierung abschließen, falls nötig', 'Finish the initialisation if needed'),
          text: L(
            '<p>Zeigt die Übersicht {ui:dashboard.setupTitle}, fehlt noch ein Teil der Einrichtung. {ui:dashboard.setupAction} ergänzt ihn als Job.</p>',
            '<p>If the dashboard shows {ui:dashboard.setupTitle}, part of the setup is still missing. {ui:dashboard.setupAction} completes it as a job.</p>',
          ),
        },
      ],
      after: L(
        `<h2>Die Standortdaten in <code>.env</code></h2>
<table>
<thead><tr><th>Einstellung</th><th>Bedeutung</th><th>Standard</th></tr></thead>
<tbody>
<tr><td><code>NATS_FQDN</code></td><td>Name des Servers; steht im Zertifikat, PRTG-Core und Probes verbinden sich darüber</td><td>–</td></tr>
<tr><td><code>NATS_HOST_IP</code></td><td>Adresse, auf der die Ports veröffentlicht werden</td><td>–</td></tr>
<tr><td><code>PRTG_CORE_IP</code></td><td>Adresse des PRTG-Core</td><td>–</td></tr>
<tr><td><code>NATS_PORT</code></td><td>Port für NATS mit TLS</td><td><code>23561</code></td></tr>
<tr><td><code>CA_HTTP_PORT</code></td><td>Port für den Download der öffentlichen CA</td><td><code>80</code></td></tr>
<tr><td><code>WEB_HTTPS_PORT</code></td><td>Port der Oberfläche</td><td><code>443</code></td></tr>
<tr><td><code>PROBE_CHANNEL_PORT</code></td><td>Port für den Rückkanal der Probes</td><td><code>8443</code></td></tr>
<tr><td><code>CA_ORGANIZATION</code></td><td>Organisation im Zertifikat der CA; wirkt nur bei einer neuen CA</td><td><code>PRTG NATS</code></td></tr>
</tbody>
</table>
<p><code>sudo ./prtg-nats config</code> zeigt die gültigen Werte, <code>sudo ./prtg-nats config --edit</code> fragt sie neu ab.</p>
<h2>Danach</h2>
<p><a href="@connect-prtg-core">Den PRTG-Core anbinden</a>, <a href="@manage-users">weitere Benutzer anlegen</a> und <a href="@enroll-a-probe">die erste Probe einbinden</a>.</p>`,
        `<h2>The site settings in <code>.env</code></h2>
<table>
<thead><tr><th>Setting</th><th>Meaning</th><th>Default</th></tr></thead>
<tbody>
<tr><td><code>NATS_FQDN</code></td><td>name of the server; it is in the certificate, and the PRTG core and the probes connect through it</td><td>–</td></tr>
<tr><td><code>NATS_HOST_IP</code></td><td>address the ports are published on</td><td>–</td></tr>
<tr><td><code>PRTG_CORE_IP</code></td><td>address of the PRTG core</td><td>–</td></tr>
<tr><td><code>NATS_PORT</code></td><td>port for NATS over TLS</td><td><code>23561</code></td></tr>
<tr><td><code>CA_HTTP_PORT</code></td><td>port for downloading the public CA</td><td><code>80</code></td></tr>
<tr><td><code>WEB_HTTPS_PORT</code></td><td>port of the interface</td><td><code>443</code></td></tr>
<tr><td><code>PROBE_CHANNEL_PORT</code></td><td>port for the probes’ return channel</td><td><code>8443</code></td></tr>
<tr><td><code>CA_ORGANIZATION</code></td><td>organisation in the CA certificate; only affects a new CA</td><td><code>PRTG NATS</code></td></tr>
</tbody>
</table>
<p><code>sudo ./prtg-nats config</code> shows the effective values, <code>sudo ./prtg-nats config --edit</code> asks for them again.</p>
<h2>Next</h2>
<p><a href="@connect-prtg-core">Connect the PRTG core</a>, <a href="@manage-users">add more users</a> and <a href="@enroll-a-probe">enroll the first probe</a>.</p>`,
      ),
      notes: [
        L(
          'Die Oberfläche benutzt ein Zertifikat der eigenen CA der Installation. Bis du diese CA im Browser als vertrauenswürdig hinterlegst, warnt er. Die CA liegt unter <code>http://nats.example.test/nats-ca.pem</code>; ihren Fingerprint zeigt <code>sudo ./prtg-nats ca-info</code>.',
          'The interface uses a certificate from the installation’s own CA. Until you trust that CA in the browser, it warns. The CA is at <code>http://nats.example.test/nats-ca.pem</code>; <code>sudo ./prtg-nats ca-info</code> shows its fingerprint.',
        ),
        L(
          'Ist Port 80 auf dem Host belegt oder nicht erlaubt, setze <code>CA_HTTP_PORT</code> auf einen anderen Port.',
          'If port 80 is taken or not permitted on the host, set <code>CA_HTTP_PORT</code> to another port.',
        ),
        L(
          '<code>NATS_FQDN</code> und <code>NATS_PORT</code> später zu ändern betrifft den PRTG-Core und jede Probe: nach <code>config --edit</code> folgen <code>sudo ./prtg-nats update</code> und eine neue Konfiguration für jede Probe. Das gehört in ein Wartungsfenster.',
          'Changing <code>NATS_FQDN</code> or <code>NATS_PORT</code> later affects the PRTG core and every probe: <code>config --edit</code> is followed by <code>sudo ./prtg-nats update</code> and a new configuration for every probe. That belongs in a maintenance window.',
        ),
        L(
          'Führe nie <code>docker compose down --volumes</code> aus. Das löscht die Installation samt aller Schlüssel und Zugangsdaten.',
          'Never run <code>docker compose down --volumes</code>. It deletes the installation with all its keys and credentials.',
        ),
      ],
      related: ['firewall-and-ports', 'command-line', 'connect-prtg-core'],
    },
    {
      slug: 'firewall-and-ports',
      title: L('Firewall und Ports', 'Firewall and ports'),
      short: L(
        'Welche Verbindungen zum Server und von ihm weg offen sein müssen.',
        'Which connections to and from the server have to be open.',
      ),
      lead: L(
        'Diese Verbindungen braucht die Plattform. Die Ports sind die Standardwerte aus <code>.env</code>.',
        'These are the connections the platform needs. The ports are the defaults from <code>.env</code>.',
      ),
      role: 'administrator',
      keywords: L(
        'firewall port freigabe 23561 443 80 8443 51820 udp ssh 8222',
        'firewall port rule 23561 443 80 8443 51820 udp ssh 8222',
      ),
      body: L(
        `<table>
<thead><tr><th>Port</th><th>Von</th><th>Nach</th><th>Wofür</th></tr></thead>
<tbody>
<tr><td><code>23561/tcp</code> (<code>NATS_PORT</code>)</td><td>PRTG-Core, alle Probes</td><td>Server</td><td>NATS mit TLS</td></tr>
<tr><td><code>443/tcp</code> (<code>WEB_HTTPS_PORT</code>)</td><td>Administratoren; Probes während der Einbindung</td><td>Server</td><td>die Oberfläche und der Befehl zur Einbindung</td></tr>
<tr><td><code>80/tcp</code> (<code>CA_HTTP_PORT</code>)</td><td>Probes, PRTG-Core, Administratoren</td><td>Server</td><td>Download der öffentlichen CA und der Status von NATS für einen PRTG-Sensor, unverschlüsselt</td></tr>
<tr><td><code>8443/tcp</code> (<code>PROBE_CHANNEL_PORT</code>)</td><td>alle Probes</td><td>Server</td><td>der Rückkanal: Sensor-Logs und das Zertifikat des Rückkanals</td></tr>
<tr><td><code>51820/udp</code></td><td>Probes im Overlay</td><td>Hub-Adresse des Servers</td><td>das Overlay; nur nötig, wenn es eingeschaltet ist</td></tr>
<tr><td><code>22/tcp</code></td><td>Server</td><td>Probes, Messpunkte</td><td>der Management-Zugang</td></tr>
</tbody>
</table>
<h2>Was nicht erreichbar sein darf</h2>
<p>Den Monitoring-Port von NATS, <code>8222/tcp</code>, darf niemand aus dem Netz erreichen. Docker veröffentlicht Ports an UFW vorbei; eine UFW-Regel allein greift deshalb nicht. Beschränke die Quelladressen zusätzlich am Netzwerkfilter oder mit einer dauerhaften Regel in der Kette <code>DOCKER-USER</code>.</p>
<h2>Was ein Port freigibt</h2>
<p>Der Port des Rückkanals nimmt nur Verbindungen mit einem Client-Zertifikat einer Probe an und liefert nichts von der Oberfläche aus. Ihn für die Netze der Probes zu öffnen, gibt die Oberfläche dort nicht frei.</p>`,
        `<table>
<thead><tr><th>Port</th><th>From</th><th>To</th><th>What for</th></tr></thead>
<tbody>
<tr><td><code>23561/tcp</code> (<code>NATS_PORT</code>)</td><td>PRTG core, every probe</td><td>server</td><td>NATS over TLS</td></tr>
<tr><td><code>443/tcp</code> (<code>WEB_HTTPS_PORT</code>)</td><td>administrators; probes while they enroll</td><td>server</td><td>the interface and the enrollment command</td></tr>
<tr><td><code>80/tcp</code> (<code>CA_HTTP_PORT</code>)</td><td>probes, PRTG core, administrators</td><td>server</td><td>download of the public CA and the NATS status for a PRTG sensor, unencrypted</td></tr>
<tr><td><code>8443/tcp</code> (<code>PROBE_CHANNEL_PORT</code>)</td><td>every probe</td><td>server</td><td>the return channel: sensor logs and the return channel certificate</td></tr>
<tr><td><code>51820/udp</code></td><td>probes on the overlay</td><td>the server’s hub address</td><td>the overlay; only needed when it is enabled</td></tr>
<tr><td><code>22/tcp</code></td><td>server</td><td>probes, iperf endpoints</td><td>management access</td></tr>
</tbody>
</table>
<h2>What must not be reachable</h2>
<p>The NATS monitoring port, <code>8222/tcp</code>, must not be reachable from the network. Docker publishes ports past UFW, so a UFW rule alone does not take effect. Restrict the source addresses at the network filter as well, or with a persistent rule in the <code>DOCKER-USER</code> chain.</p>
<h2>What a port opens</h2>
<p>The return channel port accepts only connections with a probe’s client certificate and serves nothing of the interface. Opening it for the probe networks does not expose the interface there.</p>`,
      ),
      notes: [
        L(
          'Eine Firewall mit Applikationserkennung kann NATS beim Wechsel auf TLS zurücksetzen, obwohl ein Porttest erfolgreich ist. Was dann hilft, steht unter <a href="@troubleshooting">Fehlersuche</a>.',
          'A firewall with application detection may reset NATS when it switches to TLS, even though a port test succeeds. What helps then is in <a href="@troubleshooting">Troubleshooting</a>.',
        ),
        L(
          'Bringt ein Update einen neuen Port mit, lege die Regel an, bevor du die Funktion dahinter nutzt. Solange der Port des Rückkanals zu ist, meldet die Plattform die Probes als still.',
          'When an update brings a new port, add the rule before you use the feature behind it. As long as the return channel port is closed, the platform reports the probes as silent.',
        ),
        L(
          'Den UDP-Port des Overlays änderst du nur, wenn du das Overlay mit einem anderen Port einschaltest; siehe <a href="@overlay">Overlay</a>.',
          'The overlay’s UDP port only differs if you enable the overlay with another port; see <a href="@overlay">Overlay</a>.',
        ),
        L(
          'Durchsatzmessungen laufen direkt zwischen Probe und Messpunkt, nicht über den Server; siehe <a href="@iperf-endpoints">Messpunkte</a>.',
          'Throughput measurements run directly between probe and iperf endpoint, not through the server; see <a href="@iperf-endpoints">iperf endpoints</a>.',
        ),
      ],
      related: ['install-the-server', 'troubleshooting', 'overlay'],
    },
    {
      slug: 'backup-and-move',
      title: L('Sichern, wiederherstellen und umziehen', 'Backing up, restoring and moving'),
      short: L(
        'Was gesichert werden muss, wie du die Laufzeitdaten exportierst und wie die Installation auf einen anderen Host kommt.',
        'What has to be backed up, how to export the runtime data and how to move the installation to another host.',
      ),
      lead: L(
        'Wichtig ist der Laufzeit-Export: Er enthält alles, was sich nicht neu erzeugen lässt. Mit ihm stellst du die Installation wieder her oder ziehst sie auf einen anderen Host um.',
        'What matters is the runtime export: it holds everything that cannot be created again. With it you restore the installation or move it to another host.',
      ),
      role: 'administrator',
      keywords: L(
        'backup sicherung export wiederherstellen restore umzug migration jetstream',
        'backup export restore move migration jetstream',
      ),
      body: L(
        `<table>
<thead><tr><th>Sicherung</th><th>Enthält</th><th>Wie</th></tr></thead>
<tbody>
<tr><td>Laufzeit-Export</td><td>CA mit Schlüssel, Zertifikate, NATS-Konten, Probes, Management-Schlüssel, Zugangsdaten der Messpunkte, Benutzer und Einstellungen der Oberfläche</td><td>{ui:system.backup.export} unter {ui:nav.system}</td></tr>
<tr><td>JetStream</td><td>den Nachrichtenbestand von NATS</td><td>{ui:system.backup.jetstream} oder <code>sudo ./prtg-nats backup</code></td></tr>
<tr><td><code>.env</code></td><td>die Standortdaten</td><td>die Datei im Installationsverzeichnis kopieren</td></tr>
</tbody>
</table>`,
        `<table>
<thead><tr><th>Backup</th><th>Holds</th><th>How</th></tr></thead>
<tbody>
<tr><td>Runtime export</td><td>the CA with its key, certificates, NATS accounts, probes, the management key, iperf endpoint credentials, users and settings of the interface</td><td>{ui:system.backup.export} under {ui:nav.system}</td></tr>
<tr><td>JetStream</td><td>the NATS message store</td><td>{ui:system.backup.jetstream} or <code>sudo ./prtg-nats backup</code></td></tr>
<tr><td><code>.env</code></td><td>the site settings</td><td>copy the file in the installation directory</td></tr>
</tbody>
</table>`,
      ),
      steps: [
        {
          title: L('Laufzeitdaten exportieren', 'Export the runtime data'),
          text: L(
            '<p>Öffne {ui:nav.infrastructure} › {ui:nav.system} und klicke {ui:system.backup.export}. Der Export läuft als Job und legt ein Archiv mit Prüfsumme auf dem Server ab.</p>',
            '<p>Open {ui:nav.infrastructure} › {ui:nav.system} and click {ui:system.backup.export}. The export runs as a job and stores an archive with a checksum on the server.</p>',
          ),
        },
        {
          title: L('Archiv herunterladen', 'Download the archive'),
          text: L(
            '<p>Unter {ui:system.backups.title} lädt {ui:system.backups.download} Archiv und Prüfsumme herunter. Bewahre beides an einem sicheren Ort auf: Das Archiv enthält den CA-Schlüssel und alle Passwörter im Klartext. Ein Archiv, das nur auf dem Server liegt, schützt vor nichts.</p>',
            '<p>Under {ui:system.backups.title}, {ui:system.backups.download} fetches the archive and its checksum. Keep both in a safe place: the archive contains the CA key and every password in the clear. An archive that only exists on the server protects against nothing.</p>',
          ),
        },
      ],
      after: L(
        `<h2>Auf einen anderen Host umziehen</h2>
<ol>
<li>Auf dem alten Host: Laufzeit exportieren, Archiv und Prüfsumme herunterladen, <code>.env</code> kopieren, dann <code>sudo ./prtg-nats stop</code>.</li>
<li>Auf dem neuen Host: die Voraussetzungen aus <a href="@install-the-server">Den Server installieren</a> erfüllen, das Repository mit demselben Branch klonen und <code>.env</code> ins Installationsverzeichnis legen.</li>
<li>Prüfsumme kontrollieren und das Archiv in das Volume der Installation entpacken, bevor der Stack zum ersten Mal startet:
<pre><code>cd /opt/prtg-nats
sha256sum -c ARCHIV.tar.gz.sha256
docker volume create prtg-nats-runtime
docker run --rm -v prtg-nats-runtime:/target -v "$PWD:/source:ro" \\
  busybox:1.37.0-musl sh -c 'tar -xzf /source/ARCHIV.tar.gz -C /tmp &amp;&amp; cp -a /tmp/runtime/. /target/'</code></pre></li>
<li>Stack starten und prüfen: <code>sudo ./prtg-nats start</code>, dann <code>sudo ./prtg-nats verify</code>.</li>
<li>Den DNS-Namen <code>NATS_FQDN</code> auf den neuen Host zeigen lassen. PRTG-Core und Probes verbinden sich über diesen Namen und brauchen keine Änderung.</li>
</ol>
<h2>Auf demselben Host wiederherstellen</h2>
<p>Eine Wiederherstellung überschreibt Schlüssel, deshalb gibt es dafür keinen Knopf. Stoppe den Stack mit <code>docker compose down</code> (ohne <code>--volumes</code>), entpacke das Archiv wie oben in ein neues Volume, etwa <code>prtg-nats-runtime-restored</code>, und trage es in <code>compose.yaml</code> als Namen des Volumes <code>runtime</code> ein. Behalte das alte Volume, bis die wiederhergestellte Installation geprüft ist.</p>`,
        `<h2>Moving to another host</h2>
<ol>
<li>On the old host: export the runtime, download archive and checksum, copy <code>.env</code>, then <code>sudo ./prtg-nats stop</code>.</li>
<li>On the new host: meet the requirements from <a href="@install-the-server">Installing the server</a>, clone the repository on the same branch and put <code>.env</code> into the installation directory.</li>
<li>Check the checksum and unpack the archive into the installation’s volume before the stack starts for the first time:
<pre><code>cd /opt/prtg-nats
sha256sum -c ARCHIVE.tar.gz.sha256
docker volume create prtg-nats-runtime
docker run --rm -v prtg-nats-runtime:/target -v "$PWD:/source:ro" \\
  busybox:1.37.0-musl sh -c 'tar -xzf /source/ARCHIVE.tar.gz -C /tmp &amp;&amp; cp -a /tmp/runtime/. /target/'</code></pre></li>
<li>Start the stack and check it: <code>sudo ./prtg-nats start</code>, then <code>sudo ./prtg-nats verify</code>.</li>
<li>Point the DNS name <code>NATS_FQDN</code> at the new host. The PRTG core and the probes connect through that name and need no change.</li>
</ol>
<h2>Restoring on the same host</h2>
<p>A restore overwrites keys, so there is no button for it. Stop the stack with <code>docker compose down</code> (without <code>--volumes</code>), unpack the archive as above into a new volume, such as <code>prtg-nats-runtime-restored</code>, and enter it in <code>compose.yaml</code> as the name of the <code>runtime</code> volume. Keep the old volume until the restored installation has been checked.</p>`,
      ),
      notes: [
        L(
          'Die JetStream-Sicherung hält NATS kurz an. Probes und PRTG-Core verbinden sich danach von selbst neu.',
          'The JetStream backup stops NATS briefly. The probes and the PRTG core reconnect on their own afterwards.',
        ),
        L(
          'Behalte beim Umzug möglichst die Adresse des alten Hosts. Die Probes nehmen den Management-Zugang nur von der Adresse an, die beim Einbinden galt (<code>MPP_SSH_SOURCE_CIDR</code>, sonst <code>NATS_HOST_IP</code>).',
          'Keep the old host’s address for the move if you can. The probes accept management access only from the address that applied when they were enrolled (<code>MPP_SSH_SOURCE_CIDR</code>, otherwise <code>NATS_HOST_IP</code>).',
        ),
        L(
          'Bekommt der neue Host trotzdem eine andere Adresse, trage sie als <code>NATS_HOST_IP</code> ein und erneuere das Serverzertifikat mit <code>sudo ./prtg-nats renew-certificate</code>, damit es die neue Adresse enthält.',
          'If the new host gets a different address anyway, enter it as <code>NATS_HOST_IP</code> and renew the server certificate with <code>sudo ./prtg-nats renew-certificate</code> so it carries the new address.',
        ),
        L(
          'Ein Update aus der Oberfläche exportiert die Laufzeitdaten vorher selbst. Eine regelmäßige Sicherung ersetzt das nicht.',
          'An update from the interface exports the runtime data first on its own. That does not replace a regular backup.',
        ),
      ],
      related: ['maintenance', 'install-the-server', 'command-line'],
    },
    {
      slug: 'command-line',
      title: L('Die Kommandozeile', 'The command line'),
      short: L(
        'Der Befehl prtg-nats für Status, Logs, Neustart, Update und Reparatur, wenn die Oberfläche nicht hilft.',
        'The prtg-nats command for status, logs, restart, update and recovery when the interface cannot help.',
      ),
      lead: L(
        'Die tägliche Arbeit läuft in der Oberfläche. Für die Einrichtung und für den Fall, dass die Oberfläche selbst nicht erreichbar ist, gibt es im Installationsverzeichnis den Befehl <code>./prtg-nats</code>.',
        'Day-to-day work happens in the interface. For the setup, and for when the interface itself cannot be reached, the installation directory holds the <code>./prtg-nats</code> command.',
      ),
      role: 'administrator',
      keywords: L(
        'cli shell befehl kommandozeile status logs restart update verify',
        'cli shell command status logs restart update verify',
      ),
      steps: [
        {
          title: L('Befehl einrichten', 'Set up the command'),
          text: L(
            `<pre><code>sudo ./prtg-nats self install</code></pre>
<p>Danach ist <code>prtg-nats</code> aus jedem Verzeichnis aufrufbar, und die Shell ergänzt Befehle, Optionen und die Namen der Probes. <code>setup</code> bietet das am Ende von selbst an.</p>`,
            `<pre><code>sudo ./prtg-nats self install</code></pre>
<p>Afterwards <code>prtg-nats</code> can be called from any directory, and the shell completes commands, options and probe names. <code>setup</code> offers this on its own at the end.</p>`,
          ),
        },
        {
          title: L('Die wichtigsten Befehle', 'The most important commands'),
          text: L(
            `<table>
<thead><tr><th>Befehl</th><th>Was er tut</th></tr></thead>
<tbody>
<tr><td><code>status</code></td><td>Zustand der Container und Gültigkeit der Zertifikate</td></tr>
<tr><td><code>verify</code></td><td>prüft die Installation bis zur Anmeldung bei NATS</td></tr>
<tr><td><code>logs</code></td><td>folgt dem Log von NATS</td></tr>
<tr><td><code>restart</code></td><td>erstellt die NATS-Container neu</td></tr>
<tr><td><code>stop</code> / <code>start</code></td><td>hält den Stack an oder startet ihn, ohne Daten zu löschen</td></tr>
<tr><td><code>update</code></td><td>holt Images, baut neu und erstellt den Stack neu</td></tr>
<tr><td><code>backup</code></td><td>sichert JetStream</td></tr>
<tr><td><code>renew-certificate</code></td><td>erneuert das Serverzertifikat und startet NATS neu</td></tr>
<tr><td><code>config</code> / <code>config --edit</code></td><td>zeigt die Standortdaten oder fragt sie neu ab</td></tr>
<tr><td><code>ca-info</code> / <code>ca-show</code></td><td>Fingerprint und Inhalt der öffentlichen CA</td></tr>
<tr><td><code>probe status --all</code></td><td>Zustand aller Probes</td></tr>
</tbody>
</table>
<p>Alle Befehle laufen mit <code>sudo</code>.</p>`,
            `<table>
<thead><tr><th>Command</th><th>What it does</th></tr></thead>
<tbody>
<tr><td><code>status</code></td><td>container state and certificate validity</td></tr>
<tr><td><code>verify</code></td><td>checks the installation up to a sign-in to NATS</td></tr>
<tr><td><code>logs</code></td><td>follows the NATS log</td></tr>
<tr><td><code>restart</code></td><td>recreates the NATS containers</td></tr>
<tr><td><code>stop</code> / <code>start</code></td><td>stops or starts the stack without deleting data</td></tr>
<tr><td><code>update</code></td><td>pulls images, rebuilds and recreates the stack</td></tr>
<tr><td><code>backup</code></td><td>backs up JetStream</td></tr>
<tr><td><code>renew-certificate</code></td><td>renews the server certificate and restarts NATS</td></tr>
<tr><td><code>config</code> / <code>config --edit</code></td><td>shows the site settings or asks for them again</td></tr>
<tr><td><code>ca-info</code> / <code>ca-show</code></td><td>fingerprint and content of the public CA</td></tr>
<tr><td><code>probe status --all</code></td><td>state of every probe</td></tr>
</tbody>
</table>
<p>Every command runs with <code>sudo</code>.</p>`,
          ),
        },
        {
          title: L('Aktualisieren ohne Oberfläche', 'Update without the interface'),
          text: L(
            `<pre><code>cd /opt/prtg-nats
sudo ./prtg-nats backup
git status --short
git pull --ff-only
sudo ./prtg-nats update</code></pre>
<p>Nimm <code>prtg-nats update</code> statt <code>docker compose up -d --build</code>: Nur so weiß die Seite {ui:nav.updates}, welche Version läuft.</p>`,
            `<pre><code>cd /opt/prtg-nats
sudo ./prtg-nats backup
git status --short
git pull --ff-only
sudo ./prtg-nats update</code></pre>
<p>Use <code>prtg-nats update</code> rather than <code>docker compose up -d --build</code>: only then does the {ui:nav.updates} page know which version is running.</p>`,
          ),
        },
      ],
      notes: [
        L(
          'Befehle wie <code>verify</code>, <code>backup</code> und <code>renew-certificate</code> brauchen den laufenden Stack. Ist er gestoppt, sagt der Befehl das; starte ihn dann mit <code>sudo ./prtg-nats start</code>.',
          'Commands such as <code>verify</code>, <code>backup</code> and <code>renew-certificate</code> need the running stack. If it is stopped, the command says so; start it with <code>sudo ./prtg-nats start</code>.',
        ),
        L(
          'Jeder Befehl, der auf den Stack wirkt, nennt zuerst sein Arbeitsverzeichnis. Prüfe die Zeile, wenn auf dem Host mehrere Installationsverzeichnisse liegen.',
          'Every command that acts on the stack names its working directory first. Check that line when the host holds more than one installation directory.',
        ),
        L(
          '<code>prtg-nats help</code> listet alle Befehle. Die vollständige Beschreibung steht in der Befehlsreferenz im Repository.',
          '<code>prtg-nats help</code> lists every command. The full description is in the command reference in the repository.',
        ),
        L(
          'Führe nie <code>docker compose down --volumes</code> aus. Das löscht die Installation und die Daten von JetStream.',
          'Never run <code>docker compose down --volumes</code>. It deletes the installation and the JetStream data.',
        ),
      ],
      related: ['troubleshooting', 'update-the-installation', 'maintenance'],
    },
    {
      slug: 'manual-probe-install',
      title: L('Eine Probe ohne Einladung installieren', 'Installing a probe without an invitation'),
      short: L(
        'Wenn der Befehl zur Einbindung auf einer Probe nicht laufen kann: vom Server aus über SSH oder von Hand.',
        'When the enrollment command cannot run on a probe: from the server over SSH, or by hand.',
      ),
      lead: L(
        'Der normale Weg ist der Befehl aus <a href="@enroll-a-probe">Eine Probe einbinden</a>. Kann er nicht laufen, etwa weil die Probe die Oberfläche nicht erreicht, installierst du sie vom Server aus.',
        'The normal way is the command from <a href="@enroll-a-probe">Enrolling a probe</a>. If it cannot run, for example because the probe cannot reach the interface, you install it from the server.',
      ),
      role: 'administrator',
      keywords: L(
        'manuell installieren install-mpp ssh probe enroll ohne einladung',
        'manual install install-mpp ssh probe enroll without invitation',
      ),
      steps: [
        {
          title: L('Vom Server aus über SSH', 'From the server over SSH'),
          text: L(
            `<pre><code>sudo ./prtg-nats install-mpp --nats-user probe-kiel-01 root@198.51.100.50</code></pre>
<p>Der Befehl meldet sich einmal mit dem Passwort oder SSH-Schlüssel dieses Kontos an; bestätige den angezeigten Host-Key nur nach einem Vergleich über einen sicheren Weg. Er legt das NATS-Konto an, installiert Paket, CA und Konfiguration und bindet die Probe ein.</p>`,
            `<pre><code>sudo ./prtg-nats install-mpp --nats-user probe-kiel-01 root@198.51.100.50</code></pre>
<p>The command signs in once with that account’s password or SSH key; confirm the host key it shows only after comparing it over a trusted channel. It creates the NATS account, installs package, CA and configuration and enrolls the probe.</p>`,
          ),
        },
        {
          title: L('Oder von Hand auf der Probe', 'Or by hand on the probe'),
          text: L(
            `<p>Erreicht der Server die Probe noch nicht, lege unter {ui:nav.infrastructure} › {ui:nav.credentials} mit {ui:credentials.create} ein NATS-Konto an. Kopiere <code>install-mpp.sh</code> aus dem Installationsverzeichnis auf die Probe und führe es dort aus; das Passwort des Kontos fragt es verdeckt ab:</p>
<pre><code>sudo ./install-mpp.sh --nats-host nats.example.test --nats-user probe-kiel-01</code></pre>`,
            `<p>If the server cannot reach the probe yet, create a NATS account under {ui:nav.infrastructure} › {ui:nav.credentials} with {ui:credentials.create}. Copy <code>install-mpp.sh</code> from the installation directory to the probe and run it there; it asks for the account’s password without echo:</p>
<pre><code>sudo ./install-mpp.sh --nats-host nats.example.test --nats-user probe-kiel-01</code></pre>`,
          ),
        },
        {
          title: L('Von Hand installierte Probe einbinden', 'Enroll a probe installed by hand'),
          text: L(
            `<p>Sobald der Server die Probe über SSH erreicht, bindest du sie ein:</p>
<pre><code>sudo ./prtg-nats probe enroll probe-kiel-01 root@198.51.100.50</code></pre>`,
            `<p>As soon as the server can reach the probe over SSH, enroll it:</p>
<pre><code>sudo ./prtg-nats probe enroll probe-kiel-01 root@198.51.100.50</code></pre>`,
          ),
        },
        {
          title: L('In PRTG bestätigen', 'Approve in PRTG'),
          text: L(
            '<p>Danach geht es weiter wie bei jeder Einbindung: Access Key in PRTG eintragen und die Probe dort bestätigen, siehe <a href="@enroll-a-probe">Eine Probe einbinden</a>.</p>',
            '<p>From here it continues like any enrollment: enter the access key in PRTG and approve the probe there, see <a href="@enroll-a-probe">Enrolling a probe</a>.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Vorher testen, ob die Probe den Server so erreicht, wie sie ihn braucht: <code>sudo ./install-mpp.sh --nats-host nats.example.test --check-only</code>. Der Test ändert nichts und nennt die Stufe, an der es scheitert.',
          'Test beforehand whether the probe reaches the server the way it needs to: <code>sudo ./install-mpp.sh --nats-host nats.example.test --check-only</code>. The test changes nothing and names the stage where it fails.',
        ),
        L(
          'Ist Port 80 zum Server gesperrt, fragt <code>install-mpp.sh</code> nach der CA zum Einfügen. Den Inhalt zeigt <code>sudo ./prtg-nats ca-show</code> auf dem Server.',
          'If port 80 to the server is blocked, <code>install-mpp.sh</code> asks for the CA to be pasted in. <code>sudo ./prtg-nats ca-show</code> on the server prints it.',
        ),
        L(
          'Gib als NATS-Host immer den Namen an, nie nur die Adresse: Der Name muss zum Zertifikat des Servers passen.',
          'Always give the name as the NATS host, never just the address: the name has to match the server’s certificate.',
        ),
      ],
      related: ['enroll-a-probe', 'link-to-prtg', 'troubleshooting'],
    },
    {
      slug: 'troubleshooting',
      title: L('Fehlersuche', 'Troubleshooting'),
      short: L(
        'Die häufigsten Probleme mit Server, Probes und PRTG – und was du prüfst.',
        'The most common problems with the server, the probes and PRTG – and what to check.',
      ),
      lead: L(
        'Läuft die Oberfläche, beantwortet das Protokoll eines fehlgeschlagenen Jobs das meiste: Es nennt den Schritt, die wahrscheinliche Ursache und was zu tun ist. Für alles andere hilft diese Tabelle.',
        'If the interface is running, the log of a failed job answers most questions: it names the step, the likely cause and what to do. For everything else, use this table.',
      ),
      role: 'administrator',
      keywords: L(
        'fehler problem tls zertifikat authorization violation io error port firewall hängt',
        'error problem tls certificate authorization violation io error port firewall stuck',
      ),
      body: L(
        `<table>
<thead><tr><th>Was du siehst</th><th>Übliche Ursache</th><th>Was du tust</th></tr></thead>
<tbody>
<tr><td>Die Oberfläche ist nach der Installation nicht erreichbar, der Proxy startet immer wieder neu</td><td>Die Einrichtung ist nicht gelaufen, es gibt noch kein Zertifikat</td><td><code>sudo ./prtg-nats setup</code></td></tr>
<tr><td>Der Browser warnt vor dem Zertifikat</td><td>Die Oberfläche nutzt die eigene CA der Installation</td><td>CA von <code>http://nats.example.test/nats-ca.pem</code> im Browser hinterlegen, Fingerprint mit <code>sudo ./prtg-nats ca-info</code> vergleichen</td></tr>
<tr><td>Die Übersicht zeigt {ui:dashboard.setupTitle}</td><td>Die Initialisierung ist unvollständig</td><td>{ui:dashboard.setupAction}</td></tr>
<tr><td>Die Probe meldet <code>tls: unknown certificate authority</code></td><td>Falsche oder fehlende CA auf der Probe</td><td><code>sudo ./prtg-nats probe install-ca NAME</code>, dann <code>sudo ./prtg-nats probe configure NAME</code></td></tr>
<tr><td>Der Name passt nicht zum Zertifikat</td><td>Adresse statt Name verwendet, oder falscher DNS-Eintrag</td><td><code>nats.example.test</code> verwenden und DNS korrigieren</td></tr>
<tr><td><code>Authorization Violation</code></td><td>Falsches NATS-Konto oder Passwort</td><td>Zugangsdaten prüfen; nicht mit der Anmeldung an PRTG oder dem Access Key verwechseln</td></tr>
<tr><td><code>nats: IO error</code> in einer Schleife, obwohl ein Porttest den Server erreicht</td><td>Eine Firewall mit Applikationserkennung setzt die Verbindung beim Wechsel auf TLS zurück</td><td>Im Log der Firewall nach einer Ablehnung als <code>unknown-tcp</code> suchen und NATS auf dem Port erlauben</td></tr>
<tr><td><code>connection refused</code> oder Timeout</td><td>Firewall, DNS oder Container nicht bereit</td><td>Netzwerkweg prüfen, auf dem Server <code>sudo ./prtg-nats status</code></td></tr>
<tr><td>Die Probe erscheint nicht in PRTG</td><td>Falscher Access Key, oder die GID wurde in PRTG abgelehnt</td><td>Access Key und <i>Deny GIDs</i> in PRTG prüfen, dann den Dienst der Probe neu starten</td></tr>
<tr><td>Probes werden als still gemeldet, Sensor-Logs fehlen</td><td>Der Port des Rückkanals ist nicht offen</td><td>Freigabe nach <a href="@firewall-and-ports">Firewall und Ports</a></td></tr>
<tr><td>Der Download der CA klappt nicht</td><td>Port 80, DNS, oder der Proxy läuft nicht</td><td>CA mit <code>sudo ./prtg-nats ca-show</code> ausgeben und von Hand kopieren</td></tr>
<tr><td>Ein Job bleibt auf {ui:status.job.running}, Abbrechen wirkt nicht</td><td>Der Container der Oberfläche wurde während des Jobs neu gestartet</td><td><code>docker restart prtg-nats-web-api</code>, aber nur, wenn das Protokoll des Jobs nicht mehr wächst</td></tr>
<tr><td>Nach einem Update <code>bind: address already in use</code>, der Proxy startet immer wieder neu</td><td>Ein Container aus einem älteren Installationsverzeichnis hält den Port</td><td><code>start</code>, <code>update</code> und <code>setup</code> nennen ihn; entfernen mit <code>docker rm -f NAME</code></td></tr>
<tr><td>Die Oberfläche kommt nach einem Update nicht zurück</td><td>Der neue Stack startet nicht</td><td><code>docker compose logs web-api</code> zeigt den Grund</td></tr>
</tbody>
</table>`,
        `<table>
<thead><tr><th>What you see</th><th>Usual cause</th><th>What to do</th></tr></thead>
<tbody>
<tr><td>The interface cannot be reached after the installation, and the proxy keeps restarting</td><td>The setup has not run, so there is no certificate yet</td><td><code>sudo ./prtg-nats setup</code></td></tr>
<tr><td>The browser warns about the certificate</td><td>The interface uses the installation’s own CA</td><td>trust the CA from <code>http://nats.example.test/nats-ca.pem</code> in the browser, compare the fingerprint with <code>sudo ./prtg-nats ca-info</code></td></tr>
<tr><td>The dashboard shows {ui:dashboard.setupTitle}</td><td>The initialisation is incomplete</td><td>{ui:dashboard.setupAction}</td></tr>
<tr><td>The probe reports <code>tls: unknown certificate authority</code></td><td>Wrong or missing CA on the probe</td><td><code>sudo ./prtg-nats probe install-ca NAME</code>, then <code>sudo ./prtg-nats probe configure NAME</code></td></tr>
<tr><td>The name does not match the certificate</td><td>An address used instead of the name, or a wrong DNS record</td><td>use <code>nats.example.test</code> and correct DNS</td></tr>
<tr><td><code>Authorization Violation</code></td><td>Wrong NATS account or password</td><td>check the credentials; do not confuse them with the PRTG sign-in or the access key</td></tr>
<tr><td><code>nats: IO error</code> in a loop, although a port test reaches the server</td><td>A firewall with application detection resets the connection when it switches to TLS</td><td>look for a deny as <code>unknown-tcp</code> in the firewall log and allow NATS on the port</td></tr>
<tr><td><code>connection refused</code> or a timeout</td><td>Firewall, DNS, or a container that is not ready</td><td>check the network path, run <code>sudo ./prtg-nats status</code> on the server</td></tr>
<tr><td>The probe does not appear in PRTG</td><td>Wrong access key, or the GID was denied in PRTG</td><td>check the access key and <i>Deny GIDs</i> in PRTG, then restart the probe’s service</td></tr>
<tr><td>Probes are reported as silent, sensor logs are missing</td><td>The return channel port is not open</td><td>open it as in <a href="@firewall-and-ports">Firewall and ports</a></td></tr>
<tr><td>The CA download fails</td><td>Port 80, DNS, or the proxy is not running</td><td>print the CA with <code>sudo ./prtg-nats ca-show</code> and copy it by hand</td></tr>
<tr><td>A job stays on {ui:status.job.running} and cancelling does nothing</td><td>The interface’s container was restarted during the job</td><td><code>docker restart prtg-nats-web-api</code>, but only when the job’s log no longer grows</td></tr>
<tr><td><code>bind: address already in use</code> after an update, and the proxy keeps restarting</td><td>A container from an older installation directory holds the port</td><td><code>start</code>, <code>update</code> and <code>setup</code> name it; remove it with <code>docker rm -f NAME</code></td></tr>
<tr><td>The interface does not come back after an update</td><td>The new stack does not start</td><td><code>docker compose logs web-api</code> shows why</td></tr>
</tbody>
</table>`,
      ),
      notes: [
        L(
          'Bei jedem Verbindungsproblem einer Probe zuerst auf der Probe testen: <code>sudo ./install-mpp.sh --nats-host nats.example.test --check-only</code>. Der Test geht denselben Weg wie die Probe und nennt die Stufe, an der es scheitert.',
          'For any connection problem of a probe, test on the probe first: <code>sudo ./install-mpp.sh --nats-host nats.example.test --check-only</code>. The test takes the same path as the probe and names the stage where it fails.',
        ),
        L(
          'Den Zustand der ganzen Installation prüft <code>sudo ./prtg-nats verify</code> auf dem Server oder {ui:system.verify.run} unter {ui:nav.system}.',
          '<code>sudo ./prtg-nats verify</code> on the server, or {ui:system.verify.run} under {ui:nav.system}, checks the state of the whole installation.',
        ),
        L(
          'Kommt die Oberfläche nach einem Update nicht zurück, reicht es nicht, den Code zurückzusetzen. Das Update hat vorher die Laufzeitdaten gesichert; zurück geht es nur zusammen mit diesem Archiv.',
          'If the interface does not come back after an update, resetting the code is not enough. The update archived the runtime data first; going back only works together with that archive.',
        ),
      ],
      related: ['follow-jobs', 'firewall-and-ports', 'command-line'],
    },
  ],
}
