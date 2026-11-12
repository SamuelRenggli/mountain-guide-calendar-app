# Gäste: Kartei, Angaben, Login, Spesen-Konto

Stand: 2026-11-10.

## Umgesetzt
- **Anfrage bleibt schlank:** Name, E-Mail, Telefon, Wunsch. Mehr nicht.
- **Angaben für die Tourenplanung** (Rechnungsadresse, Notfallkontakt, Ernährung, Allergien, Alpenverein + Nummer,
  GA/Halbtax, Auto mit freien Plätzen): Der Gast trägt sie auf seiner Buchungsseite ein (Link aus der E-Mail, **ohne Login**),
  sobald der Guide die Anfrage angenommen hat. Gespeichert auf der Buchung und in der Kartei des Guides pro E-Mail-Adresse
  (`GuestProfile`). Adresse, Notfallkontakt und Allergien verschlüsselt.
  **Seit 2026-11-11 (Fragebogen Frage 8, Antwort C):** Der Gast sieht nur, was er auf *dieser* Buchung eingetragen hat.
  Die Angaben früherer Touren sieht nur der Guide. Grund: Die E-Mail-Adresse wird nicht geprüft; wer die Adresse einer
  anderen Person eingibt, darf deren Daten nicht sehen. Der Gast tippt also bei jeder Tour neu, bis es den Gast-Login gibt.
  Der Guide kann per Knopf «Gast um diese Angaben bitten» eine Nachricht schicken.
- **Gegenrecht:** Auswahlliste nach der SAC-Liste 2025 (SAC, DAV, ÖAV, AVS, CAI, FFCAM, FEDME, LAV, PZS, FAT) plus
  «Anderer Verein». Quelle: https://sac-cas.ch/fileadmin/Huetten_und_Touren/153_SAC-H%C3%BCtten/H%C3%BCtten-Knigge/PDF/Gegenrecht_SAC_2025.pdf
  Die Liste ändert jede Saison – jährlich prüfen (u. a. FAM Andorra, CAB Belgien, AC Grossbritannien, NKBV sind je nach
  Liste dabei oder nicht).
- **Gäste-Kartei** (Meine Touren → Gäste): alle, die gebucht haben (bestätigte Touren, Rechnungen, bezahlte Anmeldungen).
  Reine Anfragen kommen nicht hinein (Datensparsamkeit). Suche nach Name/E-Mail. «Gastdaten löschen» in den Einstellungen
  löscht auch die Angaben.

## Später: Gast-Login (freiwillig)
Buchen bleibt immer ohne Login. Ein Login lohnt sich erst, wenn der Gast etwas über mehrere Guides hinweg will:
- Angaben einmal für alle Guides (heute: pro Guide).
- Mit Login (bestätigte E-Mail-Adresse) darf der Gast seine gespeicherten Angaben wieder sehen und muss sie nicht neu
  tippen. Das löst die Einschränkung von Frage 8.
- **Datenschutz klären (Frage des Guides):** Muss der Gast wissen, dass der Guide seine Angaben für weitere Touren
  aufbewahrt? Die Buchungsseite sagt es jetzt («bewahrt sie für weitere Touren mit dir auf»); Datenschutzerklärung
  entsprechend ergänzen. Erst danach den Gast bei wiederholter Buchung gar nicht mehr fragen.
- **Folgen:** Gast folgt Guides und sieht in einer Timeline, was sie gerade ausschreiben.
- Login per E-Mail-Link (kein Passwort), angelegt aus einer bestehenden Buchung heraus («Konto anlegen, damit du deine
  Angaben nicht nochmals eintragen musst»).

## Frage: Honorar und Spesen auf zwei Konten (Stripe)
Stripe zahlt pro Connect-Konto und Währung auf **ein** Standard-Bankkonto aus. Ein zweites Konto kann hinterlegt werden,
aber Stripe teilt eine einzelne Zahlung nicht automatisch auf zwei Bankkonten auf. Möglichkeiten:
1. **Eine Auszahlung, sauber getrennte Belege (empfohlen):** Die Rechnung hat getrennte Posten (Tourenpreis / Spesen),
   der Buchhaltungs-Export weist sie getrennt aus. Der Guide überweist die Spesen intern aufs Privatkonto. Kein Umbau.
2. **Zwei Zahlungen:** Honorar per Stripe, Spesen per Überweisung (IBAN des Privatkontos auf der Rechnung). Etwas mehr
   Aufwand für den Gast.
3. **Zwei Stripe-Konten pro Guide:** technisch möglich (zweites Connect-Konto für Spesen, Checkout mit zwei Zahlungen),
   aber doppeltes Onboarding und doppelte Gebühren – nicht empfohlen.
Vorschlag: 1. Der Buchhaltungs-Export hat schon eine Spalte «davon Spesen CHF». Mit der Treuhand des Pilot-Guides klären.

## Umgesetzt 2026-11-12: Angaben nach der Zahlung
- Solange die Planungsangaben fehlen, zeigt die Gast-Seite statt des grünen «Bestätigt»-Balkens eine gelbe Box
  «Fast geschafft»: 1. Zahlung erhalten / Tour bestätigt ✓, 2. der Guide braucht noch Angaben, 3. dann ist die
  Anmeldung komplett. Knopf «Jetzt Angaben eintragen».
- Nach dem Speichern: grüne Box «Vielen Dank für deine Angaben, <Guide> wird sich melden.» Die Angaben bleiben sichtbar
  und änderbar (zugeklappt).
- Formular: Notfallkontakt als Name (max. 60 Zeichen) plus Telefon mit Vorwahl, gespeichert als «Name, +41 79…».
  Mitgliedernummer Alpenverein entfernt (Feld gelöscht). «Weitere Bemerkung» (max. 500 Zeichen) statt Nachricht an
  den Guide; sie eröffnet keinen Dialog.
- Häkchen «Meine Angaben für weitere Touren speichern» (Standard: aus). Nur mit Häkchen landen die Angaben in der
  Gäste-Kartei des Guides; ohne Häkchen nur bei dieser Buchung. Vorausfüllen bei der nächsten Buchung bleibt wegen Q8
  aus (E-Mail-Adresse nicht bestätigt) – geht erst mit Gast-Login per E-Mail-Link (siehe oben).
- Erinnerung: fehlen die Angaben 24 h nach Zahlung/Zusage noch (`DETAILS_REMINDER_HOURS`), geht einmal eine Mail
  «<Guide> braucht noch deine Infos für die anstehende Tour» (stündlicher Job `expire_bookings`).
- Das Nachrichtenfenster auf der Gast-Seite ist weg (siehe Skizze in `messenger.md`).
