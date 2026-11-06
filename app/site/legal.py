"""Impressum und Datenschutzerklärung.

Angaben kommen aus Umgebungsvariablen; wer eigene Texte will, legt data/legal/impressum.html bzw.
data/legal/datenschutz.html ab (nur der Inhalt, ohne <html>-Gerüst) – die ersetzen die erzeugten Texte.
Die erzeugte Datenschutzerklärung beschreibt, was diese Software tatsächlich tut; sie ersetzt keine Rechtsberatung.
"""
from __future__ import annotations

import html
import os

from .. import paths
from ..sources import corescope, tiles
from ..store import appconfig
from . import auth

LEGAL_DIR = os.path.join(paths.DATA_DIR, "legal")


def _c(k):
    return (appconfig.get().get(k) or "").strip()


def configured():
    return bool(_c("imprint_name") or os.path.exists(os.path.join(LEGAL_DIR, "impressum.html")))


def _lines(s):
    return "<br>".join(html.escape(x.strip()) for x in s.replace("|", "\n").splitlines() if x.strip())


def _override(name):
    p = os.path.join(LEGAL_DIR, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read()
    return None


def _contact():
    NAME, ADDRESS, EMAIL = _c("imprint_name"), _c("imprint_address"), _c("imprint_email")
    parts = [html.escape(NAME)] if NAME else []
    if ADDRESS:
        parts.append(_lines(ADDRESS))
    if EMAIL:
        e = html.escape(EMAIL)
        parts.append(f'E-Mail: <a href="mailto:{e}">{e}</a>')
    return "<br>".join(parts)


def impressum_body():
    o = _override("impressum.html")
    if o is not None:
        return o
    if not _c("imprint_name"):
        return ("<h1>Impressum</h1><p>Der Betreiber dieser Instanz hat noch keine Angaben hinterlegt.</p>"
                "<p class='hint'>Betreiber: in TerraRF unter ⚙ Einstellungen → Webseite &amp; Zugang eintragen, "
                "auf eine bestehende Impressumsseite verlinken oder <code>data/legal/impressum.html</code> anlegen.</p>")
    EXTRA = _c("imprint_extra")
    extra = f"<p>{_lines(EXTRA)}</p>" if EXTRA else ""
    return f"<h1>Impressum</h1><h2>Angaben gemäß § 5 DDG</h2><p>{_contact()}</p>{extra}"


def datenschutz_body():
    o = _override("datenschutz.html")
    if o is not None:
        return o
    who = _contact() or "<em>(Betreiber hat noch keine Angaben hinterlegt)</em>"
    days = auth.SESSION_S // 86400
    if tiles.ENABLED:
        maps = ("<p>Kartenkacheln (OpenStreetMap, OpenTopoMap, Luftbild Geobasis NRW) lädt Ihr Browser ausschließlich "
                "von diesem Server. Der Server ruft sie seinerseits bei den Kartendiensten ab und speichert sie "
                "zwischen. Ihre IP-Adresse wird dabei nicht an die Kartendienste übermittelt.</p>")
    else:
        maps = ("<p>Zur Darstellung der Karte lädt Ihr Browser Kartenkacheln direkt von folgenden Anbietern. Dabei "
                "werden Ihre IP-Adresse und technische Angaben zum Browser an den jeweiligen Anbieter übermittelt. "
                "Rechtsgrundlage ist Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse an einer funktionierenden "
                "Kartendarstellung).</p><ul>"
                "<li>OpenStreetMap Foundation, Vereinigtes Königreich – "
                "<a href='https://osmfoundation.org/wiki/Privacy_Policy'>Datenschutzerklärung</a></li>"
                "<li>OpenTopoMap – <a href='https://opentopomap.org/about'>Informationen</a></li>"
                "<li>Esri (World Imagery), USA – "
                "<a href='https://www.esri.com/en-us/privacy/overview'>Datenschutzerklärung</a></li>"
                "<li>Geobasis NRW (Luftbild), Bezirksregierung Köln</li></ul>")
    cs = ""
    if corescope.enabled():
        cs = ("<h2>MeshCore-Netzdaten</h2><p>Der Reiter „MeshCore“ zeigt öffentlich abrufbare Daten eines MeshCore-Analysedienstes "
              f"(<a href='{html.escape(corescope.BASE)}'>{html.escape(corescope.BASE)}</a>): Knotennamen, Standorte, wie sie "
              "die Knoten selbst aussenden, und Verbindungsstatistiken. Diese Daten ruft der Server ab; Ihr Browser "
              "verbindet sich nicht mit dem Dienst.</p>")
    PRIVACY_EXTRA = _c("privacy_extra")
    extra = f"<h2>Weitere Hinweise</h2><p>{_lines(PRIVACY_EXTRA)}</p>" if PRIVACY_EXTRA else ""
    return f"""<h1>Datenschutzerklärung</h1>
<h2>Verantwortlicher</h2><p>{who}</p>
<h2>Kurzfassung</h2>
<p>Diese Seite verwendet keine Analyse- oder Trackingdienste, keine Werbung, keine Schriftarten oder Skripte von
Drittanbietern und kein Cookie-Banner, weil sie keine einwilligungspflichtigen Cookies setzt.</p>
<h2>Aufruf der Seite</h2>
<p>Beim Aufruf überträgt Ihr Browser technisch notwendig Ihre IP-Adresse und Angaben wie Browsertyp an den Server.
Die Anwendung TerraRF selbst schreibt keine Zugriffsprotokolle mit IP-Adressen. Ausnahme: Fehlgeschlagene
Anmeldeversuche werden zum Schutz vor Passwort-Raten bis zu 10 Minuten lang je IP-Adresse im Arbeitsspeicher
gezählt (Art. 6 Abs. 1 lit. f DSGVO).</p>
<h2>Cookies und lokaler Speicher</h2>
<p>Besucher erhalten keine Cookies. Nur nach der Anmeldung des Administrators wird das Cookie
<code>terrarf_session</code> gesetzt. Es ist für den angemeldeten Bereich unbedingt erforderlich (§ 25 Abs. 2 Nr. 2
TDDDG) und wird beim Schließen des Browsers gelöscht, sofern nicht „angemeldet bleiben“ gewählt wurde (dann höchstens
{days} Tage). Lokaler Browserspeicher (localStorage) wird nicht verwendet.</p>
<h2>Kartendarstellung</h2>{maps}
{cs}
<h2>Messfahrten</h2><p>Messwerte von Messfahrten (z. B. aus der App MeshMapper) werden mit Ort, Zeit, Signalwerten,
Funkeinstellungen und gegebenenfalls einer kurzen Gerätekennung gespeichert, um die Rechnung mit der Wirklichkeit zu
vergleichen. Sie sind nur für angemeldete Nutzer sichtbar, sofern der Betreiber sie nicht freigibt; Besucher sehen dann
nur Ort und Signalwerte, keine Zeit und keine Gerätekennung. Löschung auf Anfrage beim Betreiber.</p>
<h2>Projekte</h2><p>Angezeigte Projekte und Stationen werden vom Betreiber angelegt. Von Besuchern werden keine Eingaben
gespeichert; Berechnungen erfolgen ohne Bezug zu Ihrer Person.</p>
{extra}
<h2>Ihre Rechte</h2><p>Sie haben nach der DSGVO das Recht auf Auskunft (Art. 15), Berichtigung (Art. 16), Löschung
(Art. 17), Einschränkung der Verarbeitung (Art. 18) und Widerspruch (Art. 21) sowie das Recht, sich bei einer
Datenschutz-Aufsichtsbehörde zu beschweren (Art. 77).</p>"""


PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} – TerraRF</title><link rel="icon" href="/static/icon.svg">
<style>body{{font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#1d2430;max-width:760px;margin:0 auto;padding:24px}}
h1{{font-size:24px}}h2{{font-size:17px;margin-top:26px}}a{{color:#2563eb}}code{{background:#f1f5f9;padding:0 4px;border-radius:3px}}
.hint{{color:#6b7280;font-size:13px}}nav{{margin-bottom:18px}}footer{{margin-top:40px;color:#6b7280;font-size:13px}}</style></head>
<body><nav><a href="/">← zurück zur Karte</a> · <a href="/impressum">Impressum</a> · <a href="/datenschutz">Datenschutz</a></nav>
{body}<footer>TerraRF – freie Software (AGPL-3.0), <a href="https://github.com/RideMyByte/TerraRF">Quellcode</a></footer></body></html>"""


def page(kind):
    body, title = (impressum_body(), "Impressum") if kind == "impressum" else (datenschutz_body(), "Datenschutz")
    return PAGE.format(title=title, body=body)
