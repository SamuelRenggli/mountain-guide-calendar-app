# Gäste: Kartei, Angaben, Login, Spesen-Konto

Stand: 2026-11-10.

## Umgesetzt
- **Anfrage bleibt schlank:** Name, E-Mail, Telefon, Wunsch. Mehr nicht.
- **Angaben für die Tourenplanung** (Rechnungsadresse, Notfallkontakt, Ernährung, Allergien, Alpenverein + Nummer,
  GA/Halbtax, Auto mit freien Plätzen): Der Gast trägt sie auf seiner Buchungsseite ein (Link aus der E-Mail, **ohne Login**),
  sobald der Guide die Anfrage angenommen hat. Gespeichert pro Guide und E-Mail-Adresse (`GuestProfile`), also bei der
  nächsten Tour mit demselben Guide schon ausgefüllt. Adresse, Notfallkontakt und Allergien verschlüsselt.
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
