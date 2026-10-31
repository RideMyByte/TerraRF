# Changelog

## [Unreleased]

## 0.4.2
### Neu
- Reiter „Netz“ heißt jetzt „MeshCore“
- Klick auf einen Repeater: Markierung pulsiert beim Laden, danach nur noch seine Nachbarn (nach den Filtern
  im Reiter) ohne Linien, eingefärbt nach SNR; erneuter Klick hebt die Auswahl auf
- Klick auf eine mit MeshCore verknüpfte Station zeigt deren Repeater-Nachbarn; erneuter Klick oder eine andere
  Station blendet sie aus
- Popup am Repeater: als Station hinzufügen, Höhe/Gewinn für den Abgleich hinterlegen (auch ohne Station), abwählen
- Selbst angelegte Stationen lassen sich mit einem Repeater im Umkreis von 3 km verknüpfen und wieder lösen
- Abgleich: Werte der Gegenstelle aus der Station im Projekt (Höhe, Richtgewinn, Kabel) bzw. der hinterlegten
  Höhe; nur Partner, die auch auf der Karte stehen; Klick auf einen Partner zeigt die Strecke
- Richtfunk: Antenne per Griff mit der Maus drehen, „Strecke zeigen“ rechnet mit beiden Antennen,
  „Richtantennen aufeinander ausrichten“ setzt Azimut und Neigung beider Stationen
- Statusanzeige auf der Karte: was gerade gerechnet wird, für den Admin CPU und RAM des Containers

## 0.4.1
### Behoben
- 502-Fehler nach großen Berechnungen: Abdeckungsbilder werden einzeln erzeugt und auf der Platte abgelegt,
  der RAM-Zwischenspeicher ist nach Größe begrenzt (RFP_STATION_CACHE_MB, Standard 1200)

## 0.4.0
### Neu
- Handytauglich: Seitenleiste als Schublade (☰), Profil als unteres Blatt, einklappbare „Ansicht & Legende“,
  Statusmeldungen auf der Karte, größere Bedienelemente auf Touch-Geräten, Safe-Area für iPhone
- Als App installierbar (Web-App-Manifest, eigene Icons): Start vom Startbildschirm ohne Browserleiste,
  Anleitung unter „Als App installieren“; bewusst ohne Service Worker, es wird nichts auf dem Gerät gespeichert
- Admin startet mit dem zuletzt geöffneten Projekt
### Geändert
- Auf dem Handy werden gröbere Abdeckungsbilder geladen (weniger Speicherbedarf)
- Profilachse zeigt bei kurzen Strecken Meter statt „0.0 km“

## 0.3.2
### Neu
- Profilfenster: Empfängerhöhe und -gewinn einstellbar, nur das Profil wird neu gerechnet (#15)
- Impressum und Datenschutzerklärung unter /impressum und /datenschutz (RFP_IMPRINT_*) (#16)
- Kachel-Proxy mit Cache: Kartenkacheln laufen über den eigenen Server, keine Besucher-IPs an Kartendienste (#16)
### Geändert
- Keine Zugriffsprotokolle, kein localStorage; Login-Cookie standardmäßig nur für die Sitzung,
  „angemeldet bleiben“ als eigene Option (#16)
- Esri-Satellit nur noch ohne Kachel-Proxy verfügbar
### Behoben
- Öffentliche Projekte ohne Karteninhalt: werden beim Speichern vorgerechnet, Zeitlimits eines
  Reverse-Proxys werden mit erneutem Versuch überbrückt (#17)

## 0.3.1
### Behoben
- Eine neue Station löst keine Neuberechnung der übrigen Stationen mehr aus (Rechengitter hing vom
  Mittel der Stationsbreiten ab); Statuszeile zeigt „berechnet / aus dem Cache“

## 0.3.0
### Neu
- Tab „Netz“: MeshCore-Repeater aus einer CoreScope-Instanz (RFP_CORESCOPE_URL), Filter nach Alter und Rolle
- Verbindungen eines Repeaters: gemessenes SNR (farbig) und aus Pfaden abgeleitete Kanten (gestrichelt)
- Repeater als Station übernehmen; Abgleich Modell ↔ Messung je Station mit Mittel, Median und Streuung
- Automatischer Sync der Repeater-Liste (RFP_CORESCOPE_SYNC_H, Standard 24 h)

## 0.2.0
- **Admin-Login** (#13): ein Admin aus der Konfiguration (`RFP_ADMIN_USER`/`RFP_ADMIN_PASSWORD`),
  Besucher sehen öffentliche Projekte nur lesend; Projekte privat/öffentlich, „Link teilen“
- **Stationen ein-/ausblenden ohne Neuberechnung** (#12): jede Station wird einmal berechnet und als
  eigenes Datenbild geliefert; der Browser setzt die Ansichten selbst zusammen, Haken, Ansicht und
  Reserve-Regler wirken sofort. Auswahl „Nur: Station …“ entfernt
- Basiskarte OpenStreetMap in Graustufen
- Abfrage per Klick in die Karte ohne Serveranfrage
- Projekt heißt jetzt **TerraRF**, neues Logo

## 0.1.2
- Lokaler NRW-Kachelspeicher (5 m), Vorladen von ganz NRW beim Start, fortsetzbar
- Haken an Stationen blendet sie samt Abdeckung ein/aus, automatische Neuberechnung aus dem Cache
- Regler „Mindest-Reserve“ in der Kartenlegende
- Hindernisdämpfung nach ITU-R P.833, Deygout als Standard, Sicht auf Dächer/Baumkronen
- Netzwerkfehler schalten nicht mehr fälschlich auf 1-m-Download um

## 0.1.1
- Clutter am Empfänger (ITU-R P.2108), Bodenreflexion, Fading-/Standortreserve
- NRW DOM1 als Hindernisquelle, Regionen-Download, Cache-Verwaltung

## 0.1.0
- Erste Version: Stationen, Abdeckung, Überlappung, Profil mit Fresnelzone, Projekte
