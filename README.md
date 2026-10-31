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
| `RFP_CORESCOPE_URL` | – | CoreScope-Instanz für den Tab „Netz“, z. B. `https://corescope.meshrheinland.de` |
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
| Quelle | Art | Hinweis |
|---|---|---|
| **NRW DGM** (Standard Gelände) | Geländemodell, WCS `wcs_nw_dgm` | lokal als 5-m-Kacheln, dl-de/zero-2-0 |
| **NRW DOM1** (Standard Hindernisse) | Oberflächenmodell aus Laserscan, WCS `wcs_nw_dom` | lokal als 5-m-Kacheln |
| Copernicus GLO-30 | Oberflächenmodell 30 m, weltweit | Rückfall außerhalb NRW |
| Lokal | eigene GeoTIFFs in `data/dem/<ordner>/` | erscheinen als „Lokal: …“ |

## MeshCore-Netz (CoreScope)
Mit `RFP_CORESCOPE_URL` holt TerraRF die Repeater einer CoreScope-Instanz (nur lesend, serverseitig, zwischengespeichert)
und zeigt sie im Tab „Netz“. Ein Klick auf einen Repeater zeigt seine Verbindungen:
- **farbig durchgezogen** – gemessenes SNR: Der Partner ist selbst Observer und hat den Repeater direkt gehört.
- **grau gestrichelt** – nur aus Paketpfaden abgeleitet. Kurze Hop-Präfixe können falsch zugeordnet sein; der Filter
  „nur beidseitig“ + Mindestanzahl blendet die meisten Fehlkanten aus.

„Als Station übernehmen“ legt eine Station mit Annahmen an (10 m, Rundstrahler 5,8 dBi, 22 dBm) – CoreScope kennt
Antennenhöhe, Gewinn und Leistung nicht. „Mit Messwerten vergleichen“ rechnet für jede gemessene Strecke das
SNR im Modell und zeigt die Abweichung. SNR-Werte ab 10 dB fließen nicht in die Statistik ein (LoRa-Meldungen sind
nach oben begrenzt). Bitte die Instanz schonend nutzen und im Zweifel bei den Betreibern nachfragen.

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
  „Automatisch“ bestimmt die Höhe aus Oberfläche − Gelände im Nahbereich des Empfängers.
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
