# Changelog

## [Unreleased]

## 0.4.0
- **Admin-Login** (#13): ein Admin aus der Konfiguration (`RFP_ADMIN_USER`/`RFP_ADMIN_PASSWORD`),
  Besucher sehen öffentliche Projekte nur lesend; Projekte privat/öffentlich, „Link teilen“
- **Stationen ein-/ausblenden ohne Neuberechnung** (#12): jede Station wird einmal berechnet und als
  eigenes Datenbild geliefert; der Browser setzt die Ansichten selbst zusammen, Haken, Ansicht und
  Reserve-Regler wirken sofort. Auswahl „Nur: Station …“ entfernt
- Basiskarte OpenStreetMap in Graustufen
- Abfrage per Klick in die Karte ohne Serveranfrage
- Projekt heißt jetzt **TerraRF**, neues Logo

## 0.3.0
- Lokaler NRW-Kachelspeicher (5 m), Vorladen von ganz NRW beim Start, fortsetzbar
- Haken an Stationen blendet sie samt Abdeckung ein/aus, automatische Neuberechnung aus dem Cache
- Regler „Mindest-Reserve“ in der Kartenlegende
- Hindernisdämpfung nach ITU-R P.833, Deygout als Standard, Sicht auf Dächer/Baumkronen
- Netzwerkfehler schalten nicht mehr fälschlich auf 1-m-Download um

## 0.2.0
- Clutter am Empfänger (ITU-R P.2108), Bodenreflexion, Fading-/Standortreserve
- NRW DOM1 als Hindernisquelle, Regionen-Download, Cache-Verwaltung

## 0.1.0
- Erste Version: Stationen, Abdeckung, Überlappung, Profil mit Fresnelzone, Projekte
