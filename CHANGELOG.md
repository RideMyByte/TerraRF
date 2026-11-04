# Changelog

## [Unreleased]

## 0.4.6
### Neu
- Vergleich mit dem Modell in einer Karte: CoreScope (Repeater ↔ Repeater, nur direkt gemeldete Nachbar-SNR, 0 Hop)
  und Messfahrten (Repeater → Handy) getrennt wählbar, Übersicht je Quelle, Quelle an jeder Zeile (CS/MM/✎/CSV) (#21)
- Messreihen: Fahrten werden automatisch getrennt (Pause > 30 min); je Reihe Name, Antennenhöhe, Gewinn, Kabel,
  externe Antenne und Notiz – der Vergleich rechnet mit diesen Angaben (#22)
- Repeater oder verknüpfte Station antippen zeigt automatisch deren Messungen; Klick auf einen Messpunkt zeigt alle
  Daten (SNR ↑/↓, RSSI, Rauschen, Leistung, Messreihe, Modellwert) (#22)
- Station angetippt: nur ihre Abdeckung wird gezeigt, „alle Stationen zeigen“ oben auf der Karte (#19)
- Abstrahlsektor je Station (z. B. Antenne an einer Hauswand): außerhalb gedämpft, ohne Richtcharakteristik (#23)
- Station „auf der Karte neu setzen“ im Editor (#24)
### Geändert
- Modell 9: Umkreis der Station (Standard 150 m) wird in 5 m abgetastet, Gebäude direkt an der Antenne schatten
  jetzt unabhängig von der Rechenauflösung ab; die Antennenzelle selbst gilt als frei (#23)
- Empfänger-Nahbereich (P.2108) höchstens die halbe Strecke – bei kurzen Strecken verschwanden Gebäude zwischen
  Sender und Empfänger sonst ganz aus der Beugung (#25). Alle Stationen werden einmal neu gerechnet
- Stationen lassen sich nicht mehr auf der Karte ziehen (#24)
- Messpunkte als weicher Farbfleck, Repeater als größere Kreise (#20)
- MeshCore: Filter steht über der Nachbarliste (#18)
- Profil: Legende „Oberfläche (Gebäude, Bäume)“, Zeile „Durch Objekte“ statt „Hindernisse durchquert“ (#25)
### Behoben
- PDF-Bericht: „Vergleich nicht möglich: 'list' object has no attribute 'get'“ (#21)

## 0.4.5
### Neu
- Messfahrten: MeshMapper-Weiterleitung („Custom API Endpoint“) nach `/api/ingest/meshmapper` mit API-Schlüssel;
  unter ⚙ Schlüssel erzeugen, Adresse und Import-Link für die App kopieren
- Messungen von Hand (Rechtsklick → „Messung hier eintragen“) und als CSV
- Reiter MeshCore → Messfahrten: Messpunkte nach SNR, Messplan (~300-m-Zellen; „genug“ ab 3 Messungen desselben
  Repeaters mit höchstens 6 dB Streuung), Zeitraum- und Repeater-Filter
- Vergleich Messung ↔ Modell je Station (Δ SNR und Δ RSSI, größte Abweichungen anklickbar, Punkte nach Δ gefärbt);
  auch im PDF-Bericht
- Messpunkte optional ohne Anmeldung sichtbar (ohne Zeit und Gerät); Admin kann Messungen je Quelle löschen
### Geändert
- CoreScope-Abgleich nutzt nur noch direkt gemeldete Nachbar-SNR (`neighbor_report` der Observer); aus Paketpfaden
  abgeleitete Werte (letzter Hop, mehrdeutige Präfixe, Dach-Observer) werden nur noch angezeigt
- SNR-Vergleiche lassen Werte am oberen Ende weg, auch wenn das Modell dort mehr als ~10 dB vorhersagt
### Behoben
- Netzwerkfehler beim Laden einzelner NRW-Kacheln (Proxy, DNS) brachen die Berechnung mit HTTP 500 ab

## 0.4.4
### Neu
- PDF-Bericht je Station: Karte, Parameter, Kennzahlen nach Reserve und Entfernung, MeshCore-Nachbarn und Abgleich
- Ergebnis-Reiter ausführlich: Reserveklassen, Versorgung nach Entfernung, Median-/90-%-Reserve, letzter Abgleich
- Stationen werden nacheinander gerechnet, Fortschrittsring an der Station; ▶ je Station erzwingt eine Neuberechnung
- Zweiter Zugang „Benutzer“ (lesen + eigene Stationen anlegen/ändern/speichern), Passwort unter ⚙ im Klartext
- ⚙ Webseite & Zugang: „Diese Seite gehört zu …“, GitHub-Link, Impressum erzeugen/verlinken/aus, Datenschutz erzeugen/verlinken
- MeshCore: dünne Linien zu den Nachbarn nach SNR (Deckkraft einstellbar), Filter nach SNR sowie Tx- und Rx-Anzahl
- Rechtsklick bzw. langes Tippen auf die Karte: Pegel, Profil, „Station hier platzieren“
- Seitenleiste breiter, in der Breite ziehbar und ausblendbar
### Geändert
- Klick auf eine Station öffnet ein Kurzfenster statt der Einstellungen; Editor über ✎
- „automatisch berechnen“ entfällt; geänderte Stationen sind markiert
- Karte startet mit OSM Graustufen, „Ansicht & Legende“ anfangs eingeklappt
- Menüs und Popups schließen bei Klick woanders
### Behoben
- Rand von NRW: WCS-Ablehnung außerhalb brach die Berechnung ab; Lücken wurden mit 0 m gefüllt (Artefakte) –
  jetzt füllt die Ersatzquelle (Standard Copernicus GLO-30), im Grenzgebiet werden beide kombiniert
- Griff der Richtantenne verdeckte herausgezoomt die Station

## 0.4.3
### Neu
- Einstellungen als Dialog hinter dem Zahnrad (⚙) statt eigenem Reiter
- Stations-Editor als Fenster über der Karte mit aufklappbaren Abschnitten; „Strecke zu anderer Station“ ganz oben
- Kartenebenen aus der Rechengrundlage: Gelände (Höhe + Schummerung), Oberfläche, Hindernishöhe über Grund
- MeshCore-Reiter neu gegliedert; Klick auf einen Repeater zeigt Metadaten (Rolle, zuletzt gehört, Akku,
  Temperatur, Funkparameter falls verfügbar), Nachbarn erst auf Wunsch; Nachbarliste mit Beobachtungen je Richtung
- Abgleich mit eigenem Knopf „▶ Abgleich rechnen“ und Stationsauswahl
### Geändert
- Klicks auf der Karte wechseln den Reiter nicht mehr
- Knopf „Nachbarn zeigen“ im Stations-Editor entfernt (Antippen der Station zeigt sie)
### Behoben
- Abgleich startete nicht zuverlässig, wenn die Nachbarn der Station gerade noch geladen wurden

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
