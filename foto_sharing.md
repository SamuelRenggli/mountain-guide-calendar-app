# Foto-Sharing für durchgeführte Touren – Abklärung

Stand: 27.10.2026 · Branch `v2-django`

## Ziel

Nach einer Tour sollen Teilnehmer und Bergführer ihre Fotos gegenseitig teilen können: alle laden hoch, alle sehen und laden herunter.

## Fazit

Gut machbar. Eine solide erste Version braucht **ca. 2–4 Arbeitstage**. Der laufende Betrieb kostet **unter CHF 5 pro Monat**, bei normaler Nutzung praktisch nichts. Die Hauptpunkte sind nicht der Code, sondern **Speicherort** und **Datenschutz**.

## Ausgangslage im Code

**Was schon passt**

- Jede Buchung hat einen geheimen Gast-Link `/b/<token>/` (`bookings/urls_guest.py`). Gäste brauchen kein Login, und das Album lässt sich dort anhängen.
- Der Bergführer hat ein Dashboard mit Login und kann dort Fotos hochladen und verwalten.
- Für Aufgaben nach Fristen gibt es bereits Management-Commands (`expire_bookings`). Das automatische Löschen von Alben passt gleich daneben.

**Was fehlt**

- **Teilnehmer existieren im System nicht einzeln.** Eine `Booking` hat genau eine Person (`guest_name`, `guest_email`). `participants` ist nur eine Anzahl (`bookings/models.py`). Nur die buchende Person hat einen Link.
- **Dateien liegen lokal.** `STORAGES["default"]` ist `FileSystemStorage` (`config/settings.py`). Fotos füllen die Disk, und im Docker-Container gehen sie ohne Volume beim nächsten Deploy verloren.
- **Keine Bildverarbeitung.** Pillow ist nicht installiert.

## Wie kommen die anderen Teilnehmer an die Fotos?

| Variante | Beschreibung | Aufwand | Bewertung |
|---|---|---|---|
| **A: Album-Link** (empfohlen) | Eigener geheimer Link nur fürs Album. Die buchende Person leitet ihn in den Gruppenchat weiter. Wer den Link hat, kann ansehen und hochladen und gibt beim Upload seinen Namen ein. | klein | Einfach und passt zu kleinen Gruppen. Der Link ist aber übertragbar. |
| B: Teilnehmer erfassen | Bei der Buchung werden alle Teilnehmer mit E-Mail erfasst und erhalten je einen eigenen Link. | gross | Sauberer, ändert aber den Buchungsablauf und das Formular. Erst sinnvoll, wenn Teilnehmerdaten auch sonst gebraucht werden, z.B. für Notfallkontakte. |

Variante A lässt sich später zu B ausbauen, ohne dass etwas weggeworfen wird.

## Umfang der ersten Version (Variante A)

| Baustein | Inhalt | Aufwand |
|---|---|---|
| Datenmodell | `TourAlbum` (gehört zur Buchung, eigener Token, `expires_at`) und `Photo` (Datei, Vorschaubild, Name der hochladenden Person, Zeitpunkt) | 0.25 Tag |
| Speicher | `django-storages` mit S3-kompatiblem Bucket, Konfiguration über `.env` | 0.25 Tag |
| Upload | Mehrfach-Upload vom Handy, Grössen- und Typprüfung, Rate-Limit über das bestehende `core/ratelimit.py` | 0.5 Tag |
| Bildverarbeitung | Mit Pillow auf ca. 2500 px verkleinern, Vorschaubild erzeugen, **GPS-Daten aus EXIF entfernen** | 0.5 Tag |
| Galerie | Rasteransicht, Vollbild, einzelne Fotos und ganzes Album als ZIP herunterladen | 0.5–1 Tag |
| Bergführer-Dashboard | Album ansehen, Fotos löschen, Album schliessen | 0.25–0.5 Tag |
| Lebenszyklus | Album öffnet am Tourdatum und wird nach 90 Tagen automatisch gelöscht. Kurz vor der Löschung geht eine Mail an den Gast („Fotos herunterladen“). | 0.25–0.5 Tag |
| Mail nach der Tour | „Hier ist euer Fotoalbum“ mit Link, DE/EN wie die bestehenden Mails | 0.25 Tag |
| Tests | Im Stil der bestehenden Testsuite | 0.25–0.5 Tag |
| **Total** | | **ca. 2–4 Tage** |

## Kosten

### Laufend: Speicher

Annahmen für die Grössenordnung:

- 40 Fotos pro Tour, nach dem Verkleinern ca. 1.5 MB pro Foto, also **ca. 60 MB pro Tour**
- 150 Touren pro Jahr
- Löschung nach 90 Tagen, also liegen gleichzeitig nur Fotos von ca. 40 Touren im Speicher

Damit belegt das Ganze **ca. 2–3 GB**, selbst ohne Löschung höchstens ca. 9 GB pro Jahr.

| Anbieter | Standort | Preis (Richtwert) | Kosten bei 3 GB | Kosten bei 50 GB |
|---|---|---|---|---|
| Cloudflare R2 | EU-Option | 10 GB gratis, danach ca. USD 0.015/GB/Mt., kein Download-Traffic | **CHF 0** | ca. CHF 1/Mt. |
| Infomaniak Object Storage | Schweiz | ca. CHF 0.01–0.02/GB/Mt. | < CHF 1/Mt. | ca. CHF 1/Mt. |
| Exoscale SOS | Schweiz | ca. CHF 0.02/GB/Mt. plus Traffic | < CHF 1/Mt. | ca. CHF 1–2/Mt. |
| Hetzner Object Storage | DE/FI | ca. EUR 6/Mt. pauschal inkl. 1 TB | ca. CHF 6/Mt. | ca. CHF 6/Mt. |

Die Preise sind Richtwerte und müssen vor dem Entscheid auf den Anbieterseiten geprüft werden.

**Empfehlung:** Für Kunden in der Schweiz ein Schweizer Anbieter (Infomaniak oder Exoscale), damit das Argument „Daten in der Schweiz“ gilt. Die Kosten liegen in jedem Fall bei wenigen Franken im Monat.

### Laufend: Sonstiges

- **Server:** Das Verkleinern braucht beim Upload kurz CPU und RAM. Bei dieser Menge reicht der bestehende Server. Eine Hintergrund-Queue wie Celery ist vorerst nicht nötig.
- **Mails:** Ein bis zwei zusätzliche Mails pro Buchung, über den bestehenden Versand. Kosten vernachlässigbar.

### Was die Kosten treiben würde

- Fotos in Originalgrösse aufbewahren (5–10 MB statt 1.5 MB, also Faktor 4–6)
- Videos erlauben (schnell Hunderte MB pro Tour, nicht empfohlen)
- Keine automatische Löschung

## Datenschutz (DSG/DSGVO)

Fotos zeigen erkennbare Personen und sind damit Personendaten.

- Beim Upload einen Hinweis anzeigen: „Lade nur Fotos hoch, mit deren Teilen alle Abgebildeten einverstanden sind.“
- In der Datenschutzerklärung bzw. den AGB ergänzen: Zweck, Speicherort, Löschfrist von 90 Tagen
- GPS-Daten entfernen (siehe oben), damit Fotos keine Wohnorte oder Standorte verraten
- Der Bergführer kann einzelne Fotos auf Wunsch löschen
- Mit dem Speicheranbieter einen Auftragsverarbeitungsvertrag abschliessen. Bei Infomaniak und Exoscale ist er standardmässig dabei.

## Offene Entscheide

1. Variante A (Album-Link) oder B (Teilnehmer erfassen)?
2. Speicheranbieter: Schweiz oder günstigster?
3. Löschfrist: 90 Tage oder anders?
4. Sollen Gäste Fotos anderer Personen löschen dürfen, oder nur ihre eigenen bzw. der Bergführer alle?
