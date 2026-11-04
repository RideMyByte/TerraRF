<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img src="assets/logo.svg" alt="TerraRF – Funkplanung über echtem Gelände" width="460">
  </picture>
</p>

# TerraRF

Selbst gehostete Funkplanung im Browser: Stationen auf die Karte setzen, Abdeckung,
Überlappung und Sichtlinien berechnen, Geländeprofile mit Fresnelzone und Linkbudget ansehen.
Gedacht für **MeshCore, Meshtastic, LoRa, HAMNET und Amateurfunk**, mit besonders genauen
Höhendaten für **Nordrhein-Westfalen** (Geobasis NRW, 1-m-Laserscan, lokal auf 5 m gespeichert)
und weltweit Copernicus GLO-30.

[![CI](https://github.com/RideMyByte/TerraRF/actions/workflows/ci.yml/badge.svg)](https://github.com/RideMyByte/TerraRF/actions/workflows/ci.yml)
[![Image](https://github.com/RideMyByte/TerraRF/actions/workflows/docker.yml/badge.svg)](https://github.com/RideMyByte/TerraRF/pkgs/container/terrarf)
[![Lizenz: AGPL-3.0](https://img.shields.io/badge/Lizenz-AGPL--3.0-blue.svg)](LICENSE)

## Funktionen
- Karten: OpenStreetMap, OpenTopoMap, NRW-Luftbild, Satellit
- Beliebig viele Stationen: Sendeleistung, Frequenz, Bandbreite/Modulation (LoRa-SF, FM, frei),
  Antenne (Rundstrahler/Richtantenne, Gewinn, Azimut, Öffnungswinkel, Neigung), Höhe über Grund oder Dach
- Ansichten: bester Pegel (Reserve), Überlappung, bester Server, Sichtlinie/Fresnelzone, Dächer sichtbar
- Klick in die Karte: Pegel aller Stationen, Geländeprofil, Linkbudget, Vergleich mit Okumura-Hata
- Haken an der Station blendet sie ein/aus; Änderungen werden automatisch aus dem Cache nachgerechnet
- Regler für Deckkraft und Mindest-Reserve direkt an der Karte
- Projekte speichern, laden, als JSON exportieren/importieren
- Ein Admin-Login zum Bearbeiten; Besucher sehen öffentliche Projekte nur lesend (Teilen per Link)
- Basiskarten inkl. OpenStreetMap in Graustufen
- PDF-Bericht je Station (Karte, Parameter, Kennzahlen nach Reserve und Entfernung, MeshCore-Nachbarn und Abgleich)
- Zweiter Zugang „Benutzer“: lesen und eigene Stationen anlegen
- Kartenebenen aus der Rechengrundlage: Gelände (Höhe + Schummerung), Oberfläche, Hindernishöhe (Gebäude/Bäume)
- Statusanzeige auf der Karte: was der Server gerade rechnet, für den Admin dazu CPU und RAM des Containers
- Richtfunk: Antenne mit der Maus drehen, Strecke Station → Station mit beiden Antennen, gegenseitig ausrichten
- Handytauglich und als App installierbar (Startbildschirm, öffnet ohne Browserleiste)
- MeshCore-Netz aus [CoreScope](https://github.com/Kpa-clawbot/CoreScope): Repeater-Standorte, beobachtete
  Verbindungen und gemessenes SNR anzeigen, Repeater als Station übernehmen, Modell gegen Messwerte abgleichen

## Installation

### Unraid
**Über Community Applications:** Apps → nach „terrarf“ suchen → Installieren
*(sobald die Aufnahme freigeschaltet ist)*.

**Bis dahin manuell:** Docker → *Add Container* → bei *Template* die URL
`https://raw.githubusercontent.com/RideMyByte/TerraRF/main/templates/terrarf.xml` eintragen,
oder die Datei nach `/boot/config/plugins/dockerMan/templates-user/my-terrarf.xml` kopieren.
Danach WebUI öffnen (Port 8080).

Beim ersten Start lädt der Container die NRW-Höhendaten in `appdata/terrarf`
(grob 3–5 GB je Modell bei 5 m, je nach NRW-Server einige Stunden; fortsetzbar).
Abschalten: Variable `RFP_PRELOAD` leeren, dann wird nur bei Bedarf geladen.

### Docker / Docker Compose
```bash
docker run -d --name terrarf -p 8080:8080 -v ./data:/data ghcr.io/ridemybyte/terrarf:latest
```
oder `docker compose up -d` mit der beiliegenden `docker-compose.yml`.

### Konfiguration (Umgebungsvariablen)
| Variable | Standard | Bedeutung |
|---|---|---|
| `RFP_ADMIN_USER` | `admin` | Benutzer für den Login (Bearbeiten, Speichern, Downloads) |
| `RFP_ADMIN_PASSWORD` | – | Passwort; leer = wird beim ersten Start erzeugt und ins Log sowie nach `data/admin_password.txt` geschrieben |
| `RFP_PUBLIC_READ` | `true` | Besucher dürfen als „öffentlich“ markierte Projekte ohne Login ansehen |
| `RFP_TILE_PROXY` | `true` | Kartenkacheln über den Server laden (Besucher-IPs gehen nicht an Kartendienste); `false` = direkt, dann zusätzlich Esri-Satellit |
| `RFP_TILE_CACHE_MB` | `2000` | Größe des Kachel-Caches |
| `RFP_IMPRINT_NAME` / `_ADDRESS` / `_EMAIL` | – | Angaben für Impressum und Datenschutzerklärung (Adresszeilen mit `\|` trennen) |
| `RFP_PRIVACY_EXTRA` | – | Zusatztext für die Datenschutzerklärung, z. B. zu Logs eines vorgeschalteten Reverse-Proxys |
| `RFP_CORESCOPE_URL` | – | CoreScope-Instanz für den Reiter „MeshCore“, z. B. `https://corescope.meshrheinland.de` |
| `RFP_GRID_LAT` | `51.5` | Bezugsbreite des Rechengitters; nur ändern, wenn das Einsatzgebiet weit von 51,5° N liegt |
| `RFP_CORESCOPE_SYNC_H` | `24` | Repeater-Liste alle n Stunden neu holen (0 = nur per Knopf) |
| `RFP_PRELOAD` | `nrw_dgm,nrw_dom` | NRW-Modelle beim Start komplett laden (leer = nur bei Bedarf) |
| `RFP_NRW_RES` | `5` | Rasterweite des lokalen NRW-Speichers (m) |
| `RFP_PRELOAD_CENTER` | `51.48,7.55` | Startpunkt des Vorladens (lat,lon), nahe Kacheln zuerst |
| `RFP_REGION_WORKERS` | `2` | parallele Anfragen beim Vorladen |
| `PUID` / `PGID` | – | Dateien in `/data` gehören diesem Benutzer (Unraid-Template: 99/100) |
| `RFP_WCS_MAX_NATIVE_MB` | `400` | Limit, falls ein WCS nicht serverseitig skaliert |

**Zugriff:** Ohne Login sieht man nur Projekte mit Haken „öffentlich sichtbar“ – Stationen
ein-/ausblenden, Ansicht und Reserve-Regler, Profile ansehen, aber nichts ändern oder speichern.
„🔗 Link teilen“ kopiert einen direkten Link (`?p=<id>`). Wer TerraRF im Internet erreichbar macht,
sollte es hinter einen Reverse Proxy mit HTTPS stellen (das Session-Cookie wird dann als `Secure` gesetzt).

Updates: Neue Versionen erscheinen als `:latest`. Unraid zeigt im Docker-Tab „Update verfügbar“.
Wer immer den neuesten Stand von `main` will, nimmt `:edge`.

## Höhendaten
Am Rand der Hauptquelle (z. B. jenseits der NRW-Grenze) füllt eine Ersatzquelle die Lücken, Standard Copernicus
GLO-30 (Einstellung „Ersatz außerhalb der Hauptquelle“). Im Grenzgebiet wird also mit beiden gerechnet. Copernicus
ist ein 30-m-Oberflächenmodell: Gebäude und Bäume stecken dort in der Geländehöhe und werden nicht als Hindernis
erkannt – dort am besten einen Mindest-Clutter setzen.

| Quelle | Art | Hinweis |
|---|---|---|
| **NRW DGM** (Standard Gelände) | Geländemodell, WCS `wcs_nw_dgm` | lokal als 5-m-Kacheln, dl-de/zero-2-0 |
| **NRW DOM1** (Standard Hindernisse) | Oberflächenmodell aus Laserscan, WCS `wcs_nw_dom` | lokal als 5-m-Kacheln |
| Copernicus GLO-30 | Oberflächenmodell 30 m, weltweit | Rückfall außerhalb NRW |
| Lokal | eigene GeoTIFFs in `data/dem/<ordner>/` | erscheinen als „Lokal: …“ |

## Bedienung
- **⚙** oben links öffnet die Einstellungen – nur für den Admin: Webseite & Zugang (Benutzer, Impressum,
  Datenschutz, Zugehörigkeit „Diese Seite gehört zu …“, GitHub-Link), Höhendaten, Offline-Download, Modell, Empfänger.
- **Rechtsklick** (Handy: lange tippen) in die Karte zeigt Pegel aller Stationen, Profil und „Station hier platzieren“.
- Ein Klick auf eine Station zeigt Kurzinfo und Aktionen (✎ Bearbeiten, ▶ Rechnen, PDF); ✎ öffnet den Editor als
  Fenster über der Karte mit aufklappbaren Abschnitten. Klicks auf der Karte wechseln nie den Reiter.
- **Rechnen:** „▶ Berechnen“ rechnet alle Stationen nacheinander (Unverändertes kommt aus dem Cache), ▶ an einer
  Station rechnet nur diese neu – auch wenn ein Ergebnis im Cache liegt. Ein Ring an der Station zeigt den Fortschritt.
- Die Seitenleiste lässt sich am Rand breiter ziehen und mit « ausblenden.
- **Rollen:** Admin (alles), Benutzer (sieht Öffentliches, legt Stationen an, ändert und speichert nur eigene;
  Name/Passwort unter ⚙), Besucher (nur lesen, falls `RFP_PUBLIC_READ`).
- **Kartenebenen** (Ebenen-Knopf oben rechts, ab Zoomstufe 11): „Daten: Gelände“, „Oberfläche“ und „Hindernishöhe
  über Grund“ zeigen die Höhendaten, mit denen gerechnet wird. Gezeigt wird nur, was lokal vorliegt (vorgeladene
  NRW-Kacheln, eigene GeoTIFFs); das Ansehen löst keine Downloads aus.

## Auf dem Handy / als App
Unter 800 px Breite wird die Seitenleiste zur Schublade (☰), Profil und Legende passen sich an, Meldungen erscheinen
kurz auf der Karte. Auf dem Startbildschirm: Android/Chrome Menü ⋮ → „App installieren“, iPhone/Safari Teilen →
„Zum Home-Bildschirm“ (Hilfe auch unten links unter „Als App installieren“). **Voraussetzung ist HTTPS**, z. B. über
einen Reverse-Proxy mit Zertifikat – über `http://<unraid-ip>:8080` entsteht nur ein Lesezeichen, das im Browser
öffnet. Einen Service Worker gibt es bewusst nicht (nichts wird auf dem Gerät gespeichert); TerraRF braucht ohnehin
eine Verbindung zum Server. Als Admin öffnet die App beim Start das zuletzt geöffnete Projekt.

## Datenschutz und Impressum
TerraRF setzt für Besucher keine Cookies, nutzt keinen lokalen Browserspeicher, lädt keine Skripte oder Schriften
von Dritten und schreibt keine Zugriffsprotokolle. Kartenkacheln laufen standardmäßig über den eigenen Server
(`RFP_TILE_PROXY`). Nur der Admin-Login setzt ein Sitzungscookie – technisch notwendig, daher kein Cookie-Banner.
Unter `/impressum` und `/datenschutz` erzeugt TerraRF passende Texte aus den `RFP_IMPRINT_*`-Angaben; eigene Fassungen
als `data/legal/impressum.html` bzw. `datenschutz.html` ersetzen sie. **Keine Rechtsberatung:** Ob ein Impressum nötig
ist und ob die Texte für deinen Betrieb vollständig sind, musst du selbst prüfen. Insbesondere protokolliert ein
vorgeschalteter Reverse-Proxy (z. B. Nginx Proxy Manager) meist IP-Adressen – das gehört dann in `RFP_PRIVACY_EXTRA`
oder lässt sich dort abschalten.

## MeshCore-Netz (CoreScope)
Mit `RFP_CORESCOPE_URL` holt TerraRF die Repeater einer CoreScope-Instanz (nur lesend, serverseitig, zwischengespeichert)
und zeigt sie im Reiter „MeshCore“. Ein Klick auf einen Repeater zeigt, was CoreScope über ihn weiß (Rolle, zuletzt
gehört, Akku, Temperatur, ggf. Funkparameter – Antennenhöhe, Antennentyp und Sendeleistung sendet MeshCore nicht aus),
und bietet an: Nachbarn anzeigen, als Station hinzufügen, Höhe/Gewinn für den Abgleich hinterlegen. „Nachbarn anzeigen“
blendet alle anderen aus und färbt die Nachbarn nach SNR; die Liste im Reiter zeigt die Beobachtungen je Richtung.
Erneuter Klick hebt die Auswahl auf. Ein Klick auf eine verknüpfte Station zeigt sofort deren Nachbarn; eigene
Stationen lassen sich im Editor mit einem Repeater verknüpfen. Der Abgleich startet mit „▶ Abgleich rechnen“.
Bedeutung der Farben:
- **dunkler Rand** – direkt gemeldetes SNR: aus der Nachbartabelle, die ein Observer selbst an CoreScope meldet
  (`neighbor_report`, direkt gehörte Adverts). Nur diese Werte fließen in den Abgleich.
- **gestrichelter Rand** – aus Paketpfaden abgeleitetes SNR („Ø“). Ein Observer hört nur den letzten Hop, kurze
  Präfixe sind mehrdeutig, Dach-Observer hören über nahe Repeater – solche Werte werden nur angezeigt, nicht verglichen.
  Der Filter „nur beidseitig“ + Mindestanzahl blendet falsche Nachbarn größtenteils aus.

„Als Station übernehmen“ legt eine Station mit Annahmen an (10 m, Rundstrahler 5,8 dBi, 22 dBm) – CoreScope kennt
Antennenhöhe, Gewinn und Leistung nicht. „Mit Messwerten vergleichen“ rechnet für jede gemessene Strecke das
SNR im Modell und zeigt die Abweichung – nur für Partner, die mit den aktuellen Filtern auch auf der Karte stehen.
Für die Gegenstelle gelten, in dieser Reihenfolge: Werte der Station, falls der Partner im Projekt eingetragen ist
(Höhe, Antenne in Richtung, Kabel), die je Repeater hinterlegte Höhe/Gewinn, sonst die Standardwerte. Ein Klick auf
einen Partner zeigt die Strecke als Profil. SNR-Werte ab 10 dB fließen nicht in die Statistik ein (LoRa-Meldungen sind
nach oben begrenzt). Bitte die Instanz schonend nutzen und im Zweifel bei den Betreibern nachfragen.

## Messfahrten (MeshMapper)
Eigene Messungen sind der verlässlichste Abgleich. TerraRF nimmt sie auf drei Wegen an:
1. **MeshMapper-Weiterleitung**: unter ⚙ → Webseite & Zugang → *Messfahrten* einen API-Schlüssel erzeugen und speichern,
   dann den angezeigten Link kopieren und in der MeshMapper-App unter *Settings → API Endpoints → Import from Clipboard*
   einfügen. Die App schickt jeden erfolgreich hochgeladenen Messblock zusätzlich an `https://<host>/api/ingest/meshmapper`
   (Header `X-API-Key`). MeshMapper verlangt HTTPS – TerraRF muss also über einen Reverse-Proxy mit Zertifikat erreichbar
   sein. Gespeichert werden DISC/TRACE (SNR beim Handy und beim Repeater, RSSI) und TX/RX-Echos (SNR je Repeater);
   DEFER/SCOPES und fehlgeschlagene Erkennungen werden ignoriert, doppelte Echos (TX + RX) nur einmal gezählt.
   Repeater-Präfixe werden über die CoreScope-Liste einem Schlüssel zugeordnet, wenn das im Umkreis eindeutig ist.
2. **Von Hand**: Rechtsklick (Handy: lange tippen) → „Messung hier eintragen“.
3. **CSV**: Kopfzeile `lat;lon;rep;snr_down`, optional `snr_up;rssi;ts;rx_h;note` (Trenner `;` oder `,`, Dezimalkomma geht).

Im Reiter „MeshCore“ → *Messfahrten*: Messpunkte (Farbfleck nach SNR, antippen zeigt alle Daten), **Messplan** und
**Messreihen**. Eine Messreihe ist eine Fahrt (Pause über 30 min trennt, ein CSV-Import ist eine Reihe); je Reihe lassen
sich Antennenhöhe, Gewinn, Kabel, externe Antenne und eine Notiz eintragen. Einen Repeater oder eine verknüpfte Station
antippen zeigt automatisch nur deren Messungen.

**Vergleich mit dem Modell** (eigene Karte im Reiter, auch im PDF-Bericht), getrennt nach Quelle:
- **CS – CoreScope, Repeater ↔ Repeater**: SNR, das ein Partner-Repeater in seiner Nachbartabelle für ein direkt gehörtes
  Advert der Station meldet (0 Hop). Modell: Strecke Station → Partner mit dessen Höhe/Gewinn.
- **MM – Messfahrten, Repeater → Handy**: SNR und RSSI am Handy. Modell: Abdeckung der Station am Messort mit Höhe und
  Gewinn der Messreihe (je vorkommender Höhe wird die Abdeckung einmal gerechnet).
Nahe am Repeater sagt SNR nichts – LoRa meldet höchstens etwa 10 dB –, dort zählt nur RSSI.
Messplan: eine ~300-m-Zelle (das Raster von MeshMapper) gilt als **genug**, wenn ein Repeater dort mindestens 3 Messungen
mit höchstens 6 dB Streuung hat; orange Zellen lohnen eine weitere Fahrt. Die App selbst lässt sich von TerraRF nicht
steuern. Ihr *Smart Pinging* (Wardriving → Auto-Ping) überspringt Felder, die auf meshmapper.net in den letzten 14 Tagen
schon Abdeckung haben – egal von wem und wie oft. Für TerraRFs Regel (3 stimmige Messungen) heißt das: in orangen Zellen
Smart Pinging lieber ausschalten, sonst kommen dort keine weiteren Messungen an.
Messungen sind nur für angemeldete Nutzer sichtbar, außer „Messpunkte auch ohne Anmeldung zeigen“ ist gesetzt (dann ohne
Zeit, Gerät und Notiz). Der Admin kann Messungen je Quelle löschen.

## Modell

Gesamtdämpfung = Freiraum + Bodenreflexion + Beugung + Hindernisse + Clutter am Empfänger

* **Beugung** über Gelände + Oberfläche: Deygout (Standard) oder Bullington (ITU-R P.526).
  Die Fresnelzone steckt darin: v ist die Freiraum-Höhe relativ zur 1. Fresnelzone.
  Deygout ist Standard, weil Bullington in bebautem Gebiet deutlich zu optimistisch war.
* **Hindernisdämpfung** (ITU-R P.833): Wo die direkte Linie durch Bäume/Gebäude läuft
  (über Gelände, unter der Oberfläche): A = Am·(1 − exp(−d·γ/Am)), bei 869 MHz etwa
  γ ≈ 0,16 dB/m, Am ≈ 25 dB (Tabelle P.833-8, frequenzinterpoliert). Kommt zur Beugung dazu.
* **Clutter am Empfänger** (ITU-R P.2108 §3.1): Zuschlag, wenn der Empfänger unter der
  umgebenden Bebauung steht (bei 1,5 m und 869 MHz ≈ 18 dB vorstädtisch, ≈ 22 dB städtisch).
  „Automatisch“ bestimmt die Höhe aus Oberfläche − Gelände im Nahbereich des Empfängers
  (Standard 60 m, höchstens die halbe Strecke – Hindernisse davor zählen voll in der Beugung).
* **Nahfeld der Station**: Bis 150 m um die Antenne wird das Profil in 5 m abgetastet, auch wenn mit 10 oder 25 m
  gerechnet wird. So schattet ein Gebäude direkt neben der Antenne die Richtung dahinter ab, statt im groben Raster
  zu verschwimmen. Die eigene 5-m-Zelle der Antenne gilt als frei.
* **Abstrahlsektor** (je Station, optional): für Antennen an einer Wand o. Ä. – außerhalb des Sektors zusätzliche
  Dämpfung mit weichem Rand, innerhalb unverändert.
* **Gebäude vs. Bäume**: Das Oberflächenmodell (DOM) enthält beides, ohne sie zu unterscheiden. Beugung rechnet über
  beides; die P.833-Dämpfung „durch Objekte“ behandelt Gebäude wie dichten Bewuchs (höchstens ≈ 25 dB) –
  eine Mauer ist meist undurchlässiger.
* **Bodenreflexion**: 2-Strahl-Zuschlag bei freier Sicht.
* **Empfänger**: Antennengewinn (Standard 0 dBi), Zusatzverlust (Körper/Gebäude),
  Rauschanhebung, **Fading-/Standortreserve** (Standard 10 dB, Regler an der Karte).

**Grenzen:** Keine Mehrwege-/Indoor-Modelle, keine Störer. Die Standardwerte sind an wenigen
Messungen im Ruhrgebiet plausibilisiert, nicht kalibriert. Messwerte (Standort, Höhe, RSSI/SNR)
als Issue helfen, das Modell zu verbessern.

## Entwicklung
```bash
pip install -r requirements-dev.txt
python -m pytest -q
RFP_DATA=./data RFP_PRELOAD= uvicorn app.main:app --reload --port 8080
```
Aufbau und Regeln: [CLAUDE.md](CLAUDE.md). Beiträge: [CONTRIBUTING.md](CONTRIBUTING.md).

## Wünsche, Automatisierung, Releases
- **Issues**: Vorlagen „Wunsch“ und „Fehler melden“. Jeder darf Issues anlegen.
- **Freigabe**: Der Maintainer setzt das Label `freigegeben`. Erst dann wird gebaut.
- **Claude Code** (GitHub Action, läuft mit dem Abo des Maintainers):
  - `@claude …` als Kommentar des Owners oder Label `claude` → Claude arbeitet sofort an diesem Issue/PR.
  - *Actions → „Claude Wunsch-Stapel“ → Run workflow*: arbeitet bis zu N freigegebene Issues nacheinander ab,
    je ein Pull Request. Optional automatisch freitags (Repo-Variable `CLAUDE_BATCH_SCHEDULE=true`).
  - Kommentare und Issues von Fremden lösen nie etwas aus; nichts landet ohne Merge in `main`.
- **CI** testet jeden Pull Request; `main` wird als `:edge` veröffentlicht.
- **Release**: *Actions → Release → Run workflow* (patch/minor/major) → Tag, Release-Notizen,
  Image `:x.y.z` und `:latest`.

Einmalige Einrichtung: `scripts/setup-github.sh` (Labels, Secret `CLAUDE_CODE_OAUTH_TOKEN`).

## Lizenz
[AGPL-3.0](LICENSE). Drittanbieter-Software und Datenlizenzen: [THIRD_PARTY.md](THIRD_PARTY.md).
