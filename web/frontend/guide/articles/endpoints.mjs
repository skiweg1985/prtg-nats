import { L } from '../lib/text.mjs'

export const endpoints = {
  id: 'endpoints',
  illustration: 'endpoints',
  illustrationAlt: L(
    'Zwei Server auf Sockeln, zwischen ihnen ein Bogen aus Punkten; eine Frau liest am Laptop eine Messung ab.',
    'Two servers on pedestals with an arc of dots between them; a woman reads a measurement on her laptop.',
  ),
  title: L('Messpunkte', 'iperf endpoints'),
  who: L(
    'Für Operatoren, die Durchsatz mit dem Sensor iperf-throughput messen: Messpunkte einrichten, eintragen und den Probes zuweisen.',
    'For operators measuring throughput with the iperf-throughput sensor: set up and add endpoints and assign them to probes.',
  ),
  articles: [
    {
      slug: 'iperf-endpoints',
      title: L('Messpunkte einrichten und betreuen', 'Setting up and looking after iperf endpoints'),
      short: L(
        'Ein Messpunkt für den Sensor iperf-throughput: einrichten, prüfen, Passwort wechseln, entfernen.',
        'An endpoint for the iperf-throughput sensor: set it up, check it, change its password, remove it.',
      ),
      lead: L(
        'Ein Messpunkt ist ein Host mit iperf3, gegen den der Sensor iperf-throughput den Durchsatz einer Probe misst. Die Plattform richtet ihn ein, hält seine Zugangsdaten und gibt sie an die Probes weiter.',
        'An iperf endpoint is a host running iperf3 that the iperf-throughput sensor measures a probe’s throughput against. The platform sets it up, keeps its credentials and hands them to the probes.',
      ),
      role: 'operator',
      illustration: 'endpoints',
      illustrationAlt: L(
        'Zwei Server auf Sockeln, zwischen ihnen ein Bogen aus Punkten; eine Frau liest am Laptop eine Messung ab.',
        'Two servers on pedestals with an arc of dots between them; a woman reads a measurement on her laptop.',
      ),
      keywords: L(
        'iperf iperf3 durchsatz bandbreite endpunkt einladung passwort wechseln entfernen',
        'iperf iperf3 throughput bandwidth endpoint invitation change password remove',
      ),
      appPaths: ['/infrastructure/iperf'],
      steps: [
        {
          title: L('Host benennen', 'Name the host'),
          text: L(
            '<p>Öffne {ui:nav.iperf} und wähle {ui:infrastructure.iperf.add}. Gib {ui:infrastructure.iperf.columns.name}, {ui:infrastructure.iperf.host}, {ui:infrastructure.iperf.sshPort} und {ui:infrastructure.iperf.iperfPort} an, zum Beispiel <code>iperf-zentrale</code> unter <code>203.0.113.20</code>. Unter diesem Namen tragen die Probes die Zugangsdaten, und er steht später im Sensor.</p>',
            '<p>Open {ui:nav.iperf} and choose {ui:infrastructure.iperf.add}. Enter {ui:infrastructure.iperf.columns.name}, {ui:infrastructure.iperf.host}, {ui:infrastructure.iperf.sshPort} and {ui:infrastructure.iperf.iperfPort}, for example <code>iperf-zentrale</code> at <code>203.0.113.20</code>. The probes carry the credentials under this name, and the sensor refers to it later.</p>',
          ),
        },
        {
          title: L('Hostschlüssel prüfen', 'Check the host keys'),
          text: L(
            '<p>{ui:infrastructure.iperf.scan} zeigt die SSH-Fingerabdrücke des Hosts. Vergleiche sie mit dem Host, bevor du im zweiten Schritt ein Administratorkonto eingibst.</p>',
            '<p>{ui:infrastructure.iperf.scan} shows the host’s SSH fingerprints. Compare them with the host before you enter an administrator account in the second step.</p>',
          ),
          image: 'endpoints-setup-dialog',
          alt: L(
            'Der Dialog „Messpunkt einrichten“ mit gelesenen Hostschlüsseln und den Feldern für die Anmeldung',
            'The “Set up a measurement endpoint” dialog with the host keys read and the sign-in fields',
          ),
          narrow: true,
        },
        {
          title: L('Einmal anmelden und einrichten', 'Sign in once and set it up'),
          text: L(
            '<p>Gib unter {ui:infrastructure.iperf.adminUser} ein Konto an, das auf dem Host root werden darf, mit {ui:auth.password} oder {ui:infrastructure.iperf.privateKey}. {ui:infrastructure.iperf.sourceCidr} ist das Netz, aus dem der Host den Zugang der Plattform annimmt, etwa <code>192.0.2.10/32</code>. Leer lassen kannst du es nur, wenn auf dem Server eine Vorgabe dafür gesetzt ist.</p>',
            '<p>Under {ui:infrastructure.iperf.adminUser}, enter an account that may become root on the host, with {ui:auth.password} or {ui:infrastructure.iperf.privateKey}. {ui:infrastructure.iperf.sourceCidr} is the network the host accepts the platform’s access from, for example <code>192.0.2.10/32</code>. You can leave it empty only when the server has a default set for it.</p>',
          ),
        },
        {
          title: L('Job abwarten', 'Wait for the job'),
          text: L(
            '<p>{ui:infrastructure.iperf.setUp} startet einen Job, der iperf3 auf dem Host einrichtet. Ist er durch, steht der Messpunkt in der Liste. Gemessen wird erst, wenn du ihn <a href="@endpoint-credentials">Probes zuweist</a> und der Sensor dort ausgerollt ist.</p>',
            '<p>{ui:infrastructure.iperf.setUp} starts a job that sets up iperf3 on the host. Once it has finished, the endpoint is in the list. Nothing is measured until you <a href="@endpoint-credentials">assign it to probes</a> and the sensor is deployed there.</p>',
          ),
        },
        {
          title: L('Die Seite des Messpunkts', 'The endpoint’s page'),
          text: L(
            '<p>Ein Klick auf eine Zeile öffnet den Messpunkt. {ui:infrastructure.iperf.stateTitle} zeigt Adresse, Port, Messbenutzer und wer ihn betreibt. Unter {ui:infrastructure.iperf.prtgTitle} steht je Probe, was das Sensorobjekt in PRTG braucht: {ui:infrastructure.iperf.noParameterNeeded} oder eine Parameterzeile zum Kopieren.</p>',
            '<p>Clicking a row opens the endpoint. {ui:infrastructure.iperf.stateTitle} shows the address, port, measurement user and who operates it. {ui:infrastructure.iperf.prtgTitle} says per probe what the sensor object in PRTG needs: {ui:infrastructure.iperf.noParameterNeeded}, or a parameter line to copy.</p>',
          ),
          image: 'endpoints-detail',
          alt: L(
            'Die Seite des Messpunkts iperf-zentrale mit Zustand und den Probes, die ihn halten',
            'The page of the iperf-zentrale endpoint with its state and the probes holding it',
          ),
        },
        {
          title: L('Hinweise oben auf der Seite', 'Notices at the top of the page'),
          text: L(
            '<p>{ui:infrastructure.iperf.notDeployedTitle} heißt: Der Eintrag steht, aber keine Probe hat die Zugangsdaten. {ui:infrastructure.iperf.sensorMissingTitle} heißt: Probes halten den Messpunkt, aber der Sensor fehlt dort. {ui:sensors.deployTo} rollt ihn auf genau diese Probes aus.</p>',
            '<p>{ui:infrastructure.iperf.notDeployedTitle} means the record exists, but no probe has the credentials. {ui:infrastructure.iperf.sensorMissingTitle} means probes hold the endpoint, but the sensor is missing there. {ui:sensors.deployTo} deploys it to exactly those probes.</p>',
          ),
        },
        {
          title: L('Passwort wechseln', 'Change the password'),
          text: L(
            '<p>{ui:infrastructure.iperf.rotate} setzt auf dem Host ein neues Passwort und gibt es im selben Job an alle Probes weiter, die gegen den Messpunkt messen. Sensorobjekte, die ihre Zugangsdaten vom Messpunkt beziehen, brauchen dafür keine Änderung in PRTG.</p>',
            '<p>{ui:infrastructure.iperf.rotate} sets a new password on the host and hands it, in the same job, to every probe measuring against the endpoint. Sensor objects that take their credentials from the endpoint need no change in PRTG.</p>',
          ),
        },
        {
          title: L('Messpunkt entfernen', 'Remove the endpoint'),
          text: L(
            '<p>Unter {ui:common.moreActions} wählst du {ui:common.remove}. Der Job nimmt die Zugangsdaten von den Probes, baut den Messpunkt auf dem Host ab und vergisst den Eintrag. Mit {ui:infrastructure.iperf.keepService} bleibt der iperf3-Dienst auf dem Host, wie er ist.</p>',
            '<p>Under {ui:common.moreActions}, choose {ui:common.remove}. The job takes the credentials off the probes, removes the endpoint from its host and forgets the record. With {ui:infrastructure.iperf.keepService}, the iperf3 service on the host stays as it is.</p>',
          ),
        },
      ],
      after: L(
        `<h2>Wenn die Plattform den Host nicht per SSH erreicht</h2>
<p>Steht der Host hinter NAT oder einer Firewall, wählst du {ui:infrastructure.iperf.invite}. Du gibst Name, Messport, Messbenutzer und Quellnetz an, die Adresse nur, wenn der Host sie nicht selbst mitbringt, und legst die {ui:infrastructure.iperf.inviteTtl} fest. {ui:infrastructure.iperf.inviteCreate} liefert einen Befehl, den jemand mit sudo-Recht auf dem Host ausführt. Der Host muss dafür nur diese Plattform erreichen. Der Dialog wartet, bis er sich meldet, und zeigt dann den Job.</p>
<p>Eine Einladung, die noch nicht eingelöst ist, steht unter {ui:infrastructure.iperf.inviteOpenTitle} und lässt sich dort mit {ui:probes.enroll.open.revoke} zurückziehen.</p>`,
        `<h2>When the platform cannot reach the host over SSH</h2>
<p>If the host sits behind NAT or a firewall, choose {ui:infrastructure.iperf.invite}. Enter the name, measurement port, measurement user and source network, the address only if the host does not bring it along itself, and set {ui:infrastructure.iperf.inviteTtl}. {ui:infrastructure.iperf.inviteCreate} gives you a command that somebody with sudo runs on the host. The host only has to reach this platform for it. The dialog waits until it reports back and then shows the job.</p>
<p>An invitation that has not been used yet is listed under {ui:infrastructure.iperf.inviteOpenTitle}, and {ui:probes.enroll.open.revoke} withdraws it there.</p>`,
      ),
      notes: [
        L(
          'Das Administratorkonto gilt nur für diese eine Verbindung. Es wird nicht gespeichert, nicht protokolliert und nicht in den Job geschrieben; danach hält die Plattform nur ihren eigenen, eingeschränkten Zugang zum Host.',
          'The administrator account is used for that one connection only. It is not stored, not logged and not written into the job; afterwards the platform holds only its own restricted access to the host.',
        ),
        L(
          'In der Liste markiert {ui:infrastructure.iperf.notDeployed} einen Messpunkt, den noch keine Probe hält. Die Zahl in der Spalte {ui:infrastructure.iperf.columns.deployedTo} öffnet direkt die Zuweisung.',
          'In the list, {ui:infrastructure.iperf.notDeployed} marks an endpoint no probe holds yet. The number in the {ui:infrastructure.iperf.columns.deployedTo} column opens the assignment straight away.',
        ),
        L(
          'Das iperf3-Paket wird beim Entfernen nie deinstalliert, weil etwas anderes auf dem Host es benutzen könnte.',
          'Removing an endpoint never uninstalls the iperf3 package, because something else on the host may be using it.',
        ),
        L(
          'Einen Host, den jemand anderes betreibt, richtest du nicht hier ein, sondern <a href="@external-endpoint">trägst ihn als externen Messpunkt ein</a>.',
          'A host somebody else operates is not set up here; you <a href="@external-endpoint">add it as an external endpoint</a> instead.',
        ),
      ],
      related: ['endpoint-credentials', 'external-endpoint', 'deploy-sensors'],
    },
    {
      slug: 'external-endpoint',
      title: L('Einen externen Messpunkt eintragen', 'Adding an external endpoint'),
      short: L(
        'Für einen iperf3-Host, den jemand anderes betreibt: was der Eintrag braucht und was dort laufen muss.',
        'For an iperf3 host somebody else operates: what the record needs and what has to run there.',
      ),
      lead: L(
        'Betreibt jemand anderes den iperf3-Host, installiert die Plattform dort nichts und meldet sich dort nie an. Du trägst nur Adresse und Zugangsdaten ein, damit die Probes dagegen messen können.',
        'When somebody else operates the iperf3 host, the platform installs nothing there and never signs in to it. You only add the address and credentials, so the probes can measure against it.',
      ),
      role: 'operator',
      keywords: L(
        'iperf iperf3 extern provider public.pem schlüssel passwort aktualisieren',
        'iperf iperf3 external foreign provider public.pem key update password',
      ),
      steps: [
        {
          title: L('Was der Betreiber einrichtet', 'What the operator sets up'),
          text: L(
            '<p>Der Host braucht iperf3 ab Version 3.17 mit Authentifizierung; <code>iperf3 --version</code> zeigt beides. Die Befehle für den Rest stehen im Dialog unter {ui:infrastructure.iperf.setupHelp}. Sie erzeugen ein Schlüsselpaar und ein Passwort und starten iperf3 so, dass es nur noch mit Anmeldung antwortet.</p>',
            '<p>The host needs iperf3 version 3.17 or newer with authentication; <code>iperf3 --version</code> shows both. The commands for the rest are in the dialog under {ui:infrastructure.iperf.setupHelp}. They create a key pair and a password and start iperf3 so that it only answers with a sign-in.</p>',
          ),
        },
        {
          title: L('Was zurückkommen muss', 'What has to come back'),
          text: L(
            '<p>Vom Betreiber brauchst du den Messbenutzer, das Passwort und den Inhalt der Datei <code>public.pem</code>. Bitte ihn um die Gegenprobe aus dem Dialog: Ohne Zugangsdaten muss iperf3 abweisen, mit ihnen durchgehen. Das Passwort lässt sich auf dem Host später nicht mehr auslesen.</p>',
            '<p>From the operator you need the measurement user, the password and the contents of the <code>public.pem</code> file. Ask them for the counter-check from the dialog: without credentials iperf3 has to refuse, with them it has to succeed. The password cannot be read back from the host later.</p>',
          ),
        },
        {
          title: L('Eintragen', 'Add the record'),
          text: L(
            '<p>Öffne {ui:nav.iperf} und wähle {ui:infrastructure.iperf.register}. Gib {ui:infrastructure.iperf.columns.name}, {ui:infrastructure.iperf.host} und {ui:infrastructure.iperf.iperfPort} an, zum Beispiel <code>iperf-extern</code> unter <code>203.0.113.50</code> mit Port <code>5201</code>.</p>',
            '<p>Open {ui:nav.iperf} and choose {ui:infrastructure.iperf.register}. Enter {ui:infrastructure.iperf.columns.name}, {ui:infrastructure.iperf.host} and {ui:infrastructure.iperf.iperfPort}, for example <code>iperf-extern</code> at <code>203.0.113.50</code> on port <code>5201</code>.</p>',
          ),
          image: 'endpoints-external-dialog',
          alt: L(
            'Der Dialog „Externen Messpunkt eintragen“ mit ausgefüllten Feldern und öffentlichem Schlüssel',
            'The “Add an external endpoint” dialog with its fields and the public key filled in',
          ),
          narrow: true,
        },
        {
          title: L('Zugangsdaten', 'Credentials'),
          text: L(
            '<p>Sobald {ui:infrastructure.iperf.columns.user} ausgefüllt ist, verlangt der Dialog auch {ui:auth.password} und {ui:infrastructure.iperf.publicKey}. Den Schlüssel fügst du komplett mit BEGIN- und END-Zeile ein; ein OpenSSH-Schlüssel (<code>ssh-rsa …</code>) passt nicht. Misst der Host ohne Anmeldung, bleibt der Messbenutzer leer.</p>',
            '<p>As soon as {ui:infrastructure.iperf.columns.user} is filled in, the dialog also asks for {ui:auth.password} and {ui:infrastructure.iperf.publicKey}. Paste the key whole, with its BEGIN and END lines; an OpenSSH key (<code>ssh-rsa …</code>) does not work. For a host that measures without a sign-in, leave the measurement user empty.</p>',
          ),
        },
        {
          title: L('Probes zuweisen', 'Assign probes'),
          text: L(
            '<p>{ui:infrastructure.iperf.register} legt den Eintrag an und öffnet die Seite des Messpunkts. Von dort <a href="@endpoint-credentials">weist du ihn Probes zu</a> und rollst den Sensor aus.</p>',
            '<p>{ui:infrastructure.iperf.register} creates the record and opens the endpoint’s page. From there you <a href="@endpoint-credentials">assign it to probes</a> and deploy the sensor.</p>',
          ),
        },
        {
          title: L('Ein neues Passwort nachtragen', 'Store a new password'),
          text: L(
            '<p>Hat der Betreiber das Passwort geändert, wählst du auf der Seite des Messpunkts {ui:infrastructure.iperf.updateForeign}. {ui:infrastructure.iperf.updateForeignSubmit} speichert es und gibt es an alle Probes weiter, die den Messpunkt halten. Den Host selbst kontaktiert die Plattform dabei nicht.</p>',
            '<p>When the operator has changed the password, choose {ui:infrastructure.iperf.updateForeign} on the endpoint’s page. {ui:infrastructure.iperf.updateForeignSubmit} stores it and hands it to every probe holding the endpoint. The platform does not contact the host for this.</p>',
          ),
        },
      ],
      notes: [
        L(
          'In der Liste und auf seiner Seite trägt ein externer Messpunkt die Markierung {ui:infrastructure.iperf.foreign}.',
          'In the list and on its page, an external endpoint is marked {ui:infrastructure.iperf.foreign}.',
        ),
        L(
          'Entfernen nimmt nur den Eintrag und die Zugangsdaten auf den Probes weg. Der Dienst beim Betreiber läuft weiter.',
          'Removing it only takes away the record and the credentials on the probes. The service at the operator’s end keeps running.',
        ),
        L(
          'Messbenutzer oder Schlüssel lassen sich nicht ändern. Dafür entfernst du den Eintrag und trägst ihn neu ein.',
          'The measurement user and the key cannot be changed. To change them, remove the record and add it again.',
        ),
        L(
          'Scheitert die Anmeldung mit frisch erzeugten Zugangsdaten, prüf die Uhrzeit auf dem Host: iperf3 weist Anmeldungen mit zu großer Zeitabweichung ab.',
          'If signing in fails with freshly created credentials, check the clock on the host: iperf3 rejects sign-ins whose time is too far off.',
        ),
      ],
      related: ['iperf-endpoints', 'endpoint-credentials'],
    },
    {
      slug: 'endpoint-credentials',
      title: L('Zugangsdaten an Probes geben und entziehen', 'Giving endpoint credentials to probes and taking them back'),
      short: L(
        'Festlegen, welche Probes gegen einen Messpunkt messen – und was mit dem Zugangsdaten-Profil default passiert.',
        'Decide which probes measure against an endpoint – and what happens to the default credential profile.',
      ),
      lead: L(
        'Ein Messpunkt nützt einer Probe erst, wenn sie seine Zugangsdaten hält. Welche Probes das sind, legst du auf der Seite des Messpunkts fest.',
        'An endpoint is of use to a probe only once the probe holds its credentials. You decide which probes those are on the endpoint’s page.',
      ),
      role: 'operator',
      keywords: L(
        'iperf zuweisen entziehen probes default profile zugangsdaten',
        'iperf assign revoke probes default profile credentials',
      ),
      steps: [
        {
          text: L(
            '<p>Öffne den Messpunkt unter {ui:nav.iperf} und wähle {ui:infrastructure.iperf.assign}. Aus der Liste geht es auch direkt über die Zahl in der Spalte {ui:infrastructure.iperf.columns.deployedTo}.</p>',
            '<p>Open the endpoint under {ui:nav.iperf} and choose {ui:infrastructure.iperf.assign}. From the list, the number in the {ui:infrastructure.iperf.columns.deployedTo} column gets you there directly.</p>',
          ),
        },
        {
          text: L(
            '<p>Probes, die die Zugangsdaten schon halten, tragen {ui:infrastructure.iperf.probeHolds}. Hake die Probes an, die du ändern willst. Der Knopf zum Ausrollen gibt den neu angehakten die Zugangsdaten, der Knopf zum Entziehen nimmt sie den angehakten, die sie schon halten. Beide nennen die Zahl der betroffenen Probes.</p>',
            '<p>Probes that already hold the credentials are marked {ui:infrastructure.iperf.probeHolds}. Tick the probes you want to change. The deploy button gives the credentials to the newly ticked ones, the revoke button takes them from the ticked ones that already hold them. Both show the number of probes affected.</p>',
          ),
          image: 'endpoints-assign-probes',
          alt: L(
            'Der Dialog „Probes, die gegen diesen Messpunkt messen“ mit einer Probe, die ihn hält, und einer angehakten',
            'The “Probes measuring against this endpoint” dialog with one probe holding it and one ticked',
          ),
          narrow: true,
        },
        {
          text: L(
            '<p>Jeder Knopf startet einen eigenen Job und öffnet ihn. Ausrollen und Entziehen laufen getrennt, weil sie auf den Probes unabhängig voneinander scheitern können.</p>',
            '<p>Each button starts a job of its own and opens it. Deploying and revoking run separately because they can fail independently on the probes.</p>',
          ),
        },
        {
          text: L(
            '<p>Die Zugangsdaten allein messen nichts. Fehlt der Sensor iperf-throughput auf einer der Probes, zeigt die Seite des Messpunkts {ui:infrastructure.iperf.sensorMissingTitle}; {ui:sensors.deployTo} rollt ihn dort aus.</p>',
            '<p>The credentials alone measure nothing. If the iperf-throughput sensor is missing on one of the probes, the endpoint’s page shows {ui:infrastructure.iperf.sensorMissingTitle}; {ui:sensors.deployTo} deploys it there.</p>',
          ),
        },
      ],
      after: L(
        `<h2>Das Zugangsdaten-Profil default</h2>
<p>Hält eine Probe genau einen Messpunkt, trägt sie dessen Zugangsdaten zusätzlich unter dem Namen <code>default</code>. Ein Sensorobjekt in PRTG braucht dann keine Verbindungsangabe. Bekommt die Probe einen zweiten Messpunkt, fällt <code>default</code> weg: Objekte ohne <code>--server</code> werden rot, Objekte mit <code>--server</code> messen unbemerkt ohne Anmeldung weiter.</p>
<p>Davor warnt der Dialog mit {ui:infrastructure.iperf.aliasLostTitle}, bevor du ausrollst. Trag vorher in jedem betroffenen Sensorobjekt <code>--profile</code> mit dem Namen des Messpunkts ein; die passende Zeile steht auf der Seite des Messpunkts unter {ui:infrastructure.iperf.prtgTitle}. Hält eine Probe danach wieder nur einen Messpunkt, bekommt sie <code>default</code> zurück; ein eingetragenes <code>--profile</code> bleibt richtig.</p>`,
        `<h2>The default credential profile</h2>
<p>A probe holding exactly one endpoint also carries its credentials under the name <code>default</code>. A sensor object in PRTG then needs no connection parameter. When the probe gets a second endpoint, <code>default</code> goes away: objects without <code>--server</code> turn red, objects with <code>--server</code> keep measuring without a sign-in, unnoticed.</p>
<p>The dialog warns about this with {ui:infrastructure.iperf.aliasLostTitle} before you deploy. Add <code>--profile</code> with the endpoint’s name to every affected sensor object first; the matching line is on the endpoint’s page under {ui:infrastructure.iperf.prtgTitle}. When a probe is back to one endpoint, it gets <code>default</code> back; a <code>--profile</code> already entered stays correct.</p>`,
      ),
      notes: [
        L(
          'Zuweisen und Entziehen braucht die Rolle Operator, wie das Einrichten des Messpunkts. Ein Betrachter sieht, welche Probes die Zugangsdaten halten, kann es aber nicht ändern.',
          'Assigning and revoking need the operator role, as setting up the endpoint does. A viewer sees which probes hold the credentials but cannot change it.',
        ),
        L(
          'Eine hier entzogene Probe bleibt entzogen. Ein späteres Ausrollen des Sensors hält sich an dieselbe Zuordnung.',
          'A probe revoked here stays revoked. Deploying the sensor later follows the same assignment.',
        ),
        L(
          'Beim ersten Ausrollen des Sensors auf eine Probe bekommt sie alle eingetragenen Messpunkte, weil es für sie noch keine Zuordnung gibt.',
          'The first time the sensor is deployed to a probe, the probe gets every endpoint on record, because it has no assignment yet.',
        ),
        L(
          'Ein Passwortwechsel am Messpunkt erreicht alle Probes, die ihn halten, ohne dass du sie neu zuweist.',
          'Changing the endpoint’s password reaches every probe holding it, without assigning them again.',
        ),
      ],
      related: ['iperf-endpoints', 'deploy-sensors'],
    },
  ],
}
