# Bildbibliothek und Foto-Cloud

Stand: 2026-11-12.

## Umgesetzt
- Bildbibliothek pro Bergführer unter Dashboard → Meine Touren → Bilder, sortiert nach Tourenart: Freeride, Skitour (1 Tag),
  Skitour (mehrtägig), Ski- & Splitboardtour, Schneeschuhtour, Eisklettern & Drytooling, Hochtour klassisch, Hochtour mit Fels,
  Alpinklettern, Sportklettern, Gletschertrekking, Trekking. Alte Arten wurden umgehängt (Frühling → Skitour 1 Tag,
  Bergtour Fels → Hochtour mit Fels, Bergtour Eis → Hochtour klassisch).
- Hochladen: zuerst nur die Dateien (mehrere auf einmal, auch HEIC vom iPhone; das Skript schickt sie einzeln, damit keine
  Anfrage zu gross wird). Danach unter «Neu hochgeladen – bitte zuordnen» pro Bild Tourenart und Beschreibung, Speichern oder Löschen.
- Beim Ausschreiben (Termin, Vorlage, externe Tour) ein Bild aus der Bibliothek wählen. Neu hochgeladene Bilder kommen automatisch in die Bibliothek.
- Externe Tour per Link: Fotos des Anbieters werden nie übernommen (Urheberrecht). Reihenfolge: eigenes hochgeladenes Bild,
  Bild aus der Bibliothek, eigenes Bild der gewählten Tourenart, sonst das Logo des Anbieters (Apple-Touch-Icon,
  schema.org-Logo oder Favicon der Seite) zentriert auf einer weissen Kachel.
- Tourchef-Bilder: liegen in `stock_pictures/` (Dateiname = Tourenart, z. B. `skitour_tag-3.jpg`) und werden bei jedem
  Deploy geladen (`deploy/migrate.sh` → `python manage.py load_stock_pictures`; schon geladene Dateien werden übersprungen).
  Erster Satz für den Testlauf: 40 Fotos aus dem Google-Drive-Ordner vom 10.11.2026, von Hand den Tourenarten zugeordnet.
  Noch ohne Tourchef-Bild: Schneeschuhtour, Sportklettern. Nur je ein Bild: Freeride, Splitboard, Eisklettern.
  Das Foto `tobinmeyers_andermattguides_1849.JPEG` ist bewusst **nicht** dabei (Fotograf Tobin Meyers / Andermatt Guides,
  Rechte unklar). Originale liegen ausserhalb des Repos unter `N:\Tourchef\bilder-originale`.
  **Für den echten Betrieb braucht es eigene, lizenzierte Bilder.**
- Einrichtung: «Erste Schritte» auf der Startseite nennt das Hochladen als optionalen Schritt. Danach erscheint ein Hinweis, solange keine eigenen Bilder da sind. Blockiert nichts (anders als Stripe).
- Speicher: Bilder werden auf 1200 px verkleinert und in der Datenbank abgelegt (ca. 100–300 KB pro Bild). Für einige hundert Bibliotheksbilder pro Guide reicht das.

## Umgesetzt 2026-11-12: Bildarchiv und Galerie
- **Zwischenablage:** Neu hochgeladene Bilder liegen unter «Zwischenablage – bitte zuordnen», bis sie eine Tourenart haben.
  Pro Bild: Name, Tourenart, Region, Ort, Bewertung (1–4 Sterne), eigene Bemerkung. Region und Ort schlagen bereits
  verwendete Werte vor.
- **Archiv durchsuchen:** Suche über Name, Region, Ort, Bemerkung; Filter nach Tourenart, Region, Mindestbewertung.
  Innerhalb einer Tourenart stehen die best bewerteten Bilder zuerst.
- **Galerie:** Vorlage und Termin haben ein Hauptbild plus bis zu 9 weitere Bilder (`TourDetails.gallery`, Liste von
  Bild-Schlüsseln, max. 10 insgesamt). Die Terminseite für Gäste zeigt Hauptbild gross und die weiteren als Kacheln.
  Gelöschte Bilder fallen aus der Galerie, fremde Bilder werden nie angezeigt.
- **Noch offen:** Wenn das Foto-Sharing mit Gästen kommt (`foto_sharing.md`), landen die eigenen Fotos des Guides aus
  einem Tour-Album automatisch in seiner Zwischenablage (vorbelegt mit Tourenart, Ort und Datum der Tour).

## Später: Foto-Cloud mit Gedächtnis
Sobald die Foto-Cloud für Tourenfotos (siehe `foto_sharing.md`) läuft:

- **Favoriten:** Der Guide markiert Fotos aus einer Tour mit einem Stern. Favoriten erscheinen in der Bildbibliothek und lassen sich für Ausschreibungen verwenden.
- **Gleiche Sortierung:** Die Cloud ordnet nach denselben Tourenarten wie die Bibliothek.
- **Gedächtnis über Metadaten:** Jedes Foto merkt sich Tour, Tourenart, Ort / Gebiet, Gipfel und Datum. Schreibt der Guide eine Tour aus, die er schon einmal geführt hat (gleicher Titel, gleiches Gebiet oder gleiche Vorlage), schlägt Tourchef die Fotos von damals vor.
- **Datenschutz:** Fotos von Gästen werden nur mit deren Einverständnis zu Favoriten für Werbung. Beim Hochladen fragen oder nur Fotos des Guides verwenden.
- **Speicher:** Die Cloud braucht einen Objektspeicher. Empfehlung: Google Cloud Storage in Zürich (`europe-west6`) im bestehenden Google-Cloud-Projekt, kein zusätzlicher Vertrag. Bucket privat, Zugriff nur über die App.

## Idee: Illustrationen statt Fotos
Wie Google Kalender: pro Tourenart eine einfache Illustration im gleichen Stil (flache Flächen, 2–3 Farben, Berg-Silhouette
plus Motiv: Skispur, Seilschaft, Eisfall …). Vorteile: keine Rechtefragen, einheitlicher Look im Buchungskalender, und der
Unterschied zu echten Fotos motiviert die Guides, eigene Bilder hochzuladen. Umsetzung als SVG, beim Laden in JPEG
umgerechnet wie die anderen Tourchef-Bilder.
