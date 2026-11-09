# Messenger / Gästekommunikation

Stand: 2026-11-09. Status: **umgesetzt (E-Mail mit Verlauf im Dashboard).** Für Antworten im Dashboard fehlt nur noch die Mail-Einrichtung (siehe unten).

## Entscheid
Der Guide schreibt im Dashboard, der Gast bekommt eine normale E-Mail und antwortet einfach darauf. Die Antwort landet im Verlauf der Buchung. Kein Login, keine App, kein WhatsApp-Konto nötig.

- Absender: `Anna Berg via Tourchef <noreply@…>`
- Antwortadresse pro Buchung: `antwort+4821k7qz@<REPLY_EMAIL_DOMAIN>` (Buchungsnummer plus 4 Zeichen Zufallscode, damit Nummern nicht erraten werden)
- Stimmt die Absenderadresse der Antwort nicht mit der des Gastes überein, wird die Nachricht dem Guide zur Prüfung gezeigt («Gehört dazu» / «Löschen»), dem Gast nie.
- Fehlt die Antwortadresse (Mail weitergeleitet), ordnet Tourchef über die Mail-Header (`In-Reply-To`, `References`) zu.
- Der Gast kann auch auf seiner Buchungsseite `/b/<token>/` schreiben und sieht dort den Verlauf.
- Pro Termin: «Nachricht an alle Angemeldeten» schickt jedem einzeln, Antworten landen bei der jeweiligen Person. Pro Person: Klick auf «Nachricht» in der Teilnehmerliste.
- Absage- und Ersatzdatum-Nachrichten erscheinen ebenfalls im Verlauf.
- Neue Buchungen, Anfragen und Nachrichten erscheinen oben rechts im Dashboard.
- Ist die Telefonnummer mit Vorwahl erfasst, gibt es zusätzlich «WhatsApp öffnen» (nicht im Verlauf).

Ohne `REPLY_EMAIL_DOMAIN` geht die Antwort direkt an die E-Mail-Adresse des Guides, also nicht in den Verlauf.

## Mail einrichten (einmalig)
Ohne `EMAIL_HOST` verschickt die App gar keine Mails, sie erscheinen nur im Log. Deshalb bekommen Gäste heute keine Bestätigung.

Empfehlung: **Mailgun (EU-Region)** für Versand und Empfang, ein Anbieter für beides. Preise und Plan vor dem Abschluss prüfen, Empfangs-Routen sind je nach Plan begrenzt.

1. Eigene Domain bereithalten. Hat die Hauptdomain schon Postfächer bei einem anderen Anbieter, eine Subdomain für Antworten nehmen, z. B. `post.tourchef.ch`. Die MX-Einträge der Hauptdomain darf man sonst nicht ändern.
2. In Mailgun (EU) die Domain hinzufügen und die angezeigten DNS-Einträge setzen: SPF (TXT), DKIM (TXT), MX (für den Empfang). Dazu einen DMARC-Eintrag `_dmarc` setzen.
3. SMTP-Zugang in Mailgun anlegen und in `.env.cloudrun` eintragen:
   ```
   DEFAULT_FROM_EMAIL=Tourchef <noreply@tourchef.ch>
   EMAIL_HOST=smtp.eu.mailgun.org
   EMAIL_PORT=587
   EMAIL_HOST_USER=<SMTP-Benutzer>
   EMAIL_HOST_PASSWORD=<SMTP-Passwort>
   REPLY_EMAIL_DOMAIN=post.tourchef.ch
   MAILGUN_WEBHOOK_SIGNING_KEY=<Mailgun: Sending → Webhooks → HTTP webhook signing key>
   ```
4. In Mailgun unter Receiving eine Route anlegen:
   `match_recipient("antwort\+.*@post.tourchef.ch")` → `forward("https://<App-Adresse>/eingang/mail/")` → `stop()`
5. `bash deploy/push-config.sh`
6. Test: Buchung im Dashboard öffnen, Nachricht senden, auf die Mail antworten. Die Antwort muss nach wenigen Sekunden im Verlauf stehen und oben rechts erscheinen.

Der Eingang `/eingang/mail/` prüft die Mailgun-Signatur, lehnt alte oder wiederholte Anfragen ab und ist pro IP begrenzt.

## Verworfene Varianten (Recherche vom 2026-11-09)

### Eigener Chat mit Magic Link
Gäste öffnen eine Web-Seite selten von selbst. Als Ergänzung ist er trotzdem drin: Verlauf und Antworten auf der Gast-Seite.

### WhatsApp-Gruppe per Knopf (WhatsApp Business Groups API)
Laut Drittquellen (Metas Doku nicht erreichbar): Gäste lassen sich nicht direkt hinzufügen, sie müssen einen Einladungslink öffnen. Höchstens 8 Teilnehmer inklusive Business-Nummer. Die Gruppe gehört der Tourchef-Nummer, nicht dem Guide. Zulassung meist nur für verifizierte Business-Konten, dazu Gebühren pro Nachricht. Für Tourchef unpraktisch.

Quellen: unipile.com/whatsapp-group-api, periskope.app/blog/whatsapp-groups-api-requirements-eligibility-limits, kapso.com/blog/whatsapp-groups-api-state-2026, chatbotx.io (WhatsApp Groups API Explained), ominiflow.com/blog/whatsapp-groups-api
