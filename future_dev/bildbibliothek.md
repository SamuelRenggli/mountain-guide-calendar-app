# Bildbibliothek und Foto-Cloud

Stand: 2026-11-09.

## Umgesetzt
- Bildbibliothek pro Bergführer unter Dashboard → Meine Touren → Bilder, sortiert nach Tourenart: Freeride, Skitour Eintag, Skitourenwoche, Frühling, Gletschertrekking, Sportklettern, Bergtour Fels, Bergtour Eis und Gipfel.
- Beim Ausschreiben (Termin, Vorlage, externe Tour) ein Bild aus der Bibliothek wählen. Neu hochgeladene Bilder kommen automatisch in die Bibliothek.
- Externe Tour per Link: Mit gewählter Tourenart wird das eigene Bild dieser Art genommen statt des Vorschaubilds des Anbieters.
- Tourchef-Bilder: eines oder mehrere pro Tourenart, für alle sichtbar. Laden mit
  `python manage.py load_stock_pictures <Ordner> [--replace]`, Dateien benannt nach Tourenart, z. B. `skitour_tag.jpg`, `freeride-2.jpg`. **Die Bilder selbst fehlen noch.**
- Einrichtung: «Erste Schritte» auf der Startseite nennt das Hochladen als optionalen Schritt. Danach erscheint ein Hinweis, solange keine eigenen Bilder da sind. Blockiert nichts (anders als Stripe).
- Speicher: Bilder werden auf 1200 px verkleinert und in der Datenbank abgelegt (ca. 100–300 KB pro Bild). Für einige hundert Bibliotheksbilder pro Guide reicht das.

## Später: Foto-Cloud mit Gedächtnis
Sobald die Foto-Cloud für Tourenfotos (siehe `foto_sharing.md`) läuft:

- **Favoriten:** Der Guide markiert Fotos aus einer Tour mit einem Stern. Favoriten erscheinen in der Bildbibliothek und lassen sich für Ausschreibungen verwenden.
- **Gleiche Sortierung:** Die Cloud ordnet nach denselben Tourenarten wie die Bibliothek.
- **Gedächtnis über Metadaten:** Jedes Foto merkt sich Tour, Tourenart, Ort / Gebiet, Gipfel und Datum. Schreibt der Guide eine Tour aus, die er schon einmal geführt hat (gleicher Titel, gleiches Gebiet oder gleiche Vorlage), schlägt Tourchef die Fotos von damals vor.
- **Datenschutz:** Fotos von Gästen werden nur mit deren Einverständnis zu Favoriten für Werbung. Beim Hochladen fragen oder nur Fotos des Guides verwenden.
- **Speicher:** Die Cloud braucht einen Objektspeicher. Empfehlung: Google Cloud Storage in Zürich (`europe-west6`) im bestehenden Google-Cloud-Projekt, kein zusätzlicher Vertrag. Bucket privat, Zugriff nur über die App.
