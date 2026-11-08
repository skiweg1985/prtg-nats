import { L } from '../lib/text.mjs'

export const sensors = {
  id: 'sensors',
  illustration: 'sensors',
  illustrationAlt: L(
    'Eine Frau setzt Sensormodule in drei gleiche Probe-Gehäuse ein.',
    'A woman fits sensor modules into three identical probe cases.',
  ),
  title: L('Sensoren', 'Sensors'),
  who: L(
    'Für Operatoren, die Messungen auf die Probes bringen: welche Sensoren es gibt, wie sie ausgerollt und in PRTG angelegt werden.',
    'For operators who bring measurements to the probes: which sensors exist, how they are deployed and set up in PRTG.',
  ),
  articles: [
    {
      slug: 'available-sensors',
      title: L('Die mitgelieferten Sensoren', 'The sensors that ship with the platform'),
      short: L(
        'Was jeder Sensor misst, was er auf der Probe braucht und wie die Sensorliste ihn zeigt.',
        'What each sensor measures, what it needs on the probe and how the sensor list shows it.',
      ),
      lead: L(
        'Ein Sensor ist ein Skript, das die Plattform auf Probes ausrollt und das PRTG als Script-v2-Sensor ausführt. Diese Installation bringt fünf Sensoren mit.',
        'A sensor is a script the platform deploys to probes and PRTG runs as a Script v2 sensor. This installation ships five sensors.',
      ),
      role: 'viewer',
      illustration: 'sensors',
      illustrationAlt: L(
        'Eine Frau setzt Sensormodule in drei gleiche Probe-Gehäuse ein.',
        'A woman fits sensor modules into three identical probe cases.',
      ),
      keywords: L(
        'katalog script v2 wlan wifi speedtest iperf ping aruba uplink lte',
        'catalog script v2 wlan wifi speedtest iperf ping aruba uplink lte',
      ),
      appPaths: ['/sensors'],
      body: L(
        `<table>
<thead><tr><th>Sensor</th><th>Misst</th><th>Braucht</th></tr></thead>
<tbody>
<tr><td><code>wlan-auth</code></td><td>ob sich ein Gerät am Standort im WLAN anmelden kann (WPA2-PSK, PEAP/MSCHAPv2, EAP-TLS), und wie lange die einzelnen Stufen bis einschließlich DHCP dauern</td><td>ein eigenes Funk-Interface für die Tests, das auf der Probe für den Sensor reserviert ist; die Probe selbst ist auf anderem Weg im Netz, meist per Kabel. Je Netz eine <a href="@sensor-variants">Variante</a> mit SSID und Zugangsdaten.</td></tr>
<tr><td><code>internet-speed</code></td><td>Download, Upload und Latenz gegen speedtest.net – also ob der Provider die vertraglich zugesagte Leitung liefert</td><td>Zugriff der Probe auf speedtest.net und beim Ausrollen auf einen Python-Paketindex. Der Sensor lastet die Leitung bei jedem Lauf etwa zwanzig Sekunden lang voll aus.</td></tr>
<tr><td><code>link-quality</code></td><td>Paketverlust, Antwortzeit und Jitter gegen mehrere Ziele, getrennt nach Zielen im Internet und im eigenen Netz – so unterscheidest du „Internet weg“ von „nur der Tunnel weg“</td><td>nichts Zusätzliches; den privilegierten Hilfsdienst für ICMP richtet der Rollout selbst ein.</td></tr>
<tr><td><code>iperf-throughput</code></td><td>Durchsatz, Verlust, Jitter und Latenz gegen einen eigenen iperf3-Messpunkt – also ob der Standort seine Arbeit erledigen kann</td><td>einen <a href="@iperf-endpoints">Messpunkt</a>, der der Probe zugewiesen ist.</td></tr>
<tr><td><code>aruba-uplink</code></td><td>welcher Uplink eines Aruba-Gateways den Standort gerade trägt, die Pfadqualität jedes Uplinks und das LTE-Signal; erzeugt selbst keinen Verkehr</td><td>ein Gateway mit AOS-10, das die Probe per HTTPS erreicht, und ein Konto auf dem Gateway, das nur lesen darf. Adresse und Konto setzt PRTG über Platzhalter ein.</td></tr>
</tbody>
</table>`,
        `<table>
<thead><tr><th>Sensor</th><th>Measures</th><th>Needs</th></tr></thead>
<tbody>
<tr><td><code>wlan-auth</code></td><td>whether a device at the site can sign in to a Wi-Fi network (WPA2-PSK, PEAP/MSCHAPv2, EAP-TLS), and how long each stage up to and including DHCP takes</td><td>a dedicated radio interface for the tests, reserved for the sensor on the probe; the probe itself is on the network by another route, usually by cable. One <a href="@sensor-variants">variant</a> per network with SSID and credentials.</td></tr>
<tr><td><code>internet-speed</code></td><td>download, upload and latency against speedtest.net – whether the provider delivers the contracted line</td><td>access from the probe to speedtest.net and, while deploying, to a Python package index. The sensor saturates the line for about twenty seconds on every run.</td></tr>
<tr><td><code>link-quality</code></td><td>packet loss, round-trip time and jitter against several targets, split into targets on the internet and on your own network – so you can tell “internet down” from “only the tunnel down”</td><td>nothing extra; the rollout sets up the privileged helper for ICMP itself.</td></tr>
<tr><td><code>iperf-throughput</code></td><td>throughput, loss, jitter and latency against your own iperf3 endpoint – whether the site can do its work</td><td>an <a href="@iperf-endpoints">iperf endpoint</a> assigned to the probe.</td></tr>
<tr><td><code>aruba-uplink</code></td><td>which uplink of an Aruba gateway currently carries the site, the path quality of each uplink and the LTE signal; produces no traffic of its own</td><td>a gateway running AOS-10 that the probe reaches over HTTPS, and a read-only account on the gateway. PRTG fills in the address and the account through placeholders.</td></tr>
</tbody>
</table>`,
      ),
      steps: [
        {
          title: L('Die Sensorliste', 'The sensor list'),
          text: L(
            '<p>Unter {ui:nav.sensors} steht jeder Sensor mit Version und Beschreibung. Die Beschreibung bringt der Sensor selbst mit, deshalb ist sie englisch. Unter dem Namen steht, was er von einer Probe braucht, etwa {ui:sensors.metaInterface} oder {ui:sensors.metaEndpoint}.</p><p>{ui:sensors.columns.installed} zählt die Probes, auf denen der Sensor liegt, {ui:sensors.columns.outdated} die mit einer älteren Version. Ein Klick auf diese Zahl öffnet einen Rollout mit genau diesen Probes. Neue Sensorversionen kommen mit einem Update der Installation.</p>',
            '<p>Under {ui:nav.sensors} every sensor is listed with its version and description. The description comes with the sensor itself, which is why it is in English. Below the name you see what it needs from a probe, such as {ui:sensors.metaInterface} or {ui:sensors.metaEndpoint}.</p><p>{ui:sensors.columns.installed} counts the probes that have the sensor, {ui:sensors.columns.outdated} those running an older version. Clicking that number opens a rollout with exactly those probes. New sensor versions arrive with an update of the installation.</p>',
          ),
          image: 'sensors-list',
          alt: L(
            'Die Sensorliste mit fünf Sensoren, Version, Beschreibung und der Zahl der Probes',
            'The sensor list with five sensors, version, description and the number of probes',
          ),
        },
        {
          title: L('Die Seite eines Sensors', 'A sensor’s page'),
          text: L(
            '<p>Ein Klick auf eine Zeile öffnet die Seite des Sensors. Dort stehen seine {ui:sensors.requirements}, unter {ui:sensors.prtg.title} der Skriptname für PRTG, die Probes, auf denen er installiert ist, und seine {ui:sensors.parameters}. Die ausführliche Anleitung des Sensors klappst du unter {ui:sensors.prtg.readme} auf.</p>',
            '<p>Clicking a row opens the sensor’s page. It shows the {ui:sensors.requirements}, the script name for PRTG under {ui:sensors.prtg.title}, the probes it is installed on, and its {ui:sensors.parameters}. The sensor’s full manual unfolds under {ui:sensors.prtg.readme}.</p>',
          ),
        },
      ],
      unnumbered: true,
      related: ['deploy-sensors', 'sensor-variants', 'iperf-endpoints'],
    },
    {
      slug: 'deploy-sensors',
      title: L('Einen Sensor ausrollen', 'Deploying a sensor'),
      short: L(
        'Einen Sensor auf eine oder mehrere Probes bringen, den Rollout verfolgen und den Sensor in PRTG anlegen.',
        'Bring a sensor to one or several probes, follow the rollout and set the sensor up in PRTG.',
      ),
      lead: L(
        'Ein Rollout installiert einen Sensor auf den Probes, die du auswählst, und prüft auf jeder, ob er läuft. Gemessen wird erst, wenn du den Sensor danach in PRTG anlegst.',
        'A rollout installs a sensor on the probes you choose and checks on each one that it runs. Nothing is measured until you then set the sensor up in PRTG.',
      ),
      role: 'operator',
      keywords: L(
        'ausrollen rollout installieren aktualisieren probelauf script v2 parameterzeile',
        'deploy rollout install update dry run script v2 parameter line',
      ),
      steps: [
        {
          title: L('Den Rollout öffnen', 'Open the rollout'),
          text: L(
            '<p>Öffne unter {ui:nav.sensors} die Seite des Sensors und wähle {ui:sensors.deployTo}. Denselben Dialog erreichst du über {ui:jobs.filters.rollouts} unter {ui:nav.jobs} mit {ui:deployments.create}, über ausgewählte Probes in der Probe-Liste mit {ui:sensors.deploy} und auf der Seite einer Probe im Reiter {ui:probes.tabs.sensors} mit {ui:probes.deploySensor}.</p>',
            '<p>Under {ui:nav.sensors}, open the sensor’s page and choose {ui:sensors.deployTo}. The same dialog opens from {ui:jobs.filters.rollouts} under {ui:nav.jobs} with {ui:deployments.create}, from selected probes in the probe list with {ui:sensors.deploy}, and on a probe’s page in the {ui:probes.tabs.sensors} tab with {ui:probes.deploySensor}.</p>',
          ),
        },
        {
          title: L('Probes auswählen', 'Choose the probes'),
          text: L(
            '<p>Hake die Probes an; das Suchfeld grenzt die Liste ein, {ui:deployments.selectAll} nimmt alle Treffer. Läuft auf einer Probe eine ältere Version, steht der Versionswechsel daneben. Hinweise im Dialog nennen, was auf den gewählten Probes noch fehlt, zum Beispiel ein Messpunkt oder ein reserviertes Funk-Interface.</p>',
            '<p>Tick the probes; the search field narrows the list, {ui:deployments.selectAll} takes every match. Where a probe runs an older version, the version change is shown next to it. Notices in the dialog name what the chosen probes still lack, for example an iperf endpoint or a reserved radio interface.</p>',
          ),
          image: 'sensors-deploy-dialog',
          alt: L(
            'Der Dialog für einen neuen Rollout mit Sensor, Probe-Auswahl und Probelauf',
            'The new deployment dialog with sensor, probe selection and dry run',
          ),
          narrow: true,
        },
        {
          title: L('Starten und verfolgen', 'Start and follow it'),
          text: L(
            '<p>Mit {ui:deployments.start} beginnt der Rollout, und die Seite des Jobs öffnet sich. Die Probes kommen nacheinander an die Reihe; schlägt der Sensor auf einer Probe fehl, stellt sie ihren vorherigen Stand wieder her. Wie es auf jeder Probe ausging, steht unter {ui:jobs.result}.</p>',
            '<p>{ui:deployments.start} begins the rollout and opens the job’s page. The probes are handled one after another; if the sensor fails on a probe, that probe restores its previous state. How it went on each probe is shown under {ui:jobs.result}.</p>',
          ),
        },
        {
          title: L('Den Sensor in PRTG anlegen', 'Set the sensor up in PRTG'),
          text: L(
            '<p>Lege in PRTG auf dem Gerät der Probe einen Script-v2-Sensor an. Den Skriptnamen für das Auswahlfeld kopierst du auf der Seite des Sensors unter {ui:sensors.prtg.title}, die Parameterzeile unter {ui:sensors.parameters} oder bei der passenden Variante.</p>',
            '<p>In PRTG, add a Script v2 sensor on the probe’s device. Copy the script name for the dropdown from the sensor’s page under {ui:sensors.prtg.title}, and the parameter line from {ui:sensors.parameters} or from the matching variant.</p>',
          ),
          image: 'sensors-prtg-card',
          alt: L(
            'Die Karte „In PRTG“ mit dem Skriptnamen und der Schaltfläche zum Kopieren',
            'The “In PRTG” card with the script name and the copy button',
          ),
          narrow: true,
        },
      ],
      notes: [
        L(
          'Mit {ui:deployments.dryRun} siehst du, was passieren würde, ohne eine Probe zu verändern. War der Probelauf erfolgreich, startet {ui:jobs.deploy.runForReal} auf der Seite des Jobs den echten Rollout mit denselben Probes.',
          'With {ui:deployments.dryRun} you see what would happen without changing a probe. After a successful dry run, {ui:jobs.deploy.runForReal} on the job’s page starts the real rollout with the same probes.',
        ),
        L(
          '<code>wlan-auth</code> misst erst, wenn auf der Probe ein Funk-Interface für ihn reserviert ist. Das erledigst du nach dem Rollout auf der <a href="@probe-details">Seite der Probe</a> im Reiter {ui:probes.tabs.sensors} unter {ui:probes.interfaces.title}.',
          '<code>wlan-auth</code> does not measure until a radio interface is reserved for it on the probe. You do that after the rollout on the <a href="@probe-details">probe’s page</a> in the {ui:probes.tabs.sensors} tab under {ui:probes.interfaces.title}.',
        ),
        L(
          'Steht ein Sensor auf einer Probe auf {ui:status.sensor.outdated} oder {ui:status.sensor.drifted}, rollst du ihn im Reiter {ui:probes.tabs.sensors} der Probe mit {ui:sensors.updateOnProbe} erneut aus.',
          'When a sensor on a probe shows {ui:status.sensor.outdated} or {ui:status.sensor.drifted}, roll it out again with {ui:sensors.updateOnProbe} in the probe’s {ui:probes.tabs.sensors} tab.',
        ),
        L(
          'Warnt der Dialog vor einem veralteten Verwaltungshelfer, aktualisiere ihn zuerst auf der Seite der Probe mit {ui:probes.updateHelper}.',
          'If the dialog warns about an outdated management helper, update it first on the probe’s page with {ui:probes.updateHelper}.',
        ),
      ],
      related: ['follow-jobs', 'sensor-variants', 'available-sensors'],
    },
    {
      slug: 'sensor-variants',
      title: L('Varianten anlegen und bearbeiten', 'Creating and editing variants'),
      short: L(
        'Einstellungen, Zugangsdaten und Dateien für einen Zweck bündeln, etwa ein WLAN oder einen Standort.',
        'Bundle settings, credentials and files for one purpose, such as one Wi-Fi network or one site.',
      ),
      lead: L(
        'Eine Variante hält Einstellungen, Zugangsdaten und Dateien für einen Zweck, etwa eine SSID. In PRTG wird daraus ein eigener Sensor, der nur noch den Namen der Variante als Parameter braucht.',
        'A variant holds the settings, credentials and files for one purpose, such as one SSID. In PRTG it becomes a sensor of its own that needs only the variant’s name as its parameter.',
      ),
      role: 'operator',
      keywords: L('variante ssid zugangsdaten zertifikat --profile', 'variant profile ssid credentials certificate --profile'),
      steps: [
        {
          title: L('Eine Variante anlegen', 'Create a variant'),
          text: L(
            '<p>Öffne unter {ui:nav.sensors} die Seite des Sensors und wähle bei {ui:sensors.variants.title} {ui:sensors.variants.add}. Den Abschnitt gibt es nur bei Sensoren, die Varianten vorsehen; von den mitgelieferten ist das <code>wlan-auth</code>.</p>',
            '<p>Under {ui:nav.sensors}, open the sensor’s page and choose {ui:sensors.variants.add} under {ui:sensors.variants.title}. The section only exists for sensors that support variants; of those that ship with the platform, that is <code>wlan-auth</code>.</p>',
          ),
        },
        {
          title: L('Name und Werte eintragen', 'Enter the name and values'),
          text: L(
            '<p>Der {ui:sensors.variants.name} steht später als Parameter in PRTG; erlaubt sind Buchstaben, Ziffern, Punkt, Bindestrich und Unterstrich. Fülle {ui:sensors.variants.settings}, {ui:sensors.variants.credentials} und {ui:sensors.variants.files} aus. Das Formular zeigt nur die Felder, die zur gewählten Anmeldeart passen.</p>',
            '<p>The {ui:sensors.variants.name} becomes the parameter in PRTG; letters, digits, dot, dash and underscore are allowed. Fill in {ui:sensors.variants.settings}, {ui:sensors.variants.credentials} and {ui:sensors.variants.files}. The form only shows the fields that fit the chosen authentication method.</p>',
          ),
          image: 'sensors-variant-dialog',
          alt: L(
            'Der Dialog „Neue Variante“ mit Name, Einstellungen, Zugangsdaten und Probe-Auswahl',
            'The “New variant” dialog with name, settings, credentials and probe selection',
          ),
        },
        {
          title: L('Probes wählen und speichern', 'Choose probes and save'),
          text: L(
            '<p>Hake unter {ui:sensors.variants.probes} die Probes an, auf denen die Variante liegen soll, und wähle {ui:common.save}. Die Plattform rollt sie in einem Job aus. Ohne Auswahl liegt die Variante nur auf dem Server und trägt den Hinweis {ui:sensors.variants.notDeployed}.</p>',
            '<p>Under {ui:sensors.variants.probes}, tick the probes the variant belongs on and choose {ui:common.save}. The platform deploys it in a job. With none selected the variant is only stored on the server and is marked {ui:sensors.variants.notDeployed}.</p>',
          ),
        },
        {
          title: L('In PRTG verwenden', 'Use it in PRTG'),
          text: L(
            '<p>In PRTG trägt der Parameter <code>--profile</code> den Namen der Variante. Die fertige Zeile steht neben der Variante zum Kopieren. Bei <code>wlan-auth</code> kommt je Probe noch <code>--interface</code> mit dem reservierten Funk-Interface dazu.</p>',
            '<p>In PRTG, the <code>--profile</code> parameter carries the name of the variant. The finished line stands next to the variant, ready to copy. For <code>wlan-auth</code>, add <code>--interface</code> with the reserved radio interface of each probe.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Mit {ui:common.edit} änderst du Werte und Probes einer Variante. Gespeicherte Passwörter zeigt das Formular nie wieder an; ein leeres Feld lässt den gespeicherten Wert unverändert. Eine hinterlegte Datei ersetzt du, indem du eine neue wählst.',
          '{ui:common.edit} changes a variant’s values and probes. The form never shows stored passwords again; an empty field keeps the stored value. To replace a stored file, choose a new one.',
        ),
        L(
          'Über {ui:common.moreActions} und {ui:common.remove} löschst du eine Variante vom Server und von allen Probes, die sie halten. PRTG-Sensoren, die sie nennen, schlagen danach fehl.',
          '{ui:common.moreActions} and {ui:common.remove} delete a variant from the server and from every probe that holds it. PRTG sensors naming it fail afterwards.',
        ),
        L(
          'Wer den Sensor auf eine Probe ausrollt, bringt die Varianten, die dieser Probe zugewiesen sind, gleich mit. Welche Varianten eine Probe hält, steht auf ihrer Seite im Reiter {ui:probes.tabs.sensors} unter {ui:probes.variants.title}.',
          'Deploying the sensor to a probe brings along the variants assigned to that probe. Which variants a probe holds is shown on its page in the {ui:probes.tabs.sensors} tab under {ui:probes.variants.title}.',
        ),
      ],
      related: ['deploy-sensors', 'available-sensors'],
    },
    {
      slug: 'remove-sensors',
      title: L('Einen Sensor entfernen', 'Removing a sensor'),
      short: L(
        'Einen Sensor wieder von einer Probe nehmen, samt seiner Konfiguration und Varianten.',
        'Take a sensor off a probe again, together with its configuration and variants.',
      ),
      lead: L(
        'Ein Sensor wird je Probe entfernt, auf der Seite der Probe. Dabei verschwinden auch seine Konfiguration und die Varianten auf dieser Probe.',
        'A sensor is removed per probe, on the probe’s page. Its configuration and the variants on that probe go with it.',
      ),
      role: 'operator',
      keywords: L('entfernen löschen deinstallieren', 'remove delete uninstall'),
      steps: [
        {
          text: L(
            '<p>Öffne unter {ui:nav.probes} die Probe und wechsle in den Reiter {ui:probes.tabs.sensors}.</p>',
            '<p>Under {ui:nav.probes}, open the probe and switch to the {ui:probes.tabs.sensors} tab.</p>',
          ),
        },
        {
          text: L(
            '<p>Wähle in der Zeile des Sensors {ui:common.moreActions} und dann {ui:sensors.remove}.</p>',
            '<p>In the sensor’s row, choose {ui:common.moreActions} and then {ui:sensors.remove}.</p>',
          ),
        },
        {
          text: L(
            '<p>Bestätige mit {ui:sensors.remove}. Die Seite des Jobs öffnet sich und zeigt, wie das Entfernen verläuft.</p>',
            '<p>Confirm with {ui:sensors.remove}. The job’s page opens and shows how the removal goes.</p>',
          ),
          image: 'sensors-remove-confirm',
          alt: L(
            'Die Rückfrage vor dem Entfernen eines Sensors von einer Probe',
            'The confirmation before removing a sensor from a probe',
          ),
          narrow: true,
        },
        {
          text: L(
            '<p>Lösche den Sensor danach auch in PRTG. Bis dahin meldet er dort einen Fehler.</p>',
            '<p>Then delete the sensor in PRTG as well. Until you do, it reports an error there.</p>',
          ),
        },
      ],
      notes: [
        L(
          'Ein reserviertes Funk-Interface gibt die Probe beim Entfernen wieder frei.',
          'A reserved radio interface is released when the sensor is removed.',
        ),
        L(
          'Wer eine Probe ausbindet, kann dabei alle ihre Sensoren auf einmal entfernen, siehe <a href="@unenroll-a-probe">Eine Probe ausbinden</a>.',
          'When a probe is unenrolled, all of its sensors can be removed in one go, see <a href="@unenroll-a-probe">Unenrolling a probe</a>.',
        ),
      ],
      related: ['deploy-sensors', 'probe-details'],
    },
  ],
}
