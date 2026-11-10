#!/usr/bin/env python3
"""
Személyre szabott hírportál – RSS-ből rovatos, áttekinthető címlap.

Használat:
    python3 hirportal.py                 # letölt, frissít, legenerálja az index.html-t
    python3 hirportal.py -o docs/index.html

Csak a Python beépített könyvtárait használja (3.9+), semmit nem kell telepíteni.
A hírek egy kis állapotfájlban (hirek_allapot.json) gyűlnek, így az is megmarad,
ami már kiesett a lapok RSS-éből. 72 óránál régebbi hír törlődik.
"""
import argparse
import hashlib
import html
import json
import random
import re
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

# ════════════════════════════════════════════════════════════════════
#  BEÁLLÍTÁSOK – ezt a részt nyugodtan szerkeszd
# ════════════════════════════════════════════════════════════════════

OLDAL_CIM = "A Kezdőlap"
# Fejléc: "veletlen" = minden megnyitáskor a három közül egy másik (amíg a lap nyitva van, nem cserélődik);
# vagy rögzítve: "hirlap" (Pesti Hírlap-szerű), "nemzeti" (reformkori, a Nemzeti Dal címe nyomán), "nepszava" (a régi Népszava címe nyomán)
FEJLEC_STILUS = "veletlen"
# A fejléc háttérszíne napszak szerint, a néző órája alapján: (kezdő óra, záró óra, szín)
NAPSZAK_SZINEK = [
    (5, 8, "#a03f5a"),    # hajnal – rózsás
    (8, 12, "#1d5fa6"),   # délelőtt – égkék
    (12, 17, "#0e6b67"),  # délután – zöldeskék
    (17, 21, "#a1452a"),  # este – téglavörös
    (21, 24, "#2e2a63"),  # éjszaka – mélyindigó
    (0, 5, "#2e2a63"),
]
FEJLEC_SZIN = None
# A címek betűtípusa a fejléchez igazodik (Google Fonts; csak a kiválasztott töltődik le)
CIMBETUK = {
    "hirlap": {"csalad": "Oswald", "url": "Oswald:wght@400..700", "suly": 500, "nagy": 600, "ter": ".005em"},
    "nemzeti": {"csalad": "Playfair Display", "url": "Playfair+Display:wght@400..900", "suly": 700, "nagy": 800, "ter": "0",
                "tartalek": "Georgia,'Times New Roman',serif"},
    "nepszava": {"csalad": "Roboto Serif", "url": "Roboto+Serif:opsz,wdth,wght@8..144,50..150,100..900", "suly": 700, "nagy": 750,
                 "ter": "-.005em", "nyujt": "70%", "tartalek": "Georgia,'Times New Roman',serif"},
}        # ha egy állandó színt szeretnél, pl. "#16202f"; None = napszak szerint

# Országos lapok. Az „alap” rovat akkor számít, ha a cikkről semmi más nem derül ki.
FORRASOK = [
    {"id": "telex",  "nev": "Telex",         "szin": "#2563eb", "feeds": ["https://telex.hu/rss"],
     # a Telex időnként kitiltja a GitHub gépét (403); ilyenkor a Google Hírek Telexre szűrt csatornája
     # pótolja a címeket, a következő sikeres futásnál pedig a teljes (képes) változat lép a helyükre
     "tartalek": "https://news.google.com/rss/search?q=site:telex.hu+when:2d&hl=hu&gl=HU&ceid=HU:hu",
     # a Google-csatornában az al-oldalak a cím elején állnak („G7: …”); ebből a rovat, és a cím előtag nélkül
     "tartalek_elotag": {"After": "kultura", "Karakter": "kultura", "G7": "gazdasag"}},
    {"id": "444",    "nev": "444",           "szin": "#e11d48", "feeds": ["https://444.hu/feed"]},
    {"id": "24",     "nev": "24.hu",         "szin": "#ea580c", "feeds": ["https://24.hu/feed/"]},
    {"id": "hvg",    "nev": "HVG",           "szin": "#7c3aed", "feeds": ["https://hvg.hu/rss"]},
    {"id": "mhang",  "nev": "Magyar Hang",   "szin": "#0d9488", "feeds": ["https://hang.hu/rss/"]},
    {"id": "atlatszo", "nev": "Átlátszó",    "szin": "#65a30d", "feeds": ["https://atlatszo.hu/feed/"],
     "alap": "belfold", "alap_elony": 1},
    {"id": "valasz", "nev": "Válasz Online", "szin": "#a16207", "feeds": ["https://www.valaszonline.hu/feed/"]},
    {"id": "nszo",   "nev": "Népszabadság",  "szin": "#991b1b", "feeds": ["https://nszo.hu/feed/"],
     "kep_oldalrol": True},   # a hírfolyamban többnyire nincs kép: az új cikkeknél a cikkoldalról (og:image) olvassuk ki
    {"id": "infostart", "nev": "Infostart", "szin": "#4f46e5", "feeds": ["https://infostart.hu/24ora/rss/"]},
    {"id": "euronews", "nev": "Euronews",    "szin": "#0369a1",
     "feeds": ["https://hu.euronews.com/rss?level=theme&name=news"], "alap": "kulfold",
     "alap_elony": 2},   # a kulcsszavas pontozásban ennyi előnnyel indul az alapértelmezett rovat

    # Helyi lapok: ezek nem kerülnek a fő hírek és az országos rovatok közé,
    # hanem a „Helyi” rovatban jelennek meg, a „hely” szerinti oszlopokban.
    {"id": "pecsma",   "nev": "PécsMa",      "szin": "#be123c", "feeds": ["https://www.pecsma.hu/feed/"],
     "hely": "Pécs"},
    {"id": "pecsakt",  "nev": "PécsAktuál",  "szin": "#9333ea", "feeds": ["https://pecsaktual.hu/feed/"],
     "hely": "Pécs"},
    {"id": "szpecs",   "nev": "Szabad Pécs", "szin": "#047857", "feeds": ["https://szabadpecs.hu/feed/"],
     "hely": "Pécs"},
    {"id": "hirbalaton", "nev": "HírBalaton", "szin": "#0284c7", "feeds": ["https://www.hirbalaton.hu/feed/"],
     "hely": "Balaton"},

    # Az oldal alján, az időjárás-előrejelzés mellett jelenik meg
    {"id": "idokep", "nev": "Időkép", "szin": "#0284c7", "feeds": ["https://adat.idokep.hu/rss/news.xml"],
     "panel": "idojaras", "panel_cim": "Az Időkép hírei"},
    {"id": "hungaromet", "nev": "HungaroMet", "szin": "#1e40af", "feeds": ["https://met.hu/methu/rss/rss.php"],
     "panel": "idojaras", "panel_cim": "A HungaroMet hírei"},
]

# Időjárás-előrejelzés (Open-Meteo, ingyenes, regisztráció nélkül). Helyszín: név + koordináták.
IDOJARAS_HELYEK = [
    {"nev": "Budapest", "lat": 47.4979, "lon": 19.0402},
    {"nev": "Pécs",     "lat": 46.0727, "lon": 18.2323},
    {"nev": "Balaton (Siófok)", "lat": 46.9041, "lon": 18.0580},
]
IDOJARAS_NAPOK = 5
# Az első kártya a böngésző helye szerinti előrejelzés (engedélykérés gombnyomásra; a hely a böngészőben marad)
IDOJARAS_SAJAT_HELY = True

# Friss mért adatok a HungaroMet automata állomásairól (nyílt adat, odp.met.hu; forrásmegjelöléssel szabadon
# használható). Megjelenítési név + állomásszám (az állomáslista: .../10_minutes/station_meta_auto.csv).
# Ha a mérések nem érhetők el, helyettük az IDOJARAS_HELYEK Open-Meteo-kártyái jelennek meg.
# Égképet (derült, borult, eső…) csak kb. 16 állomás jelent; ahol lehet, ezeket választottuk.
# A lista ABC-rendben jelenik meg, a sorrend itt mindegy.
MERES_ALLOMASOK = [
    ("Budapest", 44527),      # Pestszentlőrinc
    ("Békéscsaba", 66522),    # repülőtér
    ("Debrecen", 64711),      # repülőtér
    ("Győr", 24423),          # Pér repülőtér (Győrben nincs égképet jelentő állomás)
    ("Kékestető", 53101),     # az ország legmagasabb pontja, 1011 m
    ("Miskolc", 52744),       # Diósgyőr
    ("Nagykanizsa", 17809),
    ("Paks", 37402),
    ("Pécs", 38528),          # Egyetem
    ("Siófok", 36100),
    ("Szeged", 58116),        # külterület
    ("Szombathely", 15310),
]
MERES_10P_URL = "https://odp.met.hu/weather/weather_reports/synoptic/hungary/10_minutes/csv/HABP_10M_SYNOP_LATEST.csv.zip"
MERES_ORAS_URL = "https://odp.met.hu/weather/weather_reports/synoptic/hungary/hourly/csv/HABP_1H_SYNOP_LATEST.csv.zip"

FO_HIREK_SZAMA = 11       # ennyi kerül a lap tetejére (1 nyitó + 4 képes + 6 kép nélküli)
FO_MAX_ORA = 8            # a kiemelt részbe ennél frissebb hír kerülhet (a hír legutóbbi cikke számít)
NYITO_MAX_ORA = 3         # a nyitóhír ennél frissebb legyen; ha nincs ilyen, fokozatosan enged (5, 8, 12 óra)
FO_TARTALEK_ORA = 12      # ha (pl. hajnalban) nincs elég 8 óránál frissebb hír, legfeljebb eddig nyúl vissza
FO_STILUS = "arnyalt"     # a kiemelt rész háttere: "arnyalt" (a fejléc színének halvány árnyalata), "vilagos", "sotet"
FRISS_ORA = 24            # ennél frissebb hír dobozban, régebbi listában jelenik meg
HELYI_LISTA = 8           # a helyi oszlopokban a kiemeltek alatt ennyi friss hír látszik; a többi a napokra bontott listában
MEGORZES_ORA = 72         # ennél régebbi hír kiesik
DOBOZ_ROVATONKENT = 8     # rovatonként legfeljebb ennyi doboz (az első kiemelt, dupla széles), a többi listába megy;
                          # egy rovat a "dobozok" kulccsal ettől eltérhet (Belföld, Külföld: 11 = egy sorral több)
# Ugyanarról szóló cikkek összevonása (betű-n-gramos hasonlóság, 0–1 között)
OSSZEVON_ABLAK_ORA = 12   # csak az ennyi órán belül megjelent cikkek vethetők össze
OSSZEVON_EROS = 0.30      # legalább egy csoporttaghoz ennyi hasonlóság kell (kisebb: többet von össze)
OSSZEVON_ATLAG = 0.18     # és a csoport közeli tagjaihoz átlagosan ennyi (a láncolódás ellen)
OSSZEVON_NEV = 0.15       # ha a címekben közös egy ritka tulajdonnév (pl. „Republikon”), ennyi hasonlóság is elég
RITKA_NEV_MAX = 5         # ritkának számít a (legalább 6 betűs) név, ha legfeljebb ennyi cikk címében szerepel
KIEMELT_ORA = 4           # a rovat kiemelt dobozánál ennyi óránként ér egy lappal kevesebbet egy téma
ROVAT_VALTOZATOS = True   # a rovatok kiemelt kártyája óránként sorsolt elrendezést kap (széles/magas, bal/jobb)
SZINES_ESELY = 0.35       # ekkora eséllyel színes (a rovat színében) a kiemelt kártya; 0 = soha
DOBOZ_HELYENKENT = 3      # a helyi rovatban oszloponként ennyi kiemelt hír

# Országos rovatok a megjelenés sorrendjében. A „Helyi” rovat helyét a HELYI_UTAN adja meg.
#   utvonal: a cikk URL-jének egy szakasza (telex.hu/belfold/... → "belfold") vagy a
#            lap által megadott kategória (WordPress-lapoknál, pl. Magyar Hang, Válasz)
#   kulcsszavak: ha sem az URL, sem a kategória nem árulkodó, a cím és a lead alapján
ROVATOK = [
    {"id": "belfold", "nev": "Belföld", "szin": "#1d4ed8", "dobozok": 11,
     "utvonal": ["belfold", "itthon", "kozelet", "politika", "belpol", "orszag", "kozugyek",
                 "orszagszerte", "jogallamisag", "kozpenz", "kozeleti"],
     "kulcsszavak": ["kormány", "orbán", "fidesz", "tisza párt", "magyar péter", "parlament", "országgyűlés",
                     "miniszter", "polgármester", "önkormányzat", "választás", "ellenzék", "budapest",
                     "rendőrség", "bíróság", "ügyészség", "kdnp", "mi hazánk", "ügynök", "állambiztonság"]},
    {"id": "kulfold", "nev": "Külföld", "szin": "#0f766e", "dobozok": 11,
     "utvonal": ["kulfold", "vilag", "nagyvilag", "europa", "eu", "kulpolitika", "my-europe", "world"],
     "kulcsszavak": ["ukrajn", "orosz", "putyin", "trump", "amerikai", "európai unió", "brüsszel",
                     "izrael", "gáza", "kína", "német", "nato", "szlovák", "román", "lengyel", "francia",
                     "egyesült államok", "washington", "fehér ház", "kongresszus", "szenátus", "republikánus",
                     "demokrata párt", "kreml", "moszkva", "kijev", "zelenszkij", "irán", "brit", "olasz", "spanyol",
                     "török", "osztrák", "szerb", "horvát", "ensz", "európai bizottság", "von der leyen",
                     "pozsony", "bécs", "prága", "varsó", "bukarest", "belgrád", "zágráb", "berlin", "párizs", "london",
                     "fico", "vučić", "vucic", "netanjahu", "macron", "merz", "meloni", "starmer", "erdoğan", "erdogan",
                     "belga", "holland", "portugál", "svéd", "finn", "dán", "norvég", "bolgár",
                     "japán", "kínai", "indiai", "brüsszel", "amszterdam", "lisszabon", "madrid", "róma", "athén",
                     "stockholm", "koppenhága", "oslo", "helsinki", "dublin", "szófia"]},
    {"id": "gazdasag", "nev": "Gazdaság", "szin": "#b45309",
     "utvonal": ["gazdasag", "fn", "kkv", "penz", "ingatlan", "fogyasztas", "uzlet", "befektetes", "ceg",
                 "business", "bank", "deviza", "tozsde", "unios-forrasok", "krypto", "kriptopenz", "g7"],
     "kulcsszavak": ["infláció", "forint", "jegybank", "mnb", "kamat", "gdp", "költségvetés", "adó",
                     "árak", "tőzsde", "vállalat", "cég", "bér", "nyugdíj", "benzin", "beruházás",
                     "fogyasztás", "büdzsé", "kiskereskedelem", "ksh", "munkanélküli"]},
    {"id": "kultura", "nev": "Kultúra", "szin": "#be185d",
     "utvonal": ["kultura", "kult", "kulturalis", "zene", "film", "konyv", "szinhaz", "mediapiac", "media",
                 "culture", "after", "karakter"],
     "kulcsszavak": ["film", "színház", "koncert", "album", "regény", "író", "kiállítás", "múzeum",
                     "rendező", "színész", "fesztivál", "könyv"]},
    {"id": "tech", "nev": "Tudomány és tech", "szin": "#6d28d9",
     "utvonal": ["tech", "techtud", "tudomany", "tudomany-technika", "kornyezet", "zold", "mobil", "next",
                 "cegauto", "auto", "autos"],
     "kulcsszavak": ["kutató", "tudós", "mesterséges intelligencia", "űrszonda", "klíma", "apple",
                     "google", "okostelefon", "szoftver", "energia"]},
    {"id": "sport", "nev": "Sport", "szin": "#15803d",
     "utvonal": ["sport", "foci", "futball", "sportmenu", "forma-1", "f1"],
     "kulcsszavak": ["meccs", "bajnokság", "válogatott", "gól", "edző", "olimpi",
                     "labdarúg", "focist", "futball", "játékvezet", "tizenegyes", "premier league", "csapatkapitány",
                     "forma-1", "teniszez", "kézilabd", "vízilabd", "úszó", "nb i.", "fradi",
                     "forma–1", "forma-1", "nb i", "bajnokok ligája",
                     "ftc", "európa-liga", "európa liga", "konferencialiga", "ultrá", "szurkol"]},
    {"id": "eletmod", "nev": "Életmód", "szin": "#a16207",
     "utvonal": ["eletmod", "elet-stilus", "elet", "egeszseg", "utazas", "gasztro", "otthon",
                 "eletstilus"],
     "kulcsszavak": ["recept", "egészség", "utazás", "étterem", "kert", "kutya", "macska"]},
    {"id": "egyeb", "nev": "Egyéb", "szin": "#64748b",
     "utvonal": ["kozlemeny", "promo", "x-promo", "szponzoralt", "partner", "hirdetes", "pr", "pr-cikk",
                 "tamogatott-tartalom"], "kulcsszavak": []},
]
HELYI_UTAN = "kulfold"    # a Helyi rovat ez után jön (None = rögtön a fő hírek után)
HELYI_SZIN = "#9f1239"
IDOJARAS_SZIN = "#0369a1"

# ════════════════════════════════════════════════════════════════════

ALLAPOT_FAJL = "hirek_allapot.json"
FEJLECEK = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.6",
}
MOST = datetime.now(timezone.utc)
HONAPOK = ["január", "február", "március", "április", "május", "június", "július",
           "augusztus", "szeptember", "október", "november", "december"]
NAPOK = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]
STOP = set("""a az egy és de hogy is nem meg már csak még van volt lesz lett ez azt ezt mint
mert vagy ha akkor után előtt alatt között szerint miatt felé ellen nélkül mellett óta
majd most itt ott így úgy akik aki amit ami amely ahol mi mit kik ki sem se pedig
illetve újabb új nagy kis lehet kell kellett fog fognak egész minden több sok
mondta szerint kiderült""".split())
FN = {f["id"]: f for f in FORRASOK}


# ── Letöltés és feldolgozás ────────────────────────────────────────

def letolt(url, probalkozas=3):
    if url.startswith(("/", "./")) or url.startswith("file:"):
        return Path(url.replace("file://", "")).read_bytes()
    for i in range(probalkozas):
        try:
            req = urllib.request.Request(url, headers=FEJLECEK)
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except Exception as ex:
            # átmeneti hibánál (403, 429, 5xx, időtúllépés) rövid szünet után újra; a 404 végleges
            vegleges = getattr(ex, "code", None) == 404
            if vegleges or i == probalkozas - 1:
                raise
            time.sleep(4 * (i + 1))


def oldalkep(h):
    """A cikkoldal fejlécéből az og:image (vagy twitter:image) kép címe; hiba esetén üres."""
    try:
        lap = letolt(h["link"], probalkozas=1)[:300_000].decode("utf-8", "replace")
    except Exception:
        return ""
    for minta in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                  r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                  r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(minta, lap, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def oldalkepek(hirek):
    """Kép nélküli hírfolyamú lapoknál (kep_oldalrol) az új cikkek képét a cikkoldalról olvassuk ki.
    Cikkenként csak egyszer próbáljuk meg, az eredményt az állapotfájl megőrzi."""
    kell = [h for h in hirek if FN.get(h["forras"], {}).get("kep_oldalrol")
            and not h["kep"] and not h.get("kep_probalva") and "news.google.com" not in h["link"]]
    if not kell:
        return
    with ThreadPoolExecutor(6) as pool:
        for h, kep in zip(kell, pool.map(oldalkep, kell)):
            h["kep"], h["kep_probalva"] = kep, True


def helyi(tag):
    return tag.rsplit("}", 1)[-1].lower()


def gyerek(el, *nevek):
    for c in el:
        if helyi(c.tag) in nevek:
            return c
    return None


def szoveg(el, *nevek):
    c = gyerek(el, *nevek)
    return (c.text or "").strip() if c is not None and c.text else ""


def datum(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def html_tisztit(s, hossz=240):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(re.sub(r"\s+", " ", s)).strip()
    if len(s) > hossz:
        s = s[:hossz].rsplit(" ", 1)[0] + "…"
    return s


def kep_keres(el, nyers_leiras):
    for c in el.iter():
        t = helyi(c.tag)
        if t in ("content", "thumbnail") and c.get("url") and (
                "image" in (c.get("medium", "") + c.get("type", "")) or t == "thumbnail"
                or re.search(r"\.(jpe?g|png|webp)", c.get("url", ""), re.I)):
            return c.get("url")
        if t == "enclosure" and "image" in c.get("type", ""):
            return c.get("url")
    m = re.search(r'<img[^>]+src="([^"]+)"', nyers_leiras or "")
    return m.group(1) if m else ""


def webcim_korlat(link, d):
    """Ha a webcímben dátum van (/2026/11/01/), a cikk ideje nem lehet későbbi annál a napnál:
    így egy frissített, újra kiadott tegnapi cikk nem „fiatalodik meg”."""
    m = re.search(r"/(20\d\d)/?(\d\d)/?(\d\d)/", link)
    if not m:
        return d
    try:
        nap_vege = datetime(int(m[1]), int(m[2]), int(m[3]), 23, 59, tzinfo=timezone.utc)
    except ValueError:
        return d
    nap_vege = nap_vege.replace(tzinfo=budapest_tz(nap_vege)).astimezone(timezone.utc)
    return min(d, nap_vege)


def feed_feldolgoz(forras, url):
    gyoker = ET.fromstring(letolt(url))
    elemek = [e for e in gyoker.iter() if helyi(e.tag) in ("item", "entry")]
    hirek = []
    for e in elemek:
        cim = html_tisztit(szoveg(e, "title"), 300)
        cim = re.sub(rf"\s+[-–]\s+{re.escape(forras['nev'])}$", "", cim, flags=re.I)
        link = szoveg(e, "link")
        if not link:
            l = gyerek(e, "link")
            link = l.get("href", "") if l is not None else ""
        if not cim or not link:
            continue
        nyers = szoveg(e, "encoded") or szoveg(e, "description", "summary", "content")
        kategoriak = [(c.text or c.get("term") or "").strip() for c in e if helyi(c.tag) == "category"]
        d = (datum(szoveg(e, "pubdate")) or datum(szoveg(e, "published")) or datum(szoveg(e, "date"))
             or datum(szoveg(e, "updated")) or MOST)
        d = webcim_korlat(link, d)
        lead = html_tisztit(nyers)
        if lead.lower().startswith(cim.lower()[:40]):   # pl. a Google Hírek a címet ismétli
            lead = ""
        hirek.append({
            "id": hashlib.sha1(link.encode()).hexdigest()[:12],
            "forras": forras["id"], "cim": cim, "link": link,
            "lead": lead, "kep": kep_keres(e, nyers),
            "kategoriak": [k for k in kategoriak if k], "ido": d.isoformat(),
        })
    return hirek


def mind_letolt():
    hirek, hibak = [], []

    def egy(f):
        talalat, gondok = [], []
        for u in f["feeds"]:
            try:
                talalat += feed_feldolgoz(f, u)
            except Exception as ex:  # egy lap hibája ne állítsa meg az egészet
                gondok.append(f"{f['nev']}: {u} – {ex}")
        if gondok and not talalat and f.get("tartalek"):
            try:
                talalat = feed_feldolgoz(f, f["tartalek"])
                print(f"  {f['nev']}: a fő csatorna nem elérhető, a tartalék csatornát használtam")
                gondok = []
            except Exception as ex:
                gondok.append(f"{f['nev']} (tartalék is): {ex}")
        return talalat, gondok

    with ThreadPoolExecutor(8) as pool:
        for h, gondok in pool.map(egy, FORRASOK):
            hirek += h
            hibak += gondok
    return hirek, hibak


# ── Rovatba sorolás ────────────────────────────────────────────────

def ekezet_nelkul(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


BETU = "0-9a-záéíóöőúüű"


# népnevek: ezek csak kisbetűvel (vagy mondat elején) számítanak, mert nagybetűvel többnyire vezetéknevek
NEPNEVEK = {"orosz", "amerikai", "német", "szlovák", "román", "lengyel", "francia", "brit", "olasz",
            "spanyol", "török", "osztrák", "szerb", "horvát", "belga", "holland", "portugál", "svéd",
            "finn", "dán", "norvég", "bolgár", "japán", "kínai", "indiai"}


def kulcsszo_talalat(szoveg, kulcsszo, eredeti=None):
    """A kulcsszó csak szó elején számít, utána toldalék állhat: az „adó” illik az „adóemelés” szóra,
    de a „kiadó” szóra nem. A népnevek (pl. „német”) nagy kezdőbetűvel csak mondat elején számítanak,
    így a „Németh” vagy a „Horváth” vezetéknév nem ad külföldi találatot."""
    k = kulcsszo.strip()
    talalatok = list(re.finditer(rf"(?<![{BETU}-]){re.escape(k)}", szoveg))
    if eredeti is None or k not in NEPNEVEK:
        return len(talalatok)
    db = 0
    for m in talalatok:
        i = m.start()
        elotte = eredeti[:i].rstrip()
        mondat_eleje = (not elotte) or elotte[-1] in ".:!?–—„\"'(«"
        if eredeti[i].islower() or mondat_eleje:
            db += 1
    return db


def rovat(h):
    """Rovat és a besorolás megbízhatósága (h["rovat_suly"]): webcím 3, a lap kategóriája 2,
    kulcsszó 1, a lap alapértelmezett rovata 0.5, besorolatlan 0."""
    forras = FN[h["forras"]]

    def ki(r, suly):
        h["rovat_suly"] = suly
        return r
    if forras.get("panel"):
        return ki(forras["panel"], 9)
    if forras.get("hely"):
        return ki("helyi", 9)
    if h.get("elotag_rovat"):
        return ki(h["elotag_rovat"], 3)
    szakaszok = [ekezet_nelkul(s.lower()) for s in urlparse(h["link"]).path.split("/") if s]
    # szponzorált/közlemény al-oldal bárhol a webcímben elsőbbséget kap (pl. telex.hu/g7/tamogatott-tartalom/…)
    if any(sz in r["utvonal"] for r in ROVATOK if r["id"] == "egyeb" for sz in szakaszok[:3]):
        return ki("egyeb", 3)
    for sz in szakaszok[:3]:
        for r in ROVATOK:
            if sz in r["utvonal"]:
                return ki(r["id"], 3)
    # a lap címkéi: címkénként megnézzük, melyik rovathoz illik, és a legtöbb címkét kapó rovat nyer
    # (pl. a 444 „foci”, „magyar foci”, „külföld” címkéinél a Sport, nem a Külföld)
    kat_pont = {}
    for k in h["kategoriak"]:
        k = ekezet_nelkul(k.lower())
        for r in ROVATOK:
            if any(re.search(rf"\b{re.escape(u)}\b", k) for u in r["utvonal"]) or ekezet_nelkul(r["nev"].lower()) in k:
                kat_pont[r["id"]] = kat_pont.get(r["id"], 0) + 1
    eredeti = h["cim"] + ". " + h["lead"]
    szoveg_ = eredeti.lower()
    pontok = {r["id"]: sum(kulcsszo_talalat(szoveg_, k, eredeti) for k in r["kulcsszavak"]) for r in ROVATOK}
    if kat_pont:
        # döntetlennél a cím és a bevezető kulcsszavai döntenek, utána a rovatok sorrendje
        sorrend = [r["id"] for r in ROVATOK]
        return ki(max(kat_pont, key=lambda r: (kat_pont[r], pontok[r], -sorrend.index(r))), 2)
    if sum(pontok.values()) > 0:
        # lapfüggő előny: pl. az Euronews cikkei a Külföld javára indulnak, hogy egy-egy belföldes szó
        # (pl. „választás”) ne vigye el őket; csak egyértelmű belpolitikai cikk kerül máshova
        if forras.get("alap") in pontok:
            pontok[forras["alap"]] += forras.get("alap_elony", 0)
        # döntetlennél a lap alapértelmezett rovata nyer (pl. Euronews: Külföld)
        legjobb = max(pontok, key=lambda r: (pontok[r], r == forras.get("alap")))
        return ki(legjobb, 1)
    if forras.get("alap"):
        return ki(forras["alap"], 0.5)
    return ki("egyeb", 0)


# ── Ugyanarról szóló hírek összevonása, fő hírek pontozása ─────────

def szavak(szoveg_):
    return re.findall(r"[a-záéíóöőúüű0-9]+", (szoveg_ or "").lower())


# napi összefoglalók (pl. „A nap legfontosabb hírei”, „Reggeli pakk”): sok témát érintenek, ezért nem vonjuk össze őket
OSSZEFOGLALO = re.compile(r"nap (legfontosabb )?hírei|friss hírek|reggeli pakk|hírösszefoglaló", re.I)


def ngramok(h, n=4):
    """A cím (kétszeres súllyal) és a bevezető első 35 szavának 4 betűs darabjai, ékezet nélkül.
    A darabolás miatt a toldalékolt és összetett alakok is közös darabokat kapnak (ügynökakták – akták)."""
    v = {}
    for suly, szoveg_ in ((2.0, h["cim"]), (1.0, " ".join((h.get("lead") or "").split()[:35]))):
        for w in szavak(szoveg_):
            if w in STOP or len(w) < 3:
                continue
            w = f"_{ekezet_nelkul(w)}_"
            for i in range(len(w) - n + 1):
                v[w[i:i + n]] = v.get(w[i:i + n], 0) + suly
    return v


def osszevon(hirek):
    """Ugyanarról szóló cikkek csoportosítása betű-n-gramos TF-IDF hasonlósággal.
    Minden cikkből súlyozott vektor készül a cím és a bevezető 4 betűs darabjaiból; a ritka darabok többet
    érnek (IDF), a nagyon gyakoriak (a cikkek >10%-ában) kimaradnak. Időrendben haladva egy cikk akkor kerül
    egy meglévő csoportba, ha
    – legalább egy, OSSZEVON_ABLAK_ORA órán belüli tagjához OSSZEVON_EROS feletti a hasonlósága, és
    – az ablakon belüli tagokhoz mért átlagos hasonlósága is eléri az OSSZEVON_ATLAG értéket
      (ez akadályozza meg, hogy a csoport láncszerűen átcsússzon más témákra);
    több jelölt közül az átlagosan leghasonlóbb csoport nyer."""
    import math
    hirek = sorted(hirek, key=lambda h: h["ido"])
    N = len(hirek)
    nyers = [ngramok(h) if not OSSZEFOGLALO.search(h["cim"]) else {} for h in hirek]
    df = {}
    for v in nyers:
        for g in v:
            df[g] = df.get(g, 0) + 1
    V = []
    for v in nyers:
        w = {g: (1 + math.log(tf)) * math.log(N / df[g]) for g, tf in v.items() if 2 <= df[g] < 0.1 * N}
        nrm = math.sqrt(sum(x * x for x in w.values())) or 1
        V.append({g: x / nrm for g, x in w.items()})
    T = [datetime.fromisoformat(h["ido"]).timestamp() for h in hirek]
    # ritka tulajdonnevek a címekben (pl. „Republikon”): nagybetűs, a mondat belsejében álló szavak, amelyek
    # legfeljebb RITKA_NEV_MAX cikkben fordulnak elő; ha két cikk címe ilyenen osztozik, gyengébb hasonlóság is elég
    nevek, nev_df = [], {}
    for h in hirek:
        szo = re.findall(r"[A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű]{5,}", h["cim"])
        n = {ekezet_nelkul(w.lower())[:7] for w in szo} - {ekezet_nelkul(w.lower())[:7] for w in szo[:1]
                                                          if h["cim"].startswith(w) and ":" not in h["cim"][:len(w) + 2]}
        n -= STOP
        nevek.append(n)
        for w in n:
            nev_df[w] = nev_df.get(w, 0) + 1
    ritka = [{w for w in n if nev_df[w] <= RITKA_NEV_MAX} for n in nevek]
    ablak = OSSZEVON_ABLAK_ORA * 3600
    # hasonlóságok a fordított indexszel: csak a közös darabot tartalmazó, időben közeli párokat számoljuk
    index, hason = {}, {}
    for i, v in enumerate(V):
        acc = {}
        for g, x in v.items():
            for j in index.get(g, ()):
                if T[i] - T[j] <= ablak:
                    acc[j] = acc.get(j, 0) + x * V[j][g]
            index.setdefault(g, []).append(i)
        hason[i] = acc
    csoportok, hova = [], {}
    for i in range(N):
        acc = hason[i]
        kuszob = lambda j: OSSZEVON_NEV if ritka[i] & ritka[j] else OSSZEVON_EROS
        jeloltek = {hova[j] for j, s in acc.items() if s >= kuszob(j)}
        legjobb, legjobb_atl = None, 0
        for k in jeloltek:
            kozeli = [j for j in csoportok[k]["idx"] if T[i] - T[j] <= ablak]
            atl = sum(acc.get(j, 0) for j in kozeli) / len(kozeli)
            nevvel = atl >= OSSZEVON_NEV and any(ritka[i] & ritka[j] for j in kozeli)
            if (atl >= OSSZEVON_ATLAG or nevvel) and atl > legjobb_atl:
                legjobb, legjobb_atl = k, atl
        if legjobb is None:
            hova[i] = len(csoportok)
            csoportok.append({"idx": [i], "hirek": [hirek[i]]})
        else:
            hova[i] = legjobb
            csoportok[legjobb]["idx"].append(i)
            csoportok[legjobb]["hirek"].append(hirek[i])
    for cs in csoportok:
        hk = cs["hirek"]
        # a fő megjelenítendő változat: amelyiknek van képe és hosszabb leadje
        hk.sort(key=lambda h: (bool(h["kep"]), len(h["lead"])), reverse=True)
        cs["fo"] = hk[0]
        cs["forrasok"] = sorted({h["forras"] for h in hk})
        cs["ido"] = max(h["ido"] for h in hk)
        # a doboz rovata súlyozott szavazással: a webcímből vagy a lap kategóriájából biztosan
        # tudott rovat többet ér, mint a kulcsszavas tipp; döntetlennél a legbiztosabb besorolás nyer
        szavazat = {}
        for h in hk:
            szavazat[h["rovat"]] = szavazat.get(h["rovat"], 0) + h.get("rovat_suly", 1)
        if len(szavazat) > 1:
            szavazat.pop("egyeb", None)
        cs["rovat"] = max(szavazat, key=lambda r: (szavazat[r], max(h.get("rovat_suly", 1) for h in hk if h["rovat"] == r)))
        kor_ora = (MOST - datetime.fromisoformat(cs["ido"])).total_seconds() / 3600
        # a sztori kora: az első róla szóló cikk óta eltelt idő (a kiemelt részhez ez számít)
        cs["kezdet_ora"] = (MOST - min(datetime.fromisoformat(h["ido"]) for h in hk)).total_seconds() / 3600
        # fontosság: hány lap írt róla + frissesség
        cs["pont"] = 3 * len(cs["forrasok"]) + 0.5 * len(hk) + 5 * max(0.0, 1 - cs["kezdet_ora"] / FO_MAX_ORA)
        cs["kor_ora"] = kor_ora
    return csoportok


# ── Időjárás ───────────────────────────────────────────────────────

WMO = {  # WMO időjáráskódok → (leírás, ikon)
    0: ("Derült", "nap"), 1: ("Többnyire derült", "napfelho"), 2: ("Részben felhős", "napfelho"),
    3: ("Borult", "felho"), 45: ("Köd", "kod"), 48: ("Zúzmarás köd", "kod"),
    51: ("Gyenge szitálás", "eso"), 53: ("Szitálás", "eso"), 55: ("Erős szitálás", "eso"),
    56: ("Ónos szitálás", "eso"), 57: ("Ónos szitálás", "eso"),
    61: ("Gyenge eső", "eso"), 63: ("Eső", "eso"), 65: ("Erős eső", "eso"),
    66: ("Ónos eső", "eso"), 67: ("Ónos eső", "eso"),
    71: ("Gyenge havazás", "ho"), 73: ("Havazás", "ho"), 75: ("Erős havazás", "ho"), 77: ("Hószemcse", "ho"),
    80: ("Gyenge zápor", "eso"), 81: ("Zápor", "eso"), 82: ("Heves zápor", "eso"),
    85: ("Gyenge hózápor", "ho"), 86: ("Hózápor", "ho"),
    95: ("Zivatar", "zivatar"), 96: ("Zivatar jégesővel", "zivatar"), 99: ("Zivatar jégesővel", "zivatar"),
}

_FELHO = '<path class="i-felho" d="M10 25h13.5a5 5 0 0 0 .4-9.98A7 7 0 0 0 10.4 16.1 4.5 4.5 0 0 0 10 25z"/>'
_FELHO_FENT = '<path class="i-felho" d="M10 20h13.5a5 5 0 0 0 .4-9.98A7 7 0 0 0 10.4 11.1 4.5 4.5 0 0 0 10 20z"/>'
IKONOK = {
    "nap": ('<circle class="i-nap" cx="16" cy="16" r="6"/><path class="i-nap" d="M16 3v3M16 26v3M3 16h3M26 16h3'
            'M6.8 6.8l2.1 2.1M23.1 23.1l2.1 2.1M6.8 25.2l2.1-2.1M23.1 8.9l2.1-2.1"/>'),
    "napfelho": ('<circle class="i-nap" cx="12" cy="11" r="4.5"/><path class="i-nap" d="M12 2.5v2M3.5 11h2'
                 'M6 5l1.4 1.4M18 5l-1.4 1.4"/>' + _FELHO),
    "felho": _FELHO,
    "kod": '<path class="i-felho" d="M5 12h22M8 17h18M5 22h20M9 27h14"/>',
    "eso": _FELHO_FENT + '<path class="i-csepp" d="M11 24l-1.5 4M17 24l-1.5 4M23 24l-1.5 4"/>',
    "ho": _FELHO_FENT + '<path class="i-ho" d="M11 25v.1M16 27v.1M21 25v.1M13.5 29.5v.1M18.5 29.5v.1"/>',
    "zivatar": _FELHO_FENT + '<path class="i-villam" d="M17 21l-3.5 5h4l-2.5 5"/>',
    # éjszakai változatok (napnyugta és napkelte között)
    "hold": '<path class="i-hold" d="M20.5 5.5A11 11 0 1 0 26.5 21 9 9 0 0 1 20.5 5.5z"/>',
    "holdfelho": '<path class="i-hold" d="M14 3.5A7 7 0 1 0 19.5 13 5.5 5.5 0 0 1 14 3.5z"/>' + _FELHO,
}
EJJEL = {"nap": "hold", "napfelho": "holdfelho"}


def nap_magassag(lat, lon, ido):
    """A Nap horizont feletti magassága fokban (egyszerűsített csillagászati képlet, ~1 fok pontos)."""
    import math
    d = (ido - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400
    r = math.radians
    g, q = r(357.529 + 0.98560028 * d), 280.459 + 0.98564736 * d
    lam = r(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = r(23.439 - 0.00000036 * d)
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ora = (18.697374558 + 24.06570982441908 * d) % 24
    lha = r(ora * 15 + lon - ra)
    return math.degrees(math.asin(math.sin(r(lat)) * math.sin(dec) + math.cos(r(lat)) * math.cos(dec) * math.cos(lha)))


def ikon(kod, meret=32, ejjel=False):
    nev = WMO.get(kod, ("", "felho"))[1]
    if ejjel:
        nev = EJJEL.get(nev, nev)
    return (f'<svg class="ido-ikon" width="{meret}" height="{meret}" viewBox="0 0 32 32" aria-hidden="true">'
            f'{IKONOK[nev]}</svg>')


# HungaroMet időkép-kódok (órás adatsor „we” oszlopa) → (leírás, ikon)
HM_KODOK = {
    1: ("Derült", "nap"), 2: ("Kissé felhős", "napfelho"), 3: ("Közepesen felhős", "napfelho"),
    4: ("Erősen felhős", "felho"), 5: ("Borult", "felho"), 6: ("Fátyolfelhős", "napfelho"), 7: ("Ködös", "kod"),
    9: ("Derült, párás", "nap"), 10: ("Közepesen felhős, párás", "napfelho"), 11: ("Borult, párás", "felho"),
    12: ("Erősen fátyolfelhős", "felho"), 101: ("Szitálás", "eso"), 102: ("Eső", "eso"), 103: ("Zápor", "eso"),
    104: ("Zivatar esővel", "zivatar"), 105: ("Ónos szitálás", "eso"), 106: ("Ónos eső", "eso"),
    107: ("Hószállingózás", "ho"), 108: ("Havazás", "ho"), 109: ("Hózápor", "ho"), 110: ("Havas eső", "ho"),
    112: ("Hózivatar", "zivatar"), 202: ("Erős eső", "eso"), 203: ("Erős zápor", "eso"), 208: ("Erős havazás", "ho"),
    209: ("Erős hózápor", "ho"), 304: ("Zivatar záporral", "zivatar"), 310: ("Havas eső zápor", "ho"),
    500: ("Hófúvás", "ho"), 600: ("Jégeső", "zivatar"), 601: ("Dörgés", "zivatar"),
}


def hm_csv(nyers):
    """A HungaroMet tömörített csv-jének sorai szótárként. Az oszlopokat név szerint keressük
    (szóköz és kis-nagybetű nélkül), a -999 hiányzó adat. Egy fájlban több időpont is lehet."""
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(nyers)) as z:
        nev = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        szoveg_ = z.read(nev).decode("utf-8", "replace")
    fejlec, sorok = None, []
    for sor in szoveg_.splitlines():
        if not sor.strip() or sor.lstrip().startswith("#"):
            continue
        mezok = [m.strip() for m in sor.split(";")]
        if fejlec is None:
            if any(re.sub(r"\s", "", m).lower() == "stationnumber" for m in mezok):
                fejlec = [re.sub(r"\s", "", m).lower() for m in mezok]
            continue
        sorok.append(dict(zip(fejlec, mezok)))
    return sorok


def hm_szam(x):
    try:
        v = float((x or "").replace(",", "."))
    except ValueError:
        return None
    return None if v <= -999 else v


def meres_letolt():
    """Az állomások legfrissebb mérése. Visszaad: {állomásszám: {...}} vagy None, ha nem érhető el."""
    try:
        sorok = hm_csv(letolt(MERES_10P_URL))
    except Exception as ex:
        print(f"  HungaroMet 10 perces mérések: {ex}")
        return None
    allomasok = {}
    for r in sorok:
        try:
            szam = int(float(r.get("stationnumber", "")))
            ido = datetime.strptime(r.get("time", "")[:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        a = allomasok.setdefault(szam, {"nev": r.get("stationname", ""), "lat": hm_szam(r.get("latitude")),
                                        "lon": hm_szam(r.get("longitude")), "ido": None, "csap": {}})
        a["csap"][ido] = hm_szam(r.get("r"))
        t = hm_szam(r.get("t"))
        if t is not None and (a["ido"] is None or ido > a["ido"]):
            a.update(ido=ido, t=t, szel=hm_szam(r.get("fs")), lokes=hm_szam(r.get("fx")), para=hm_szam(r.get("u")),
                     irany=hm_szam(r.get("fsd")))
    for a in allomasok.values():
        if a["ido"]:   # csapadék az utolsó órában (az utolsó hat tízperces összeg)
            ertek = [v for k, v in a["csap"].items() if a["ido"] - timedelta(minutes=55) <= k <= a["ido"] and v is not None]
            a["csap1h"] = round(sum(ertek), 1) if ertek else None
            # ma éjfél óta (magyar idő szerint); a tízperces értékek a mérési időpontot megelőző 10 percre vonatkoznak
            ejfel = bp(a["ido"]).replace(hour=0, minute=0, second=0, microsecond=0)
            mai = [v for k, v in a["csap"].items() if k > ejfel and v is not None]
            a["csap_ma"] = round(sum(mai), 1) if mai else None
        del a["csap"]
    try:   # égkép az órás adatsorból (a tízpercesben nincs); ha nem érhető el, ikon nélkül
        for r in hm_csv(letolt(MERES_ORAS_URL)):
            try:
                szam = int(float(r.get("stationnumber", "")))
                ido = datetime.strptime(r.get("time", "")[:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
                kod = int(hm_szam(r.get("we")))
            except (ValueError, TypeError):
                continue
            a = allomasok.get(szam)
            if a and kod in HM_KODOK and (a.get("kod_ido") is None or ido > a["kod_ido"]):
                a.update(kod=kod, kod_ido=ido)
    except Exception as ex:
        print(f"  HungaroMet órás mérések: {ex}")
    ervenyes = {k: v for k, v in allomasok.items() if v["ido"]}
    return ervenyes or None


def hm_ikon(kod, meret=32, ejjel=False):
    nev = HM_KODOK.get(kod, ("", ""))[1]
    if ejjel:
        nev = EJJEL.get(nev, nev)
    if not nev:
        return f'<span class="ido-ikon-ures" style="width:{meret}px"></span>'
    return (f'<svg class="ido-ikon" width="{meret}" height="{meret}" viewBox="0 0 32 32" aria-hidden="true">'
            f'{IKONOK[nev]}</svg>')


def meres_lista(meresek):
    """A „Most mért” kártya: soronként település, égkép, hőmérséklet, szél, csapadék."""
    sorok, idok = [], []
    for nev, szam in sorted(MERES_ALLOMASOK, key=lambda x: ekezet_nelkul(x[0].lower())):
        a = meresek.get(szam)
        if not a:
            continue
        idok.append(a["ido"])
        kod = a.get("kod") if a.get("kod_ido") and a["ido"] - a["kod_ido"] <= timedelta(hours=2) else None
        reszlet = []
        if a.get("szel") is not None:
            ir = a.get("irany")
            irany = (["É", "ÉK", "K", "DK", "D", "DNy", "Ny", "ÉNy"][round(ir / 45) % 8] + "-i ") \
                if ir is not None and a["szel"] >= 0.5 else ""
            sz = f'{irany}szél {round(a["szel"] * 3.6)} km/h'
            if a.get("lokes") and a["lokes"] * 3.6 >= 40:
                sz += f', lökés {round(a["lokes"] * 3.6)}'
            reszlet.append(sz)
        if a.get("para") is not None:
            reszlet.append(f'pára {round(a["para"])}%')
        if a.get("csap1h"):
            reszlet.append(f'{str(a["csap1h"]).replace(".", ",")} mm az utolsó órában')
        if a.get("csap_ma"):
            reszlet.append(f'ma eddig {str(a["csap_ma"]).replace(".", ",")} mm')
        leiras = HM_KODOK.get(kod, ("", ""))[0]
        sorok.append(
            f'<li data-ido="{a["ido"].isoformat()}"><span class="meres-nev">{e(nev)}'
            f'<small>{e(a["nev"]) + " · " if a["nev"] != nev else ""}{bp(a["ido"]):%H:%M}</small></span>'
            f'<span title="{e(leiras)}">{hm_ikon(kod, 28, a.get("lat") is not None and nap_magassag(a["lat"], a["lon"], a["ido"]) < -0.833)}</span>'
            f'<span class="meres-fok">{round(a["t"])}°</span>'
            f'<span class="meres-reszlet">{e(", ".join(reszlet))}</span></li>')
    if not sorok:
        return ""
    friss = bp(max(idok))
    return (f'<div class="ido-hely ido-meres"><h3>Most mért <small>{friss:%H:%M}-kor</small></h3>'
            f'<ul class="meres-lista" style="--sorok:{(len(sorok) + 1) // 2}">{"".join(sorok)}</ul>'
            f'<p class="meres-forras">Forrás: <a href="https://odp.met.hu/" target="_blank" rel="noopener">'
            f'HungaroMet Nonprofit Zrt.</a>, automata állomások</p></div>')


def idojaras_letolt():
    eredmeny = []
    for hely in IDOJARAS_HELYEK:
        url = ("https://api.open-meteo.com/v1/forecast?"
               f"latitude={hely['lat']}&longitude={hely['lon']}"
               "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day"
               "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
               f"&timezone=Europe%2FBudapest&forecast_days={IDOJARAS_NAPOK}")
        try:
            adat = json.loads(letolt(hely.get("teszt_fajl") or url))
            eredmeny.append({"nev": hely["nev"], "adat": adat})
        except Exception as ex:
            print(f"  időjárás – {hely['nev']}: {ex}")
            eredmeny.append({"nev": hely["nev"], "adat": None})
    return eredmeny


def ido_nap(d, i):
    dt = datetime.fromisoformat(d["time"][i])
    cimke = "Ma" if i == 0 else ("Holnap" if i == 1 else ["H", "K", "Sze", "Cs", "P", "Szo", "V"][dt.weekday()])
    kod = d["weather_code"][i]
    esely = (d.get("precipitation_probability_max") or [None] * 16)[i]
    csap = f"{esely}%" if esely is not None else ""
    return (f'<li title="{e(WMO.get(kod, ("", ""))[0])}"><span class="ido-nap">{cimke}</span>'
            f'{ikon(kod, 26)}'
            f'<span class="ido-max">{round(d["temperature_2m_max"][i])}°</span>'
            f'<span class="ido-min">{round(d["temperature_2m_min"][i])}°</span>'
            f'<span class="ido-csap">{csap}</span></li>')


def ido_kartya(h):
    a = h["adat"]
    if not a:
        return (f'<div class="ido-hely"><h3>{e(h["nev"])}</h3>'
                f'<p class="ido-nincs">Az előrejelzés most nem érhető el.</p></div>')
    c, d = a["current"], a["daily"]
    leiras = WMO.get(c["weather_code"], ("", ""))[0]
    napok = "".join(ido_nap(d, i) for i in range(len(d["time"])))
    return (f'<div class="ido-hely"><h3>{e(h["nev"])}</h3>'
            f'<div class="ido-most">{ikon(c["weather_code"], 52, c.get("is_day") == 0)}'
            f'<span class="ido-fok">{round(c["temperature_2m"])}°</span>'
            f'<span class="ido-leiras">{e(leiras)}<br><small>hőérzet {round(c["apparent_temperature"])}°, '
            f'szél {round(c["wind_speed_10m"])} km/h</small></span></div>'
            f'<ul class="ido-napok">{napok}</ul></div>')


def idojaras_panel(helyek, panel_hirek, meresek=None):
    if not helyek and not panel_hirek and not meresek:
        return ""
    sajat = ('<div class="ido-hely ido-itt" id="ido-itt" hidden></div>' if IDOJARAS_SAJAT_HELY else "")
    kartyak = sajat + (meres_lista(meresek) if meresek else "") + "".join(ido_kartya(h) for h in helyek)
    hirdobozok = []
    for f in FORRASOK:
        if f.get("panel") != "idojaras":
            continue
        hirek = sorted([x for x in panel_hirek if x["forras"] == f["id"]], key=lambda x: x["ido"], reverse=True)[:8]
        if not hirek:
            continue
        lista = "".join(
            f'<li><time>{ido_szoveg(x["ido"])}</time>'
            f'<a href="{e(x["link"])}" target="_blank" rel="noopener">{e(x["cim"])}</a></li>' for x in hirek)
        hirdobozok.append(f'<aside class="ido-hirek"><h3>{e(f.get("panel_cim", f["nev"] + " hírei"))}</h3><ul>{lista}</ul></aside>')
    js = ""
    if IDOJARAS_SAJAT_HELY:
        adat = {
            "url": ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                    "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day"
                    "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"
                    "&hourly=temperature_2m,precipitation_probability,weather_code,is_day&forecast_hours=13"
                    f"&timezone=Europe%2FBudapest&forecast_days={IDOJARAS_NAPOK}"),
            "wmo": {str(k): v for k, v in WMO.items()}, "ikonok": IKONOK, "telepulesek": TELEPULESEK,
            "napok": ["H", "K", "Sze", "Cs", "P", "Szo", "V"],
            # a saját helyhez legközelebbi HungaroMet-állomás mért hőmérsékletéhez
            "allomasok": [[a["lat"], a["lon"], a["nev"], round(a["t"], 1), f'{bp(a["ido"]):%H:%M}',
                           list(HM_KODOK[a["kod"]]) if a.get("kod") in HM_KODOK
                           and a["ido"] - a["kod_ido"] <= timedelta(hours=2) else None,
                           a.get("szel"), a.get("para")]
                          for a in (meresek or {}).values() if a.get("lat") and a.get("lon")],
        }
        js = "<script>" + IDO_JS.replace("__IDO_ADAT__", json.dumps(adat, ensure_ascii=False).replace("</", "<\\/")) + "</script>"
    return (f'<section class="rovat idojaras" id="idojaras" style="--r:{IDOJARAS_SZIN}">'
            f'{rovatfej("idojaras", "Időjárás", IDOJARAS_SZIN, 0)}'
            f'<div class="tartalom ido-racs"><div class="ido-helyek">{kartyak}</div>'
            f'{"<div class=" + chr(34) + "ido-hirsor" + chr(34) + ">" + "".join(hirdobozok) + "</div>" if hirdobozok else ""}'
            f'</div></section>{js}')


# A helymeghatározásos kártya ehhez a listához méri a legközelebbi települést (név, szélesség, hosszúság)
# Magyarországi települések (500 főnél népesebbek, kb. 1200) a saját hely megnevezéséhez.
# Forrás: GeoNames (geonames.org, CC BY 4.0), a geonamescache Python-csomagon keresztül.
TELEPULESEK = [["Aba",47.029,18.522],["Abaliget",46.143,18.117],["Abasár",47.797,20.003],["Abaújkér",48.3,21.2],["Abaújszántó",48.283,21.2],["Abda",47.695,17.545],["Abony",47.19,20.005],["Abádszalók",47.467,20.6],["Acsa",47.794,19.388],["Acsád",47.325,16.733],["Adony",47.119,18.865],["Adyliget",47.545,18.933],["Adács",47.692,19.977],["Adásztevel",47.303,17.541],["Aggtelek",48.467,20.517],["Agyagosszergény",47.61,16.938],["Agárd",47.191,18.594],["Ajak",48.177,22.063],["Ajka",47.102,17.559],["Akadémiaújtelep",47.483,19.222],["Akasztó",46.692,19.204],["Alacska",48.217,20.65],["Alacskai úti lakótelep",47.414,19.204],["Alap",46.799,18.689],["Alattyán",47.427,20.042],["Albertfalva",47.444,19.037],["Albertirsa",47.243,19.617],["Alcsútdoboz",47.428,18.604],["Aldebrő",47.789,20.23],["Algyő",46.335,20.208],["Almásfüzitő",47.728,18.262],["Almáskamarás",46.467,21.1],["Almáskert",47.411,19.209],["Alsóberecki",48.35,21.7],["Alsónemesapáti",46.853,16.935],["Alsónyék",46.202,18.734],["Alsónána",46.252,18.657],["Alsónémedi",47.315,19.158],["Alsópetény",47.876,19.244],["Alsópáhok",46.769,17.172],["Alsórákos",47.528,19.123],["Alsószentiván",46.787,18.73],["Alsószentmárton",45.79,18.306],["Alsóvadász",48.25,20.917],["Alsózsolca",48.07,20.88],["Alsóörs",46.991,17.976],["Alsóújlak",47.08,16.854],["Ambrózfalva",46.35,20.733],["Anarcs",48.176,22.112],["Andocs",46.648,17.928],["Andornaktálya",47.85,20.417],["Angyalföld",47.548,19.08],["Annavölgy",47.7,18.667],["Apagy",47.964,21.935],["Apaj",47.113,19.088],["Aparhant",46.329,18.449],["Apc",47.794,19.694],["Apostag",46.882,18.962],["Apátfalva",46.167,20.583],["Aquincum",47.559,19.049],["Aranyosapáti",48.206,22.259],["Arló",48.183,20.267],["Arnót",48.131,20.858],["Aszaló",48.217,20.967],["Aszód",47.652,19.479],["Atkár",47.721,19.891],["Attala",46.378,18.064],["Babarc",46.004,18.552],["Babócsa",46.042,17.343],["Babót",47.576,17.074],["Badacsonytomaj",46.807,17.514],["Badacsonytördemic",46.811,17.474],["Bag",47.633,19.483],["Bagamér",47.449,21.989],["Baj",47.646,18.364],["Baja",46.183,18.953],["Bajna",47.654,18.597],["Bajót",47.728,18.556],["Bak",46.73,16.846],["Bakonycsernye",47.324,18.075],["Bakonyszentlászló",47.389,17.803],["Baks",46.543,20.102],["Baktalórántháza",48.0,22.083],["Balassagyarmat",48.073,19.296],["Balatonalmádi",47.035,18.021],["Balatonberény",46.707,17.32],["Balatonboglár",46.775,17.644],["Balatonfenyves",46.715,17.492],["Balatonföldvár",46.853,17.881],["Balatonfüred",46.962,17.872],["Balatonkenese",47.04,18.107],["Balatonlelle",46.783,17.695],["Balatonszabadi",46.894,18.137],["Balatonszemes",46.81,17.78],["Balatonszárszó",46.829,17.824],["Balatonvilágos",46.963,18.16],["Balkány",47.771,21.863],["Ballószög",46.862,19.571],["Balmazújváros",47.617,21.35],["Balotaszállás",46.351,19.54],["Balástya",46.423,20.008],["Baracs",46.882,18.907],["Baracska",47.282,18.759],["Barcs",45.96,17.459],["Battonya",46.283,21.017],["Becsehely",46.448,16.777],["Bekecs",48.15,21.183],["Beled",47.466,17.093],["Beloiannisz",47.182,18.824],["Belső-Ferencváros",47.485,19.065],["Belsőmajor",47.396,19.193],["Bercel",47.871,19.407],["Beregsurány",48.166,22.552],["Beremend",45.791,18.433],["Berente",48.233,20.667],["Berettyóújfalu",47.224,21.537],["Berhida",47.111,18.129],["Berzence",46.209,17.148],["Berzék",48.017,20.95],["Besenyszög",47.3,20.267],["Besenyőtelek",47.7,20.433],["Bezenye",47.96,17.215],["Biatorbágy",47.471,18.819],["Bicske",47.484,18.644],["Biharkeresztes",47.133,21.717],["Biharnagybajom",47.217,21.233],["Bodajk",47.324,18.233],["Bogyiszló",46.386,18.83],["Bogács",47.9,20.533],["Bokod",47.492,18.247],["Boldog",47.603,19.688],["Boldva",48.217,20.8],["Bolhó",46.039,17.297],["Bonyhád",46.299,18.53],["Bordány",46.318,19.923],["Borota",46.266,19.223],["Borsodbóta",48.217,20.4],["Borsodnádasd",48.117,20.25],["Borsodszentgyörgy",48.183,20.2],["Bucsa",47.2,21.0],["Buda",47.5,19.033],["Budafok",47.424,19.036],["Budajenő",47.557,18.802],["Budakalász",47.617,19.05],["Budakeszi",47.511,18.927],["Budapest",47.498,19.04],["Budatétény",47.409,19.021],["Budaörs",47.462,18.958],["Bugac",46.687,19.681],["Bugyi",47.227,19.147],["Buj",48.1,21.65],["Buják",47.884,19.544],["Bábolna",47.644,17.978],["Bábonymegyer",46.754,18.095],["Bácsalmás",46.126,19.333],["Bácsbokod",46.125,19.156],["Bácsborsód",46.099,19.158],["Bácsszőlős",46.15,19.45],["Bánd",47.122,17.781],["Bánhorváti",48.233,20.517],["Bánk",47.925,19.174],["Bárna",48.1,19.936],["Báránd",47.3,21.233],["Báta",46.129,18.77],["Bátaszék",46.194,18.723],["Bátmonostor",46.102,18.928],["Bátonyterenye",47.97,19.841],["Bátorliget",47.762,22.273],["Bátya",46.488,18.954],["Békés",46.767,21.133],["Békéscsaba",46.683,21.1],["Békésszentandrás",46.867,20.483],["Békéssámson",46.417,20.633],["Bélapátfalva",48.05,20.367],["Bélatelep",47.458,19.198],["Bócsa",46.61,19.484],["Bókaytelep",47.438,19.181],["Bóly",45.967,18.518],["Böhönye",46.413,17.38],["Bököny",47.733,21.75],["Bölcske",46.741,18.967],["Bük",47.385,16.751],["Bükkszentkereszt",48.067,20.633],["Bükkösd",46.108,17.988],["Bő",47.368,16.815],["Bőcs",48.05,20.967],["Bőny",47.65,17.87],["Bősárkány",47.688,17.25],["Cece",46.771,18.628],["Cegléd",47.173,19.8],["Ceglédbercel",47.224,19.668],["Celldömölk",47.257,17.15],["Cibakháza",46.96,20.198],["Cigánd",48.256,21.892],["Cinkota",47.522,19.23],["Corvinnegyed",47.49,19.071],["Csabrendek",47.014,17.291],["Csanytelek",46.595,20.123],["Csanádapáca",46.55,20.883],["Csanádpalota",46.25,20.733],["Csarnoknegyed",47.494,19.074],["Csemő",47.118,19.691],["Csengele",46.542,19.864],["Csenger",47.836,22.681],["Csengőd",46.715,19.268],["Csepel-Belváros",47.43,19.068],["Csepel-Csillagtelep",47.409,19.059],["Csepel-Erdősor",47.407,19.076],["Csepel-Háros",47.395,19.054],["Csepel-Kertváros",47.42,19.085],["Csepel-Királyerdő",47.403,19.092],["Csepel-Királymajor",47.425,19.089],["Csepel-Rózsadomb",47.407,19.049],["Csepel-Szabótelep",47.435,19.076],["Csepel-Ófalu",47.439,19.066],["Csepreg",47.401,16.709],["Cserkeszőlő",46.863,20.187],["Csernely",48.15,20.35],["Cserszegtomaj",46.802,17.221],["Csetény",47.318,17.992],["Csikéria",46.129,19.471],["Csillaghegy",47.587,19.057],["Csincse",47.887,20.768],["Csobánka",47.646,18.962],["Csokonyavisonta",46.077,17.444],["Csokvaomány",48.167,20.383],["Csolnok",47.691,18.716],["Csongrád",46.713,20.142],["Csopak",46.977,17.918],["Csorna",47.612,17.25],["Csorvás",46.633,20.833],["Csurgó",46.253,17.101],["Csákvár",47.392,18.465],["Csány",47.648,19.83],["Császár",47.498,18.143],["Császártöltés",46.422,19.184],["Csátalja",46.037,18.947],["Csávoly",46.189,19.147],["Csécse",47.867,19.633],["Csólyospálos",46.418,19.84],["Csór",47.206,18.257],["Csökmő",47.033,21.3],["Csömör",47.55,19.233],["Dabas",47.186,19.311],["Dad",47.519,18.226],["Debrecen",47.532,21.624],["Decs",46.284,18.76],["Demecser",48.116,21.926],["Derecske",47.35,21.567],["Deszk",46.218,20.243],["Devecser",47.103,17.438],["Diósd",47.41,18.949],["Diósgyőr",48.1,20.683],["Diósjenő",47.94,19.043],["Doboz",46.733,21.25],["Domaháza",48.183,20.117],["Domaszék",46.249,20.011],["Dombegyház",46.333,21.133],["Dombrád",48.233,21.933],["Dombóvár",46.377,18.137],["Domony",47.656,19.432],["Domoszló",47.833,20.117],["Dorog",47.721,18.736],["Dunaalmás",47.729,18.322],["Dunabogdány",47.791,19.041],["Dunaegyháza",46.838,18.954],["Dunaföldvár",46.807,18.928],["Dunaharaszti",47.355,19.098],["Dunakeszi",47.636,19.139],["Dunakiliti",47.965,17.287],["Dunapataj",46.644,18.996],["Dunaszekcső",46.087,18.759],["Dunaszentbenedek",46.593,18.894],["Dunaszentgyörgy",46.529,18.818],["Dunasziget",47.94,17.353],["Dunatetétlen",46.757,19.095],["Dunavarsány",47.279,19.066],["Dunavecse",46.915,18.971],["Dunaújváros",46.965,18.94],["Dusnok",46.391,18.963],["Dág",47.663,18.72],["Dánszentmiklós",47.215,19.547],["Dány",47.52,19.544],["Dávod",45.995,18.917],["Dédestapolcsány",48.183,20.483],["Dég",46.868,18.45],["Délegyháza",47.241,19.09],["Dévaványa",47.033,20.967],["Dóc",46.438,20.137],["Döbrököz",46.422,18.24],["Döge",48.262,22.063],["Dömsöd",47.09,19.011],["Dömös",47.762,18.913],["Dör",47.599,17.3],["Ebes",47.467,21.5],["Ecseg",47.898,19.606],["Ecser",47.444,19.325],["Ecséd",47.733,19.767],["Edelény",48.3,20.733],["Eger",47.903,20.373],["Egerszalók",47.867,20.333],["Egyek",47.633,20.9],["Elek",46.533,21.25],["Előszállás",46.831,18.835],["Emőd",47.933,20.817],["Encs",48.333,21.133],["Encsencs",47.733,22.117],["Endrefalva",48.127,19.577],["Enying",46.93,18.242],["Ercsi",47.252,18.896],["Erdőkertes",47.673,19.308],["Erdőskert",47.399,19.205],["Erdőtelek",47.683,20.317],["Erzsébettelep",47.46,19.183],["Erzsébetváros",47.502,19.072],["Esztergom",47.793,18.741],["Ete",47.531,18.075],["Etyek",47.448,18.753],["Fadd",46.465,18.819],["Fajsz",46.417,18.919],["Farkaslyuk",48.183,20.317],["Farmos",47.361,19.846],["Farád",47.606,17.2],["Fegyvernek",47.267,20.533],["Fehérgyarmat",47.983,22.517],["Fehérvárcsurgó",47.293,18.265],["Felsőlajos",47.067,19.497],["Felsőnyárád",48.333,20.6],["Felsőpakony",47.343,19.237],["Felsőrákos",47.496,19.16],["Felsőszentiván",46.197,19.187],["Felsőtag",47.389,19.093],["Felsőtárkány",47.967,20.417],["Felsőzsolca",48.1,20.867],["Ferencváros",47.478,19.091],["Ferihegy",47.442,19.236],["Fertőd",47.622,16.871],["Fertőrákos",47.72,16.65],["Fertőszentmiklós",47.59,16.875],["Foktő",46.528,18.919],["Fonyód",46.743,17.558],["Forráskút",46.365,19.91],["Forrópuszta",47.455,19.66],["Fábiánsebestyén",46.683,20.467],["Fényeslitke",48.271,22.1],["Fót",47.618,19.189],["Földes",47.3,21.367],["Földeák",46.317,20.5],["Fülöpháza",46.883,19.417],["Fülöpjakab",46.742,19.721],["Fülöpszállás",46.821,19.237],["Füzesabony",47.75,20.417],["Füzesgyarmat",47.1,21.217],["Galgahévíz",47.617,19.567],["Galgamácsa",47.696,19.387],["Ganzkertváros",47.419,19.215],["Ganznegyed",47.488,19.1],["Ganztelep",47.43,19.227],["Gara",46.032,19.043],["Gellérthegy",47.483,19.05],["Gencsapáti",47.285,16.596],["Gesztely",48.1,20.967],["Gloriett-telep",47.432,19.193],["Gomba",47.371,19.531],["Gubacs",47.431,19.095],["Gubacsidűlő",47.449,19.094],["Gubacsipuszta",47.431,19.141],["Gyenesdiás",46.771,17.287],["Gyomaendrőd",46.933,20.833],["Gyula",46.65,21.283],["Gyulaháza",48.133,22.117],["Gyál",47.384,19.221],["Gyárdűlő",47.476,19.12],["Gyömrő",47.427,19.401],["Gyöngyös",47.783,19.928],["Gyöngyöshalász",47.742,19.929],["Gyöngyöspata",47.815,19.789],["Gyöngyössolymos",47.817,19.936],["Gyöngyöstarján",47.814,19.867],["Gyönk",46.556,18.477],["Győr",47.683,17.635],["Győrszemere",47.553,17.564],["Győrújbarát",47.606,17.649],["Gádoros",46.667,20.6],["Gárdony",47.209,18.636],["Géderlak",46.607,18.914],["Gégény",48.15,21.95],["Göd",47.683,19.134],["Gödre",46.286,17.973],["Gödöllő",47.597,19.355],["Gönc",48.467,21.283],["Görbeháza",47.833,21.233],["Görömböly",48.05,20.783],["Hajdúbagos",47.393,21.666],["Hajdúböszörmény",47.667,21.517],["Hajdúdorog",47.817,21.5],["Hajdúhadház",47.683,21.667],["Hajdúnánás",47.85,21.433],["Hajdúszoboszló",47.45,21.4],["Hajdúszovát",47.383,21.483],["Hajdúsámson",47.6,21.767],["Hajmáskér",47.145,18.02],["Hajós",46.399,19.121],["Halmaj",48.25,21.0],["Halászi",47.889,17.326],["Halásztelek",47.362,18.981],["Hangony",48.233,20.2],["Harka",47.633,16.6],["Harkakötöny",46.463,19.608],["Harkány",45.85,18.237],["Harsány",47.967,20.75],["Harta",46.698,19.031],["Hatvan",47.667,19.683],["Havanna-lakótelep",47.44,19.167],["Hegyeshalom",47.913,17.154],["Hegyfalu",47.354,16.884],["Hegykő",47.619,16.797],["Hejőbába",47.9,20.95],["Helvécia",46.837,19.623],["Hercegszántó",45.95,18.939],["Herend",47.133,17.75],["Hernád",47.162,19.433],["Hernádkak",48.089,20.964],["Hernádnémeti",48.067,20.983],["Heréd",47.706,19.633],["Heves",47.6,20.283],["Hidas",46.257,18.495],["Himesháza",46.088,18.57],["Hodász",47.918,22.202],["Homokmégy",46.49,19.073],["Hort",47.691,19.789],["Hortobágy",47.583,21.151],["Hosszúhetény",46.164,18.351],["Hosszúpályi",47.393,21.733],["Hárskút",47.188,17.817],["Hédervár",47.829,17.453],["Héhalom",47.78,19.585],["Hévíz",46.79,17.184],["Hévízgyörk",47.633,19.517],["Hódmezővásárhely",46.417,20.333],["Hőgyész",46.497,18.418],["Ibrány",48.123,21.71],["Iklad",47.665,19.436],["Imrehegy",46.477,19.312],["Inárcs",47.262,19.327],["Iregszemcse",46.693,18.186],["Isaszeg",47.53,19.402],["Istvándi",46.018,17.623],["Istvánmező",47.505,19.096],["Isztimér",47.279,18.195],["Iván",47.444,16.906],["Ivánc",46.944,16.502],["Iváncsa",47.157,18.82],["Izsák",46.805,19.352],["Izsófalva",48.3,20.667],["Jakabszállás",46.76,19.607],["Jobbágyi",47.832,19.678],["Ják",47.142,16.581],["Jánoshalma",46.299,19.326],["Jánoshida",47.383,20.067],["Jánosháza",47.119,17.165],["Jánossomorja",47.786,17.136],["Járdánháza",48.15,20.25],["Jászalsószentgyörgy",47.367,20.1],["Jászapáti",47.517,20.15],["Jászberény",47.5,19.917],["Jászdózsa",47.566,20.015],["Jászfelsőszentgyörgy",47.507,19.784],["Jászjákóhalma",47.52,19.991],["Jászkarajenő",47.05,20.067],["Jászkisér",47.45,20.217],["Jászladány",47.367,20.167],["Jászszentandrás",47.583,20.183],["Jászszentlászló",46.567,19.761],["Jászárokszállás",47.642,19.98],["József Attila-lakótelep",47.464,19.117],["Józsefváros",47.489,19.073],["Kaba",47.35,21.283],["Kadarkút",46.236,17.62],["Kakucs",47.242,19.365],["Kalocsa",46.53,18.973],["Kaposmérő",46.362,17.704],["Kaposvár",46.367,17.8],["Kapuvár",47.592,17.029],["Karancsberény",48.184,19.748],["Karancskeszi",48.164,19.697],["Karancslapujtő",48.15,19.733],["Karcag",47.317,20.933],["Karcsa",48.311,21.805],["Kardos",46.798,20.718],["Kardos",46.233,20.617],["Kartal",47.671,19.542],["Karácsond",47.73,20.031],["Karád",46.691,17.841],["Kaskantyú",46.672,19.387],["Kaszaper",46.467,20.833],["Katymár",46.034,19.209],["Kazincbarcika",48.25,20.633],["Kazár",48.05,19.861],["Kecel",46.525,19.252],["Kecskemét",46.906,19.691],["Kecskéd",47.523,18.309],["Kelebia",46.197,19.617],["Kelenföld",47.466,19.031],["Kelenvölgy",47.45,19.024],["Kemecse",48.075,21.806],["Kenderes",47.25,20.683],["Kengyel",47.083,20.333],["Kerecsend",47.8,20.35],["Kerekegyháza",46.937,19.478],["Kerepes",47.56,19.283],["Kerepesdűlő",47.494,19.096],["Keresztúridűlő",47.473,19.191],["Keszthely",46.768,17.243],["Kesztölc",47.713,18.802],["Kevermes",46.417,21.183],["Kimle",47.817,17.366],["Kincsesbánya",47.264,18.278],["Kisbér",47.503,18.037],["Kisfalud",47.528,17.091],["Kisgyőr",48.017,20.7],["Kiskanizsa",46.45,16.967],["Kiskunfélegyháza",46.712,19.845],["Kiskunhalas",46.434,19.485],["Kiskunlacháza",47.188,19.009],["Kiskunmajsa",46.49,19.74],["Kisköre",47.5,20.5],["Kiskőrös",46.621,19.285],["Kisláng",46.957,18.388],["Kisléta",47.842,22.004],["Kismaros",47.837,19.005],["Kispest",47.452,19.14],["Kisszállás",46.28,19.49],["Kistarcsa",47.548,19.262],["Kistelek",46.472,19.98],["Kistokaj",48.04,20.839],["Kisvárda",48.217,22.083],["Kiszombor",46.183,20.433],["Kisújszállás",47.217,20.767],["Kocs",47.608,18.215],["Kocsord",47.939,22.383],["Kocsér",47.002,19.921],["Komló",46.193,18.265],["Kompolt",47.742,20.242],["Komádi",47.0,21.5],["Komárom",47.743,18.119],["Kondoros",46.767,20.8],["Konyár",47.317,21.667],["Kosd",47.808,19.178],["Kossuth Ferenc-telep",47.406,19.176],["Kossuthfalva",47.439,19.123],["Kozármisleny",46.03,18.292],["Krepuska Géza-telep",47.412,19.2],["Kunadacs",46.956,19.289],["Kunbaja",46.084,19.421],["Kunbaracs",46.989,19.403],["Kunfehértó",46.361,19.415],["Kunhegyes",47.367,20.633],["Kunmadaras",47.433,20.8],["Kunpeszér",47.059,19.276],["Kunszentmiklós",47.027,19.126],["Kunszentmárton",46.839,20.289],["Kunszállás",46.763,19.754],["Kunágota",46.433,21.05],["Kurityán",48.317,20.633],["Kál",47.733,20.267],["Kállósemjén",47.861,21.939],["Kálmánháza",47.883,21.583],["Káloz",46.955,18.483],["Kántorjánosi",47.933,22.15],["Kápolnásnyék",47.24,18.676],["Kék",48.117,21.883],["Kétegyháza",46.533,21.183],["Kéthely",46.646,17.394],["Kóka",47.486,19.579],["Kóny",47.631,17.357],["Kótaj",48.05,21.717],["Kömpöc",46.464,19.867],["Körmend",47.011,16.606],["Környe",47.547,18.321],["Körösladány",46.967,21.083],["Köröstarcsa",46.883,21.033],["Középső-Ferencváros",47.473,19.08],["Külső-Ferencváros",47.457,19.09],["Kőbánya",47.484,19.139],["Kőbánya-Kertváros",47.483,19.202],["Kőszeg",47.389,16.541],["Ladánybene",47.034,19.456],["Lajoskomárom",46.842,18.338],["Lajosmizse",47.021,19.562],["Lak",48.35,20.867],["Lakatos-lakótelep",47.454,19.174],["Lakihegy",47.378,19.01],["Lakitelek",46.876,19.995],["Laposdűlő",47.492,19.115],["Lengyeltóti",46.67,17.644],["Lenti",46.624,16.539],["Lepsény",46.99,18.244],["Letenye",46.433,16.726],["Levelek",47.963,21.985],["Levél",47.894,17.204],["Leányfalu",47.718,19.086],["Ligettelek",47.493,19.128],["Liptáktelep",47.446,19.16],["Lipótváros",47.502,19.049],["Litér",47.101,18.005],["Losoncinegyed",47.484,19.082],["Lovas",46.994,17.957],["Lovasberény",47.31,18.552],["Lucfalva",48.03,19.69],["Lábatlan",47.747,18.496],["Lábod",46.205,17.454],["Lágymányos",47.472,19.058],["Lánycsók",46.005,18.625],["Lébény",47.736,17.391],["Létavértes",47.383,21.9],["Lónyaytelep",47.45,19.183],["Lőkösháza",46.433,21.233],["Lőrinci",47.733,19.679],["Madaras",46.059,19.261],["Madocsa",46.688,18.958],["Madárdomb",47.477,19.232],["Magdolnanegyed",47.493,19.083],["Maglód",47.443,19.364],["Magyarbánhegyes",46.45,20.967],["Maklár",47.8,20.417],["Makó",46.217,20.483],["Marcali",46.585,17.412],["Maroslele",46.267,20.35],["Martin Kertváros",48.092,20.814],["Martonvásár",47.316,18.79],["Mecseknádasd",46.225,18.471],["Medgyesegyháza",46.5,21.033],["Megyaszó",48.183,21.05],["Mende",47.431,19.456],["Mezőberény",46.817,21.033],["Mezőcsát",47.817,20.917],["Mezőfalva",46.932,18.772],["Mezőhegyes",46.317,20.817],["Mezőkeresztes",47.833,20.7],["Mezőkovácsháza",46.4,20.917],["Mezőkövesd",47.817,20.583],["Mezőszilas",46.811,18.478],["Mezőtúr",47.0,20.633],["Mezőzombor",48.15,21.267],["Mihályi",47.514,17.095],["Mikepércs",47.45,21.633],["Miklóstelep",47.447,19.195],["Millenniumtelep",47.379,19.108],["Milota",48.104,22.784],["Mindszent",46.524,20.19],["Miske",46.446,19.033],["Miskolc",48.103,20.778],["Miskolctapolca",48.063,20.748],["Mocsa",47.67,18.185],["Mogyoród",47.597,19.241],["Mohács",45.99,18.686],["Monok",48.211,21.151],["Monor",47.351,19.447],["Monostorpályi",47.4,21.783],["Mosonmagyaróvár",47.868,17.27],["Mosonszentmiklós",47.728,17.428],["Murakeresztúr",46.364,16.882],["Mád",48.194,21.282],["Mágocs",46.35,18.232],["Mályi",48.017,20.833],["Mándok",48.321,22.191],["Mány",47.534,18.656],["Máriapócs",47.883,22.025],["Mártonhegy",47.495,18.996],["Mátraderecske",47.95,20.083],["Mátranovák",48.038,19.983],["Mátraterenye",48.033,19.948],["Mátraverebély",47.974,19.78],["Mátyásföld",47.509,19.211],["Mátészalka",47.955,22.323],["Méhkerék",46.783,21.45],["Mélykút",46.215,19.381],["Mérk",47.788,22.38],["Mór",47.378,18.204],["Mórahalom",46.218,19.885],["Múcsony",48.267,20.683],["Nagyatád",46.23,17.358],["Nagybajom",46.392,17.511],["Nagybaracska",46.042,18.906],["Nagybarca",48.25,20.533],["Nagycenk",47.604,16.697],["Nagycserkesz",47.967,21.533],["Nagydobos",48.058,22.304],["Nagydobsza",46.033,17.667],["Nagydorog",46.627,18.656],["Nagyecsed",47.865,22.392],["Nagyfüged",47.683,20.117],["Nagyhalász",48.133,21.761],["Nagyhangács",47.455,19.222],["Nagyigmánd",47.641,18.081],["Nagykanizsa",46.453,16.991],["Nagykarácsony",46.871,18.772],["Nagykovácsi",47.581,18.885],["Nagykálló",47.875,21.841],["Nagykáta",47.415,19.744],["Nagykőrös",47.034,19.779],["Nagymaros",47.793,18.96],["Nagymányok",46.279,18.455],["Nagyoroszi",48.005,19.09],["Nagyrábé",47.2,21.333],["Nagyrécse",46.49,17.052],["Nagyréde",47.765,19.848],["Nagyszénás",46.683,20.667],["Nagytarcsa",47.531,19.283],["Nagytétény",47.395,18.986],["Nagyvenyim",46.958,18.864],["Naphegy",47.488,19.025],["Napkor",47.938,21.868],["Naszály",47.703,18.258],["Nekézseny",48.167,20.433],["Nemesbük",46.817,17.15],["Nemesnádudvar",46.341,19.051],["Nemesvámos",47.055,17.875],["Neszmély",47.736,18.36],["Nyergesújfalu",47.76,18.557],["Nyáregyháza",47.262,19.501],["Nyárlőrinc",46.86,19.878],["Nyékládháza",47.983,20.833],["Nyíracsád",47.603,21.972],["Nyíradony",47.697,21.919],["Nyírbogdány",48.057,21.882],["Nyírbogát",47.803,22.066],["Nyírbátor",47.833,22.133],["Nyírbéltek",47.7,22.133],["Nyírcsaholy",47.904,22.336],["Nyíregyháza",47.955,21.717],["Nyírgyulaj",47.886,22.098],["Nyírkarász",48.095,22.105],["Nyírlugos",47.693,22.045],["Nyírmada",48.067,22.2],["Nyírmeggyes",47.917,22.267],["Nyírmihálydi",47.74,21.964],["Nyírmártonfalva",47.583,21.9],["Nyírpazony",47.983,21.8],["Nyírtass",48.117,22.033],["Nyírtelek",48.017,21.633],["Nyírvasvári",47.817,22.187],["Nyírábrány",47.553,22.024],["Nyúl",47.59,17.689],["Nádasd",46.968,16.611],["Nádasdladány",47.138,18.24],["Nádudvar",47.417,21.167],["Németkér",46.716,18.763],["Népliget",47.48,19.109],["Népszínháznegyed",47.496,19.079],["Nógrád",47.909,19.048],["Nógrádmegyer",48.068,19.625],["Okány",46.9,21.35],["Olaszliszka",48.25,21.433],["Onga",48.117,20.917],["Orbánhegy",47.495,19.006],["Orczynegyed",47.485,19.088],["Ordas",46.636,18.946],["Orgovány",46.751,19.473],["Ormosbánya",48.35,20.65],["Orosháza",46.567,20.667],["Oroszlány",47.487,18.312],["Országút",47.511,19.027],["Ostoros",47.867,20.433],["Ozora",46.751,18.4],["Pacsa",46.72,17.014],["Pacsirtatelep",47.422,19.122],["Paks",46.622,18.856],["Palotanegyed",47.493,19.065],["Palotás",47.795,19.596],["Pannonhalma",47.549,17.755],["Pap",48.216,22.145],["Papos",47.983,22.25],["Parasznya",48.167,20.65],["Parád",47.923,20.03],["Paszab",48.15,21.683],["Pellérd",46.034,18.154],["Penyige",47.996,22.564],["Penészlek",47.633,22.15],["Perbál",47.59,18.761],["Perkupa",48.467,20.7],["Perkáta",47.047,18.787],["Pest",47.5,19.083],["Pesterzsébet",47.433,19.117],["Pesthidegkút",47.558,18.964],["Pestlőrinc",47.43,19.216],["Pestújhely",47.54,19.134],["Petneháza",48.061,22.075],["Petőfibánya",47.77,19.7],["Petőfiszállás",46.622,19.862],["Pilis",47.289,19.548],["Pilisborosjenő",47.607,18.993],["Piliscsaba",47.634,18.829],["Piliscsév",47.679,18.819],["Pilismarót",47.785,18.875],["Pilisszentiván",47.61,18.899],["Pilisszentkereszt",47.691,18.905],["Pilisszántó",47.669,18.888],["Pilisvörösvár",47.614,18.909],["Pincehely",46.681,18.439],["Pirtó",46.514,19.432],["Pocsaj",47.283,21.817],["Polgár",47.867,21.117],["Polgárdi",47.061,18.302],["Pomáz",47.642,19.028],["Porcsalma",47.883,22.567],["Poroszló",47.65,20.667],["Prügy",48.083,21.25],["Pusztaföldvár",46.533,20.8],["Pusztahencse",46.6,18.717],["Pusztaszabolcs",47.137,18.767],["Pusztaszer",46.551,19.988],["Pusztavám",47.429,18.226],["Putnok",48.3,20.433],["Pácin",48.335,21.836],["Páhi",46.713,19.387],["Pákozd",47.214,18.533],["Pálháza",48.467,21.517],["Pálmonostora",46.624,19.952],["Pánd",47.353,19.636],["Pápa",47.33,17.467],["Pásztó",47.92,19.698],["Pátroha",48.167,22.0],["Páty",47.517,18.829],["Pátyod",47.869,22.619],["Pázmánd",47.288,18.654],["Pécel",47.49,19.342],["Pécs",46.076,18.228],["Pécsvárad",46.16,18.423],["Pély",47.483,20.35],["Pér",47.612,17.806],["Péteri",47.391,19.41],["Pétervására",48.017,20.1],["Pétfürdő",47.167,18.117],["Püski",47.885,17.408],["Püspökladány",47.317,21.117],["Radostyán",48.179,20.653],["Rajka",47.996,17.198],["Rakamaz",48.124,21.464],["Recsk",47.933,20.117],["Remetekertváros",47.551,18.956],["Remeteszőlős",47.56,18.92],["Rendessytelep",47.453,19.166],["Ricse",48.326,21.971],["Rimóc",48.037,19.53],["Romhány",47.926,19.257],["Rudabánya",48.383,20.633],["Rudolftelep",48.309,20.67],["Ruzsa",46.288,19.747],["Rábacsécsény",47.586,17.424],["Rábapatona",47.632,17.48],["Rábaszentmihály",47.578,17.432],["Rácalmás",47.023,18.941],["Ráckeresztúr",47.274,18.833],["Ráckeve",47.161,18.945],["Rákoscsaba",47.488,19.298],["Rákoscsaba-Újtelep",47.501,19.286],["Rákoshegy",47.463,19.239],["Rákoskeresztúr",47.473,19.262],["Rákoskert",47.465,19.298],["Rákosliget",47.49,19.257],["Rákospalota",47.565,19.122],["Rákosszentmihály",47.525,19.155],["Rákóczifalva",47.083,20.233],["Rákócziújfalu",47.067,20.267],["Régiakadémiatelep",47.49,19.247],["Rém",46.247,19.141],["Répcelak",47.421,17.018],["Rétság",47.928,19.137],["Révfülöp",46.826,17.62],["Rómaifürdő",47.577,19.065],["Rózsaszentmárton",47.782,19.742],["Röszke",46.188,20.034],["Sajóbábony",48.167,20.733],["Sajóecseg",48.2,20.783],["Sajóivánka",48.267,20.583],["Sajókaza",48.283,20.583],["Sajólád",48.04,20.902],["Sajópetri",48.035,20.888],["Sajópálfala",48.167,20.85],["Sajószentpéter",48.217,20.717],["Sajószöged",47.95,21.0],["Sajóvámos",48.182,20.831],["Sajóörös",47.95,21.033],["Salgótarján",48.099,19.803],["Sarkad",46.75,21.383],["Sashalom",47.515,19.171],["Segesd",46.341,17.351],["Sellye",45.872,17.847],["Seregélyes",47.111,18.565],["Siklós",45.855,18.298],["Simontornya",46.755,18.555],["Sirok",47.933,20.2],["Sióagárd",46.393,18.653],["Siófok",46.904,18.058],["Solt",46.801,19.001],["Soltszentimre",46.771,19.287],["Soltvadkert",46.579,19.394],["Solymár",47.592,18.932],["Somogyszil",46.519,17.998],["Somogyudvarhely",46.174,17.189],["Somogyvár",46.581,17.663],["Somoskőújfalu",48.164,19.823],["Soponya",47.015,18.453],["Sopron",47.685,16.59],["Soroksár",47.405,19.124],["Soroksár-Ujtelep",47.416,19.132],["Szabadbattyán",47.119,18.368],["Szabadkígyós",46.617,21.1],["Szabadszállás",46.876,19.223],["Szabás",46.289,17.448],["Szada",47.633,19.317],["Szajol",47.183,20.3],["Szakcs",46.539,18.11],["Szakmár",46.555,19.075],["Szakoly",47.767,21.917],["Szalkszentmárton",46.976,19.012],["Szamosszeg",48.046,22.366],["Szanda",47.927,19.441],["Szank",46.557,19.661],["Szany",47.464,17.304],["Szarvas",46.867,20.55],["Szatymaz",46.343,20.04],["Szedres",46.476,18.683],["Szeged",46.253,20.148],["Szeghalom",47.033,21.167],["Szegvár",46.587,20.224],["Szekszárd",46.347,18.712],["Szelevény",46.803,20.203],["Szemeretelep",47.439,19.213],["Szendrő",48.4,20.733],["Szent Imre-kertváros",47.43,19.207],["Szent Lőrinc-lakótelep",47.434,19.172],["Szentendre",47.669,19.076],["Szentes",46.658,20.261],["Szentgotthárd",46.953,16.274],["Szentgálpuszta",46.375,18.626],["Szentistván",47.767,20.667],["Szentkirály",46.919,19.918],["Szentkirályszabadja",47.058,17.971],["Szentlőrinc",46.04,17.987],["Szentlőrinckáta",47.519,19.753],["Szentmártonkáta",47.454,19.701],["Szeremle",46.144,18.881],["Szerencs",48.16,21.21],["Szigetcsép",47.265,18.97],["Szigethalom",47.322,19.003],["Szigetszentmiklós",47.344,19.043],["Szigetvár",46.049,17.806],["Szigetújfalu",47.234,18.927],["Szigliget",46.801,17.435],["Szihalom",47.767,20.483],["Szikszó",48.2,20.933],["Szilvásvárad",48.1,20.4],["Szirma",48.067,20.833],["Szirmabesenyő",48.15,20.8],["Szirák",47.831,19.531],["Szob",47.819,18.87],["Szolnok",47.181,20.198],["Szombathely",47.231,16.622],["Szomód",47.685,18.341],["Szuhakálló",48.283,20.652],["Szurdokpüspöki",47.859,19.692],["Szálka",46.275,18.638],["Szárliget",47.516,18.495],["Szászvár",46.277,18.376],["Századosnegyed",47.493,19.107],["Százhalombatta",47.329,18.939],["Szécsény",48.081,19.52],["Székesfehérvár",47.19,18.41],["Székkutas",46.5,20.533],["Sződ",47.724,19.17],["Sződliget",47.733,19.147],["Ságújfalu",48.104,19.68],["Sály",47.95,20.667],["Sándorfalva",46.361,20.099],["Sáp",47.25,21.367],["Sárbogárd",46.887,18.62],["Sárisáp",47.677,18.679],["Sárkeresztúr",47.005,18.544],["Sármellék",46.712,17.169],["Sárosd",47.043,18.644],["Sárospatak",48.325,21.574],["Sárpilis",46.249,18.742],["Sárrétudvari",47.233,21.2],["Sárszentmihály",47.153,18.339],["Sárvár",47.254,16.935],["Sáránd",47.4,21.633],["Sásd",46.255,18.108],["Sáta",48.183,20.4],["Sátoraljaújhely",48.395,21.659],["Sé",47.243,16.556],["Sóshartyán",48.073,19.679],["Sóskút",47.407,18.822],["Súr",47.369,18.03],["Sükösd",46.282,18.995],["Sülysáp",47.452,19.534],["Sümeg",46.977,17.282],["Süttő",47.759,18.449],["Tab",46.731,18.032],["Tabdi",46.682,19.326],["Tabán",47.492,19.043],["Tahitótfalu",47.752,19.079],["Taksony",47.33,19.067],["Taktaharkány",48.083,21.133],["Taktaszada",48.117,21.183],["Tamási",46.633,18.283],["Tapolca",46.882,17.441],["Tar",47.954,19.746],["Tarcal",48.133,21.35],["Tard",47.883,20.617],["Tardona",48.167,20.533],["Tardos",47.662,18.444],["Tarján",47.61,18.511],["Tarnalelesz",48.05,20.183],["Tarnaörs",47.595,20.053],["Tarpa",48.105,22.537],["Tass",47.021,19.03],["Taszár",46.375,17.906],["Tata",47.653,18.318],["Tatabánya",47.592,18.381],["Tataháza",46.174,19.3],["Telki",47.548,18.828],["Tengelic",46.529,18.711],["Tihany",46.914,17.889],["Tiszaalpár",46.813,19.998],["Tiszabercel",48.15,21.65],["Tiszabezdéd",48.367,22.15],["Tiszabura",47.45,20.467],["Tiszabő",47.3,20.483],["Tiszacsege",47.7,21.0],["Tiszadada",48.033,21.25],["Tiszadob",48.017,21.167],["Tiszaeszlár",48.05,21.467],["Tiszaföldvár",46.983,20.25],["Tiszafüred",47.617,20.767],["Tiszakarád",48.2,21.717],["Tiszakeszi",47.783,21.0],["Tiszakécske",46.933,20.103],["Tiszalök",48.017,21.383],["Tiszalúc",48.038,21.073],["Tiszanagyfalu",48.092,21.473],["Tiszanána",47.567,20.533],["Tiszapüspöki",47.217,20.317],["Tiszaroff",47.4,20.45],["Tiszaszentimre",47.483,20.733],["Tiszaszőlős",47.557,20.719],["Tiszasüly",47.383,20.4],["Tiszaug",46.856,20.055],["Tiszavasvári",47.967,21.35],["Tiszaújváros",47.933,21.083],["Tisztviselőtelep",47.481,19.097],["Tokaj",48.117,21.409],["Tokod",47.723,18.659],["Tolcsva",48.283,21.45],["Tolmács",47.929,19.109],["Tolna",46.427,18.782],["Tompa",46.206,19.539],["Tornyospálca",48.267,22.183],["Torony",47.241,16.54],["Tunyogmatolcs",47.967,22.467],["Tura",47.609,19.603],["Tuzsér",48.344,22.118],["Tyukod",47.854,22.563],["Táborfalva",47.099,19.478],["Tállya",48.233,21.233],["Tápióbicske",47.361,19.686],["Tápiógyörgye",47.335,19.953],["Tápiószecső",47.45,19.609],["Tápiószele",47.336,19.877],["Tápiószentmárton",47.34,19.746],["Tápiószőlős",47.302,19.851],["Tápióság",47.402,19.63],["Táplánszentkereszt",47.195,16.696],["Tárnok",47.373,18.846],["Tát",47.741,18.648],["Tázlár",46.548,19.514],["Téglagyárdűlő",47.482,19.168],["Téglás",47.717,21.683],["Tét",47.519,17.508],["Tóalmás",47.508,19.667],["Tószeg",47.1,20.15],["Tótkomlós",46.417,20.733],["Tököl",47.322,18.962],["Töltéstava",47.626,17.734],["Tömörkény",46.617,20.044],["Törtel",47.122,19.937],["Törökbálint",47.429,18.914],["Törökszentmiklós",47.183,20.417],["Túrkeve",47.1,20.75],["Türje",46.984,17.107],["Uszód",46.57,18.901],["Vadna",48.267,20.567],["Vaja",48.006,22.168],["Vajszló",45.86,17.984],["Valkó",47.564,19.513],["Vanyarc",47.826,19.453],["Varbó",48.163,20.622],["Vaskút",46.108,18.985],["Vasvár",47.049,16.8],["Vecsés",47.407,19.286],["Velence",47.239,18.655],["Veresegyház",47.646,19.295],["Verpelét",47.85,20.233],["Verőce",47.825,19.035],["Veszprém",47.093,17.911],["Villány",45.869,18.454],["Visegrád",47.785,18.971],["Vonyarcvashegy",46.757,17.312],["Vác",47.776,19.136],["Vácszentlászló",47.574,19.538],["Vál",47.363,18.679],["Vámosgyörk",47.684,19.929],["Vámospércs",47.533,21.9],["Városföld",46.817,19.757],["Várpalota",47.199,18.14],["Vásárosnamény",48.125,22.313],["Vép",47.23,16.722],["Vértesszőlős",47.621,18.381],["Vésztő",46.917,21.267],["Wekerletelep",47.456,19.126],["Zagyvarékas",47.267,20.133],["Zagyvaszántó",47.777,19.671],["Zalaegerszeg",46.837,16.844],["Zalakaros",46.565,17.125],["Zalakomár",46.538,17.181],["Zalalövő",46.848,16.587],["Zalaszentgrót",46.945,17.079],["Zamárdi",46.885,17.954],["Zebegény",47.8,18.911],["Zichyújfalu",47.133,18.683],["Zirc",47.264,17.874],["Zomba",46.411,18.566],["Zsana",46.406,19.681],["Zsira",47.458,16.677],["Zsombó",46.326,19.975],["Zsámbok",47.544,19.61],["Zsámbék",47.548,18.72],["Zugliget",47.515,18.979],["Zugló",47.518,19.105],["Záhony",48.409,22.176],["Zákányszék",46.275,19.89],["Zámoly",47.317,18.408],["Zánka",46.871,17.685],["Ábrahámhegy",46.81,17.556],["Ács",47.712,18.011],["Ácsteszér",47.405,18.006],["Ádánd",46.859,18.164],["Ágasegyháza",46.84,19.452],["Ágfalva",47.69,16.517],["Álmosd",47.419,21.978],["Áporka",47.233,19.017],["Ároktő",47.733,20.95],["Árpádföld",47.533,19.197],["Árpádhalom",46.617,20.55],["Ártánd",47.117,21.767],["Ásotthalom",46.199,19.783],["Ásványráró",47.827,17.494],["Ászár",47.512,18.002],["Átány",47.616,20.362],["Érd",47.395,18.914],["Érsekcsanád",46.254,18.985],["Érsekhalma",46.349,19.122],["Érsekvadkert",47.996,19.202],["Értény",46.611,18.133],["Óbuda",47.55,19.033],["Ócsa",47.3,19.231],["Ófehértó",47.933,22.05],["Óhegy",47.478,19.148],["Ónod",48.0,20.917],["Ópusztaszer",46.486,20.087],["Ópályi",47.998,22.326],["Ózd",48.217,20.3],["Öcsöd",46.9,20.4],["Ököritófülpös",47.919,22.508],["Öregcsertő",46.518,19.115],["Örkény",47.13,19.433],["Öttevény",47.719,17.485],["Ötvöskónyi",46.287,17.36],["Újcsanálos",48.133,21.017],["Újdombrád",48.195,21.87],["Újfehértó",47.8,21.683],["Újhartyán",47.22,19.386],["Újhegy",47.467,19.166],["Újkígyós",46.583,21.033],["Újlak",47.529,19.038],["Újlipótváros",47.516,19.053],["Újpalota",47.537,19.144],["Újpest",47.569,19.085],["Újpéteritelep",47.407,19.19],["Újszeged",46.248,20.166],["Újszentiván",46.186,20.185],["Újszilvás",47.275,19.925],["Újszász",47.3,20.083],["Úri",47.414,19.528],["Úrkút",47.085,17.644],["Üllés",46.336,19.845],["Üllő",47.388,19.355],["Üröm",47.597,19.016],["Őcsény",46.314,18.757],["Őr",47.972,22.187],["Őrbottyán",47.687,19.282],["Ősi",47.147,18.188]]


IDO_JS = r"""(function(){
  var D = __IDO_ADAT__;
  var doboz = document.getElementById('ido-itt'); if (!doboz || !navigator.geolocation) return;
  doboz.hidden = false;
  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var EJ = {nap: 'hold', napfelho: 'holdfelho'};
  function rajz(nev, m, ejjel){ if (ejjel && EJ[nev]) nev = EJ[nev];
    return '<svg class="ido-ikon" width="'+m+'" height="'+m+'" viewBox="0 0 32 32" aria-hidden="true">'+D.ikonok[nev]+'</svg>'; }
  function ikon(kod, m, ejjel){ var w = D.wmo[kod] || ['', 'felho']; return rajz(w[1], m, ejjel); }
  function tav(a, b, c, d){ var r = Math.PI/180, x = (d-b)*r*Math.cos((a+c)/2*r), y = (c-a)*r; return Math.sqrt(x*x+y*y)*6371; }
  function helynev(lat, lon){
    var best = null, bt = 1e9;
    D.telepulesek.forEach(function(t){ var k = tav(lat, lon, t[1], t[2]); if (k < bt) { bt = k; best = t[0]; } });
    if (bt < 3) return best;
    if (bt < 9) return best + ' környéke';
    if (bt < 25) return best + ' közelében';
    return 'A tartózkodási helyed';
  }
  var hely = [0, 0], pontossag = null;
  function kartya(nev, a){
    var c = a.current, d = a.daily, napok = '';
    for (var i = 0; i < d.time.length; i++) {
      var dt = new Date(d.time[i] + 'T12:00:00');
      var cimke = i === 0 ? 'Ma' : (i === 1 ? 'Holnap' : D.napok[(dt.getDay()+6)%7]);
      var p = (d.precipitation_probability_max || [])[i];
      napok += '<li title="'+esc((D.wmo[d.weather_code[i]]||[''])[0])+'"><span class="ido-nap">'+cimke+'</span>'+ikon(d.weather_code[i],26)+
        '<span class="ido-max">'+Math.round(d.temperature_2m_max[i])+'°</span><span class="ido-min">'+Math.round(d.temperature_2m_min[i])+'°</span>'+
        '<span class="ido-csap">'+(p == null ? '' : p+'%')+'</span></li>';
    }
    // 15 km-en belüli mérőállomásnál a nagy szám (és ha jelent, az égkép) a mért érték; különben a modellbecslés
    var m = null, mt = 15;
    (D.allomasok || []).forEach(function(s){ var k = tav(hely[0], hely[1], s[0], s[1]); if (k < mt) { mt = k; m = s; } });
    var fok = m ? Math.round(m[3]) : Math.round(c.temperature_2m);
    var ejjel = c.is_day === 0;
    var ik = m && m[5] ? rajz(m[5][1], 52, ejjel) : ikon(c.weather_code, 52, ejjel);
    var leir = m && m[5] ? m[5][0] : (D.wmo[c.weather_code]||[''])[0];
    // hőérzet és szél: közeli állomásnál a mért szélből és páratartalomból (ausztrál „apparent temperature”
    // képlet, ugyanaz, amit az Open-Meteo is használ), különben az Open-Meteo értéke
    var hoerzet = Math.round(c.apparent_temperature), szel = Math.round(c.wind_speed_10m);
    if (m && m[6] != null) {
      szel = Math.round(m[6] * 3.6);
      var gozny = m[7] != null ? m[7] / 100 * 6.105 * Math.exp(17.27 * m[3] / (237.7 + m[3])) : 0;
      hoerzet = Math.round(m[3] + 0.33 * gozny - 0.70 * m[6] - 4.00);
    }
    var forras = m ? '<p class="ido-mert">Mérve '+m[4]+'-kor · '+esc(m[2])+'</p>' : '<p class="ido-mert">Becsült érték (Open-Meteo)</p>';
    return '<h3><span class="ido-itt-jel" aria-hidden="true">◉</span> '+esc(nev)+'</h3>'+forras+
      '<div class="ido-most">'+ik+'<span class="ido-fok">'+fok+'°</span>'+
      '<span class="ido-leiras">'+esc(leir)+'<br><small>hőérzet '+hoerzet+
      '°, szél '+szel+' km/h</small></span></div>'+nap_sor(d)+orak(a.hourly)+
      '<ul class="ido-napok">'+napok+'</ul><p class="ido-elo-forras">Előrejelzés: Open-Meteo</p>';
  }
  // napkelte, napnyugta, a nappal hossza (ma)
  function nap_sor(d){
    if (!d.sunrise || !d.sunset) return '';
    var k = new Date(d.sunrise[0]), n = new Date(d.sunset[0]), p = Math.round((n - k) / 60000);
    var hh = function(t){ return t.getHours() + ':' + String(t.getMinutes()).padStart(2, '0'); };
    return '<p class="ido-napsor"><span>Napkelte <b>'+hh(k)+'</b></span><span>napnyugta <b>'+hh(n)+'</b></span>'+
      '<span>nappal '+Math.floor(p/60)+' óra '+(p%60)+' perc</span></p>';
  }
  // a következő 12 óra kétóránként: idő, égkép, hőmérséklet, csapadékesély
  function orak(h){
    if (!h || !h.time) return '';
    var li = '';
    for (var i = 2; i < h.time.length && i <= 12; i += 2) {
      var p = (h.precipitation_probability || [])[i];
      li += '<li><span class="ido-nap">'+h.time[i].slice(11, 13)+'h</span>'+ikon(h.weather_code[i], 24, h.is_day[i] === 0)+
        '<span class="ido-max">'+Math.round(h.temperature_2m[i])+'°</span><span class="ido-csap">'+(p == null ? '' : p+'%')+'</span></li>';
    }
    return '<p class="ido-alcim">A következő órák</p><ul class="ido-orak">'+li+'</ul><p class="ido-alcim">A következő napok</p>';
  }
  function betolt(lat, lon){
    lat = Math.round(lat*100)/100; lon = Math.round(lon*100)/100;   // kb. 1 km-re kerekítve
    hely = [lat, lon];
    doboz.innerHTML = '<h3>Ahol most vagy</h3><p class="ido-nincs">Előrejelzés betöltése…</p>';
    fetch(D.url.replace('{lat}', lat).replace('{lon}', lon))
      .then(function(r){ if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function(a){
        doboz.innerHTML = kartya(helynev(lat, lon), a); doboz.classList.add('ido-itt-kesz');
        var h3 = doboz.querySelector('h3');
        if (h3) {
          if (pontossag) {   // a hely pontossága: nagy érték = a gép csak becsülte (Wi-Fi, IP-cím)
            var pt = document.createElement('small'); pt.className = 'ido-pontossag';
            pt.textContent = pontossag < 1000 ? '±' + Math.round(pontossag) + ' m' : '±' + Math.round(pontossag / 1000) + ' km';
            pt.title = 'A helymeghatározás pontossága'; h3.appendChild(pt);
          }
          var b = document.createElement('button'); b.type = 'button'; b.className = 'ido-ujra';
          b.textContent = '↻'; b.title = 'Hely újrakérése'; b.setAttribute('aria-label', 'Hely újrakérése');
          b.addEventListener('click', kerd); h3.appendChild(b);
        }
      })
      .catch(function(){ doboz.innerHTML = '<h3>Ahol most vagy</h3><p class="ido-nincs">Az előrejelzés most nem érhető el.</p>'; });
  }
  function kerd(){
    doboz.innerHTML = '<h3>Ahol most vagy</h3><p class="ido-nincs">Hely meghatározása…</p>';
    navigator.geolocation.getCurrentPosition(
      function(p){ pontossag = p.coords.accuracy; betolt(p.coords.latitude, p.coords.longitude); },
      function(){ doboz.innerHTML = '<h3>Ahol most vagy</h3><p class="ido-nincs">A böngésző nem adta meg a helyet. '+
        'A címsor melletti lakat vagy beállítás ikonnál engedélyezheted.</p>'; },
      // mindig friss helyet kérünk (nem a böngésző korábban megjegyzett helyét), a lehető legpontosabban
      { timeout: 20000, maximumAge: 0, enableHighAccuracy: true });
  }
  function gomb(){
    doboz.innerHTML = '<h3>Ahol most vagy</h3><p class="ido-nincs">Engedélyezd a helymeghatározást, és itt a saját helyedre szóló előrejelzés jelenik meg.</p>'+
      '<button type="button" class="ido-gomb">Időjárás a saját helyeden</button>';
    doboz.querySelector('button').addEventListener('click', kerd);
  }
  // ha korábban már engedélyezted, magától betölt; különben gombbal kérjük
  if (navigator.permissions && navigator.permissions.query) {
    navigator.permissions.query({ name: 'geolocation' }).then(function(s){ s.state === 'granted' ? kerd() : gomb(); }, gomb);
  } else { gomb(); }
})();
"""


# ── HTML ───────────────────────────────────────────────────────────

def e(s):
    return html.escape(s or "", quote=True)


def budapest_tz(d):
    # közép-európai idő nyári időszámítással (márc. utolsó vas. – okt. utolsó vas., 01:00 UTC)
    ev = d.year
    marc = max(datetime(ev, 3, n, 1, tzinfo=timezone.utc) for n in range(25, 32)
               if datetime(ev, 3, n).weekday() == 6)
    okt = max(datetime(ev, 10, n, 1, tzinfo=timezone.utc) for n in range(25, 32)
              if datetime(ev, 10, n).weekday() == 6)
    return timezone(timedelta(hours=2 if marc <= d < okt else 1))


def bp(d):
    return d.astimezone(budapest_tz(d))


def ido_szoveg(iso):
    d = datetime.fromisoformat(iso)
    ora = (MOST - d).total_seconds() / 3600
    h = bp(d)
    if ora < 1:
        return f"{max(1, int(ora * 60))} perce"
    if ora < 12:
        return f"{int(ora)} órája"
    if h.date() == bp(MOST).date():
        return h.strftime("ma %H:%M")
    if h.date() == (bp(MOST) - timedelta(days=1)).date():
        return h.strftime("tegnap %H:%M")
    return f"{HONAPOK[h.month-1][:3]}. {h.day}. {h:%H:%M}"


def forras_nev(fid):
    f = FN.get(fid, {"nev": fid, "szin": "#666"})
    return f'<span class="forras" style="--c:{f["szin"]}">{e(f["nev"])}</span>'


def lefedettseg(cs):
    """Pöttyök: hány és melyik lap írt a hírről."""
    if len(cs["forrasok"]) < 2:
        return ""
    nevek = ", ".join(FN[f]["nev"] for f in cs["forrasok"])
    pottyok = "".join(f'<i style="--c:{FN[f]["szin"]}"></i>' for f in cs["forrasok"])
    return f'<span class="lefed" title="{e(nevek)}">{pottyok}{len(cs["forrasok"])} lap írt róla</span>'


def lapok(cs):
    """Lapnként egy cikk: elöl a doboz fő cikke, utána a többi lap (a képes, hosszabb bevezetős előrébb).
    Ugyanez a sorrend az „Erről írt” sorban és a lapozásban."""
    latott, lista = set(), []
    for h in [cs["fo"]] + sorted(cs["hirek"], key=lambda h: (bool(h["kep"]), len(h["lead"])), reverse=True):
        if h["forras"] not in latott:
            latott.add(h["forras"])
            lista.append(h)
    return lista


def tovabbi(cs):
    """„Erről írt: …” – az összes lap linkje (a dobozban látható félkövéren), mellette lapozógombok."""
    l = lapok(cs)
    if len(l) < 2:
        return ""
    linkek = ", ".join(f'<a href="{e(h["link"])}" target="_blank" rel="noopener" title="{e(h["cim"])}"'
                       f'{" class=\"mutatott\"" if k == 0 else ""}>{e(FN[h["forras"]]["nev"])}</a>'
                       for k, h in enumerate(l))
    return (f'<div class="tovabbi"><span class="tovabbi-lapok">Erről írt: {linkek}</span>'
            f'<span class="lapozo"><button type="button" class="lapoz" data-irany="-1" aria-label="Előző lap cikke">‹</button>'
            f'<button type="button" class="lapoz" data-irany="1" aria-label="Következő lap cikke">›</button></span></div>')


def alt_adat(cs, csak_ora=False, lead=True, kep=True):
    """Többlapos dobozoknál lapnként egy cikk adatai (a fő cikk elöl), hogy ha a forrásszűrőben a fő cikk
    lapját kikapcsolod, a böngésző egy másik lap cikkét mutassa helyette."""
    if len(cs["forrasok"]) < 2:
        return ""
    lista = []
    for h in lapok(cs):
        f = FN.get(h["forras"], {"nev": h["forras"], "szin": "#666"})
        ido = bp(datetime.fromisoformat(h["ido"])).strftime("%H:%M") if csak_ora else ido_szoveg(h["ido"])
        a = {"f": h["forras"], "n": f["nev"], "s": f["szin"], "c": h["cim"], "l": h["link"], "t": ido}
        if lead and h["lead"]:
            a["d"] = h["lead"]
        if kep and h["kep"]:
            a["k"] = h["kep"]
        lista.append(a)
    return f' data-alt="{e(json.dumps(lista, ensure_ascii=False))}"'


def kep_html(h, osztaly):
    if not h["kep"]:
        return ""
    return (f'<div class="{osztaly}"><img src="{e(h["kep"])}" alt="" loading="lazy" '
            f'onerror="this.parentNode.remove()"></div>')


def fo_doboz(cs, i):
    h = cs["fo"]
    tipus = "nyito" if i == 0 else ("masod" if i < 5 else "kis")
    if tipus != "kis":
        kep = kep_html(h, "kep")
    elif h["kep"]:
        kep = kep_html(h, "belyeg")
    else:   # kép nélkül is legyen bélyeg (a rovat színe és a fejléc), hogy lapozáskor be tudjon jönni a kép
        kep = '<div class="belyeg"><img alt="" style="visibility:hidden"><div class="kep-helyett"></div></div>'
    lead = f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] and tipus != "kis" else ""
    rszin = next((r["szin"] for r in ROVATOK if r["id"] == cs["rovat"]), "#64748b")
    return f'''<article class="fo-hir {tipus}" style="--r:{rszin}" data-srcs="{' '.join(cs['forrasok'])}"{alt_adat(cs, lead=tipus != "kis")}>
  {kep if tipus != "kis" else ""}<div class="szoveg">
  <div class="meta">{forras_nev(h["forras"])}<time>{ido_szoveg(h["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3>
  {lead}{lefedettseg(cs)}{tovabbi(cs)}</div>
  {kep if tipus == "kis" else ""}
</article>'''


def doboz(cs, lead=True, kiemelt=False, extra=""):
    h = cs["fo"]
    kep = kep_html(h, "kiemelt-kep") if kiemelt else kep_html(h, "belyeg")
    return f'''<article class="doboz{' kiemelt' if kiemelt else ''}{extra}" data-srcs="{' '.join(cs['forrasok'])}"{alt_adat(cs, lead=lead)}>
  <div class="szoveg">
  <div class="meta">{forras_nev(h["forras"])}<time>{ido_szoveg(h["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3>
  {f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] and lead else ''}
  {lefedettseg(cs)}{tovabbi(cs)}</div>
  {kep}
</article>'''


def sor(cs, csak_ora=False):
    h = cs["fo"]
    ido = bp(datetime.fromisoformat(h["ido"])).strftime("%H:%M") if csak_ora else ido_szoveg(h["ido"])
    return (f'<li data-srcs="{" ".join(cs["forrasok"])}"{alt_adat(cs, csak_ora, lead=False, kep=False)}><time>{ido}</time>'
            f'<span class="sor-cim"><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a> '
            f'{forras_nev(h["forras"])}{cikklista(cs)}</span></li>')


def rovid_ido(iso):
    """A lenyíló cikklistához: ma csak óra:perc, korábbi napokon a nap is elé kerül."""
    h = bp(datetime.fromisoformat(iso))
    ma = bp(MOST).date()
    if h.date() == ma:
        return h.strftime("%H:%M")
    if h.date() == ma - timedelta(days=1):
        return h.strftime("tegnap %H:%M")
    return f"{HONAPOK[h.month-1][:3]}. {h.day}. {h:%H:%M}"


def cikklista(cs):
    """Több cikkből álló sztorinál lenyitható lista az összes cikkel, a legújabbal kezdve."""
    hk = sorted({h["link"]: h for h in cs["hirek"]}.values(), key=lambda h: h["ido"], reverse=True)
    if len(hk) < 2:
        return ""
    sorok = "".join(
        f'<li data-srcs="{h["forras"]}"><time>{rovid_ido(h["ido"])}</time>'
        f'<span>{forras_nev(h["forras"])} <a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></span></li>'
        for h in hk)
    return (f' <button class="cikkek-gomb" type="button" aria-expanded="false">'
            f'<span class="nyil">▸</span> <span class="db">{len(hk)}</span> cikk</button>'
            f'<ul class="cikkek" hidden>{sorok}</ul>')


def rovatfej(rid, nev, szin, db):
    szam = "" if rid == "idojaras" else f'<span class="db">{db} hír</span>'
    return (f'<h2 class="rovatfej" style="--r:{szin}"><button class="csuk" aria-expanded="true">'
            f'{e(nev)}</button>{szam}</h2>')


def nap_cimke(nap):
    ma = bp(MOST).date()
    elteres = (ma - nap).days
    if elteres == 0:
        return "Ma"
    if elteres == 1:
        return "Tegnap"
    if elteres == 2:
        return "Tegnapelőtt"
    return f"{HONAPOK[nap.month-1]} {nap.day}., {NAPOK[nap.weekday()]}"


def korabban(lista, nyitva=False):
    """A „További hírek” napokra bontva; minden nap külön lenyitható, a legfrissebb nap nyitva."""
    if not lista:
        return ""
    lista = sorted(lista, key=lambda c: c["fo"]["ido"], reverse=True)
    napok = {}
    for c in lista:
        napok.setdefault(bp(datetime.fromisoformat(c["fo"]["ido"])).date(), []).append(c)
    csoportok = "".join(
        f'<details class="nap-csoport"{" open" if i == 0 else ""}><summary>{nap_cimke(nap)} ({len(cs)})</summary>'
        f'<ul class="lista">{"".join(sor(c, csak_ora=True) for c in cs)}</ul></details>'
        for i, (nap, cs) in enumerate(napok.items()))
    return (f'<details class="tovabbi-hirek"{" open" if nyitva else ""}><summary>További hírek ({len(lista)})</summary>'
            f'{csoportok}</details>')


def helyi_rovat(helyi_cs):
    if not helyi_cs:
        return "", ""
    helyek = list(dict.fromkeys(f["hely"] for f in FORRASOK if f.get("hely")))
    oszlopok = []
    for hely in helyek:
        cs = sorted([c for c in helyi_cs if FN[c["fo"]["forras"]].get("hely") == hely],
                    key=lambda c: c["ido"], reverse=True)
        if not cs:
            continue
        kiemelt = [c for c in cs if c["kor_ora"] < FRISS_ORA][:DOBOZ_HELYENKENT]
        tobbi = [c for c in cs if c not in kiemelt]
        friss_tobbi = [c for c in tobbi if c["kor_ora"] < FRISS_ORA][:HELYI_LISTA]
        regi = [c for c in tobbi if c not in friss_tobbi]
        oszlopok.append(f'''<div class="hely">
  <h3 class="helynev">{e(hely)}</h3>
  {"".join(doboz(c, lead=False) for c in kiemelt)}
  {f'<ul class="lista">{"".join(sor(c) for c in friss_tobbi)}</ul>' if friss_tobbi else ''}
  {korabban(regi, nyitva=not kiemelt)}
</div>''')
    html_ = f'''<section class="rovat" id="helyi" style="--r:{HELYI_SZIN}">
  {rovatfej("helyi", "Helyi", HELYI_SZIN, len(helyi_cs))}
  <div class="tartalom helyek">{"".join(oszlopok)}</div>
</section>'''
    return html_, f'<a href="#helyi" style="--r:{HELYI_SZIN}">Helyi</a>'


def oldal(orszagos, helyi_cs, hibak, idojaras=None, idokep=None, meresek=None):
    friss = [c for c in orszagos if c["kezdet_ora"] < FO_MAX_ORA]
    if len(friss) < FO_HIREK_SZAMA:           # hajnalban kevés a friss hír: legfeljebb 12 óráig visszanyúlunk
        friss = [c for c in orszagos if c["kezdet_ora"] < FO_TARTALEK_ORA]
    rangsor = sorted(friss, key=lambda c: c["pont"], reverse=True)
    # nyitóhír: a legtöbb lap által megírt képes hír a legszűkebb időablakból, ahol ilyen van
    nyito = None
    for hatar in (NYITO_MAX_ORA, 5, FO_MAX_ORA, FO_TARTALEK_ORA):
        jeloltek = [c for c in rangsor if c["kezdet_ora"] < hatar]
        nyito = next((c for c in jeloltek if c["fo"]["kep"]), None)
        if nyito:
            break
    if nyito is None and rangsor:
        nyito = rangsor[0]
    fo = ([nyito] if nyito else []) + [c for c in rangsor if c is not nyito][:FO_HIREK_SZAMA - 1]
    # az első öt (képes) helyre a rangsor képes hírei kerülnek, a többi a kép nélküli dobozokba
    # a 2–5. (képes) helyre a többi közül a képes hírek kerülnek, rangsor szerint
    if fo:
        maradek = fo[1:]
        kepes = [c for c in maradek if c["fo"]["kep"]][:4]
        fo = [fo[0]] + kepes + [c for c in maradek if c not in kepes]
    fo_id = {id(c) for c in fo}

    helyi_html, helyi_nav = helyi_rovat(helyi_cs)
    reszek, nav = [], []
    if HELYI_UTAN is None:
        reszek.append(helyi_html); nav.append(helyi_nav)
    # óránként új, de egy órán belül állandó sorsolás; két egymást követő rovat nem kap azonos
    # elrendezést, és színes kiemelés sem jön kétszer egymás után
    sors = random.Random(bp(MOST).strftime("%Y%m%d%H"))
    elozo_elr, elozo_szines = None, False
    for r in ROVATOK:
        # sorrend a dobozon látható cikk ideje szerint, hogy a kiírt időpontok is sorban legyenek
        cs = sorted([c for c in orszagos if c["rovat"] == r["id"] and id(c) not in fo_id],
                    key=lambda c: c["fo"]["ido"], reverse=True)
        if cs:
            dobozok = [c for c in cs if c["kor_ora"] < FRISS_ORA][:r.get("dobozok", DOBOZ_ROVATONKENT)]
            lista = [c for c in cs if c not in dobozok]
            kiem = len(dobozok) >= 3          # van-e kiemelt (dupla) kártya
            if kiem:
                # kiemelt: amelyikről a legtöbb lap írt, de a sztori korával csökkenő súllyal
                # (KIEMELT_ORA óránként egy lappal kevesebbet ér), hogy a reggeli téma este már ne vezessen
                k = max(dobozok, key=lambda c: (round(len(c["forrasok"]) - c["kezdet_ora"] / KIEMELT_ORA, 1),
                                                bool(c["fo"]["kep"]), c["pont"]))
                dobozok = [k] + [c for c in dobozok if c is not k]
            elr, szines = "bal-szeles", False
            if ROVAT_VALTOZATOS and kiem:
                # annyi doboz, hogy a három oszlopos rács tele legyen (2, 5, 8 vagy 11); a többi a listába kerül
                teli = max(x for x in (2, 5, 8, 11) if x <= len(dobozok))
                lista = dobozok[teli:] + lista
                dobozok = dobozok[:teli]
                lista.sort(key=lambda c: c["ido"], reverse=True)
                lehet = [x for x in ("bal-szeles", "jobb-szeles", "bal-magas", "jobb-magas") if x != elozo_elr]
                # magas kártyához legalább 5 doboz kell, hogy kitöltse a két sort
                if len(dobozok) < 5:
                    lehet = [x for x in lehet if x.endswith("szeles")] or ["bal-szeles"]
                elr = sors.choice(lehet)
                szines = (not elozo_szines) and sors.random() < SZINES_ESELY
            elozo_elr, elozo_szines = elr, szines
            extra = (" kiemelt-magas" if elr.endswith("magas") else "") + (" szines" if szines else "")
            nav.append(f'<a href="#{r["id"]}" style="--r:{r["szin"]}">{e(r["nev"])}</a>')
            reszek.append(f'''<section class="rovat" id="{r["id"]}" style="--r:{r["szin"]}">
  {rovatfej(r["id"], r["nev"], r["szin"], len(cs))}
  <div class="tartalom">
  {f'<div class="racs elr-{elr}">{"".join(doboz(c, kiemelt=(i == 0 and kiem), extra=(extra if i == 0 and kiem else "")) for i, c in enumerate(dobozok))}</div>' if dobozok else ''}
  {korabban(lista, nyitva=not dobozok)}
  </div>
</section>''')
        if r["id"] == HELYI_UTAN:
            reszek.append(helyi_html); nav.append(helyi_nav)

    ido_html = idojaras_panel(idojaras or [], idokep or [], meresek)
    if ido_html:
        reszek.append(ido_html)
        nav.append(f'<a href="#idojaras" style="--r:{IDOJARAS_SZIN}">Időjárás</a>')

    stilusok = list(LOGO)
    fix = FEJLEC_STILUS if FEJLEC_STILUS in LOGO else None
    kezdo = fix or "hirlap"
    logok = "".join(f'<span class="logo-v" data-stilus="{st}"{"" if st == kezdo else " hidden"}>{LOGO[st]}</span>'
                    for st in stilusok if fix is None or st == fix)
    fej_js = FEJ_JS.replace("__BEALLITAS__", json.dumps({
        "stilusok": stilusok, "fix_stilus": fix, "fix_szin": FEJLEC_SZIN,
        "szinek": NAPSZAK_SZINEK, "ikonok": IKON_SVG, "betuk": CIMBETUK}).replace("</", "<\\/"))

    most = bp(MOST)
    def szuro(f):
        return (f'<label class="szuro"><input type="checkbox" value="{f["id"]}" checked>'
                f'<i style="--c:{f["szin"]}"></i>{e(f["nev"])}</label>')
    orsz = "".join(szuro(f) for f in FORRASOK if not f.get("hely") and not f.get("panel"))
    helyi_sz = "".join(szuro(f) for f in FORRASOK if f.get("hely"))
    hiba_html = (f'<details class="hibak"><summary>{len(hibak)} forrás most nem volt elérhető</summary>'
                 f'<ul>{"".join(f"<li>{e(x)}</li>" for x in hibak)}</ul></details>') if hibak else ""

    return f'''<!DOCTYPE html>
<html lang="hu" data-frissites="{MOST.isoformat()}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script>try{{var t=localStorage.getItem("hp_tema");if(t==="vilagos"||t==="sotet")document.documentElement.dataset.tema=t;}}catch(e){{}}</script>
<meta name="robots" content="noindex"><link rel="manifest" href="manifest.webmanifest">
<meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes">
<title>{e(OLDAL_CIM)}, {most:%y-%m-%d}</title>
{IKON_LINKEK.get(kezdo, IKON_HIRLAP)}
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>

<style>{CSS}</style></head>
<body>
<header class="fej fej-{e(kezdo)}" id="fej">
  <div class="fejsor">
    <a class="logo-link" href="#" aria-label="{e(OLDAL_CIM)}">{logok if OLDAL_CIM == "A Kezdőlap" else f'<span class="nev">{e(OLDAL_CIM)}</span>'}</a>
    <div class="datum"><span class="nap">{most.year}. {HONAPOK[most.month-1]} {most.day}., {NAPOK[most.weekday()]}</span>
      <span class="frissitve">frissítve {most:%H:%M}</span></div>
    <div class="fej-gombok">
    <button type="button" class="tema-gomb" id="tema-gomb" aria-label="Megjelenés: automatikus" title="Megjelenés: automatikus (a készülék beállítását követi)">
      <svg class="t-auto" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/></svg>
      <svg class="t-vilagos" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/></svg>
      <svg class="t-sotet" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>
    </button>
    <details class="forrasmenu"><summary>Források</summary>
      <div class="panel">
        <p>A kikapcsolt lapok hírei elrejtődnek. A böngésző megjegyzi a beállítást.</p>
        <h4>Országos</h4><div class="szurok">{orsz}</div>
        {f'<h4>Helyi</h4><div class="szurok">{helyi_sz}</div>' if helyi_sz else ''}
      </div>
    </details>
    </div>
  </div>
</header>
<script>{fej_js}</script>
<nav class="rovatnav"><div class="nav-belso">{"".join(nav)}</div></nav>
<section class="elso elso-{e(FO_STILUS)}" aria-label="A legfontosabb hírek">
  <div class="elso-belso">{hiba_html}
  <div class="fo-racs">{"".join(fo_doboz(c, i) for i, c in enumerate(fo))}</div></div>
</section>
<main>{"".join(reszek)}</main>
<footer>A lap tetején azok a friss hírek állnak, amelyekről a legtöbb lap írt. Forrás: a lapok RSS-csatornái.
Időjárásadatok: <a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo.com</a> (CC BY 4.0).</footer>
<script>{JS}</script>
<script>{APP_JS}</script>
</body></html>'''


CSS = """
:root{
  --lap:#e9edf2;--felulet:#fff;--tinta:#16202f;--halvany:#5a6577;--vonal:#d3dae3;
  --sav:#16202f;--sav-szoveg:#f2f5f9;--sav-halvany:#a9b4c4;--sav-felulet:#212d3f;
  --cimbetu:"Bricolage Grotesque",ui-sans-serif,system-ui,sans-serif}
/* sötét mód: a rendszer beállítása szerint (data-tema="auto"), vagy kézzel választva */
@media (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]){
  --lap:#0e141d;--felulet:#18212d;--tinta:#e6ebf2;--halvany:#97a3b5;--vonal:#2a3545;
  --sav:#1c2636;--sav-felulet:#263347;color-scheme:dark}}
:root[data-tema="sotet"]{
  --lap:#0e141d;--felulet:#18212d;--tinta:#e6ebf2;--halvany:#97a3b5;--vonal:#2a3545;
  --sav:#1c2636;--sav-felulet:#263347;color-scheme:dark}
:root[data-tema="vilagos"]{color-scheme:light}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:3.5rem}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;background:var(--lap);color:var(--tinta);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:inherit;text-decoration:none}
h3 a:hover,.sor-cim a:hover{text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:3px}
a:focus-visible,button:focus-visible,summary:focus-visible,input:focus-visible{outline:2px solid #3b82f6;outline-offset:2px;border-radius:3px}
h1,h2,h3,h4{font-family:var(--cimbetu);font-stretch:var(--cim-nyujt,100%);margin:0}

/* fejléc: inverz, a háttérszín napszak szerint (a --fejszin értékét a böngésző állítja be) */
.fej{background:linear-gradient(120deg,var(--fejszin,#16202f),color-mix(in srgb,var(--fejszin,#16202f) 72%,#000));color:#fff}
/* minden fejlécnél: balra a dátum, középen a cím, jobbra a Források gomb */
.fejsor{max-width:1280px;margin:0 auto;padding:1.2rem 1.5rem 1.1rem;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:1.5rem}
.logo-link{display:block;color:#fff;line-height:0;grid-column:2;grid-row:1;justify-self:center}
.fejsor .datum{grid-column:1;grid-row:1;align-items:flex-start;padding:0}
.fejsor .fej-gombok{grid-column:3;grid-row:1;justify-self:end;display:flex;align-items:center;gap:.5rem}
.fejsor .forrasmenu{padding:0;margin:0}
.tema-gomb{width:2.1rem;height:2.1rem;border-radius:5px;display:grid;place-items:center;cursor:pointer;color:#fff;background:rgb(255 255 255/.12);border:1px solid rgb(255 255 255/.35);padding:0}
.tema-gomb:hover{background:rgb(255 255 255/.2)}
.tema-gomb svg{width:1.1rem;height:1.1rem;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;display:none}
:root:not([data-tema]) .t-auto,:root[data-tema="vilagos"] .t-vilagos,:root[data-tema="sotet"] .t-sotet{display:block}
.logo-v{display:block}.logo-v[hidden]{display:none}
.logo{height:4.6rem;width:auto;display:block}
.logo-hirlap{height:3.5rem}
.logo-nepszava{height:4.2rem}
.fej .nap{color:#fff}
.fej .frissitve{color:rgb(255 255 255/.72)}
.fej .forrasmenu summary{background:rgb(255 255 255/.12);border-color:rgb(255 255 255/.35);color:#fff}
.fej .forrasmenu summary::after{color:rgb(255 255 255/.7)}
.fej .forrasmenu summary:hover{background:rgb(255 255 255/.2)}
.fej .panel{color:var(--tinta)}
/* reformkori fejléc: a cím alatt rövid, vékony, középre zárt vonal, mint a Nemzeti Dal nyomtatványán */
.fej-nemzeti .logo-link{display:flex;flex-direction:column;align-items:center;gap:.5rem}
.fej-nemzeti .logo-link::after{content:"";width:5.5rem;height:2px;background:rgb(255 255 255/.75);border-radius:1px}
.fej-nemzeti .logo{height:3.3rem}
/* keskenyebb kijelzőn: a cím felül középen, alatta balra a dátum, jobbra a Források */
@media (max-width:900px){
  .fejsor{grid-template-columns:1fr auto;gap:.8rem 1rem}
  .logo-link{grid-column:1/-1;grid-row:1}
  .fejsor .datum{grid-column:1;grid-row:2}
  .fejsor .fej-gombok{grid-column:2;grid-row:2}
  .fej-nemzeti .logo-link{width:100%;max-width:17rem}
  .fej-nemzeti .logo{width:100%;height:auto}}
.nev{font:800 2.6rem/1 var(--cimbetu);letter-spacing:-.03em}
.datum{display:flex;flex-direction:column;align-items:flex-end;gap:.15rem;padding-bottom:.4rem}
.nap{font:650 1.05rem/1.2 var(--cimbetu);font-stretch:var(--cim-nyujt,100%)}
.frissitve{color:var(--halvany);font-size:.85rem}
/* menüsáv: a fejléc színének sötétebb árnyalata (kétszínű, újságos fejléc) */
.rovatnav{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--fejszin,#16202f) 68%,#000);box-shadow:0 1px 0 rgb(0 0 0/.25)}
.nav-belso{max-width:1280px;margin:0 auto;padding:.55rem 1.5rem;display:flex;align-items:center;gap:.4rem;overflow-x:auto;scrollbar-width:none}
/* görgetéskor a menüsáv elején megjelenik a lap neve kicsiben (az éppen kisorsolt fejléc stílusában) */
.nav-logo{display:flex;align-items:center;flex-shrink:0;color:#fff;max-width:0;opacity:0;overflow:hidden;margin-right:0;
  transition:max-width .3s ease,opacity .25s ease,margin .3s ease;line-height:0}
.nav-logo svg{height:1.45rem;width:auto;display:block}
.nav-logo .logo-nepszava{height:1.5rem}
.fej-elrejtve .nav-logo{max-width:14rem;opacity:1;margin-right:.7rem}
@media (prefers-reduced-motion:reduce){.nav-logo{transition:none}}
/* rovatgombok: enyhén lekerekített sarok, mint a Források gombon, elöl a rovat színes pöttyével */
.nav-belso a:not(.nav-logo){flex-shrink:0;font-size:.86rem;font-weight:600;padding:.22rem .7rem;border-radius:5px;color:#fff;background:rgb(255 255 255/.1);border:1px solid rgb(255 255 255/.24);display:flex;align-items:center;gap:.45rem;transition:background .15s}
.nav-belso a:not(.nav-logo)::before{content:"";width:.5rem;height:.5rem;border-radius:50%;background:var(--r);box-shadow:0 0 0 1.5px rgb(255 255 255/.9)}
.nav-belso a:not(.nav-logo):hover{background:rgb(255 255 255/.2);border-color:rgb(255 255 255/.45)}
.nav-belso a:not(.nav-logo):focus-visible{outline-color:#fff}
/* „Frissült a címlap” gomb (ha olvasás közben érkezik új változat) */
#uj-hirek{position:fixed;left:50%;bottom:1.2rem;transform:translateX(-50%);z-index:50;font:600 .9rem/1 inherit;font-family:inherit;
  padding:.7rem 1.1rem;border-radius:5px;border:0;cursor:pointer;color:#fff;background:var(--fejszin,#16202f);
  box-shadow:0 6px 20px rgb(0 0 0/.3)}
#uj-hirek:hover{filter:brightness(1.15)}

/* forrásválasztó */
.forrasmenu{position:relative;padding-bottom:.4rem;z-index:20}
.forrasmenu summary{list-style:none;cursor:pointer;font-size:.86rem;font-weight:600;padding:.3rem .8rem;border:1px solid var(--vonal);border-radius:5px;background:var(--felulet)}
.forrasmenu summary::-webkit-details-marker{display:none}
.forrasmenu summary::after{content:" ▾";color:var(--halvany)}
.panel{position:absolute;right:0;top:calc(100% + .2rem);width:min(22rem,90vw);background:var(--felulet);border:1px solid var(--vonal);border-radius:8px;padding:1rem;box-shadow:0 12px 32px rgb(15 23 42/.18)}
.panel p{margin:0 0 .8rem;font-size:.82rem;color:var(--halvany)}
.panel h4{font-size:.85rem;margin:.6rem 0 .4rem}
.szurok{display:flex;flex-wrap:wrap;gap:.35rem}
.szuro{font-size:.82rem;display:flex;align-items:center;gap:.35rem;padding:.2rem .55rem;border:1px solid var(--vonal);border-radius:99px;cursor:pointer}
.szuro input{position:absolute;opacity:0;pointer-events:none}
.szuro i{width:.6rem;height:.6rem;border-radius:50%;background:var(--c)}
.szuro:has(input:not(:checked)){opacity:.4;text-decoration:line-through}
.szuro:has(input:focus-visible){outline:2px solid #3b82f6}

/* nyitó sáv */
.elso{background:var(--sav);color:var(--sav-szoveg)}
/* visszafogottabb kiemelés: világos kártyák a lap hátterén, illetve a fejlécszín halvány árnyalatán */
.elso-arnyalt{background:color-mix(in srgb,var(--fejszin,#16202f) 9%,var(--lap));border-bottom:1px solid color-mix(in srgb,var(--fejszin,#16202f) 18%,var(--lap))}
.elso-vilagos{background:transparent}
.elso-arnyalt,.elso-vilagos{color:var(--tinta)}
.elso.elso-arnyalt h2,.elso.elso-vilagos h2{color:var(--tinta);font-weight:700}
.elso-arnyalt .fo-hir,.elso-vilagos .fo-hir{background:var(--felulet);border:1px solid var(--vonal)}
.elso-arnyalt .fo-hir .meta,.elso-vilagos .fo-hir .meta,.elso-arnyalt .fo-hir .lead,.elso-vilagos .fo-hir .lead,
.elso-arnyalt .fo-hir .tovabbi,.elso-vilagos .fo-hir .tovabbi{color:var(--halvany)}
.elso-arnyalt .fo-hir .forras,.elso-vilagos .fo-hir .forras{color:var(--c)}
.elso-arnyalt .fo-hir .lefed,.elso-vilagos .fo-hir .lefed{color:var(--tinta)}
.elso-arnyalt .fo-hir .lefed i,.elso-vilagos .fo-hir .lefed i{box-shadow:0 0 0 1.5px var(--felulet)}
.elso-arnyalt .kep,.elso-vilagos .kep{background:var(--vonal)}
.elso-vilagos .elso-belso{padding-top:1.4rem;padding-bottom:.6rem}
.elso-vilagos .nyito h3{font-size:clamp(1.45rem,2vw,1.9rem)}
.elso-belso{max-width:1280px;margin:0 auto;padding:1.6rem 1.5rem 2rem}
.elso h2{font-size:1.05rem;font-weight:600;color:var(--sav-halvany);margin-bottom:1rem}
.fo-racs{display:grid;grid-template-columns:repeat(6,1fr);gap:1.1rem}
.fo-hir{background:var(--sav-felulet);border-radius:6px;overflow:hidden;display:flex;flex-direction:column}
.fo-hir .szoveg{padding:1rem 1.1rem 1.1rem}
.fo-hir .meta{color:var(--sav-halvany)}
.fo-hir .forras{color:color-mix(in srgb,var(--c) 45%,#fff)}
.fo-hir .lead{color:var(--sav-halvany)}
.fo-hir .tovabbi{color:var(--sav-halvany)}
.fo-hir .lefed{color:var(--sav-szoveg)}
.nyito{grid-column:span 4;flex-direction:row}
.nyito .kep{flex:1.15;position:relative;min-height:15rem}
.nyito .kep img{position:absolute;inset:0}
.nyito .szoveg{flex:1;padding:1.3rem 1.4rem;display:flex;flex-direction:column;justify-content:center}
.nyito h3{font-size:clamp(1.35rem,1.9vw,1.75rem);line-height:1.15;letter-spacing:var(--cim-ter,-.02em);font-weight:var(--cim-suly-nagy,750)}
.nyito .lead{font-size:.95rem;-webkit-line-clamp:3}
.masod{grid-column:span 2}
.masod .kep{aspect-ratio:2/1}
.masod h3{font-size:1.15rem;line-height:1.22}
.masod .lead{-webkit-line-clamp:2}
.kis{grid-column:span 2}
.fo-hir.kis{flex-direction:row;align-items:flex-start}
.fo-hir.kis .szoveg{flex:1;min-width:0}
.fo-hir.kis .belyeg{margin:1rem 1.1rem 0 -.2rem}
.belyeg .kep-helyett .logo{width:80%;max-height:60%}
.kis h3{font-size:1.05rem;line-height:1.25;font-weight:650}
.kep{background:#2b384c;overflow:hidden}.kep img{width:100%;height:100%;object-fit:cover;display:block}

/* közös szövegelemek */
h3{font-weight:var(--cim-suly,650);letter-spacing:var(--cim-ter,-.01em)}
.meta{display:flex;gap:.55rem;align-items:baseline;font-size:.78rem;color:var(--halvany);margin-bottom:.3rem}
.forras{font-weight:700;color:var(--c)}
.lead{margin:.45rem 0 0;font-size:.9rem;line-height:1.45;color:var(--halvany);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.lefed{display:inline-flex;align-items:center;gap:.18rem;margin-top:.65rem;font-size:.76rem;font-weight:600}
.lefed i{width:.55rem;height:.55rem;border-radius:50%;background:var(--c);box-shadow:0 0 0 1.5px var(--felulet)}
.fo-hir .lefed i{box-shadow:0 0 0 1.5px var(--sav-felulet)}
.lefed i:last-of-type{margin-right:.35rem}
.tovabbi{margin:.35rem 0 0;font-size:.78rem;color:var(--halvany);display:flex;align-items:flex-end;gap:.6rem}
.tovabbi-lapok{flex:1;min-width:0}
/* a lapozógombok mindig a doboz jobb alsó sarkában, a dobozhoz rögzítve; a lapsor jobb oldalon helyet hagy nekik */
article[data-alt]{position:relative}
.lapozo{display:flex;gap:.3rem;position:absolute;right:1.1rem;bottom:1.1rem}
article[data-alt] .tovabbi-lapok{padding-right:4.6rem}
/* jobb oldali bélyegképnél a gombok a kép alatti üres sávba kerülnek, ott nem kell hely */
.doboz:has(.belyeg) .tovabbi-lapok,.fo-hir.kis:has(.belyeg) .tovabbi-lapok{padding-right:0}
.lapoz{width:1.9rem;height:1.9rem;border:1px solid var(--vonal);border-radius:5px;background:none;color:inherit;
  font:600 1.15rem/1 system-ui,sans-serif;cursor:pointer;display:grid;place-items:center;padding:0 0 .12rem}
.lapoz:hover{border-color:currentColor;color:var(--tinta,inherit)}
.lapoz:active{transform:scale(.94)}
.atnez{overflow:hidden}
.kep-helyett{position:absolute;inset:0;display:grid;place-items:center;
  background:color-mix(in srgb,var(--r,#64748b) 16%,var(--felulet,#fff));color:color-mix(in srgb,var(--r,#64748b) 70%,#000)}
.kep-helyett .logo{width:62%;max-width:11rem;height:auto;max-height:45%;opacity:.5}
.kep-helyett .logo .arnyek{display:none}
.belyeg,.kiemelt-kep,.kep{position:relative}.tovabbi a.mutatott{color:var(--tinta,inherit);text-decoration-color:currentColor;text-decoration-thickness:2px}
.tovabbi a{text-decoration:underline;text-decoration-color:color-mix(in srgb,currentColor 35%,transparent);text-underline-offset:2px}

/* rovatok */
main{max-width:1280px;margin:0 auto;padding:.5rem 1.5rem 3rem}
.rovat{margin-top:2.4rem}
.rovatfej{display:flex;align-items:center;gap:.8rem;margin-bottom:1rem}
.rovatfej::after{content:"";flex:1;height:2px;background:var(--r);opacity:.35;order:3}
.csuk{all:unset;cursor:pointer;font:var(--cim-suly-nagy,750) 1.25rem/1 var(--cimbetu);color:#fff;background:var(--r);padding:.42rem .9rem .48rem;border-radius:5px;letter-spacing:-.01em}
.csuk::after{content:"";display:inline-block;width:.42rem;height:.42rem;border-right:2px solid;border-bottom:2px solid;transform:translateY(-.2rem) rotate(45deg);margin-left:.6rem;opacity:.8}
.rovat.csukva .csuk::after{transform:translateY(-.05rem) rotate(-45deg)}
.rovat.csukva .tartalom{display:none}
.db{font-size:.8rem;color:var(--halvany);order:4}
.racs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.9rem;grid-auto-flow:row dense}
.doboz.kiemelt{grid-column:span 2;padding:1rem;gap:1.1rem}
/* változatos elrendezés: széles (2×1) vagy magas (1×2) kiemelt kártya, balra vagy jobbra */
.elr-jobb-szeles .doboz.kiemelt{grid-column:2/span 2;grid-row:1}
.elr-bal-magas .doboz.kiemelt{grid-column:1;grid-row:1/span 2}
.elr-jobb-magas .doboz.kiemelt{grid-column:3;grid-row:1/span 2}
.doboz.kiemelt-magas{flex-direction:column}
.doboz.kiemelt-magas .kiemelt-kep{width:100%;aspect-ratio:16/10}
.doboz.kiemelt-magas .lead{-webkit-line-clamp:6}
/* színes kiemelés: a rovat színe a háttér, fehér betűkkel */
.doboz.szines{background:var(--r);border-color:var(--r);color:#fff}
.doboz.szines .meta,.doboz.szines .lead,.doboz.szines .tovabbi{color:rgb(255 255 255/.82)}
.doboz.szines .forras,.doboz.szines .lefed{color:#fff}
.szines .tovabbi a.mutatott{color:#fff}
.doboz.szines .lefed i{box-shadow:0 0 0 1.5px var(--r)}
.doboz.szines .kiemelt-kep{background:rgb(0 0 0/.15)}
.doboz.kiemelt h3{font-size:1.32rem;line-height:1.22;letter-spacing:-.015em}
.doboz.kiemelt .lead{-webkit-line-clamp:4}
.kiemelt-kep{order:-1;width:42%;aspect-ratio:16/10;flex-shrink:0;border-radius:4px;overflow:hidden;background:var(--vonal)}
.kiemelt-kep img{width:100%;height:100%;object-fit:cover;display:block}
.doboz{background:var(--felulet);border:1px solid var(--vonal);border-radius:6px;padding:.9rem 1rem;display:flex;gap:.9rem;align-items:flex-start}
.doboz .szoveg{flex:1;min-width:0}
.doboz h3{font-size:1.04rem;line-height:1.28}
.belyeg{width:84px;height:84px;flex-shrink:0;border-radius:3px;overflow:hidden;background:var(--vonal)}
.belyeg img{width:100%;height:100%;object-fit:cover;display:block}
details{margin-top:.9rem}
summary{cursor:pointer;color:var(--halvany);font-size:.88rem;font-weight:600;width:fit-content}
.nap-csoport{margin:.55rem 0 0 1rem}
.nap-csoport summary{font-size:.82rem;font-weight:600;color:var(--tinta);opacity:.8}
.nap-csoport summary:hover{opacity:1}
.nap-csoport .lista time{min-width:3rem}
summary:hover{color:var(--tinta)}
.lista{list-style:none;margin:.5rem 0 0;padding:0;columns:2 440px;column-gap:2.5rem}
.lista li{break-inside:avoid;padding:.45rem 0;border-bottom:1px solid var(--vonal);font-size:.93rem;display:flex;gap:.8rem;align-items:baseline;line-height:1.35}
.cikkek-gomb{font:inherit;font-size:.78rem;color:var(--halvany,#888);background:none;border:1px solid var(--vonal);border-radius:3px;padding:0 .4rem;margin-left:.3rem;cursor:pointer;white-space:nowrap;vertical-align:.05em}
.cikkek-gomb:hover{color:inherit;border-color:currentColor}
.cikkek-gomb .nyil{display:inline-block;transition:transform .15s}
.cikkek-gomb[aria-expanded="true"] .nyil{transform:rotate(90deg)}
.lista .cikkek{list-style:none;margin:.45rem 0 .1rem;padding:0 0 0 .7rem;border-left:2px solid var(--vonal)}
.lista .cikkek li{display:flex;gap:.6rem;padding:.22rem 0;border:0;font-size:.85rem;line-height:1.3}
.lista .cikkek time{flex:0 0 auto;min-width:4.9rem;font-size:.74rem;white-space:nowrap}
.lista .cikkek a{color:inherit;text-decoration:none}
.lista .cikkek a:hover{text-decoration:underline;text-underline-offset:3px}
.lista time{color:var(--halvany);font-size:.76rem;min-width:5.4rem;flex-shrink:0;font-variant-numeric:tabular-nums}
.lista .forras{font-size:.76rem;margin-left:.25rem;white-space:nowrap}

/* időjárás */
.ido-racs{display:grid;grid-template-columns:1fr;gap:1.2rem}
.ido-hirsor{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1.2rem;align-items:start}
.ido-itt{border-color:color-mix(in srgb,var(--r) 45%,var(--vonal));box-shadow:inset 0 3px 0 var(--r)}
.ido-itt[hidden]{display:none}
.ido-itt-jel{color:var(--r);font-size:.8em}
.ido-gomb{margin-top:.8rem;font:600 .88rem system-ui,sans-serif;color:#fff;background:var(--r);border:0;border-radius:5px;padding:.55rem .9rem;cursor:pointer}
.ido-gomb:hover{filter:brightness(1.1)}
.ido-helyek{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:.9rem}
.ido-hely{background:var(--felulet);border:1px solid var(--vonal);border-radius:6px;padding:1rem 1.1rem}
.ido-hely h3{font-size:1.1rem;font-weight:700}
.ido-most{display:flex;align-items:center;gap:.6rem;margin:.5rem 0 .8rem}
.ido-fok{font:750 2.5rem/1 var(--cimbetu);letter-spacing:-.03em}
.ido-leiras{font-size:.88rem;line-height:1.35}.ido-leiras small{color:var(--halvany);font-size:.78rem}
.ido-napsor{display:flex;flex-wrap:wrap;gap:.2rem 1rem;font-size:.8rem;color:var(--halvany);margin:.6rem 0 0;padding:.55rem 0 0;border-top:1px solid var(--vonal)}
.ido-napsor b{color:var(--szoveg,inherit);font-weight:600}
.ido-alcim{font-size:.72rem;font-weight:650;letter-spacing:.04em;text-transform:uppercase;color:var(--halvany);margin:.75rem 0 .35rem}
.ido-orak{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(6,1fr);gap:.2rem;text-align:center}
.ido-orak li{display:flex;flex-direction:column;align-items:center;gap:.1rem;font-size:.82rem}
.ido-elo-forras{font-size:.7rem;color:var(--halvany);margin:.6rem 0 0;text-align:right}
.ido-napok{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(5,1fr);gap:.2rem;text-align:center}
.ido-napok li{display:flex;flex-direction:column;align-items:center;gap:.1rem;font-size:.82rem}
.ido-nap{font-size:.74rem;font-weight:600;color:var(--halvany)}
.ido-max{font-weight:700}.ido-min{color:var(--halvany);font-size:.76rem}
.ido-csap{color:#2563eb;font-size:.7rem;min-height:1em}
.ido-nincs{color:var(--halvany);font-size:.88rem}
.ido-pontossag{font:500 .72rem/1 inherit;font-family:inherit;color:var(--halvany);margin-left:.45rem}
.ido-ujra{margin-left:auto;float:right;border:1px solid var(--vonal);background:none;color:var(--halvany);border-radius:5px;
  width:1.7rem;height:1.7rem;cursor:pointer;font-size:.95rem;line-height:1}
.ido-ujra:hover{color:inherit;border-color:currentColor}
.ido-mert{font-size:.8rem;color:var(--halvany);margin:.25rem 0 0}.ido-mert b{color:var(--szoveg,inherit)}
.ido-meres{grid-column:span 2}
.ido-meres h3 small{font-size:.78rem;font-weight:500;color:var(--halvany)}
/* két oszlop, előbb a bal telik meg fentről lefelé, így az ABC-sorrend egyben olvasható */
.meres-lista{list-style:none;margin:.5rem 0 0;padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:repeat(var(--sorok),auto);grid-auto-flow:column;column-gap:1.4rem}
.meres-lista li{display:grid;grid-template-columns:minmax(0,1fr) 28px 3rem;grid-template-rows:auto auto;column-gap:.5rem;align-items:center;
  padding:.45rem 0;border-bottom:1px solid var(--vonal)}
.meres-nev{font-weight:650;font-size:.95rem;line-height:1.2}
.meres-nev small{display:block;font-weight:400;font-size:.72rem;color:var(--halvany)}
.meres-fok{font:750 1.45rem/1 var(--cimbetu);text-align:right;letter-spacing:-.02em}
.meres-reszlet{grid-column:1/-1;font-size:.74rem;color:var(--halvany);min-height:0}
.meres-reszlet:empty{display:none}
.ido-ikon-ures{display:inline-block}
.meres-forras{font-size:.72rem;color:var(--halvany);margin:.6rem 0 0}.meres-forras a{text-decoration:underline}
@media (max-width:760px){.ido-meres{grid-column:auto}.meres-lista{grid-template-columns:1fr;grid-template-rows:none;grid-auto-flow:row}}
.ido-ikon{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}
.i-nap{stroke:#f59e0b}.i-hold{stroke:#94a3b8;fill:rgb(148 163 184/.18)}.i-felho{stroke:var(--halvany)}.i-csepp{stroke:#3b82f6}.i-ho{stroke:#38bdf8;stroke-width:2.6}.i-villam{stroke:#eab308}
.ido-hirek{background:color-mix(in srgb,var(--r) 6%,var(--lap));border-radius:8px;padding:1rem 1.1rem}
.ido-hirek h3{font-size:1.05rem;font-weight:700;color:var(--r);margin-bottom:.4rem}
.ido-hirek ul{list-style:none;margin:0;padding:0}
.ido-hirek li{padding:.45rem 0;border-bottom:1px solid var(--vonal);font-size:.9rem;line-height:1.35}
.ido-hirek li:last-child{border-bottom:0}
.ido-hirek time{display:block;color:var(--halvany);font-size:.74rem}
.ido-hirek a:hover{text-decoration:underline}

@media (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]) .ido-hirek h3,:root:not([data-tema="vilagos"]) .helynev{color:color-mix(in srgb,var(--r) 45%,#fff)}}
:root[data-tema="sotet"] .ido-hirek h3,:root[data-tema="sotet"] .helynev{color:color-mix(in srgb,var(--r) 45%,#fff)}

/* helyi rovat */
.helyek{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:1.4rem}
.hely{background:color-mix(in srgb,var(--r) 6%,var(--lap));border-radius:8px;padding:1rem 1.1rem 1.1rem;display:flex;flex-direction:column;gap:.7rem}
.helynev{font-size:1.5rem;font-weight:800;letter-spacing:-.02em;color:var(--r)}
.hely .lista{columns:1;margin:0}
.hely details{margin-top:0}

/* elérhetetlen forrás: halvány, kis sor a kiemelt hírek sávjának tetején */
.elso .hibak{margin:-.7rem 0 .55rem;padding:0;background:none;border:0;font-size:.72rem;color:var(--halvany);text-align:right}
.elso .hibak summary{font-size:inherit;font-weight:500;color:inherit;opacity:.85;width:auto;display:inline-block;list-style:none}
.elso .hibak summary::-webkit-details-marker{display:none}
.elso .hibak summary::before{content:"⚠ ";opacity:.8}
.elso .hibak summary:hover{opacity:1;text-decoration:underline}
.elso .hibak ul{margin:.3rem 0 0;padding:0;list-style:none;opacity:.9}
.elso-sotet .hibak{color:var(--sav-halvany)}
.rejtett{display:none!important}
footer{text-align:center;color:var(--halvany);font-size:.8rem;padding:1rem 1.5rem 2.5rem;max-width:60ch;margin:0 auto}

@media (max-width:1000px){
  .fo-racs{grid-template-columns:repeat(2,1fr)}
  .nyito{grid-column:span 2;grid-row:auto;flex-direction:column}
  .nyito .kep{aspect-ratio:2/1;min-height:0;flex:none}
  .masod,.kis{grid-column:span 1}
  .racs{grid-template-columns:repeat(2,minmax(0,1fr))}
  .elr-bal-szeles .doboz.kiemelt,.elr-jobb-szeles .doboz.kiemelt{grid-column:1/-1;grid-row:auto}
  .elr-bal-magas .doboz.kiemelt{grid-column:1;grid-row:1/span 2}
  .elr-jobb-magas .doboz.kiemelt{grid-column:2;grid-row:1/span 2}}
@media (max-width:640px){
  .fejsor{padding:.9rem 1rem .8rem}
  .logo{height:3.4rem}
  .logo-nepszava{width:100%;max-width:19rem;height:auto}
  .nap{font-size:.95rem}
  .forrasmenu{padding:0}
  .nav-belso,main,.elso-belso{padding-left:1rem;padding-right:1rem}
  .fo-racs{grid-template-columns:1fr}.nyito,.masod,.kis{grid-column:auto}
  .nyito .szoveg{padding:1.1rem}
  .belyeg{width:64px;height:64px}
  .racs{grid-template-columns:1fr}.racs .doboz.kiemelt{grid-column:auto;grid-row:auto;flex-direction:column}
  .kiemelt-kep{width:100%}
  .lista .cikkek li{flex-direction:column;gap:0}
  .lista time{min-width:4.6rem}
  .helyek{grid-template-columns:1fr}}
"""

JS = """
const tarol={get(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}},
             set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
const rejtettForras=new Set(tarol.get('hp_rejtett',[]));
const csukott=new Set(tarol.get('hp_csukott',[]));
const esc=t=>String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);
const lat=(el,v)=>{if(el)el.style.display=v?'':'none';};
// ha egy többlapos doboz fő cikkének lapja ki van kapcsolva, egy másik lap cikke kerül a helyére,
// a pöttyök és az „Erről írt még” sor pedig csak a bekapcsolt lapokat mutatja
function csere(el){
  const alt=JSON.parse(el.dataset.alt), l=alt.filter(a=>!rejtettForras.has(a.f));
  if(!l.length) return;
  const u=l[0];
  const fs=el.querySelector('.meta .forras, .sor-cim .forras'); if(fs){fs.textContent=u.n;fs.style.setProperty('--c',u.s);}
  const t=el.querySelector('time'); if(t) t.textContent=u.t;
  const a=el.querySelector('h3 a, .sor-cim a'); if(a){a.textContent=u.c;a.href=u.l;}
  const ld=el.querySelector('.lead'); if(ld){ld.textContent=u.d||'';lat(ld,u.d);}
  const img=el.querySelector('.kep img, .belyeg img, .kiemelt-kep img');
  if(img){if(u.k&&img.getAttribute('src')!==u.k)img.src=u.k;lat(img.parentNode,u.k);}
  const lf=el.querySelector('.lefed');
  if(lf){lat(lf,l.length>1);lf.title=l.map(x=>x.n).join(', ');
    lf.innerHTML=l.map(x=>'<i style="--c:'+esc(x.s)+'"></i>').join('')+l.length+' lap írt róla';}
  const tv=el.querySelector('.tovabbi-lapok');
  if(tv){lat(tv.parentNode,l.length>1);
    tv.innerHTML='Erről írt: '+l.map((x,k)=>'<a href="'+esc(x.l)+'" target="_blank" rel="noopener" title="'+esc(x.c)+'"'+(k?'':' class="mutatott"')+'>'+esc(x.n)+'</a>').join(', ');}
}
// lapozás a többlapos dobozokban: a ‹ › gombokkal a doboz a következő/előző lap cikkét mutatja
// (lap neve, idő, cím, bevezető, kép); a doboz mérete nem változik, a cím és a bevezető legfeljebb
// annyi sort foglal, amennyit az eredeti
const lathatoLapok=el=>{const a=JSON.parse(el.dataset.alt);const l=a.filter(x=>!rejtettForras.has(x.f));return l.length?l:a;};
function mutat(el,u){
  if(!el.dataset.rogzitve){
    // a dobozt magát nem rögzítjük (az a rácsban a szomszédait is átméretezhetné), csak a belső részeit:
    // a cím és a bevezető legfeljebb az eredeti sorszámot foglalhatja, a fejléc és a lapsor magassága fix
    el.dataset.rogzitve='1';
    el.querySelectorAll('.meta').forEach(x=>{ x.style.height=x.getBoundingClientRect().height+'px'; });
    el.querySelectorAll('h3, .lead').forEach(x=>{
      const sm=parseFloat(getComputedStyle(x).lineHeight)||x.offsetHeight;
      const mag=x.getBoundingClientRect().height, sor=Math.max(1,Math.round(mag/sm));
      Object.assign(x.style,{display:'-webkit-box',webkitBoxOrient:'vertical',webkitLineClamp:String(sor),overflow:'hidden',height:mag+'px'});
    });
  }
  const fs=el.querySelector('.meta .forras'); if(fs){fs.textContent=u.n;fs.style.setProperty('--c',u.s);}
  const t=el.querySelector('.meta time'); if(t) t.textContent=u.t;
  const h=el.querySelector('h3 a'); if(h){h.textContent=u.c;h.href=u.l;}
  const ld=el.querySelector('.lead'); if(ld) ld.textContent=u.d||'';
  // kép nélküli cikknél a kép helyén a rovat színe és diszkréten a lap aktuális fejléce
  const img=el.querySelector('.kep img, .belyeg img, .kiemelt-kep img');
  if(img){
    const tarto=img.parentNode, hh=tarto.querySelector('.kep-helyett');
    if(u.k){ img.src=u.k; img.style.visibility=''; if(hh) hh.remove(); }
    else { img.style.visibility='hidden';
      if(!hh){ const d=document.createElement('div'); d.className='kep-helyett';
        const lg=[...document.querySelectorAll('#fej .logo')].find(l=>l.getClientRects().length); if(lg) d.appendChild(lg.cloneNode(true)); tarto.appendChild(d); } }
  }
  el.querySelectorAll('.tovabbi-lapok a').forEach(x=>x.classList.toggle('mutatott',x.getAttribute('href')===u.l));
}
document.addEventListener('click',ev=>{
  const b=ev.target.closest&&ev.target.closest('.lapoz'); if(!b) return;
  const el=b.closest('article[data-alt]'); if(!el) return;
  const l=lathatoLapok(el), most=l.findIndex(x=>x.l===(el.querySelector('h3 a')||{}).getAttribute?.('href'));
  const k=((most<0?0:most)+(+b.dataset.irany)+l.length)%l.length;
  mutat(el,l[k]);
});
document.addEventListener('click',ev=>{
  const b=ev.target.closest&&ev.target.closest('.cikkek-gomb'); if(!b) return;
  const ul=b.nextElementSibling, nyit=ul.hidden; ul.hidden=!nyit; b.setAttribute('aria-expanded',nyit);
});
// a kép nélküli bélyegek helyére a lap aktuális fejléce
addEventListener('load',()=>{ const lg=[...document.querySelectorAll('#fej .logo')].find(l=>l.getClientRects().length);
  if(lg) document.querySelectorAll('.kep-helyett:empty').forEach(d=>d.appendChild(lg.cloneNode(true))); });
function cikkSzam(){
  document.querySelectorAll('.cikkek').forEach(ul=>{
    const db=ul.querySelectorAll('li:not(.rejtett)').length; const g=ul.previousElementSibling;
    g.querySelector('.db').textContent=db; g.hidden=db<2; if(db<2){ul.hidden=true;g.setAttribute('aria-expanded',false);}
  });
}
function szur(){
  document.querySelectorAll('[data-srcs]').forEach(el=>{
    const s=el.dataset.srcs.split(' ');const rejt=s.every(x=>rejtettForras.has(x));
    el.classList.toggle('rejtett',rejt);
    if(!rejt&&el.dataset.alt&&(el.dataset.csere||s.some(x=>rejtettForras.has(x)))){csere(el);el.dataset.csere='1';}
  });
  cikkSzam();
}
document.querySelectorAll('.szuro input').forEach(i=>{
  i.checked=!rejtettForras.has(i.value);
  i.addEventListener('change',()=>{i.checked?rejtettForras.delete(i.value):rejtettForras.add(i.value);
    tarol.set('hp_rejtett',[...rejtettForras]);szur();});
});
document.querySelectorAll('.rovat').forEach(r=>{
  const b=r.querySelector('.csuk');
  if(csukott.has(r.id)){r.classList.add('csukva');b.setAttribute('aria-expanded','false');}
  b.addEventListener('click',()=>{const cs=r.classList.toggle('csukva');b.setAttribute('aria-expanded',!cs);
    cs?csukott.add(r.id):csukott.delete(r.id);tarol.set('hp_csukott',[...csukott]);});
});
const temaGomb=document.getElementById('tema-gomb');
const TEMAK=[[null,'automatikus (a készülék beállítását követi)'],['vilagos','világos'],['sotet','sötét']];
function temaFelirat(){const t=document.documentElement.dataset.tema||null;const f=TEMAK.find(x=>x[0]===t)[1];
  temaGomb.title='Megjelenés: '+f+' – kattints a váltáshoz';temaGomb.setAttribute('aria-label','Megjelenés: '+f);}
if(temaGomb){temaFelirat();temaGomb.addEventListener('click',()=>{
  const t=document.documentElement.dataset.tema||null;const kov=TEMAK[(TEMAK.findIndex(x=>x[0]===t)+1)%3][0];
  if(kov)document.documentElement.dataset.tema=kov;else delete document.documentElement.dataset.tema;
  try{kov?localStorage.setItem('hp_tema',kov):localStorage.removeItem('hp_tema');}catch(e){}
  temaFelirat();});}
(function(){
  const nav=document.querySelector('.nav-belso'), fej=document.getElementById('fej');
  const logo=document.querySelector('#fej .logo-v:not([hidden]) svg') || document.querySelector('#fej .logo-link svg');
  if(!nav||!fej||!logo) return;
  const a=document.createElement('a'); a.className='nav-logo'; a.href='#'; a.setAttribute('aria-label','Vissza az oldal tetejére');
  a.tabIndex=-1; a.appendChild(logo.cloneNode(true)); nav.insertBefore(a, nav.firstChild);
  if('IntersectionObserver' in window){
    new IntersectionObserver(ev=>{const rejt=!ev[0].isIntersecting; document.body.classList.toggle('fej-elrejtve',rejt); a.tabIndex=rejt?0:-1;})
      .observe(fej);
  }
})();
const menu=document.querySelector('.forrasmenu');
document.addEventListener('click',ev=>{if(menu.open&&!menu.contains(ev.target))menu.open=false;});
szur();
"""


# Népszava-szerű fejléc a lap 1910-es évekbeli címe nyomán: vastag, rövid talpú plakátbetű, függőlegesen megnyújtva
# (Alfa Slab One betűtípus, SIL Open Font License)
LOGO_NEPSZAVA = '''<svg class="logo logo-nepszava" xmlns="http://www.w3.org/2000/svg" viewBox="-8 -1326 7330 1365" role="img" aria-label="A Kezdőlap"><path fill="currentColor" d="M754 -213.2H794V0.0H402V-213.2H462L446 -317.2H310L294 -213.2H354V0.0H12V-213.2H52L221 -1011.4H585ZM332 -455.0H424L383 -713.7H373ZM1915.0 -213.2H1965.0V0.0H1692.0L1542.0 -403.0L1496.0 -353.6V-213.2H1544.0V0.0H1166.0V-213.2H1216.0V-798.2H1166.0V-1011.4H1544.0V-798.2H1496.0V-626.6L1651.0 -798.2H1588.0V-1011.4H1960.0V-798.2H1910.0L1743.0 -618.8ZM2021.0 0.0V-213.2H2071.0V-798.2H2021.0V-1011.4H2696.0V-618.8H2472.0V-798.2H2351.0V-592.8H2439.0V-444.6H2351.0V-213.2H2472.0V-418.6H2696.0V0.0ZM3210.0 -319.8H3409.0V0.0H2748.0V-187.2L3056.0 -798.2H2952.0V-691.6H2753.0V-1011.4H3393.0V-824.2L3086.0 -213.2H3210.0ZM3463.0 -213.2H3513.0V-796.9H3463.0V-1011.4H3869.0Q4036.0 -1011.4 4134.5 -892.4Q4233.0 -773.5 4233.0 -505.7Q4233.0 -237.9 4134.5 -118.9Q4036.0 0.0 3869.0 0.0H3463.0ZM3851.0 -213.2Q3885.0 -213.2 3902.0 -236.6Q3919.0 -260.0 3919.0 -309.4V-702.0Q3919.0 -798.2 3851.0 -798.2H3793.0V-213.2ZM4281.0 -505.7Q4281.0 -780.0 4380.5 -904.8Q4480.0 -1029.6 4663.0 -1029.6Q4846.0 -1029.6 4945.5 -904.8Q5045.0 -780.0 5045.0 -505.7Q5045.0 -231.4 4945.5 -106.6Q4846.0 18.2 4663.0 18.2Q4480.0 18.2 4380.5 -106.6Q4281.0 -231.4 4281.0 -505.7ZM4731.0 -309.4V-702.0Q4731.0 -751.4 4714.0 -774.8Q4697.0 -798.2 4663.0 -798.2Q4629.0 -798.2 4612.0 -774.8Q4595.0 -751.4 4595.0 -702.0V-309.4Q4595.0 -258.7 4612.0 -235.9Q4629.0 -213.2 4663.0 -213.2Q4697.0 -213.2 4714.0 -236.6Q4731.0 -260.0 4731.0 -309.4ZM4474.0 -1089.4V-1108.9L4523.0 -1306.5H4707.0V-1271.4L4594.0 -1089.4ZM4702.0 -1089.4V-1108.9L4751.0 -1306.5H4935.0V-1271.4L4822.0 -1089.4ZM5099.0 0.0V-213.2H5149.0V-798.2H5099.0V-1011.4H5521.0V-798.2H5429.0V-213.2H5519.0V-332.8H5718.0V0.0ZM6506.0 -213.2H6546.0V0.0H6154.0V-213.2H6214.0L6198.0 -317.2H6062.0L6046.0 -213.2H6106.0V0.0H5764.0V-213.2H5804.0L5973.0 -1011.4H6337.0ZM6084.0 -455.0H6176.0L6135.0 -713.7H6125.0ZM6598.0 -213.2H6648.0V-798.2H6598.0V-1011.4H7037.0Q7166.0 -1011.4 7234.0 -939.2Q7302.0 -867.1 7302.0 -705.9Q7302.0 -544.7 7234.0 -472.5Q7166.0 -400.4 7037.0 -400.4H6928.0V-213.2H7024.0V0.0H6598.0ZM6962.0 -585.0Q7020.0 -585.0 7020.0 -668.2V-715.0Q7020.0 -757.9 7005.5 -778.0Q6991.0 -798.2 6962.0 -798.2H6928.0V-585.0Z"/></svg>'''

# Böngészőfül- és kezdőképernyő-ikon (az „AK” ugyanabból a betűtípusból)
IKON_NEPSZAVA = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Cpath fill=%22%23f2f5f9%22 d=%22M29.67 40.54H30.84V46.77H19.39V40.54H21.14L20.67 37.50H16.70L16.23 40.54H17.99V46.77H8.0V40.54H9.16L14.10 17.22H24.74ZM17.34 33.48H20.03L18.83 25.92H18.54ZM54.53 40.54H56.0V46.77H48.02L43.64 35.00L42.29 36.44V40.54H43.70V46.77H32.65V40.54H34.11V23.45H32.65V17.22H43.70V23.45H42.29V28.46L46.82 23.45H44.98V17.22H55.85V23.45H54.39L49.51 28.69Z%22/%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAD/UlEQVR4AcRWa2hURxT+5maTtDbVbJtdq9hk1TTpJrl3Q1vaIG1K27S2+VH6wJYoaUWqYIOhLS1taSuNCNIHLQZNaxtsSaGKCL41RgOKCIoI0aj4iGajICSuGvGHJpK9zpnrDHPv3RV8ZYc9Z75zzsx8352Ze1kDWsuPVOaHI2ZTKBLbHo5Y/eFIzL4/ZvU7a5pNxAGtGRKHisy3sm37KGAsZMAbAAvjvjUWdtY0FhIHccmlhYBwJDadMWMrY5goCw+qJw7iCvEHJg5jXKEZtG38Q8HomtE6dlLZY0a2wT7mqiaMLjnAOSfmBAIfGQysChlqxM0FIIMCUEWXsChDG0C0RSSAQEprbJgLr1kVZa6xtW/W+MbUz/wAVc8/58vPnVPvmktBWgFTp0TQ+CkX4LF33q6lecpqp3MBnjH1M2eg6oVnffPn3YkAyyxXJDqwTPcO6LW7wWl3wKqIplyvPPo0srKyVO1kzxmMjIyomMCJkz04d+48Lg9eoVDZoW7+oVWRA9IKiFkVzgiPz84OIFr6lMo2L/8L165dVzGBX5e24EIigbGP5lEobPFPv+GT+Z8LrLuUAgIBIilR4zo6dylMwHsMNv+UUl5ajF/Uluaf1U798vsy/Nu2WpZdfUoBZdFS0JPKkTs6d0soepMTCJDGLVn8Ax7KzRVV2okVrW0Cp3IpBeivWrzvLI6fOOWaGzPdx8MYc9Vzc3JUHCoowIvT0n/rUgqIaTd98MpVhAoeVwsSKJ4aUU9IsfcIKKfbrLr39dCFUwqwtFew0irHyhVL3ZMMA6YmkjH3DvSc7nWNr3mlGhOeGO/KycAnIC/vEdBHSA44xV+z/QcOIpG4JFOip4smAHfeHVjwxbf82Hp4xfkxxlD34XtO4PE+Afr509jvflyCWbPnY8PmdgqV3e4iDg0PY/3GLWosgboZ76q3gmJpPgFN338ta6L/5ssFeKbSwuuvVYtYupdfmoaaV6vR2DAPY8Y8LNOi/+qzBqzftE1g6YLBfKxdtVKGqvcJmDy5UBUJVPIPUjA4DoVPToLe6KjGh8MoKZ7ie7LSkmIkLl7Cnr379Ckwy6OumAKfAEreiTHmvoA0l4FR59sFkfQ4n4Dmlr+h27I/WnEm3ufKyXr3kWPY0r7DV2v7f42gae/o9NVEQXN+Acu5AN1aWtHbexbNeu4WPswFbG3f6av9d0vA0NCwr6ZxC8gF2P0CZcTZ/YYNdCFDjbj5DrCMCQBYl3ED7E+uZBCj3IiTuI3BeFec2ZgzyvxIIjmbuPkRAAN9h9bZdrKW/684/6CFEAdxXYx3byAuIYDAhb7ubTcY4/9Ek4v49nQA9gDl79Wc+faAs2ZyEXEQl5MHbgIAAP//iONPkwAAAAZJREFUAwDcy3LU260ZVwAAAABJRU5ErkJggg==">'
    '<link rel="apple-touch-icon" href="data:image/png;base64,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">'
    '<meta name="theme-color" content="#16202f"><meta name="apple-mobile-web-app-title" content="A Kezdőlap">'
)

# Pesti Hírlap-szerű fejléc: keskeny, vastag talpatlan kiskapitális (Oswald betűtípus, SIL Open Font License)
LOGO_HIRLAP = '''<svg class="logo logo-hirlap" xmlns="http://www.w3.org/2000/svg" viewBox="11 -850 4236 869" role="img" aria-label="A Kezdőlap"><path fill="currentColor" d="M21 0 176 -810H370L523 0H364L336 -175H212L183 0ZM228 -279H319L274 -600ZM882.0 0V-810H1054.0V-474L1196.0 -810H1363.0L1212.0 -445L1372.0 0H1198.0L1077.0 -360L1054.0 -322V0ZM1439.8 0.0V-631.8H1719.8V-539.7H1573.9V-377.5H1686.2V-284.7H1573.9V-91.2H1721.3V0.0ZM1773.9 0.0V-81.9L1931.5 -540.5H1781.7V-631.8H2064.1V-557.7L1903.4 -91.2H2065.6V0.0ZM2145.5 0.0V-631.8H2303.0Q2384.9 -631.8 2430.2 -611.1Q2475.4 -590.4 2494.1 -546.3Q2512.8 -502.3 2512.8 -432.9V-203.5Q2512.8 -132.6 2494.1 -87.7Q2475.4 -42.9 2430.6 -21.4Q2385.7 0.0 2305.4 0.0ZM2279.6 -92.8H2304.6Q2338.1 -92.8 2353.0 -103.3Q2367.8 -113.8 2371.3 -134.5Q2374.8 -155.2 2374.8 -185.6V-453.1Q2374.8 -483.6 2370.5 -502.3Q2366.2 -521.0 2351.4 -529.2Q2336.6 -537.4 2303.8 -537.4H2279.6ZM2789.3 9.3Q2719.1 9.3 2677.7 -15.6Q2636.4 -40.5 2618.8 -87.3Q2601.3 -134.1 2601.3 -198.9V-434.4Q2601.3 -499.2 2619.2 -545.2Q2637.2 -591.2 2678.1 -615.8Q2719.1 -640.3 2789.3 -640.3Q2860.2 -640.3 2901.2 -615.8Q2942.2 -591.2 2960.1 -545.2Q2978.0 -499.2 2978.0 -434.4V-198.9Q2978.0 -134.1 2960.1 -87.3Q2942.2 -40.5 2901.2 -15.6Q2860.2 9.3 2789.3 9.3ZM2789.3 -86.5Q2811.9 -86.5 2823.2 -97.8Q2834.5 -109.2 2837.6 -127.5Q2840.7 -145.8 2840.7 -165.3V-466.4Q2840.7 -487.5 2837.6 -505.0Q2834.5 -522.6 2823.2 -533.9Q2811.9 -545.2 2789.3 -545.2Q2767.4 -545.2 2756.1 -533.9Q2744.8 -522.6 2741.7 -505.0Q2738.6 -487.5 2738.6 -466.4V-165.3Q2738.6 -145.8 2741.7 -127.5Q2744.8 -109.2 2755.3 -97.8Q2765.9 -86.5 2789.3 -86.5ZM2822.8 -675.4 2872.0 -840.0H2978.8L2886.0 -675.4ZM2687.9 -675.4 2733.1 -840.0H2832.2L2751.1 -675.4ZM3075.1 0.0V-631.8H3209.2V-91.2H3356.6V0.0ZM3398.3 0.0 3519.2 -631.8H3670.5L3789.8 0.0H3665.8L3644.0 -136.5H3547.2L3524.6 0.0ZM3559.7 -217.6H3630.7L3595.6 -468.0ZM3865.8 0.0V-631.8H4072.5Q4129.4 -631.8 4165.7 -610.3Q4202.0 -588.9 4219.5 -548.3Q4237.1 -507.7 4237.1 -449.2Q4237.1 -378.3 4213.7 -340.0Q4190.3 -301.8 4149.7 -286.6Q4109.2 -271.4 4057.7 -271.4H3999.9V0.0ZM3999.9 -365.0H4046.0Q4073.3 -365.0 4087.3 -374.4Q4101.4 -383.7 4106.4 -402.4Q4111.5 -421.2 4111.5 -451.6Q4111.5 -478.1 4107.6 -496.8Q4103.7 -515.5 4089.7 -526.5Q4075.6 -537.4 4046.0 -537.4H3999.9Z"/></svg>'''

IKON_HIRLAP = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Cpath fill=%22%23f2f5f9%22 d=%22M9.0 48.87 15.45 15.12H23.54L29.91 48.87H23.29L22.12 41.58H16.95L15.75 48.87ZM17.62 37.25H21.41L19.54 23.87ZM34.58 48.87V15.12H41.75V29.12L47.66 15.12H54.62L48.33 30.33L55.0 48.87H47.75L42.70 33.87L41.75 35.45V48.87Z%22/%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAEN0lEQVR4AcRWW0xUVxRd54YhQC2I7YxCYRgwwlzoQGvSxjZWY2xURIiavrTFElPTtCamP036ZaI//eiPsekrfUhLFFvTh1SpFJu2xkSNUTG+UYYZQHRgUL/UBDLXs8/M7Hvh3hlNVLiZfc66a+999przmtFgeab7npvu8QW2uH01HR5fdcTjqzEejVVH4mMGtlANWB4tid0lgVqXYZwDtM0CWAIIDx7ZIzzxMbXNVINqJYdWAjy+mqVCaO1CoDDpeFw91aBabvmFqYaW5w3kGwZ20MvkmvZdblHlDM2liXelqoLJLQ7ImoWZGRnrNAExD1P0UG0pAFMoAPNoE5akmoDZZT5s2riBbe2bqx1DG1Ys45imdW9xjLe4iHkax+N2sy8BSkhAAtu7xYsWYNOHG9g++fgje5BkGlYs5ZimRlPAe+vfYX7NG6swHI3K6PGftAIq9Ypx0TnZWfB6i8ZxqV6mTXsCK+uXs3vX7l9hyOPGRAKkFVDlHy+Acir95dTd115bXQ8STIGxWAyte/4gaLOUArKzslBa6rUl6A8ooHHN65zb+c//iEZH+N0KUgrwV8zhuN7ePsb+cpNn0mAkpxlYMP8llHiLmWz9+TfGE0FKAdb1P3zkKOfpfgcB7I2DxrXmt786eA2HjxyLOxzaNALMte6+HMTgtesqvbBgFvLychV2ap7Kz8eihfPZ1bLzF8ZOIKUA61qH+/rR1z/A+ek2Yk5ONscRuNjdQ11KcxQghIBeYc5AuG8A/QODPIhVnCKFah2butpXHfkk6SigfE4ZXK4MFTM2NqamPxzuV+/UVFrE0Tssm/D2nbtobtmtaGpqlyxGZqaLoKM5CtAt539YHp8XX5iLbMvU+tMcxRs3b6CldQ8XowvJuifYkQCOAqosN2DBrJnYueMrbHx/fSIFsM4QkwlAlx3N1oVL3QkGWNVQx3gicBSgW44abcBjx0+AjJaDBtA0DbPLSgnaTG4fxe1v71Q9NQtfeRl5uU8StJmjgOrAsxy47fNv8HbTB8p6gmHmq3Q/Yzhswrb9Heyn/VRXK/9mMmMCm4DiZwr5DqewK8EQdcqCvSZOdxQpmO6NcxcuEVS2sn6Z6ic2NgG6bh4/Cr7SE6ROWTBkzoB1mZTToWk/YC7D3OdrMNNj/6NtExAduYntX36r7NPPtmF0dIyH7jz4n+LJf/DfQ8y37etg3noE9/55gHnKcT89g3OSQAowIskX6k+eOo3tX0gB0r5v3kUU29nzF9n3w4+mr22fLCTjKa/5J/MOuB4Z4njyUT4PpoAR0eQd0qXwFDRUW86AmDIBgOjSRiG+lkpuYZIfqkm1tVuhrpAwYF5zkyQkhlgT1ZZLAAyFT/9uGLHl8ho1f/IekxCqQbVGQmf2UgklgMBw+Mxfo0JUAbGtcnr+Bowh4h/W4vnGUHzM2FaqQbXiPHAPAAD//1CliUEAAAAGSURBVAMAMwVs1BrBzKwAAAAASUVORK5CYII=">'
    '<link rel="apple-touch-icon" href="data:image/png;base64,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">'
    '<meta name="theme-color" content="#16202f"><meta name="apple-mobile-web-app-title" content="A Kezdőlap">'
)

# Reformkori fejléc a Nemzeti Dal címének mintájára: hízott antikva (Abril Fatface, SIL Open Font License)
LOGO_NEMZETI = '''<svg class="logo logo-nemzeti" xmlns="http://www.w3.org/2000/svg" viewBox="-4 -766 5267 1014" role="img" aria-label="A Kezdőlap"><path fill="currentColor" d="M432 -19 359 -235H161L84 -19H159V0H6V-19H53L308 -706H390L642 -19H688V0H361V-19ZM264 -519 170 -257H351ZM909.0 -700H1214.0V-681H1159.0V-365L1167.0 -367L1449.0 -681H1370.0V-700H1545.0V-681H1485.0L1313.0 -489L1553.0 -19H1607.0V0H1286.0V-19H1333.0L1168.0 -338L1159.0 -336V-19H1214.0V0H909.0V-19H964.0V-681H909.0ZM1895.0 -486Q2122.0 -486 2113.0 -258H1831.0V-219Q1831.0 -121 1856.0 -71.0Q1881.0 -21 1943.0 -21Q2059.0 -21 2095.0 -145L2113.0 -142Q2094.0 -70 2044.5 -30.0Q1995.0 10 1896.0 10Q1767.0 10 1702.5 -56.5Q1638.0 -123 1638.0 -241.5Q1638.0 -360 1710.5 -423.0Q1783.0 -486 1895.0 -486ZM1832.0 -278H1935.0V-327Q1935.0 -408 1925.5 -437.5Q1916.0 -467 1887.5 -467.0Q1859.0 -467 1845.5 -435.5Q1832.0 -404 1832.0 -327ZM2619.0 -476V-458L2382.0 -18H2395.0Q2479.0 -18 2527.0 -62.0Q2575.0 -106 2602.0 -205H2616.0L2600.0 0H2180.0V-15L2421.0 -458H2410.0Q2309.0 -458 2269.5 -425.0Q2230.0 -392 2199.0 -284H2186.0L2199.0 -476ZM3006.0 -136V-354Q3006.0 -397 2989.0 -427.0Q2972.0 -457 2939.0 -457.0Q2906.0 -457 2894.0 -413.0Q2882.0 -369 2882.0 -259V-207Q2882.0 -102 2893.0 -67.5Q2904.0 -33 2933.5 -33.0Q2963.0 -33 2984.5 -59.0Q3006.0 -85 3006.0 -136ZM3019.0 0 3008.0 -70Q2974.0 10 2870.0 10Q2784.0 10 2736.5 -52.0Q2689.0 -114 2689.0 -237Q2689.0 -486 2887.0 -486Q2975.0 -486 3003.0 -437V-732H2942.0V-750H3192.0V-18H3241.0V0ZM3595.0 -197V-282Q3595.0 -379 3584.5 -423.5Q3574.0 -468 3536.0 -468Q3514.0 -468 3500.5 -455.5Q3487.0 -443 3481.0 -414Q3472.0 -369 3472.0 -277V-199Q3472.0 -90 3478.5 -68.0Q3485.0 -46 3492.0 -32Q3503.0 -8 3535.0 -8Q3574.0 -8 3586.0 -53Q3595.0 -85 3595.0 -197ZM3787.0 -245Q3787.0 10 3533.0 10Q3408.0 10 3344.0 -54.0Q3280.0 -118 3280.0 -237.5Q3280.0 -357 3348.5 -421.5Q3417.0 -486 3541.0 -486.0Q3665.0 -486 3726.0 -426.5Q3787.0 -367 3787.0 -245ZM3578.0 -547 3641.0 -738 3688.0 -748Q3719.0 -755 3735.5 -740.5Q3752.0 -726 3752.0 -701.5Q3752.0 -677 3723.0 -651L3589.0 -540ZM3430.0 -546 3483.0 -740 3529.0 -753Q3559.0 -761 3576.5 -747.5Q3594.0 -734 3595.0 -709.5Q3596.0 -685 3569.0 -658L3441.0 -540ZM3816.0 -750H4061.0V-18H4117.0V0H3820.0V-18H3875.0V-732H3816.0ZM4381.0 -235H4410.0V-312Q4410.0 -407 4399.5 -439.5Q4389.0 -472 4359.0 -472Q4341.0 -472 4326.5 -462.5Q4312.0 -453 4312.0 -436.5Q4312.0 -420 4318.0 -403H4343.0Q4351.0 -382 4351.0 -352.5Q4351.0 -323 4326.0 -301.5Q4301.0 -280 4263.0 -280Q4177.0 -280 4177.0 -363Q4177.0 -486 4386.0 -486Q4505.0 -486 4550.5 -445.5Q4596.0 -405 4596.0 -297V-94Q4596.0 -42 4625.0 -42Q4659.0 -42 4665.0 -145L4680.0 -144Q4676.0 -56 4647.0 -23.0Q4618.0 10 4552.0 10Q4432.0 10 4414.0 -64Q4401.0 -26 4375.0 -8.0Q4349.0 10 4299.0 10Q4151.0 10 4151.0 -111Q4151.0 -182 4207.5 -208.5Q4264.0 -235 4381.0 -235ZM4410.0 -126V-219H4403.0Q4341.0 -219 4341.0 -128V-112Q4341.0 -60 4346.5 -45.0Q4352.0 -30 4367.5 -30.0Q4383.0 -30 4396.5 -55.5Q4410.0 -81 4410.0 -126ZM4936.0 -305V-122Q4936.0 -79 4953.0 -49.0Q4970.0 -19 5003.0 -19.0Q5036.0 -19 5048.0 -63.0Q5060.0 -107 5060.0 -217V-269Q5060.0 -372 5050.0 -407.5Q5040.0 -443 5011.0 -443.0Q4982.0 -443 4959.0 -405.5Q4936.0 -368 4936.0 -305ZM4934.0 -476V-397Q4973.0 -486 5072.0 -486Q5158.0 -486 5205.5 -424.0Q5253.0 -362 5253.0 -236.0Q5253.0 -110 5201.5 -50.0Q5150.0 10 5047.0 10Q4999.0 10 4975.0 -2.0Q4951.0 -14 4936.0 -39V220H5065.0V238H4695.0V220H4750.0V-458H4697.0V-476Z"/></svg>'''

IKON_NEMZETI = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Cpath fill=%22%23f2f5f9%22 d=%22M22.09 43.83 19.50 36.18H12.49L9.76 43.83H12.42V44.50H7.0V43.83H8.66L17.70 19.49H20.60L29.53 43.83H31.16V44.50H19.57V43.83ZM16.14 26.11 12.81 35.40H19.22ZM32.26 19.70H43.07V20.37H41.12V31.57L41.40 31.50L51.40 20.37H48.60V19.70H54.80V20.37H52.67L46.58 27.18L55.08 43.83H57.0V44.50H45.62V43.83H47.29L41.44 32.53L41.12 32.60V43.83H43.07V44.50H32.26V43.83H34.21V20.37H32.26Z%22/%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAADxUlEQVR4AcRWWUhUURj+zk2t1NKkmVJHZzJo0UaDFi2khzZL0aYsCoq2h6geAiF6FOqlherBoqKkiOi1khbTLKMFWtDcsZLSdIwZFazUSHNu57/jPV2duWOQzhzmv+c7//n/8333bHckaEqkZWGk0WI9arCklBgtyQ6jJUUeG0t2uMe0HiUOaIqkYoPZuj5YlusBKZ8BawFmxJgVZnSPKeUTB3GpQysCjJaUDMakB4whRu0Yr5o4iMvAX5g4pIh46zRZxjVq+NekwqmmxCgpWGK7uKpo/5IDnDMmJChop8TA0hCgQtxcAAIoAGm0Cc0BmgCiNZMAAgGzUQWEhk5G6tJFiIqa5lVkSnKS0k8xqsWbYkXs3DmzRb9h+nThV8GoAnJt2bh57SIO7tuj5njU588eV2IobvvWXI/+Hds2Izws1MNPjlEFbLJlURw2bsiCJHmGV9fUY+D3gBJDD4ezA1/a7AQVW5CYiCtXb+Bx+XN0dHYqPu3Dc0RNb0KCGdak+YonYuoUrEhfpuB/faxeuQKtdjtq6hp0U3wKSF+eisqqGpFsy8kUWA/I/FqlvvRlqTDHm/DmbSU1dU1XAE13SHAISh49Ecn0RrQphWMIqKRDTSTOm4sLBaewIXu96tKtdQUsT12CFy9f4WFpuUieNHEiMjPWiLY3kDDLjOuF50BCSchoy6YrgI5X44cm2Nu/oq6hUXDZctYJ7A0ETZiAyMgI0ZV3aL/A3oBXAWH8yMTGRovz29T0WeSmLV2M6JkzRHskeP/xE4ruPRRu2sS+ZkESkRqwbs0qnDxdgNdvKhQ7caYA2nW2+Vhb/pVDwfnLcLlcYsTDeQcFHgm8Cpg5w4Bv33+I2M7OLrzTnIYtuTmijwBjjCrFGGPKPVB0t1hp08PXXvAQEBcbA4fT88IoKfu7GePjTOJ+IAJvdu5CIQYHB0WX3ix4CMjOykBxaZlIVIH2NJDPlu37TqDb8FbRfQpVTG8Whgkw8bev4FO9gN9+tBGVTP4IDw+DyRSD7XsOCCt98hRmcxzotOQdyRf+svJnoI8RHcM7fBm0Ob/6+0EcfEjxGyagzd6ubDrafL29fSKop6dX+KlPtZaWVtC3QG2rNb19X99PrznEIQbmYJgA3vb7jwuQHX5nFYSyQ5KBKgSoEDefARYwAQCrkgbALnEl3fBzIU7ilrqbq5qZjL1+5ocLrt3EzZcAcLZU35ZlVyb/L9E+3kKIg7i6mmuLiEsRQKCjpbZ4gLEkwHWMT08pIDvJ/7/mzped7jFdx4iDuNx+4A8AAAD//4L9EaUAAAAGSURBVAMAH5xj1IV/Q5oAAAAASUVORK5CYII=">'
    '<link rel="apple-touch-icon" href="data:image/png;base64,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">'
    '<meta name="theme-color" content="#16202f"><meta name="apple-mobile-web-app-title" content="A Kezdőlap">'
)

IKON_SVG = {"hirlap": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" d=\"M9.0 48.87 15.45 15.12H23.54L29.91 48.87H23.29L22.12 41.58H16.95L15.75 48.87ZM17.62 37.25H21.41L19.54 23.87ZM34.58 48.87V15.12H41.75V29.12L47.66 15.12H54.62L48.33 30.33L55.0 48.87H47.75L42.70 33.87L41.75 35.45V48.87Z\"/></svg>", "nemzeti": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" d=\"M22.09 43.83 19.50 36.18H12.49L9.76 43.83H12.42V44.50H7.0V43.83H8.66L17.70 19.49H20.60L29.53 43.83H31.16V44.50H19.57V43.83ZM16.14 26.11 12.81 35.40H19.22ZM32.26 19.70H43.07V20.37H41.12V31.57L41.40 31.50L51.40 20.37H48.60V19.70H54.80V20.37H52.67L46.58 27.18L55.08 43.83H57.0V44.50H45.62V43.83H47.29L41.44 32.53L41.12 32.60V43.83H43.07V44.50H32.26V43.83H34.21V20.37H32.26Z\"/></svg>", "nepszava": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" d=\"M29.67 40.54H30.84V46.77H19.39V40.54H21.14L20.67 37.50H16.70L16.23 40.54H17.99V46.77H8.0V40.54H9.16L14.10 17.22H24.74ZM17.34 33.48H20.03L18.83 25.92H18.54ZM54.53 40.54H56.0V46.77H48.02L43.64 35.00L42.29 36.44V40.54H43.70V46.77H32.65V40.54H34.11V23.45H32.65V17.22H43.70V23.45H42.29V28.46L46.82 23.45H44.98V17.22H55.85V23.45H54.39L49.51 28.69Z\"/></svg>"}

FEJ_JS = r"""(function(){
  var B = __BEALLITAS__;
  var fej = document.getElementById('fej'); if (!fej) return;
  // napszak szerinti háttérszín (a néző saját órája szerint)
  var ora = new Date().getHours(), szin = B.szinek[B.szinek.length - 1][2];
  for (var i = 0; i < B.szinek.length; i++) {
    var s = B.szinek[i]; if (ora >= s[0] && ora < s[1]) { szin = s[2]; break; }
  }
  if (B.fix_szin) szin = B.fix_szin;
  document.documentElement.style.setProperty('--fejszin', szin);
  // fejlécstílus: fix, vagy megnyitásonként véletlenszerű (nyitott lapon újratöltéskor marad)
  var st = B.fix_stilus;
  if (!st) {
    try { st = sessionStorage.getItem('hp_stilus'); } catch (e) {}
    if (B.stilusok.indexOf(st) < 0) {
      var elozo = null; try { elozo = localStorage.getItem('hp_utolso'); } catch (e) {}
      var lehet = B.stilusok.filter(function (x) { return x !== elozo; });
      st = lehet[Math.floor(Math.random() * lehet.length)];
      try { sessionStorage.setItem('hp_stilus', st); localStorage.setItem('hp_utolso', st); } catch (e) {}
    }
  }
  fej.className = 'fej fej-' + st;
  // a fejléchez illő címbetű: csak ezt az egyet töltjük le
  var bt = B.betuk && B.betuk[st];
  if (bt) {
    var gy = document.documentElement.style;
    gy.setProperty('--cimbetu', '"' + bt.csalad + '",' + (bt.tartalek || 'ui-sans-serif,system-ui,sans-serif'));
    gy.setProperty('--cim-suly', bt.suly); gy.setProperty('--cim-suly-nagy', bt.nagy); gy.setProperty('--cim-ter', bt.ter); gy.setProperty('--cim-nyujt', bt.nyujt || '100%');
    var lk = document.createElement('link'); lk.rel = 'stylesheet';
    lk.href = 'https://fonts.googleapis.com/css2?family=' + bt.url + '&display=swap';
    document.head.appendChild(lk);
  }
  fej.querySelectorAll('.logo-v').forEach(function (el) { el.hidden = el.dataset.stilus !== st; });
  // böngészőikon és mobil címsor a fejléc színében
  var ikon = B.ikonok[st];
  if (ikon) {
    var href = 'data:image/svg+xml,' + encodeURIComponent(ikon.replace('#16202f', szin));
    document.querySelectorAll('link[rel="icon"]').forEach(function (l) { l.remove(); });
    var l = document.createElement('link'); l.rel = 'icon'; l.type = 'image/svg+xml'; l.href = href;
    document.head.appendChild(l);
  }
  var tc = document.querySelector('meta[name="theme-color"]'); if (tc) tc.content = szin;
})();
"""

LOGO = {"hirlap": LOGO_HIRLAP, "nemzeti": LOGO_NEMZETI, "nepszava": LOGO_NEPSZAVA}
IKON_LINKEK = {"hirlap": IKON_HIRLAP, "nemzeti": IKON_NEMZETI, "nepszava": IKON_NEPSZAVA}


# ── Fő program ─────────────────────────────────────────────────────

# ── Telepíthető webalkalmazás (PWA) ───────────────────────────────
# A program az index.html mellé kiírja az app leíróját, ikonjait és a háttérszkriptet (service worker);
# így a lap Windowson (Edge/Chrome) és Androidon „alkalmazásként” telepíthető, és térerő nélkül is
# megnyílik a legutóbb letöltött címlap.

APP_IKONOK = {"ikon-hirlap-192.png": "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", "ikon-hirlap-512.png": "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", "ikon-hirlap-maskable.png": "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"}

SW_JS = r"""// A Kezdőlap – háttérszkript: a címlapot mindig a hálózatról kéri, de elmenti, hogy offline is megnyíljon
const TAR = 'kezdolap-v2';
const ALAP = ['./', 'manifest.webmanifest', 'ikon-hirlap-192.png', 'ikon-hirlap-512.png', 'ikon-hirlap-maskable.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(TAR).then(c => c.addAll(ALAP)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(k => Promise.all(k.filter(n => n !== TAR).map(n => caches.delete(n))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const u = new URL(r.url);
  if (u.origin === location.origin) {
    if (u.pathname.endsWith('frissites.json')) return;            // mindig a hálózatról
    if (r.mode === 'navigate' || u.pathname.endsWith('/') || u.pathname.endsWith('.html')) {
      e.respondWith(fetch(r).then(v => {                          // előbb a friss változat…
        const m = v.clone(); caches.open(TAR).then(c => c.put('./', m)); return v;
      }).catch(() => caches.match('./')));                         // …offline az elmentett
      return;
    }
    e.respondWith(caches.match(r).then(m => m || fetch(r)));
    return;
  }
  if (u.hostname === 'fonts.googleapis.com' || u.hostname === 'fonts.gstatic.com') {
    e.respondWith(caches.open(TAR).then(c => c.match(r).then(m => {
      const h = fetch(r).then(v => { if (v.ok) c.put(r, v.clone()); return v; }).catch(() => m);
      return m || h;
    })));
  }
});
"""

APP_JS = r"""(function(){
  var sajat = document.documentElement.dataset.frissites, betoltve = Date.now(), elrejtve = 0;
  if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) {
    navigator.serviceWorker.register('sw.js').catch(function(){});
  }
  // új címlap esetén: ha a lap tetején vagy, vagy régóta nem nézted, magától frissül;
  // ha éppen olvasol lejjebb, csak egy gomb jelzi, hogy ne ugorjon el az oldal
  function gomb(){
    if (document.getElementById('uj-hirek')) return;
    var g = document.createElement('button'); g.id = 'uj-hirek'; g.type = 'button';
    g.textContent = 'Frissült a címlap – megnézem ↻';
    g.addEventListener('click', function(){ window.scrollTo(0, 0); location.reload(); });
    document.body.appendChild(g);
  }
  function dont(regota){
    if (window.scrollY < 150 || regota) location.reload(); else gomb();
  }
  function ellenoriz(regota){
    if (location.protocol === 'file:') {            // helyi futtatásnál a fájlt 10 percenként újratölti
      if (Date.now() - betoltve > 10 * 60000) dont(regota);
      return;
    }
    fetch('frissites.json?t=' + Date.now(), { cache: 'no-store' })
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(d){ if (d && d.ido && d.ido !== sajat) dont(regota); })
      .catch(function(){});
  }
  // asztali gépen appként futtatva a cikkek az app ablakában nyílnak meg (Androidon a rendszer
  // amúgy is beágyazott olvasóablakot használ, böngészőben pedig marad az új fül);
  // visszalépéskor a lap ugyanoda görget, ahol a cikkre kattintottál
  var app = window.matchMedia && (matchMedia('(display-mode: standalone)').matches || matchMedia('(display-mode: minimal-ui)').matches);
  if (app && !/Android/i.test(navigator.userAgent)) {
    document.addEventListener('click', function(ev){
      var a = ev.target.closest && ev.target.closest('a[target="_blank"]');
      if (!a || ev.ctrlKey || ev.shiftKey || ev.metaKey || ev.button !== 0) return;
      ev.preventDefault();
      try { sessionStorage.setItem('hp_gorgetes', String(window.scrollY)); } catch (e) {}
      location.href = a.href;
    });
    try {
      var g = sessionStorage.getItem('hp_gorgetes');
      if (g !== null) {
        sessionStorage.removeItem('hp_gorgetes');
        if (history.scrollRestoration) history.scrollRestoration = 'manual';
        window.addEventListener('load', function(){ window.scrollTo(0, +g); });
      }
    } catch (e) {}
    // ha a böngésző a memóriából hozza vissza a lapot (ott a görgetés amúgy is megmarad)
    window.addEventListener('pageshow', function(ev){
      if (ev.persisted) { try { sessionStorage.removeItem('hp_gorgetes'); } catch (e) {} }
    });
  }
  setInterval(function(){ if (!document.hidden) ellenoriz(false); }, 5 * 60000);
  document.addEventListener('visibilitychange', function(){
    if (document.hidden) { elrejtve = Date.now(); return; }
    ellenoriz(elrejtve && Date.now() - elrejtve > 20 * 60000);
  });
})();
"""


def pwa_fajlok(mappa, most):
    import base64
    mappa.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": OLDAL_CIM, "short_name": OLDAL_CIM.replace("A ", "", 1) if len(OLDAL_CIM) > 12 else OLDAL_CIM,
        "description": "Rovatokba rendezett személyes hírcímlap a magyar lapok RSS-csatornáiból",
        "lang": "hu", "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#16202f", "theme_color": "#16202f",
        "icons": [
            {"src": "ikon-hirlap-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "ikon-hirlap-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "ikon-hirlap-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    (mappa / "manifest.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    (mappa / "sw.js").write_text(SW_JS, "utf-8")
    for nev, adat in APP_IKONOK.items():
        (mappa / nev).write_bytes(base64.b64decode(adat))
    (mappa / "frissites.json").write_text(json.dumps({"ido": most}), "utf-8")


def nem_cikk(cim):
    """Igaz, ha a cím csak egy név vagy címke (szerzői, címke- vagy rovatoldal), nem cikkcím:
    legfeljebb négy szó, mind nagybetűs (vagy „dr.”), szám és mondatírásjel nélkül."""
    szavak = cim.split()
    if re.match(r"null\b", cim, re.I):   # rovatoldal, pl. „null: Külföld”
        return True
    if not szavak or len(szavak) > 4 or re.search(r'[0-9?!,;:–—"„”«»()]', cim):
        return False
    return all(sz[0].isupper() or sz.lower() in ("dr.", "id.", "ifj.", "von", "de") for sz in szavak)


def main():
    ap = argparse.ArgumentParser(description="Személyre szabott hírportál generálása RSS-ből")
    ap.add_argument("-o", "--kimenet", default="index.html")
    ap.add_argument("--allapot", default=ALLAPOT_FAJL)
    a = ap.parse_args()

    allapot_ut = Path(a.allapot)
    tarolt = {}
    if allapot_ut.exists():
        try:
            tarolt = {h["id"]: h for h in json.loads(allapot_ut.read_text("utf-8"))}
        except (ValueError, KeyError):
            tarolt = {}

    uj, hibak = mind_letolt()
    for h in uj:
        if h["id"] in tarolt:
            regi = tarolt[h["id"]]
            h["ido"] = min(h["ido"], regi["ido"])
            if not h["kep"] and regi.get("kep"):
                h["kep"] = regi["kep"]
            if regi.get("kep_probalva"):
                h["kep_probalva"] = True
        tarolt[h["id"]] = h
    oldalkepek(uj)
    hatar = (MOST - timedelta(hours=MEGORZES_ORA)).isoformat()
    ervenyes = [h for h in tarolt.values() if h["ido"] >= hatar and h["forras"] in FN
                # a Google Hírek tartalékból mentett cikkek törlése, ha a lapnak már nincs tartaléka
                and not ("news.google.com" in h["link"] and not FN[h["forras"]].get("tartalek"))]
    for h in ervenyes:
        # Google-tartalék: „G7: Cím” → cím előtag nélkül + rovat, így a lap saját változatával is összepárosul
        m = re.match(r"^([^:]{1,15}):\s+(.+)$", h["cim"])
        elotagok = FN[h["forras"]].get("tartalek_elotag", {})
        if m and "news.google.com" in h["link"] and m.group(1) in elotagok:
            h["cim"], h["elotag_rovat"] = m.group(2), elotagok[m.group(1)]
    # a Google Hírek néha szerzői vagy címkeoldalt is cikknek ad ki (pl. „Dr. Kovács Asztrik”)
    ervenyes = [h for h in ervenyes if not ("news.google.com" in h["link"] and nem_cikk(h["cim"]))]
    # ugyanannak a lapnak azonos című cikkei közül csak egy maradjon (amelyiknek van képe/leadje)
    egyedi = {}
    for h in sorted(ervenyes, key=lambda h: (bool(h["kep"]), len(h["lead"])), reverse=True):
        egyedi.setdefault((h["forras"], h["cim"].strip().lower()), h)
    ervenyes = list(egyedi.values())
    for h in ervenyes:
        # a mentett hírekre is (pl. egy korábban tévesen frissnek látszó, újra kiadott cikkre)
        h["ido"] = webcim_korlat(h["link"], datetime.fromisoformat(h["ido"])).isoformat()
        h["rovat"] = rovat(h)

    allapot_ut.write_text(json.dumps(ervenyes, ensure_ascii=False), "utf-8")
    orszagos = osszevon([h for h in ervenyes if h["rovat"] not in ("helyi", "idojaras")])
    helyi_cs = osszevon([h for h in ervenyes if h["rovat"] == "helyi"])
    Path(a.kimenet).parent.mkdir(parents=True, exist_ok=True)
    idokep = [h for h in ervenyes if h["rovat"] == "idojaras"]
    meresek = meres_letolt()
    # ha vannak friss mérések, a három rögzített Open-Meteo-kártya helyett a mért lista jelenik meg
    helyek = [] if meresek else idojaras_letolt()
    Path(a.kimenet).write_text(oldal(orszagos, helyi_cs, hibak, helyek, idokep, meresek), "utf-8")
    pwa_fajlok(Path(a.kimenet).parent, MOST.isoformat())
    print(f"{len(uj)} letöltött, {len(ervenyes)} megőrzött hír → {a.kimenet}")
    for x in hibak:
        print("  hiba:", x)


if __name__ == "__main__":
    main()
