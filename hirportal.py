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
    {"id": "mhang",  "nev": "Magyar Hang",   "szin": "#0d9488", "feeds": ["https://hang.hu/rss/"],
     # csak ezek a rovatok (a webcím első tagja) mennek a „Háttér”-be: hetilapos riportok, interjúk, publicisztikák
     "hatter_rovatok": {"magyar-hang-plusz": "Plusz", "vendegoldal": "Vendégoldal"}},
    {"id": "atlatszo", "nev": "Átlátszó",    "szin": "#65a30d", "feeds": ["https://atlatszo.hu/feed/"],
     "hatter": True},            # a „Háttér” rovatba kerül, kimarad a hírversenyből és az összevonásból
    {"id": "valasz", "nev": "Válasz Online", "szin": "#a16207", "feeds": ["https://www.valaszonline.hu/feed/"],
     "hatter": True},
    {"id": "nszo",   "nev": "Népszabadság",  "szin": "#991b1b", "feeds": ["https://nszo.hu/feed/"],
     "kep_oldalrol": True},   # a hírfolyamban többnyire nincs kép: az új cikkeknél a cikkoldalról (og:image) olvassuk ki
    {"id": "infostart", "nev": "Infostart", "szin": "#4f46e5", "feeds": ["https://infostart.hu/24ora/rss/"]},
    {"id": "euronews", "nev": "Euronews",    "szin": "#0369a1",
     "feeds": ["https://hu.euronews.com/rss?level=theme&name=news"], "alap": "kulfold",
     "alap_elony": 2},   # a kulcsszavas pontozásban ennyi előnnyel indul az alapértelmezett rovat

    # Helyi lapok: ezek nem kerülnek a fő hírek és az országos rovatok közé,
    # hanem a „Helyi” rovatban jelennek meg, a „hely” szerinti fülekben (új hely: elég új „hely” értéket megadni).
    {"id": "pecsma",   "nev": "PécsMa",      "szin": "#be123c", "feeds": ["https://www.pecsma.hu/feed/"],
     "hely": "Pécs"},
    {"id": "pecsakt",  "nev": "PécsAktuál",  "szin": "#9333ea", "feeds": ["https://pecsaktual.hu/feed/"],
     "hely": "Pécs"},
    {"id": "szpecs",   "nev": "Szabad Pécs", "szin": "#047857", "feeds": ["https://szabadpecs.hu/feed/"],
     "hely": "Pécs"},
    {"id": "hirbalaton", "nev": "HírBalaton", "szin": "#0284c7", "feeds": ["https://www.hirbalaton.hu/feed/"],
     "hely": "Balaton"},
    {"id": "komlo7300", "nev": "7300.hu",      "szin": "#c2410c", "feeds": ["https://7300.hu/feed/"],
     "hely": "Komló"},
    {"id": "komloiu",  "nev": "Komlói Újság", "szin": "#4d7c0f", "feeds": ["https://komloiujsag.hu/feed/"],
     "hely": "Komló"},
    {"id": "kapospont", "nev": "KaposPont",   "szin": "#0e7490", "feeds": ["https://kapos.hu/rss/hirek/legfrissebb_hirek.xml"],
     "hely": "Somogy"},
    {"id": "kapost",   "nev": "Kapos-T",     "szin": "#b91c1c", "feeds": ["https://www.kapost.hu/feed/"],
     "hely": "Somogy"},
    {"id": "atadhir",  "nev": "Atádhír",     "szin": "#7c3aed", "feeds": ["https://www.atadhir.hu/feed/"],
     "hely": "Somogy"},

    # Az oldal alján, az időjárás-előrejelzés mellett jelenik meg
    {"id": "idokep", "nev": "Időkép", "szin": "#0284c7", "feeds": ["https://adat.idokep.hu/rss/news.xml"],
     "panel": "idojaras", "panel_cim": "Az Időkép hírei"},
    {"id": "hungaromet", "nev": "HungaroMet", "szin": "#1e40af", "feeds": ["https://met.hu/methu/rss/rss.php"],
     "panel": "idojaras", "panel_cim": "A HungaroMet hírei",
     "datum_oldalrol": True},   # a hírfolyam minden cikknek a letöltés idejét adja: a valódi dátum a cikkoldalon van
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
OSSZEVON_NEV = 0.15       # ha a címekben közös egy ritka tulajdonnév (pl. „Republikon”), ennyi hasonlóság is elég,
OSSZEVON_NEV_MARAD = 0.05 # …feltéve, hogy a név betűdarabjai nélkül is marad ennyi közös tartalom
OSSZEVON_EGYESIT = 0.25   # utólag két csoport egybeolvad, ha a tagpárjaik átlagos hasonlósága legalább ennyi
RITKA_NEV_MAX = 5         # ritkának számít a (legalább 6 betűs) név, ha legfeljebb ennyi cikk címében szerepel
KIEMELT_ORA = 4           # a rovat kiemelt dobozánál ennyi óránként ér egy lappal kevesebbet egy téma
ROVAT_VALTOZATOS = True   # a rovatok kiemelt kártyája óránként sorsolt elrendezést kap (széles/magas, bal/jobb)
SZINES_ESELY = 0.35       # ekkora eséllyel színes (a rovat színében) a kiemelt kártya; 0 = soha
DOBOZ_HELYENKENT = 4      # a helyi rovatban helyenként ennyi kiemelt (legfrissebb) hír a bal oldalon

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
HATTER_UTAN = "kultura"   # a Háttér rovat ez után jön
HATTER_SZIN = "#57534e"
HATTER_SORREND = ["atlatszo", "valasz", "mhang"]   # a hasábok sorrendje; a többi háttérlap utánuk jön
HATTER_NAP = 7            # a háttéranyagok ennyi napig maradnak meg (a hírek 72 óráig)
HATTER_DOBOZ = 2          # lapokként ennyi legfrissebb anyag kap dobozt (bevezetővel)
HATTER_LISTA = 6          # alattuk ennyi cím látszik, a többi a lenyitható „Korábban” részbe kerül
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


DATUM_MINTA = re.compile(r"(20\d\d)\.\s*(" + "|".join(HONAPOK) + r")\s+(\d{1,2})\.(?:\s*(\d{1,2}):(\d\d))?")


def oldal_letolt(url):
    """A cikkoldal HTML-je; a HungaroMet-féle átirányító oldalaknál (meta refresh, location) egyszer továbblép."""
    lap = letolt(url, probalkozas=1)[:300_000].decode("utf-8", "replace")
    m = (re.search(r'http-equiv=["\']?refresh["\']?[^>]*?url=([^"\'>\s]+)', lap, re.I)
         or re.search(r'location(?:\.href)?\s*=\s*["\']([^"\']+)', lap))
    if m and len(lap) < 5000:
        cel = html.unescape(m.group(1))
        if cel.startswith("/"):
            u = urlparse(url)
            cel = f"{u.scheme}://{u.netloc}{cel}"
        lap = letolt(cel, probalkozas=1)[:300_000].decode("utf-8", "replace")
    return lap


def oldal_kep(lap):
    """Az og:image (vagy twitter:image) kép címe; ha nincs, üres."""
    for minta in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                  r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                  r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(minta, lap, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def oldal_datum(lap):
    """Az oldalon látható magyar dátum (pl. „HungaroMet: 2026. november 6. 14:24”), UTC ISO alakban; ha nincs, None."""
    szoveg_ = re.sub(r"<[^>]+>", " ", lap)
    # csak a cikk saját „HungaroMet: <dátum>” sorát fogadjuk el (a fejlécben a mai nap dátuma áll)
    m = re.search(r"HungaroMet:\s*" + DATUM_MINTA.pattern, szoveg_)
    if not m:
        return None
    ev, ho, nap, ora, perc = m.groups()
    helyi_ido = datetime(int(ev), HONAPOK.index(ho) + 1, int(nap), int(ora or 12), int(perc or 0))
    tz = budapest_tz(helyi_ido.replace(tzinfo=timezone.utc))
    return helyi_ido.replace(tzinfo=tz).astimezone(timezone.utc).isoformat()


def oldal_adatok(hirek):
    """Ami a hírfolyamból hiányzik, azt az új cikkek oldaláról olvassuk ki, cikkenként egyszer
    (az eredményt az állapotfájl megőrzi):
    – kep_oldalrol: a kép (pl. Népszabadság, ahol a hírfolyamban többnyire nincs kép);
    – datum_oldalrol: a megjelenés ideje (a HungaroMet hírfolyama minden cikknek a letöltés idejét adja,
      ezért a régi cikkek háromnaponta újként bukkantak fel)."""
    def kell_kep(h):
        return FN.get(h["forras"], {}).get("kep_oldalrol") and not h["kep"] and not h.get("kep_probalva")

    def kell_datum(h):
        return FN.get(h["forras"], {}).get("datum_oldalrol") and not h.get("datum_kesz")

    kell = [h for h in hirek if (kell_kep(h) or kell_datum(h)) and "news.google.com" not in h["link"]]
    if not kell:
        return

    def egy(h):
        try:
            return oldal_letolt(h["link"])
        except Exception:
            return None

    with ThreadPoolExecutor(6) as pool:
        for h, lap in zip(kell, pool.map(egy, kell)):
            if kell_kep(h):
                h["kep"], h["kep_probalva"] = (oldal_kep(lap) if lap else ""), True
            if kell_datum(h) and lap:
                d = oldal_datum(lap)
                if d:
                    h["ido"], h["datum_kesz"] = d, True


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


def xml_beolvas(adat):
    """XML-elemzés kis hibatűréssel: ha a hírfolyam csonka (pl. hiányzik a záró </rss>), az utolsó teljes
    cikk után lezárjuk."""
    try:
        return ET.fromstring(adat)
    except ET.ParseError:
        vege = adat.rfind(b"</item>")
        if vege < 0:
            raise
        return ET.fromstring(adat[:vege + 7] + b"</channel></rss>")


def feed_feldolgoz(forras, url):
    gyoker = xml_beolvas(letolt(url))
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


def hatter_rovat_nev(h):
    """Ha a cikk háttéranyag, a lapon belüli rovat neve („” ha a lap egésze háttér); ha nem az, None."""
    f = FN.get(h["forras"], {})
    if f.get("hatter"):
        return ""
    elso = urlparse(h["link"]).path.strip("/").split("/")[0]
    return f.get("hatter_rovatok", {}).get(elso)


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
    if hatter_rovat_nev(h) is not None:
        return ki("hatter", 9)
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


def egyesit(csoportok, hason, T, ablak):
    """Utólagos egyesítés: az időrendi besorolás az első cikkeknél még kevés adatból dönt, ezért egy sztori
    két csoportba is szakadhat (pl. a második cikk épp a küszöb alatt marad, a később jövők pedig az elsőhöz
    csatlakoznak). Két csoport egybeolvad, ha az időben közeli tagpárjaik átlagos hasonlósága eléri az
    OSSZEVON_EGYESIT értéket, és van köztük OSSZEVON_EROS feletti pár is. A legjobban illő pár olvad össze
    először; a kiürült csoport idx-listája üres marad."""
    def sim(a, b):
        return hason[max(a, b)].get(min(a, b), 0.0)

    hova = {i: k for k, cs in enumerate(csoportok) for i in cs["idx"]}
    while True:
        parok = {tuple(sorted((hova[i], hova[j]))) for i, acc in hason.items() for j, x in acc.items()
                 if x >= OSSZEVON_EROS and hova[i] != hova[j]}
        legjobb, legjobb_atl = None, 0.0
        for a, b in parok:
            ertekek = [sim(i, j) for i in csoportok[a]["idx"] for j in csoportok[b]["idx"] if abs(T[i] - T[j]) <= ablak]
            if not ertekek:
                continue
            atl = sum(ertekek) / len(ertekek)
            if atl >= OSSZEVON_EGYESIT and atl > legjobb_atl:
                legjobb, legjobb_atl = (a, b), atl
        if legjobb is None:
            return
        a, b = legjobb
        for i in csoportok[b]["idx"]:
            hova[i] = a
        csoportok[a]["idx"] += csoportok[b]["idx"]
        csoportok[a]["hirek"] += csoportok[b]["hirek"]
        csoportok[b]["idx"], csoportok[b]["hirek"] = [], []


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
    # a közös ritka név önmagában nem elég: a névből származó betűdarabok nélkül is kell némi közös tartalom
    # (különben pl. két „Dublinba” utazás – Magyar Péteré és Joe Bidené – egy sztorivá állna össze)
    def nev_nelkul(i, j):
        kozos = ritka[i] & ritka[j]
        tilt = set()
        for h in (hirek[i], hirek[j]):
            for w in szavak(h["cim"]):
                w = ekezet_nelkul(w)
                if w[:7] in kozos:
                    w = f"_{w}_"
                    tilt |= {w[k:k + 4] for k in range(len(w) - 3)}
        return sum(x * V[j].get(g, 0) for g, x in V[i].items() if g not in tilt)

    nev_ok = {}
    def nevvel_eleg(i, j):
        if (i, j) not in nev_ok:
            nev_ok[(i, j)] = bool(ritka[i] & ritka[j]) and nev_nelkul(i, j) >= OSSZEVON_NEV_MARAD
        return nev_ok[(i, j)]

    csoportok, hova = [], {}
    for i in range(N):
        acc = hason[i]
        kuszob = lambda j: OSSZEVON_NEV if nevvel_eleg(i, j) else OSSZEVON_EROS
        jeloltek = {hova[j] for j, s in acc.items() if s >= kuszob(j)}
        legjobb, legjobb_atl = None, 0
        for k in jeloltek:
            kozeli = [j for j in csoportok[k]["idx"] if T[i] - T[j] <= ablak]
            atl = sum(acc.get(j, 0) for j in kozeli) / len(kozeli)
            nevvel = atl >= OSSZEVON_NEV and any(nevvel_eleg(i, j) for j in kozeli)
            if (atl >= OSSZEVON_ATLAG or nevvel) and atl > legjobb_atl:
                legjobb, legjobb_atl = k, atl
        if legjobb is None:
            hova[i] = len(csoportok)
            csoportok.append({"idx": [i], "hirek": [hirek[i]]})
        else:
            hova[i] = legjobb
            csoportok[legjobb]["idx"].append(i)
            csoportok[legjobb]["hirek"].append(hirek[i])
    egyesit(csoportok, hason, T, ablak)
    csoportok = [cs for cs in csoportok if cs["idx"]]
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


def hatter_szekcio(cs_lista):
    """„Háttér” rovat: laponként egy hasáb; felül a legfrissebb anyagok bevezetővel, alatta címlista,
    a régebbiek (legfeljebb HATTER_NAP napig) lenyitható részben. Nincs összevonás."""
    if not cs_lista:
        return "", ""

    def sor_(c):
        h = c["fo"]
        rov = hatter_rovat_nev(h)
        cimke = f' <span class="hatter-rov">{e(rov)}</span>' if rov else ""
        return (f'<li data-srcs="{h["forras"]}"><time>{rovid_ido(h["ido"])}</time>'
                f'<span class="sor-cim"><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a>{cimke}</span></li>')

    hasabok = []
    sorrend = HATTER_SORREND + [f["id"] for f in FORRASOK if f["id"] not in HATTER_SORREND]
    for f in (FN[x] for x in sorrend):
        cs = sorted([c for c in cs_lista if c["fo"]["forras"] == f["id"]], key=lambda c: c["fo"]["ido"], reverse=True)
        if not cs:
            continue
        dob, lista = cs[:HATTER_DOBOZ], cs[HATTER_DOBOZ:]
        lathato, regi = lista[:HATTER_LISTA], lista[HATTER_LISTA:]
        reszlet = (f'<details class="tovabbi-hirek"><summary>Korábban ({len(regi)})</summary>'
                   f'<ul class="lista">{"".join(sor_(c) for c in regi)}</ul></details>') if regi else ""
        hasabok.append(f'''<div class="hatter-hasab" data-srcs="{f["id"]}">
  <h3 class="helynev" style="--r:{f["szin"]}">{e(f["nev"])}</h3>
  {"".join(doboz(c) for c in dob)}
  {f'<ul class="lista">{"".join(sor_(c) for c in lathato)}</ul>' if lathato else ""}{reszlet}
</div>''')
    html_ = f'''<section class="rovat" id="hatter" style="--r:{HATTER_SZIN}">
  {rovatfej("hatter", "Háttér", HATTER_SZIN, len(cs_lista))}
  <div class="tartalom hatter-hasabok">{"".join(hasabok)}</div>
</section>'''
    return html_, f'<a href="#hatter" style="--r:{HATTER_SZIN}">Háttér</a>'


def hely_azon(hely):
    return re.sub(r"[^a-z0-9]+", "-", ekezet_nelkul(hely).lower()).strip("-")


def helyi_rovat(helyi_cs):
    """Helyi rovat fülekkel: egyszerre egy hely látszik (alapból az első, a választást a böngésző megjegyzi;
    ?hely=komlo a címben). Bal oldalon a legfrissebb hírek kiemelve, jobbra a többi friss hír listája,
    alatta a korábbiak napokra bontva."""
    if not helyi_cs:
        return "", ""
    helyek = list(dict.fromkeys(f["hely"] for f in FORRASOK if f.get("hely")))
    fulek, lapok_ = [], []
    for hely in helyek:
        cs = sorted([c for c in helyi_cs if FN[c["fo"]["forras"]].get("hely") == hely],
                    key=lambda c: c["ido"], reverse=True)
        if not cs:
            continue
        az = hely_azon(hely)
        elso = not lapok_
        kiemelt = cs[:DOBOZ_HELYENKENT]
        tobbi = cs[DOBOZ_HELYENKENT:]
        friss_tobbi = [c for c in tobbi if c["kor_ora"] < FRISS_ORA][:HELYI_LISTA]
        regi = [c for c in tobbi if c not in friss_tobbi]
        fulek.append(f'<button type="button" class="hely-ful" data-hely="{az}" aria-selected="{str(elso).lower()}">'
                     f'{e(hely)} <span class="db">{len(cs)}</span></button>')
        lista = f'<ul class="lista">{"".join(sor(c) for c in friss_tobbi)}</ul>' if friss_tobbi else ""
        lapok_.append(f'''<div class="hely" data-hely="{az}"{"" if elso else " hidden"}>
  <div class="hely-kiemelt">{"".join(doboz(c, lead=False) for c in kiemelt)}</div>
  <div class="hely-lista">{lista}{korabban(regi, nyitva=not friss_tobbi)}</div>
</div>''')
    html_ = f'''<section class="rovat" id="helyi" style="--r:{HELYI_SZIN}">
  {rovatfej("helyi", "Helyi", HELYI_SZIN, len(helyi_cs))}
  <div class="hely-fulek" role="tablist">{"".join(fulek)}</div>
  <div class="tartalom helyek">{"".join(lapok_)}</div>
</section>'''
    return html_, f'<a href="#helyi" style="--r:{HELYI_SZIN}">Helyi</a>'


def oldal(orszagos, helyi_cs, hibak, idojaras=None, idokep=None, meresek=None, hatter_cs=None):
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
    hatter_html, hatter_nav = hatter_szekcio(hatter_cs or [])
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
        if r["id"] == HATTER_UTAN and hatter_html:
            reszek.append(hatter_html); nav.append(hatter_nav)

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
@media (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]) .hely-ful[aria-selected="false"]{color:#e5e7eb;border-color:color-mix(in srgb,var(--r) 45%,#9ca3af)}}
:root[data-tema="sotet"] .hely-ful[aria-selected="false"]{color:#e5e7eb;border-color:color-mix(in srgb,var(--r) 45%,#9ca3af)}

/* helyi rovat */
.helyek{display:block}
.hatter-hasabok{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1.4rem;align-items:start}
.hatter-hasab{background:color-mix(in srgb,var(--r) 7%,var(--lap));border-radius:8px;padding:1.1rem;display:flex;flex-direction:column;gap:.7rem}
.hatter-hasab .lista{columns:1;margin:0}
.hatter-hasab .lista time{min-width:3.4rem;font-size:.78rem;white-space:nowrap}
.hatter-rov{font-size:.74rem;color:var(--halvany,#888);margin-left:.35rem;white-space:nowrap}
.hely-fulek{display:flex;gap:.5rem;margin:0 0 1rem;overflow-x:auto;scrollbar-width:none}
.hely-ful{font:inherit;font-weight:700;font-size:1rem;padding:.35rem .9rem;border-radius:5px;cursor:pointer;white-space:nowrap;
  border:1.5px solid color-mix(in srgb,var(--r) 55%,transparent);background:transparent;color:var(--r)}
.hely-ful .db{font-weight:400;font-size:.8rem;opacity:.75;color:inherit}
.hely-ful[aria-selected="true"]{background:var(--r);border-color:var(--r);color:#fff}
.hely-ful[aria-selected="true"] .db{opacity:.85}
.hely[hidden]{display:none!important}
.hely{display:grid!important;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1.4rem;align-items:start}
.hely-kiemelt{display:flex;flex-direction:column;gap:.7rem}
.hely-lista .lista{columns:1;margin:0}
.hely-lista details{margin-top:.6rem}
.hely{background:color-mix(in srgb,var(--r) 6%,var(--lap));border-radius:8px;padding:1.1rem}
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
  .hely{grid-template-columns:1fr!important}
  .hely-fulek{gap:.35rem}.hely-ful{padding:.3rem .65rem;font-size:.92rem}}
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
// helyi rovat fülei: a választás megmarad, ?hely=komlo felülírja
(function(){
  const fulek=[...document.querySelectorAll('.hely-ful')]; if(!fulek.length) return;
  const valt=(az,ment)=>{ if(!fulek.some(f=>f.dataset.hely===az)) return;
    fulek.forEach(f=>f.setAttribute('aria-selected',f.dataset.hely===az));
    document.querySelectorAll('.hely[data-hely]').forEach(d=>d.hidden=d.dataset.hely!==az);
    if(ment) tarol.set('hp_hely',az); };
  const url=new URLSearchParams(location.search).get('hely');
  if(url){ valt(url,true); } else { valt(tarol.get('hp_hely',''),false); }
  fulek.forEach(f=>f.addEventListener('click',()=>valt(f.dataset.hely,true)));
})();
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

APP_IKONOK = {"ikon-lap-512.png": "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", "ikon-lap-192.png": "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", "ikon-lap-maskable.png": "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"}

SW_JS = r"""// A Kezdőlap – háttérszkript: a címlapot mindig a hálózatról kéri, de elmenti, hogy offline is megnyíljon
const TAR = 'kezdolap-v2';
const ALAP = ['./', 'manifest.webmanifest', 'ikon-lap-192.png', 'ikon-lap-512.png', 'ikon-lap-maskable.png'];
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
            {"src": "ikon-lap-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "ikon-lap-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "ikon-lap-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
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
            if regi.get("datum_kesz"):
                h["ido"], h["datum_kesz"] = regi["ido"], True
        tarolt[h["id"]] = h
    oldal_adatok(uj)
    hatar = (MOST - timedelta(hours=MEGORZES_ORA)).isoformat()
    # a megbízhatatlan dátumú hírfolyamok (datum_oldalrol) lejárt cikkeit 30 napig még megjegyezzük, így ha
    # a lap újra kiadja őket, a korábbi dátumot kapják vissza, és nem bukkannak fel újként
    emlek = [h for h in tarolt.values() if h["forras"] in FN and FN[h["forras"]].get("datum_oldalrol")
             and (MOST - timedelta(days=30)).isoformat() <= h["ido"] < hatar]
    hatar_hatter = (MOST - timedelta(days=HATTER_NAP)).isoformat()
    ervenyes = [h for h in tarolt.values() if h["forras"] in FN
                and h["ido"] >= (hatar_hatter if hatter_rovat_nev(h) is not None else hatar)
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
    # fizetett tartalom („… (x)”) nem kell
    ervenyes = [h for h in ervenyes if not re.search(r"\(x\)\s*$", h["cim"])]
    # ugyanannak a lapnak azonos című cikkei közül csak egy maradjon (amelyiknek van képe/leadje)
    egyedi = {}
    for h in sorted(ervenyes, key=lambda h: (bool(h["kep"]), len(h["lead"])), reverse=True):
        egyedi.setdefault((h["forras"], h["cim"].strip().lower()), h)
    ervenyes = list(egyedi.values())
    for h in ervenyes:
        # a mentett hírekre is (pl. egy korábban tévesen frissnek látszó, újra kiadott cikkre)
        h["ido"] = webcim_korlat(h["link"], datetime.fromisoformat(h["ido"])).isoformat()
        h["rovat"] = rovat(h)

    allapot_ut.write_text(json.dumps(ervenyes + emlek, ensure_ascii=False), "utf-8")
    orszagos = osszevon([h for h in ervenyes if h["rovat"] not in ("helyi", "idojaras", "hatter")])
    # a háttéranyagokat nem vonjuk össze: mindegyik önálló „sztori”
    hatter_cs = [osszevon([h])[0] for h in ervenyes if h["rovat"] == "hatter"]
    helyi_cs = osszevon([h for h in ervenyes if h["rovat"] == "helyi"])
    Path(a.kimenet).parent.mkdir(parents=True, exist_ok=True)
    idokep = [h for h in ervenyes if h["rovat"] == "idojaras"]
    meresek = meres_letolt()
    # ha vannak friss mérések, a három rögzített Open-Meteo-kártya helyett a mért lista jelenik meg
    helyek = [] if meresek else idojaras_letolt()
    Path(a.kimenet).write_text(oldal(orszagos, helyi_cs, hibak, helyek, idokep, meresek, hatter_cs), "utf-8")
    pwa_fajlok(Path(a.kimenet).parent, MOST.isoformat())
    print(f"{len(uj)} letöltött, {len(ervenyes)} megőrzött hír → {a.kimenet}")
    for x in hibak:
        print("  hiba:", x)


if __name__ == "__main__":
    main()
