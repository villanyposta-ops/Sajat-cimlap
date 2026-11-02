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

OLDAL_CIM = "Saját Címlap"
# Fejléc: "veletlen" = minden megnyitáskor a három közül egy másik (amíg a lap nyitva van, nem cserélődik);
# vagy rögzítve: "hirlap" (Pesti Hírlap-szerű), "got" (gót betűs), "ecset" (kézírásos)
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
    "got":    {"csalad": "Newsreader", "url": "Newsreader:opsz,wght@6..72,400..800", "suly": 600, "nagy": 650, "ter": "-.01em",
               "tartalek": "Georgia,'Times New Roman',serif"},
    "ecset":  {"csalad": "Jost", "url": "Jost:wght@400..800", "suly": 600, "nagy": 650, "ter": "-.01em"},
}        # ha egy állandó színt szeretnél, pl. "#16202f"; None = napszak szerint

# Országos lapok. Az „alap” rovat akkor számít, ha a cikkről semmi más nem derül ki.
FORRASOK = [
    {"id": "telex",  "nev": "Telex",         "szin": "#2563eb", "feeds": ["https://telex.hu/rss"],
     # a Telex időnként kitiltja a GitHub gépét (403); ilyenkor a Google Hírek Telexre szűrt csatornája
     # pótolja a címeket, a következő sikeres futásnál pedig a teljes (képes) változat lép a helyükre
     "tartalek": "https://news.google.com/rss/search?q=site:telex.hu+when:2d&hl=hu&gl=HU&ceid=HU:hu"},
    {"id": "444",    "nev": "444",           "szin": "#e11d48", "feeds": ["https://444.hu/feed"]},
    {"id": "24",     "nev": "24.hu",         "szin": "#ea580c", "feeds": ["https://24.hu/feed/"]},
    {"id": "hvg",    "nev": "HVG",           "szin": "#7c3aed", "feeds": ["https://hvg.hu/rss"]},
    {"id": "mhang",  "nev": "Magyar Hang",   "szin": "#0d9488", "feeds": ["https://hang.hu/rss/"]},
    {"id": "atlatszo", "nev": "Átlátszó",    "szin": "#65a30d", "feeds": ["https://atlatszo.hu/feed/"],
     "alap": "belfold"},
    {"id": "valasz", "nev": "Válasz Online", "szin": "#a16207", "feeds": ["https://www.valaszonline.hu/feed/"]},
    {"id": "infostart", "nev": "Infostart", "szin": "#4f46e5", "feeds": ["https://infostart.hu/24ora/rss/"]},
    {"id": "euronews", "nev": "Euronews",    "szin": "#0369a1",
     "feeds": ["https://hu.euronews.com/rss?level=theme&name=news"], "alap": "kulfold"},

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

FO_HIREK_SZAMA = 11       # ennyi kerül a lap tetejére (1 nyitó + 4 képes + 6 kép nélküli)
FO_MAX_ORA = 8            # a kiemelt részbe ennél frissebb hír kerülhet (a hír legutóbbi cikke számít)
NYITO_MAX_ORA = 3         # a nyitóhír ennél frissebb legyen; ha nincs ilyen, fokozatosan enged (5, 8, 12 óra)
FO_TARTALEK_ORA = 12      # ha (pl. hajnalban) nincs elég 8 óránál frissebb hír, legfeljebb eddig nyúl vissza
FO_STILUS = "arnyalt"     # a kiemelt rész háttere: "arnyalt" (a fejléc színének halvány árnyalata), "vilagos", "sotet"
FRISS_ORA = 24            # ennél frissebb hír dobozban, régebbi listában jelenik meg
MEGORZES_ORA = 72         # ennél régebbi hír kiesik
DOBOZ_ROVATONKENT = 8     # rovatonként legfeljebb ennyi doboz (az első kiemelt, dupla széles), a többi listába megy
DOBOZ_HELYENKENT = 3      # a helyi rovatban oszloponként ennyi kiemelt hír

# Országos rovatok a megjelenés sorrendjében. A „Helyi” rovat helyét a HELYI_UTAN adja meg.
#   utvonal: a cikk URL-jének egy szakasza (telex.hu/belfold/... → "belfold") vagy a
#            lap által megadott kategória (WordPress-lapoknál, pl. Magyar Hang, Válasz)
#   kulcsszavak: ha sem az URL, sem a kategória nem árulkodó, a cím és a lead alapján
ROVATOK = [
    {"id": "belfold", "nev": "Belföld", "szin": "#1d4ed8",
     "utvonal": ["belfold", "itthon", "kozelet", "politika", "belpol", "orszag", "kozugyek",
                 "orszagszerte", "jogallamisag", "kozpenz", "kozeleti"],
     "kulcsszavak": ["kormány", "orbán", "fidesz", "tisza párt", "magyar péter", "parlament", "országgyűlés",
                     "miniszter", "polgármester", "önkormányzat", "választás", "ellenzék", "budapest",
                     "rendőrség", "bíróság", "ügyészség", "kdnp", "mi hazánk"]},
    {"id": "kulfold", "nev": "Külföld", "szin": "#0f766e",
     "utvonal": ["kulfold", "vilag", "nagyvilag", "europa", "eu", "kulpolitika", "my-europe", "world"],
     "kulcsszavak": ["ukrajn", "orosz", "putyin", "trump", "amerikai", "európai unió", "brüsszel",
                     "izrael", "gáza", "kína", "német", "nato", "szlovák", "román", "lengyel", "francia"]},
    {"id": "gazdasag", "nev": "Gazdaság", "szin": "#b45309",
     "utvonal": ["gazdasag", "fn", "kkv", "penz", "ingatlan", "fogyasztas", "uzlet", "befektetes", "ceg",
                 "business", "bank", "deviza", "tozsde", "unios-forrasok", "krypto", "kriptopenz"],
     "kulcsszavak": ["infláció", "forint", "jegybank", "mnb", "kamat", "gdp", "költségvetés", "adó",
                     "árak", "tőzsde", "vállalat", "cég", "bér", "nyugdíj", "benzin", "beruházás"]},
    {"id": "tech", "nev": "Tudomány és tech", "szin": "#6d28d9",
     "utvonal": ["tech", "techtud", "tudomany", "tudomany-technika", "kornyezet", "zold", "mobil", "next"],
     "kulcsszavak": ["kutató", "tudós", "mesterséges intelligencia", "űrszonda", "klíma", "apple",
                     "google", "okostelefon", "szoftver", "energia"]},
    {"id": "kultura", "nev": "Kultúra", "szin": "#be185d",
     "utvonal": ["kultura", "kult", "kulturalis", "zene", "film", "konyv", "szinhaz", "mediapiac", "media",
                 "culture"],
     "kulcsszavak": ["film", "színház", "koncert", "album", "regény", "író", "kiállítás", "múzeum",
                     "rendező", "színész", "fesztivál", "könyv"]},
    {"id": "velemeny", "nev": "Vélemény és elemzés", "szin": "#475569",
     "utvonal": ["velemeny", "publicisztika", "jegyzet", "kommentar", "elemzes", "interju", "esszé", "essze"],
     "kulcsszavak": []},
    {"id": "sport", "nev": "Sport", "szin": "#15803d",
     "utvonal": ["sport", "foci", "futball", "sportmenu", "forma-1", "f1"],
     "kulcsszavak": ["meccs", "bajnokság", "válogatott", "gól", "edző", "olimpi", "ferencváros",
                     "labdarúg", "focist", "futball", "játékvezet", "tizenegyes", "premier league", "csapatkapitány",
                     "forma-1", "teniszez", "kézilabd", "vízilabd", "úszó", "nb i.", "fradi",
                     "forma–1", "forma-1", "nb i", "bajnokok ligája"]},
    {"id": "eletmod", "nev": "Életmód", "szin": "#a16207",
     "utvonal": ["eletmod", "elet-stilus", "elet", "after", "egeszseg", "utazas", "gasztro", "otthon",
                 "eletstilus"],
     "kulcsszavak": ["recept", "egészség", "utazás", "étterem", "kert", "kutya", "macska"]},
    {"id": "egyeb", "nev": "Egyéb", "szin": "#64748b", "utvonal": [], "kulcsszavak": []},
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
        d = datum(szoveg(e, "pubdate", "published", "updated", "date")) or MOST
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


def kulcsszo_talalat(szoveg, kulcsszo):
    """A kulcsszó csak szó elején számít, utána toldalék állhat: az „adó” illik az „adóemelés” szóra,
    de a „kiadó” szóra nem."""
    return len(re.findall(rf"(?<![{BETU}]){re.escape(kulcsszo.strip())}", szoveg))


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
    szakaszok = [ekezet_nelkul(s.lower()) for s in urlparse(h["link"]).path.split("/") if s]
    for sz in szakaszok[:3]:
        for r in ROVATOK:
            if sz in r["utvonal"]:
                return ki(r["id"], 3)
    kat = ekezet_nelkul(" ".join(h["kategoriak"]).lower())
    for r in ROVATOK:
        if any(re.search(rf"\b{re.escape(u)}\b", kat) for u in r["utvonal"]) or \
                ekezet_nelkul(r["nev"].lower()) in kat:
            return ki(r["id"], 2)
    szoveg_ = (h["cim"] + " " + h["lead"]).lower()
    pontok = {r["id"]: sum(kulcsszo_talalat(szoveg_, k) for k in r["kulcsszavak"]) for r in ROVATOK}
    legjobb = max(pontok, key=pontok.get)
    if pontok[legjobb] > 0:
        return ki(legjobb, 1)
    if forras.get("alap"):
        return ki(forras["alap"], 0.5)
    return ki("egyeb", 0)


# ── Ugyanarról szóló hírek összevonása, fő hírek pontozása ─────────

def tokenek(h):
    szavak = re.findall(r"[a-záéíóöőúüű0-9]+", h["cim"].lower())
    return {ekezet_nelkul(w)[:6] for w in szavak if len(w) >= 4 and w not in STOP}


def osszevon(hirek):
    """Mohó csoportosítás: két hír ugyanaz a sztori, ha a címük szótövei erősen átfednek."""
    hirek = sorted(hirek, key=lambda h: h["ido"], reverse=True)
    csoportok = []
    for h in hirek:
        t = tokenek(h)
        hova = None
        for cs in csoportok:
            kozos = len(t & cs["tok"])
            if kozos >= 2 and kozos / max(1, min(len(t), len(cs["tok"]))) >= 0.5 and \
                    abs((datetime.fromisoformat(h["ido"]) - cs["uj"]).total_seconds()) < 36 * 3600:
                hova = cs
                break
        if hova:
            hova["hirek"].append(h)
            hova["tok"] |= t
        else:
            csoportok.append({"hirek": [h], "tok": set(t), "uj": datetime.fromisoformat(h["ido"])})
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
        # fontosság: hány lap írt róla + frissesség
        cs["pont"] = 3 * len(cs["forrasok"]) + 0.5 * len(hk) + 5 * max(0.0, 1 - kor_ora / FO_MAX_ORA)
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
}


def ikon(kod, meret=32):
    nev = WMO.get(kod, ("", "felho"))[1]
    return (f'<svg class="ido-ikon" width="{meret}" height="{meret}" viewBox="0 0 32 32" aria-hidden="true">'
            f'{IKONOK[nev]}</svg>')


def idojaras_letolt():
    eredmeny = []
    for hely in IDOJARAS_HELYEK:
        url = ("https://api.open-meteo.com/v1/forecast?"
               f"latitude={hely['lat']}&longitude={hely['lon']}"
               "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
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
            f'<div class="ido-most">{ikon(c["weather_code"], 52)}'
            f'<span class="ido-fok">{round(c["temperature_2m"])}°</span>'
            f'<span class="ido-leiras">{e(leiras)}<br><small>hőérzet {round(c["apparent_temperature"])}°, '
            f'szél {round(c["wind_speed_10m"])} km/h</small></span></div>'
            f'<ul class="ido-napok">{napok}</ul></div>')


def idojaras_panel(helyek, panel_hirek):
    if not helyek and not panel_hirek:
        return ""
    sajat = ('<div class="ido-hely ido-itt" id="ido-itt" hidden></div>' if IDOJARAS_SAJAT_HELY else "")
    kartyak = sajat + "".join(ido_kartya(h) for h in helyek)
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
                    "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
                    "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                    f"&timezone=Europe%2FBudapest&forecast_days={IDOJARAS_NAPOK}"),
            "wmo": {str(k): v for k, v in WMO.items()}, "ikonok": IKONOK, "telepulesek": TELEPULESEK,
            "napok": ["H", "K", "Sze", "Cs", "P", "Szo", "V"],
        }
        js = "<script>" + IDO_JS.replace("__IDO_ADAT__", json.dumps(adat, ensure_ascii=False).replace("</", "<\\/")) + "</script>"
    return (f'<section class="rovat idojaras" id="idojaras" style="--r:{IDOJARAS_SZIN}">'
            f'{rovatfej("idojaras", "Időjárás", IDOJARAS_SZIN, 0)}'
            f'<div class="tartalom ido-racs"><div class="ido-helyek">{kartyak}</div>'
            f'{"<div class=" + chr(34) + "ido-hirsor" + chr(34) + ">" + "".join(hirdobozok) + "</div>" if hirdobozok else ""}'
            f'</div></section>{js}')


# A helymeghatározásos kártya ehhez a listához méri a legközelebbi települést (név, szélesség, hosszúság)
TELEPULESEK = [
    ["Budapest", 47.4979, 19.0402], ["Debrecen", 47.5316, 21.6273], ["Szeged", 46.2530, 20.1414],
    ["Miskolc", 48.1035, 20.7784], ["Pécs", 46.0727, 18.2323], ["Győr", 47.6875, 17.6504],
    ["Nyíregyháza", 47.9554, 21.7167], ["Kecskemét", 46.8964, 19.6897], ["Székesfehérvár", 47.1860, 18.4221],
    ["Szombathely", 47.2307, 16.6218], ["Szolnok", 47.1621, 20.1825], ["Tatabánya", 47.5692, 18.4048],
    ["Kaposvár", 46.3594, 17.7968], ["Érd", 47.3919, 18.9045], ["Veszprém", 47.0933, 17.9115],
    ["Békéscsaba", 46.6736, 21.0877], ["Zalaegerszeg", 46.8417, 16.8416], ["Sopron", 47.6817, 16.5845],
    ["Eger", 47.9025, 20.3772], ["Nagykanizsa", 46.4590, 16.9897], ["Dunaújváros", 46.9619, 18.9355],
    ["Hódmezővásárhely", 46.4181, 20.3300], ["Salgótarján", 48.0935, 19.7999], ["Szekszárd", 46.3474, 18.7062],
    ["Esztergom", 47.7928, 18.7400], ["Gödöllő", 47.5961, 19.3550], ["Vác", 47.7826, 19.1332],
    ["Szentendre", 47.6694, 19.0756], ["Baja", 46.1803, 18.9551], ["Mohács", 45.9931, 18.6831],
    ["Komló", 46.1928, 18.2617], ["Paks", 46.6219, 18.8558], ["Gyula", 46.6471, 21.2784],
    ["Jászberény", 47.5000, 19.9167], ["Cegléd", 47.1736, 19.7997], ["Kiskunfélegyháza", 46.7117, 19.8503],
    ["Ózd", 48.2192, 20.2869], ["Kazincbarcika", 48.2531, 20.6458], ["Mosonmagyaróvár", 47.8682, 17.2689],
    ["Pápa", 47.3306, 17.4675], ["Ajka", 47.1019, 17.5583], ["Siófok", 46.9041, 18.0580],
    ["Keszthely", 46.7681, 17.2432], ["Balatonfüred", 46.9617, 17.8711], ["Tapolca", 46.8828, 17.4400],
    ["Fonyód", 46.7428, 17.5531], ["Balatonalmádi", 47.0353, 18.0136], ["Szigetvár", 46.0486, 17.8056],
    ["Siklós", 45.8553, 18.2986], ["Tiszaújváros", 47.9286, 21.0428], ["Hatvan", 47.6667, 19.6833],
    ["Gyöngyös", 47.7825, 19.9281], ["Orosháza", 46.5667, 20.6667], ["Makó", 46.2167, 20.4833],
    ["Kiskunhalas", 46.4342, 19.4847], ["Monor", 47.3511, 19.4486], ["Budaörs", 47.4614, 18.9583],
    ["Szigetszentmiklós", 47.3439, 19.0436], ["Dunakeszi", 47.6364, 19.1386], ["Kőszeg", 47.3892, 16.5411],
]


IDO_JS = r"""(function(){
  var D = __IDO_ADAT__;
  var doboz = document.getElementById('ido-itt'); if (!doboz || !navigator.geolocation) return;
  doboz.hidden = false;
  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function ikon(kod, m){ var w = D.wmo[kod] || ['', 'felho'];
    return '<svg class="ido-ikon" width="'+m+'" height="'+m+'" viewBox="0 0 32 32" aria-hidden="true">'+D.ikonok[w[1]]+'</svg>'; }
  function tav(a, b, c, d){ var r = Math.PI/180, x = (d-b)*r*Math.cos((a+c)/2*r), y = (c-a)*r; return Math.sqrt(x*x+y*y)*6371; }
  function helynev(lat, lon){
    var best = null, bt = 1e9;
    D.telepulesek.forEach(function(t){ var k = tav(lat, lon, t[1], t[2]); if (k < bt) { bt = k; best = t[0]; } });
    if (bt < 12) return best + ' környéke';
    if (bt < 40) return best + ' közelében';
    return 'A tartózkodási helyed';
  }
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
    return '<h3><span class="ido-itt-jel" aria-hidden="true">◉</span> '+esc(nev)+'</h3>'+
      '<div class="ido-most">'+ikon(c.weather_code,52)+'<span class="ido-fok">'+Math.round(c.temperature_2m)+'°</span>'+
      '<span class="ido-leiras">'+esc((D.wmo[c.weather_code]||[''])[0])+'<br><small>hőérzet '+Math.round(c.apparent_temperature)+
      '°, szél '+Math.round(c.wind_speed_10m)+' km/h</small></span></div><ul class="ido-napok">'+napok+'</ul>';
  }
  function betolt(lat, lon){
    lat = Math.round(lat*100)/100; lon = Math.round(lon*100)/100;   // kb. 1 km-re kerekítve
    doboz.innerHTML = '<h3>Ahol most vagy</h3><p class="ido-nincs">Előrejelzés betöltése…</p>';
    fetch(D.url.replace('{lat}', lat).replace('{lon}', lon))
      .then(function(r){ if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function(a){ doboz.innerHTML = kartya(helynev(lat, lon), a); doboz.classList.add('ido-itt-kesz'); })
      .catch(function(){ doboz.innerHTML = '<h3>Ahol most vagy</h3><p class="ido-nincs">Az előrejelzés most nem érhető el.</p>'; });
  }
  function kerd(){
    doboz.innerHTML = '<h3>Ahol most vagy</h3><p class="ido-nincs">Hely meghatározása…</p>';
    navigator.geolocation.getCurrentPosition(
      function(p){ betolt(p.coords.latitude, p.coords.longitude); },
      function(){ doboz.innerHTML = '<h3>Ahol most vagy</h3><p class="ido-nincs">A böngésző nem adta meg a helyet. '+
        'A címsor melletti lakat vagy beállítás ikonnál engedélyezheted.</p>'; },
      { timeout: 15000, maximumAge: 30*60*1000 });
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


def tovabbi(cs):
    # csak a többi lap: minden lapból egy link, a fő cikk lapja nem ismétlődik
    latott, tobbi = {cs["fo"]["forras"]}, []
    for h in cs["hirek"]:
        if h["forras"] not in latott:
            latott.add(h["forras"])
            tobbi.append(h)
    if not tobbi:
        return ""
    linkek = ", ".join(f'<a href="{e(h["link"])}" target="_blank" rel="noopener" title="{e(h["cim"])}">'
                       f'{e(FN[h["forras"]]["nev"])}</a>' for h in tobbi)
    return f'<p class="tovabbi">Erről írt még: {linkek}</p>'


def kep_html(h, osztaly):
    if not h["kep"]:
        return ""
    return (f'<div class="{osztaly}"><img src="{e(h["kep"])}" alt="" loading="lazy" '
            f'onerror="this.parentNode.remove()"></div>')


def fo_doboz(cs, i):
    h = cs["fo"]
    tipus = "nyito" if i == 0 else ("masod" if i < 5 else "kis")
    kep = kep_html(h, "kep") if tipus != "kis" else ""
    lead = f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] and tipus != "kis" else ""
    return f'''<article class="fo-hir {tipus}" data-srcs="{' '.join(cs['forrasok'])}">
  {kep}<div class="szoveg">
  <div class="meta">{forras_nev(h["forras"])}<time>{ido_szoveg(cs["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3>
  {lead}{lefedettseg(cs)}{tovabbi(cs) if tipus != "kis" else ""}</div>
</article>'''


def doboz(cs, lead=True, kiemelt=False):
    h = cs["fo"]
    kep = kep_html(h, "kiemelt-kep") if kiemelt else kep_html(h, "belyeg")
    return f'''<article class="doboz{' kiemelt' if kiemelt else ''}" data-srcs="{' '.join(cs['forrasok'])}">
  <div class="szoveg">
  <div class="meta">{forras_nev(h["forras"])}<time>{ido_szoveg(cs["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3>
  {f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] and lead else ''}
  {lefedettseg(cs)}{tovabbi(cs)}</div>
  {kep}
</article>'''


def sor(cs):
    h = cs["fo"]
    return (f'<li data-srcs="{" ".join(cs["forrasok"])}"><time>{ido_szoveg(cs["ido"])}</time>'
            f'<span class="sor-cim"><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a> '
            f'{forras_nev(h["forras"])}</span></li>')


def rovatfej(rid, nev, szin, db):
    szam = "" if rid == "idojaras" else f'<span class="db">{db} hír</span>'
    return (f'<h2 class="rovatfej" style="--r:{szin}"><button class="csuk" aria-expanded="true">'
            f'{e(nev)}</button>{szam}</h2>')


def korabban(lista, nyitva=False):
    if not lista:
        return ""
    return (f'<details{" open" if nyitva else ""}><summary>Korábbi hírek ({len(lista)})</summary>'
            f'<ul class="lista">{"".join(sor(c) for c in lista)}</ul></details>')


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
        friss_tobbi = [c for c in tobbi if c["kor_ora"] < FRISS_ORA]
        regi = [c for c in tobbi if c["kor_ora"] >= FRISS_ORA]
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


def oldal(orszagos, helyi_cs, hibak, idojaras=None, idokep=None):
    friss = [c for c in orszagos if c["kor_ora"] < FO_MAX_ORA]
    if len(friss) < FO_HIREK_SZAMA:           # hajnalban kevés a friss hír: legfeljebb 12 óráig visszanyúlunk
        friss = [c for c in orszagos if c["kor_ora"] < FO_TARTALEK_ORA]
    rangsor = sorted(friss, key=lambda c: c["pont"], reverse=True)
    # nyitóhír: a legtöbb lap által megírt képes hír a legszűkebb időablakból, ahol ilyen van
    nyito = None
    for hatar in (NYITO_MAX_ORA, 5, FO_MAX_ORA, FO_TARTALEK_ORA):
        jeloltek = [c for c in rangsor if c["kor_ora"] < hatar]
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
    for r in ROVATOK:
        cs = sorted([c for c in orszagos if c["rovat"] == r["id"] and id(c) not in fo_id],
                    key=lambda c: c["ido"], reverse=True)
        if cs:
            dobozok = [c for c in cs if c["kor_ora"] < FRISS_ORA][:DOBOZ_ROVATONKENT]
            lista = [c for c in cs if c not in dobozok]
            if len(dobozok) >= 3:
                # kiemelt: amelyikről a legtöbb lap írt; döntetlennél a képes, majd a pontszám (frissesség)
                k = max(dobozok, key=lambda c: (len(c["forrasok"]), bool(c["fo"]["kep"]), c["pont"]))
                dobozok = [k] + [c for c in dobozok if c is not k]
            nav.append(f'<a href="#{r["id"]}" style="--r:{r["szin"]}">{e(r["nev"])}</a>')
            reszek.append(f'''<section class="rovat" id="{r["id"]}" style="--r:{r["szin"]}">
  {rovatfej(r["id"], r["nev"], r["szin"], len(cs))}
  <div class="tartalom">
  {f'<div class="racs">{"".join(doboz(c, kiemelt=(i == 0 and len(dobozok) >= 3)) for i, c in enumerate(dobozok))}</div>' if dobozok else ''}
  {korabban(lista, nyitva=not dobozok)}
  </div>
</section>''')
        if r["id"] == HELYI_UTAN:
            reszek.append(helyi_html); nav.append(helyi_nav)

    ido_html = idojaras_panel(idojaras or [], idokep or [])
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
<html lang="hu"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<script>try{{var t=localStorage.getItem("hp_tema");if(t==="vilagos"||t==="sotet")document.documentElement.dataset.tema=t;}}catch(e){{}}</script>
<meta http-equiv="refresh" content="600"><meta name="robots" content="noindex">
<title>{e(OLDAL_CIM)}, {most.month}. {most.day}.</title>
{IKON_LINKEK.get(kezdo, IKON_HIRLAP)}
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>

<style>{CSS}</style></head>
<body>
<header class="fej fej-{e(kezdo)}" id="fej">
  <div class="fejsor">
    <a class="logo-link" href="#" aria-label="{e(OLDAL_CIM)}">{logok if OLDAL_CIM == "Saját Címlap" else f'<span class="nev">{e(OLDAL_CIM)}</span>'}</a>
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
h1,h2,h3,h4{font-family:var(--cimbetu);margin:0}

/* fejléc: inverz, a háttérszín napszak szerint (a --fejszin értékét a böngésző állítja be) */
.fej{background:linear-gradient(120deg,var(--fejszin,#16202f),color-mix(in srgb,var(--fejszin,#16202f) 72%,#000));color:#fff}
/* minden fejlécnél: balra a dátum, középen a cím, jobbra a Források gomb */
.fejsor{max-width:1280px;margin:0 auto;padding:1.2rem 1.5rem 1.1rem;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:1.5rem}
.logo-link{display:block;color:#fff;line-height:0;grid-column:2;grid-row:1;justify-self:center}
.fejsor .datum{grid-column:1;grid-row:1;align-items:flex-start;padding:0}
.fejsor .fej-gombok{grid-column:3;grid-row:1;justify-self:end;display:flex;align-items:center;gap:.5rem}
.fejsor .forrasmenu{padding:0;margin:0}
.tema-gomb{width:2.1rem;height:2.1rem;border-radius:50%;display:grid;place-items:center;cursor:pointer;color:#fff;background:rgb(255 255 255/.12);border:1px solid rgb(255 255 255/.35);padding:0}
.tema-gomb:hover{background:rgb(255 255 255/.2)}
.tema-gomb svg{width:1.1rem;height:1.1rem;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;display:none}
:root:not([data-tema]) .t-auto,:root[data-tema="vilagos"] .t-vilagos,:root[data-tema="sotet"] .t-sotet{display:block}
.logo-v{display:block}.logo-v[hidden]{display:none}
.logo{height:4.6rem;width:auto;display:block}
.logo-hirlap{height:3.5rem}
.fej .nap{color:#fff}
.fej .frissitve{color:rgb(255 255 255/.72)}
.fej .forrasmenu summary{background:rgb(255 255 255/.12);border-color:rgb(255 255 255/.35);color:#fff}
.fej .forrasmenu summary::after{color:rgb(255 255 255/.7)}
.fej .forrasmenu summary:hover{background:rgb(255 255 255/.2)}
.fej .panel{color:var(--tinta)}
/* gót betűs fejléc: alatta kettős vonal, mint a hagyományos lapoknál */
.fej-got .fejsor{padding:1.4rem 1.5rem 1.2rem;border-bottom:4px double rgb(255 255 255/.55)}
.fej-got{padding-bottom:.5rem}
.fej-got .logo{height:3.6rem}
/* keskenyebb kijelzőn: a cím felül középen, alatta balra a dátum, jobbra a Források */
@media (max-width:900px){
  .fejsor{grid-template-columns:1fr auto;gap:.8rem 1rem}
  .logo-link{grid-column:1/-1;grid-row:1}
  .fejsor .datum{grid-column:1;grid-row:2}
  .fejsor .fej-gombok{grid-column:2;grid-row:2}
  .fej-got .logo-link{width:100%;max-width:17rem}
  .fej-got .logo{width:100%;height:auto}}
.nev{font:800 2.6rem/1 var(--cimbetu);letter-spacing:-.03em}
.datum{display:flex;flex-direction:column;align-items:flex-end;gap:.15rem;padding-bottom:.4rem}
.nap{font:650 1.05rem/1.2 var(--cimbetu)}
.frissitve{color:var(--halvany);font-size:.85rem}
/* menüsáv: a fejléc színének sötétebb árnyalata (kétszínű, újságos fejléc) */
.rovatnav{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--fejszin,#16202f) 68%,#000);box-shadow:0 1px 0 rgb(0 0 0/.25)}
.nav-belso{max-width:1280px;margin:0 auto;padding:.55rem 1.5rem;display:flex;gap:.4rem;overflow-x:auto;scrollbar-width:none}
.nav-belso a{flex-shrink:0;font-size:.86rem;font-weight:600;padding:.22rem .7rem;border-radius:99px;color:#fff;background:rgb(255 255 255/.1);border:1px solid rgb(255 255 255/.24);display:flex;align-items:center;gap:.45rem;transition:background .15s}
.nav-belso a::before{content:"";width:.5rem;height:.5rem;border-radius:50%;background:var(--r);box-shadow:0 0 0 1.5px rgb(255 255 255/.9)}
.nav-belso a:hover{background:rgb(255 255 255/.2);border-color:rgb(255 255 255/.45)}
.nav-belso a:focus-visible{outline-color:#fff}

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
.tovabbi{margin:.35rem 0 0;font-size:.78rem;color:var(--halvany)}
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
.racs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.9rem}
.doboz.kiemelt{grid-column:span 2;padding:1rem;gap:1.1rem}
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
summary:hover{color:var(--tinta)}
.lista{list-style:none;margin:.5rem 0 0;padding:0;columns:2 440px;column-gap:2.5rem}
.lista li{break-inside:avoid;padding:.45rem 0;border-bottom:1px solid var(--vonal);font-size:.93rem;display:flex;gap:.8rem;align-items:baseline;line-height:1.35}
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
.ido-napok{list-style:none;margin:0;padding:.7rem 0 0;border-top:1px solid var(--vonal);display:grid;grid-template-columns:repeat(5,1fr);gap:.2rem;text-align:center}
.ido-napok li{display:flex;flex-direction:column;align-items:center;gap:.1rem;font-size:.82rem}
.ido-nap{font-size:.74rem;font-weight:600;color:var(--halvany)}
.ido-max{font-weight:700}.ido-min{color:var(--halvany);font-size:.76rem}
.ido-csap{color:#2563eb;font-size:.7rem;min-height:1em}
.ido-nincs{color:var(--halvany);font-size:.88rem}
.ido-ikon{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}
.i-nap{stroke:#f59e0b}.i-felho{stroke:var(--halvany)}.i-csepp{stroke:#3b82f6}.i-ho{stroke:#38bdf8;stroke-width:2.6}.i-villam{stroke:#eab308}
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
  .racs{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:640px){
  .fejsor{padding:.9rem 1rem .8rem}
  .logo{height:3.4rem}
  .logo-ecset{width:100%;max-width:20rem;height:auto}
  .nap{font-size:.95rem}
  .forrasmenu{padding:0}
  .nav-belso,main,.elso-belso{padding-left:1rem;padding-right:1rem}
  .fo-racs{grid-template-columns:1fr}.nyito,.masod,.kis{grid-column:auto}
  .nyito .szoveg{padding:1.1rem}
  .belyeg{width:64px;height:64px}
  .racs{grid-template-columns:1fr}.doboz.kiemelt{grid-column:auto;flex-direction:column}
  .kiemelt-kep{width:100%}
  .lista time{min-width:4.6rem}
  .helyek{grid-template-columns:1fr}}
"""

JS = """
const tarol={get(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}},
             set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
const rejtettForras=new Set(tarol.get('hp_rejtett',[]));
const csukott=new Set(tarol.get('hp_csukott',[]));
function szur(){
  document.querySelectorAll('[data-srcs]').forEach(el=>{
    const s=el.dataset.srcs.split(' ');el.classList.toggle('rejtett',s.every(x=>rejtettForras.has(x)));});
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
const menu=document.querySelector('.forrasmenu');
document.addEventListener('click',ev=>{if(menu.open&&!menu.contains(ev.target))menu.open=false;});
szur();
"""


# Kézírásos fejléc (a Yesteryear betűtípusból rajzolt útvonal, SIL Open Font License)
LOGO_ECSET = '''<svg class="logo logo-ecset" xmlns="http://www.w3.org/2000/svg" viewBox="-148 -1601 9819 2401" role="img" aria-label="Saját Címlap"><path fill="currentColor" d="M1346 -1337Q1347 -1335 1350.0 -1327.5Q1353 -1320 1356.0 -1310.5Q1359 -1301 1361.5 -1291.5Q1364 -1282 1364 -1278Q1364 -1244 1350.0 -1196.5Q1336 -1149 1311.5 -1096.0Q1287 -1043 1254.0 -988.0Q1221 -933 1184.0 -883.5Q1147 -834 1107.5 -793.5Q1068 -753 1030 -729Q1016 -742 996.5 -760.0Q977 -778 955.0 -798.5Q933 -819 911.0 -840.0Q889 -861 870 -881Q899 -904 935.5 -941.5Q972 -979 1009.5 -1025.0Q1047 -1071 1083.0 -1120.0Q1119 -1169 1145.5 -1214.5Q1172 -1260 1187.0 -1297.0Q1202 -1334 1198 -1356Q1195 -1367 1188.5 -1371.5Q1182 -1376 1165 -1376Q1104 -1376 1024.0 -1352.0Q944 -1328 857.0 -1282.5Q770 -1237 681.5 -1171.0Q593 -1105 515.5 -1022.0Q438 -939 377.5 -840.0Q317 -741 285 -629Q355 -630 418.0 -634.5Q481 -639 538.0 -644.0Q595 -649 647.0 -653.0Q699 -657 748 -657Q774 -657 804.0 -629.0Q834 -601 860.0 -562.0Q886 -523 903.0 -482.0Q920 -441 920 -416Q920 -345 883.5 -273.5Q847 -202 785.5 -135.5Q724 -69 643.0 -11.5Q562 46 472.5 88.5Q383 131 290.0 155.5Q197 180 113 180Q102 171 84.0 152.5Q66 134 45.5 111.5Q25 89 3.5 64.0Q-18 39 -36.5 16.0Q-55 -7 -69.0 -26.0Q-83 -45 -88 -55Q-33 -47 44.5 -51.0Q122 -55 208.0 -73.0Q294 -91 381.5 -124.5Q469 -158 545.0 -209.0Q621 -260 679.0 -330.5Q737 -401 763 -494Q705 -493 658.0 -488.5Q611 -484 567.0 -478.5Q523 -473 477.0 -469.0Q431 -465 375 -465Q357 -465 336.0 -465.5Q315 -466 294.0 -468.5Q273 -471 254.0 -477.0Q235 -483 223 -494Q196 -517 171.0 -542.5Q146 -568 126.0 -594.5Q106 -621 94.0 -648.0Q82 -675 82 -700Q82 -776 120.0 -869.5Q158 -963 225.5 -1058.0Q293 -1153 385.0 -1242.5Q477 -1332 585.5 -1402.0Q694 -1472 814.0 -1514.0Q934 -1556 1057 -1556Q1100 -1556 1135.5 -1543.0Q1171 -1530 1200.0 -1509.0Q1229 -1488 1252.0 -1462.5Q1275 -1437 1293.0 -1413.0Q1311 -1389 1324.0 -1368.5Q1337 -1348 1346 -1337ZM1751.0 -88Q1751.0 -82 1757.0 -82Q1766.0 -82 1787.0 -95.0Q1808.0 -108 1835.0 -128.0Q1862.0 -148 1892.5 -173.0Q1923.0 -198 1951.0 -222.0Q1979.0 -246 2001.5 -266.0Q2024.0 -286 2036.0 -297Q2045.0 -288 2058.5 -273.0Q2072.0 -258 2083.0 -246Q2059.0 -224 2025.5 -192.5Q1992.0 -161 1953.5 -127.0Q1915.0 -93 1874.0 -59.5Q1833.0 -26 1794.0 0.0Q1755.0 26 1720.0 42.5Q1685.0 59 1659.0 59Q1641.0 59 1619.5 52.0Q1598.0 45 1580.0 32.5Q1562.0 20 1550.0 3.0Q1538.0 -14 1538.0 -35Q1538.0 -43 1537.5 -54.5Q1537.0 -66 1539.0 -84.5Q1541.0 -103 1547.0 -129.5Q1553.0 -156 1565.0 -195Q1532.0 -160 1487.5 -122.0Q1443.0 -84 1394.0 -50.0Q1345.0 -16 1295.5 11.5Q1246.0 39 1204.0 53Q1199.0 55 1196.0 55.0Q1193.0 55 1190.0 55Q1180.0 55 1163.5 45.0Q1147.0 35 1131.0 18.5Q1115.0 2 1103.5 -19.0Q1092.0 -40 1092.0 -61Q1092.0 -67 1098.0 -101.0Q1104.0 -135 1126.5 -187.5Q1149.0 -240 1193.0 -305.5Q1237.0 -371 1313.0 -440Q1352.0 -475 1397.0 -506Q1435.0 -532 1484.0 -559.0Q1533.0 -586 1587.0 -602Q1589.0 -602 1590.5 -603.0Q1592.0 -604 1595.0 -602Q1607.0 -598 1623.5 -589.5Q1640.0 -581 1656.5 -571.5Q1673.0 -562 1685.5 -552.5Q1698.0 -543 1702.0 -537Q1703.0 -535 1702.0 -534L1700.0 -532Q1655.0 -508 1615.0 -478.5Q1575.0 -449 1544.0 -423Q1508.0 -392 1477.0 -362Q1404.0 -290 1376.0 -237.5Q1348.0 -185 1348.0 -162Q1348.0 -145 1362.0 -145Q1376.0 -145 1397.5 -156.5Q1419.0 -168 1446.0 -187.5Q1473.0 -207 1505.5 -233.5Q1538.0 -260 1573.0 -289Q1604.0 -314 1633.0 -343Q1658.0 -367 1685.0 -398.0Q1712.0 -429 1731.0 -461Q1731.0 -462 1732.0 -463Q1733.0 -464 1735.0 -463Q1746.0 -455 1760.0 -436.5Q1774.0 -418 1786.0 -396.0Q1798.0 -374 1806.5 -353.5Q1815.0 -333 1815.0 -322Q1792.0 -275 1779.5 -236.5Q1767.0 -198 1760.5 -169.0Q1754.0 -140 1752.5 -119.5Q1751.0 -99 1751.0 -88ZM2532.0 -246Q2441.0 -184 2345.5 -124.0Q2250.0 -64 2156.0 -4Q2139.0 48 2118.5 105.5Q2098.0 163 2065.0 240Q2021.0 340 1961.5 424.5Q1902.0 509 1838.5 570.5Q1775.0 632 1713.0 667.0Q1651.0 702 1602.0 702Q1586.0 702 1564.5 685.5Q1543.0 669 1523.5 646.5Q1504.0 624 1490.5 600.0Q1477.0 576 1477.0 561Q1477.0 502 1511.0 440.5Q1545.0 379 1605.0 316.0Q1665.0 253 1747.0 188.5Q1829.0 124 1925.0 58Q1951.0 -16 1976.0 -93.5Q2001.0 -171 2023.5 -244.5Q2046.0 -318 2065.5 -383.5Q2085.0 -449 2100.0 -498.0Q2115.0 -547 2125.0 -575.5Q2135.0 -604 2138.0 -604Q2144.0 -604 2169.5 -589.0Q2195.0 -574 2222.5 -553.5Q2250.0 -533 2272.0 -512.5Q2294.0 -492 2294.0 -481Q2294.0 -476 2293.0 -472.0Q2292.0 -468 2290.0 -463Q2270.0 -403 2255.0 -353.0Q2240.0 -303 2228.0 -260.5Q2216.0 -218 2206.0 -181.5Q2196.0 -145 2187.0 -112Q2262.0 -159 2337.0 -205.0Q2412.0 -251 2487.0 -299ZM2452.0 -889Q2451.0 -885 2444.5 -869.5Q2438.0 -854 2429.0 -833.5Q2420.0 -813 2408.5 -790.5Q2397.0 -768 2386.0 -749.0Q2375.0 -730 2365.0 -717.5Q2355.0 -705 2349.0 -705Q2331.0 -706 2305.5 -716.0Q2280.0 -726 2256.5 -738.5Q2233.0 -751 2216.5 -763.0Q2200.0 -775 2200.0 -780Q2200.0 -802 2212.0 -836.0Q2224.0 -870 2240.5 -901.0Q2257.0 -932 2273.5 -954.5Q2290.0 -977 2298.0 -977Q2316.0 -977 2342.0 -965.5Q2368.0 -954 2392.5 -939.0Q2417.0 -924 2434.5 -909.5Q2452.0 -895 2452.0 -889ZM1839.0 274Q1849.0 254 1858.0 232.0Q1867.0 210 1877.0 186Q1813.0 234 1760.0 283.0Q1707.0 332 1668.5 381.0Q1630.0 430 1608.5 479.5Q1587.0 529 1587.0 580Q1587.0 594 1598.0 594Q1616.0 594 1646.5 565.5Q1677.0 537 1711.0 491.0Q1745.0 445 1779.0 388.0Q1813.0 331 1839.0 274ZM3039.0 -88Q3039.0 -82 3045.0 -82Q3054.0 -82 3075.0 -95.0Q3096.0 -108 3123.0 -128.0Q3150.0 -148 3180.5 -173.0Q3211.0 -198 3239.0 -222.0Q3267.0 -246 3289.5 -266.0Q3312.0 -286 3324.0 -297Q3333.0 -288 3346.5 -273.0Q3360.0 -258 3371.0 -246Q3347.0 -224 3313.5 -192.5Q3280.0 -161 3241.5 -127.0Q3203.0 -93 3162.0 -59.5Q3121.0 -26 3082.0 0.0Q3043.0 26 3008.0 42.5Q2973.0 59 2947.0 59Q2929.0 59 2907.5 52.0Q2886.0 45 2868.0 32.5Q2850.0 20 2838.0 3.0Q2826.0 -14 2826.0 -35Q2826.0 -43 2825.5 -54.5Q2825.0 -66 2827.0 -84.5Q2829.0 -103 2835.0 -129.5Q2841.0 -156 2853.0 -195Q2820.0 -160 2775.5 -122.0Q2731.0 -84 2682.0 -50.0Q2633.0 -16 2583.5 11.5Q2534.0 39 2492.0 53Q2487.0 55 2484.0 55.0Q2481.0 55 2478.0 55Q2468.0 55 2451.5 45.0Q2435.0 35 2419.0 18.5Q2403.0 2 2391.5 -19.0Q2380.0 -40 2380.0 -61Q2380.0 -67 2386.0 -101.0Q2392.0 -135 2414.5 -187.5Q2437.0 -240 2481.0 -305.5Q2525.0 -371 2601.0 -440Q2640.0 -475 2685.0 -506Q2723.0 -532 2772.0 -559.0Q2821.0 -586 2875.0 -602Q2877.0 -602 2878.5 -603.0Q2880.0 -604 2883.0 -602Q2895.0 -598 2911.5 -589.5Q2928.0 -581 2944.5 -571.5Q2961.0 -562 2973.5 -552.5Q2986.0 -543 2990.0 -537Q2991.0 -535 2990.0 -534L2988.0 -532Q2943.0 -508 2903.0 -478.5Q2863.0 -449 2832.0 -423Q2796.0 -392 2765.0 -362Q2692.0 -290 2664.0 -237.5Q2636.0 -185 2636.0 -162Q2636.0 -145 2650.0 -145Q2664.0 -145 2685.5 -156.5Q2707.0 -168 2734.0 -187.5Q2761.0 -207 2793.5 -233.5Q2826.0 -260 2861.0 -289Q2892.0 -314 2921.0 -343Q2946.0 -367 2973.0 -398.0Q3000.0 -429 3019.0 -461Q3019.0 -462 3020.0 -463Q3021.0 -464 3023.0 -463Q3034.0 -455 3048.0 -436.5Q3062.0 -418 3074.0 -396.0Q3086.0 -374 3094.5 -353.5Q3103.0 -333 3103.0 -322Q3080.0 -275 3067.5 -236.5Q3055.0 -198 3048.5 -169.0Q3042.0 -140 3040.5 -119.5Q3039.0 -99 3039.0 -88ZM3232.0 -930Q3176.0 -861 3111.0 -799.0Q3046.0 -737 2980.0 -684Q2975.0 -687 2964.5 -695.0Q2954.0 -703 2943.0 -713.0Q2932.0 -723 2922.0 -731.5Q2912.0 -740 2908.0 -745Q2933.0 -776 2964.0 -818.0Q2995.0 -860 3024.0 -901.5Q3053.0 -943 3076.0 -977.5Q3099.0 -1012 3109.0 -1030Q3130.0 -1020 3149.5 -1004.5Q3169.0 -989 3185.5 -974.0Q3202.0 -959 3214.0 -946.5Q3226.0 -934 3232.0 -930ZM3420.0 55Q3403.0 55 3375.5 39.5Q3348.0 24 3322.0 1.5Q3296.0 -21 3277.0 -45.0Q3258.0 -69 3258.0 -86Q3258.0 -116 3274.5 -181.0Q3291.0 -246 3318.0 -329.5Q3345.0 -413 3379.5 -508.0Q3414.0 -603 3449.0 -694H3308.0H3307.0Q3306.0 -694 3306.0 -696Q3306.0 -702 3310.5 -713.5Q3315.0 -725 3321.5 -737.5Q3328.0 -750 3336.5 -760.5Q3345.0 -771 3353.0 -774L3483.0 -780Q3511.0 -848 3536.5 -905.5Q3562.0 -963 3582.0 -1004Q3587.0 -1015 3592.5 -1027.5Q3598.0 -1040 3608.5 -1052.5Q3619.0 -1065 3636.5 -1078.5Q3654.0 -1092 3684.0 -1106Q3706.0 -1117 3737.5 -1130.0Q3769.0 -1143 3799.5 -1154.0Q3830.0 -1165 3855.0 -1172.5Q3880.0 -1180 3891.0 -1180Q3901.0 -1180 3901.0 -1176V-1174Q3901.0 -1173 3899.0 -1169Q3893.0 -1159 3877.0 -1125.5Q3861.0 -1092 3837.5 -1041.5Q3814.0 -991 3785.5 -926.5Q3757.0 -862 3727.0 -790L3967.0 -799Q3977.0 -799 3973.0 -787.0Q3969.0 -775 3958.0 -759.0Q3947.0 -743 3932.5 -726.0Q3918.0 -709 3908.0 -700Q3906.0 -698 3900.0 -696.0Q3894.0 -694 3885.0 -694H3687.0Q3651.0 -607 3618.0 -518.0Q3585.0 -429 3559.0 -349.0Q3533.0 -269 3517.5 -202.0Q3502.0 -135 3502.0 -92Q3502.0 -86 3508.0 -86Q3519.0 -86 3541.0 -98.5Q3563.0 -111 3590.5 -131.0Q3618.0 -151 3649.0 -175.0Q3680.0 -199 3708.0 -222.5Q3736.0 -246 3758.5 -265.5Q3781.0 -285 3793.0 -295Q3802.0 -286 3814.5 -272.0Q3827.0 -258 3838.0 -246Q3816.0 -224 3783.0 -192.5Q3750.0 -161 3711.5 -128.0Q3673.0 -95 3631.5 -62.0Q3590.0 -29 3550.5 -3.0Q3511.0 23 3477.0 39.0Q3443.0 55 3420.0 55ZM5289.8 -893Q5319.8 -912 5353.3 -941.5Q5386.8 -971 5420.3 -1008.5Q5453.8 -1046 5484.8 -1089.5Q5515.8 -1133 5539.3 -1177.5Q5562.8 -1222 5576.8 -1266.5Q5590.8 -1311 5590.8 -1352Q5590.8 -1383 5585.8 -1402.0Q5580.8 -1421 5572.3 -1431.5Q5563.8 -1442 5552.8 -1446.0Q5541.8 -1450 5529.8 -1450Q5481.8 -1450 5420.8 -1409.5Q5359.8 -1369 5292.3 -1299.5Q5224.8 -1230 5154.3 -1137.0Q5083.8 -1044 5017.8 -937.5Q4951.8 -831 4893.3 -718.0Q4834.8 -605 4791.3 -495.0Q4747.8 -385 4722.3 -284.5Q4696.8 -184 4696.8 -104Q4696.8 -74 4701.3 -54.5Q4705.8 -35 4714.3 -23.5Q4722.8 -12 4735.8 -8.0Q4748.8 -4 4765.8 -4Q4794.8 -4 4834.3 -28.5Q4873.8 -53 4918.3 -92.0Q4962.8 -131 5008.3 -179.0Q5053.8 -227 5093.8 -273.5Q5133.8 -320 5165.8 -359.5Q5197.8 -399 5214.8 -422Q5214.8 -421 5239.3 -401.0Q5263.8 -381 5296.8 -353.0Q5329.8 -325 5362.8 -296.5Q5395.8 -268 5412.8 -250Q5388.8 -223 5351.8 -181.0Q5314.8 -139 5268.3 -92.5Q5221.8 -46 5168.8 1.0Q5115.8 48 5059.8 85.5Q5003.8 123 4946.8 146.5Q4889.8 170 4835.8 170Q4796.8 170 4752.8 146.5Q4708.8 123 4665.8 85.5Q4622.8 48 4585.8 2.5Q4548.8 -43 4523.8 -88Q4493.8 -144 4488.8 -191.0Q4483.8 -238 4483.8 -266Q4483.8 -343 4505.8 -440.0Q4527.8 -537 4568.8 -642.5Q4609.8 -748 4667.8 -857.0Q4725.8 -966 4796.3 -1068.0Q4866.8 -1170 4949.3 -1260.0Q5031.8 -1350 5122.3 -1417.0Q5212.8 -1484 5309.3 -1522.5Q5405.8 -1561 5504.8 -1561Q5532.8 -1561 5563.8 -1546.5Q5594.8 -1532 5624.8 -1508.5Q5654.8 -1485 5682.8 -1454.0Q5710.8 -1423 5731.8 -1390.5Q5752.8 -1358 5765.3 -1325.5Q5777.8 -1293 5777.8 -1266Q5777.8 -1206 5753.8 -1133.5Q5729.8 -1061 5688.8 -987.5Q5647.8 -914 5591.3 -844.0Q5534.8 -774 5470.8 -719ZM5641.8 55Q5626.8 55 5603.8 44.5Q5580.8 34 5559.8 18.5Q5538.8 3 5523.8 -16.0Q5508.8 -35 5508.8 -53Q5518.8 -97 5535.8 -154.0Q5552.8 -211 5572.3 -270.0Q5591.8 -329 5613.3 -385.5Q5634.8 -442 5652.3 -486.0Q5669.8 -530 5682.8 -557.0Q5695.8 -584 5699.8 -584Q5705.8 -584 5729.8 -571.0Q5753.8 -558 5780.3 -539.5Q5806.8 -521 5827.8 -500.5Q5848.8 -480 5848.8 -465Q5848.8 -462 5847.8 -457.5Q5846.8 -453 5844.8 -446Q5836.8 -421 5824.3 -387.0Q5811.8 -353 5797.8 -316.5Q5783.8 -280 5769.8 -243.5Q5755.8 -207 5744.8 -175.0Q5733.8 -143 5726.8 -119.0Q5719.8 -95 5719.8 -84Q5719.8 -78 5725.8 -78Q5736.8 -78 5758.8 -91.0Q5780.8 -104 5809.3 -125.0Q5837.8 -146 5868.8 -171.0Q5899.8 -196 5928.3 -220.5Q5956.8 -245 5979.8 -265.0Q6002.8 -285 6014.8 -295Q6023.8 -286 6036.3 -272.0Q6048.8 -258 6059.8 -246Q6037.8 -224 6004.8 -192.5Q5971.8 -161 5933.3 -128.0Q5894.8 -95 5853.3 -62.0Q5811.8 -29 5772.8 -3.0Q5733.8 23 5699.8 39.0Q5665.8 55 5641.8 55ZM6104.8 -930Q6048.8 -861 5983.8 -799.0Q5918.8 -737 5852.8 -684Q5847.8 -687 5837.3 -695.0Q5826.8 -703 5815.8 -713.0Q5804.8 -723 5794.8 -731.5Q5784.8 -740 5780.8 -745Q5805.8 -776 5836.8 -818.0Q5867.8 -860 5896.8 -901.5Q5925.8 -943 5948.8 -977.5Q5971.8 -1012 5981.8 -1030Q6002.8 -1020 6022.3 -1004.5Q6041.8 -989 6058.3 -974.0Q6074.8 -959 6086.8 -946.5Q6098.8 -934 6104.8 -930ZM6274.8 -483Q6262.8 -452 6250.8 -419.0Q6238.8 -386 6227.8 -352Q6290.8 -427 6332.3 -472.0Q6373.8 -517 6401.3 -542.0Q6428.8 -567 6446.8 -577.0Q6464.8 -587 6479.8 -592Q6488.8 -592 6510.3 -582.0Q6531.8 -572 6556.3 -557.5Q6580.8 -543 6602.8 -527.5Q6624.8 -512 6633.8 -502Q6641.8 -494 6635.8 -479Q6623.8 -447 6612.3 -414.5Q6600.8 -382 6589.8 -348Q6656.8 -425 6701.8 -471.5Q6746.8 -518 6775.3 -543.0Q6803.8 -568 6819.8 -576.0Q6835.8 -584 6844.8 -584Q6857.8 -584 6883.8 -571.0Q6909.8 -558 6936.3 -539.0Q6962.8 -520 6982.3 -500.0Q7001.8 -480 7001.8 -465Q7001.8 -462 7000.8 -457.5Q6999.8 -453 6997.8 -446Q6989.8 -421 6977.3 -387.0Q6964.8 -353 6950.8 -316.5Q6936.8 -280 6922.8 -243.5Q6908.8 -207 6897.8 -175.0Q6886.8 -143 6879.8 -119.0Q6872.8 -95 6872.8 -84Q6872.8 -78 6878.8 -78Q6889.8 -78 6911.8 -91.0Q6933.8 -104 6962.3 -125.0Q6990.8 -146 7021.8 -171.0Q7052.8 -196 7081.3 -220.5Q7109.8 -245 7132.8 -265.0Q7155.8 -285 7167.8 -295Q7176.8 -286 7189.3 -272.0Q7201.8 -258 7212.8 -246Q7190.8 -224 7157.8 -192.5Q7124.8 -161 7086.3 -128.0Q7047.8 -95 7006.3 -62.0Q6964.8 -29 6925.8 -3.0Q6886.8 23 6852.8 39.0Q6818.8 55 6794.8 55Q6779.8 55 6756.8 44.5Q6733.8 34 6712.8 18.5Q6691.8 3 6676.8 -16.0Q6661.8 -35 6661.8 -53Q6670.8 -94 6685.8 -146.0Q6700.8 -198 6718.8 -252.5Q6736.8 -307 6756.3 -360.0Q6775.8 -413 6793.8 -458Q6773.8 -434 6740.8 -400.0Q6707.8 -366 6665.8 -310.5Q6623.8 -255 6575.8 -173.0Q6527.8 -91 6479.8 27Q6473.8 47 6450.8 47Q6438.8 47 6421.3 42.5Q6403.8 38 6384.3 28.5Q6364.8 19 6344.3 5.0Q6323.8 -9 6305.8 -29Q6305.8 -36 6313.3 -71.5Q6320.8 -107 6335.3 -162.0Q6349.8 -217 6369.8 -285.0Q6389.8 -353 6414.8 -425Q6390.8 -399 6354.8 -358.5Q6318.8 -318 6277.8 -260.5Q6236.8 -203 6193.8 -127.0Q6150.8 -51 6112.8 45Q6106.8 66 6084.8 66Q6067.8 66 6046.3 56.5Q6024.8 47 6002.3 31.5Q5979.8 16 5957.3 -3.0Q5934.8 -22 5916.8 -41Q5916.8 -47 5922.3 -75.0Q5927.8 -103 5937.8 -145.5Q5947.8 -188 5962.3 -242.5Q5976.8 -297 5995.8 -357.0Q6014.8 -417 6037.8 -479.5Q6060.8 -542 6086.8 -600Q6128.8 -580 6180.3 -551.0Q6231.8 -522 6274.8 -483ZM7376.8 -70Q7392.8 -70 7420.3 -84.0Q7447.8 -98 7480.8 -119.5Q7513.8 -141 7549.8 -167.0Q7585.8 -193 7617.3 -218.0Q7648.8 -243 7673.8 -264.0Q7698.8 -285 7710.8 -295Q7719.8 -286 7732.3 -272.0Q7744.8 -258 7755.8 -246Q7733.8 -224 7700.8 -192.5Q7667.8 -161 7629.3 -128.0Q7590.8 -95 7549.3 -62.0Q7507.8 -29 7468.3 -3.0Q7428.8 23 7394.8 39.0Q7360.8 55 7337.8 55Q7306.8 55 7272.3 33.0Q7237.8 11 7207.8 -18.5Q7177.8 -48 7156.8 -78.0Q7135.8 -108 7132.8 -123Q7130.8 -129 7130.8 -137.5Q7130.8 -146 7130.8 -156Q7130.8 -206 7143.3 -282.5Q7155.8 -359 7176.8 -444.5Q7197.8 -530 7225.3 -614.5Q7252.8 -699 7282.8 -764Q7343.8 -897 7408.3 -1000.0Q7472.8 -1103 7534.8 -1179.5Q7596.8 -1256 7653.8 -1308.0Q7710.8 -1360 7756.3 -1392.0Q7801.8 -1424 7833.3 -1438.0Q7864.8 -1452 7876.8 -1452Q7895.8 -1445 7915.3 -1431.5Q7934.8 -1418 7951.8 -1403.0Q7968.8 -1388 7981.8 -1373.5Q7994.8 -1359 8000.8 -1352Q8008.8 -1344 8011.3 -1336.5Q8013.8 -1329 8013.8 -1317Q8013.8 -1293 8001.8 -1251.0Q7989.8 -1209 7956.8 -1144.5Q7923.8 -1080 7865.3 -990.0Q7806.8 -900 7712.8 -780Q7668.8 -724 7619.8 -666Q7577.8 -616 7522.8 -557.0Q7467.8 -498 7409.8 -444Q7380.8 -334 7369.3 -250.0Q7357.8 -166 7357.8 -119Q7357.8 -88 7362.8 -79.0Q7367.8 -70 7376.8 -70ZM7536.8 -803Q7519.8 -765 7505.3 -729.0Q7490.8 -693 7477.8 -658Q7508.8 -693 7534.3 -725.0Q7559.8 -757 7577.8 -780Q7598.8 -807 7615.8 -831Q7685.8 -929 7736.8 -1011.0Q7787.8 -1093 7820.8 -1155.5Q7853.8 -1218 7869.3 -1259.5Q7884.8 -1301 7884.8 -1319Q7884.8 -1324 7884.3 -1326.5Q7883.8 -1329 7878.8 -1329Q7869.8 -1329 7837.8 -1299.5Q7805.8 -1270 7759.3 -1206.0Q7712.8 -1142 7655.3 -1042.5Q7597.8 -943 7536.8 -803ZM8271.8 -88Q8271.8 -82 8277.8 -82Q8286.8 -82 8307.8 -95.0Q8328.8 -108 8355.8 -128.0Q8382.8 -148 8413.3 -173.0Q8443.8 -198 8471.8 -222.0Q8499.8 -246 8522.3 -266.0Q8544.8 -286 8556.8 -297Q8565.8 -288 8579.3 -273.0Q8592.8 -258 8603.8 -246Q8579.8 -224 8546.3 -192.5Q8512.8 -161 8474.3 -127.0Q8435.8 -93 8394.8 -59.5Q8353.8 -26 8314.8 0.0Q8275.8 26 8240.8 42.5Q8205.8 59 8179.8 59Q8161.8 59 8140.3 52.0Q8118.8 45 8100.8 32.5Q8082.8 20 8070.8 3.0Q8058.8 -14 8058.8 -35Q8058.8 -43 8058.3 -54.5Q8057.8 -66 8059.8 -84.5Q8061.8 -103 8067.8 -129.5Q8073.8 -156 8085.8 -195Q8052.8 -160 8008.3 -122.0Q7963.8 -84 7914.8 -50.0Q7865.8 -16 7816.3 11.5Q7766.8 39 7724.8 53Q7719.8 55 7716.8 55.0Q7713.8 55 7710.8 55Q7700.8 55 7684.3 45.0Q7667.8 35 7651.8 18.5Q7635.8 2 7624.3 -19.0Q7612.8 -40 7612.8 -61Q7612.8 -67 7618.8 -101.0Q7624.8 -135 7647.3 -187.5Q7669.8 -240 7713.8 -305.5Q7757.8 -371 7833.8 -440Q7872.8 -475 7917.8 -506Q7955.8 -532 8004.8 -559.0Q8053.8 -586 8107.8 -602Q8109.8 -602 8111.3 -603.0Q8112.8 -604 8115.8 -602Q8127.8 -598 8144.3 -589.5Q8160.8 -581 8177.299999999999 -571.5Q8193.8 -562 8206.3 -552.5Q8218.8 -543 8222.8 -537Q8223.8 -535 8222.8 -534L8220.8 -532Q8175.8 -508 8135.8 -478.5Q8095.8 -449 8064.8 -423Q8028.8 -392 7997.8 -362Q7924.8 -290 7896.8 -237.5Q7868.8 -185 7868.8 -162Q7868.8 -145 7882.8 -145Q7896.8 -145 7918.3 -156.5Q7939.8 -168 7966.8 -187.5Q7993.8 -207 8026.3 -233.5Q8058.8 -260 8093.8 -289Q8124.8 -314 8153.8 -343Q8178.8 -367 8205.8 -398.0Q8232.8 -429 8251.8 -461Q8251.8 -462 8252.8 -463Q8253.8 -464 8255.8 -463Q8266.8 -455 8280.8 -436.5Q8294.8 -418 8306.8 -396.0Q8318.8 -374 8327.3 -353.5Q8335.8 -333 8335.8 -322Q8312.8 -275 8300.3 -236.5Q8287.8 -198 8281.3 -169.0Q8274.8 -140 8273.3 -119.5Q8271.8 -99 8271.8 -88ZM8774.8 -369Q8828.8 -436 8873.8 -479.5Q8918.8 -523 8953.3 -548.5Q8987.8 -574 9011.8 -584.0Q9035.8 -594 9047.8 -594Q9063.8 -594 9082.8 -582.0Q9101.8 -570 9120.3 -553.0Q9138.8 -536 9154.8 -518.0Q9170.8 -500 9181.8 -489Q9192.8 -478 9195.3 -465.5Q9197.8 -453 9197.8 -440Q9197.8 -393 9181.8 -337.5Q9165.8 -282 9139.8 -227.5Q9113.8 -173 9080.8 -124.0Q9047.8 -75 9013.8 -41Q9042.8 -53 9073.3 -70.5Q9103.8 -88 9133.8 -108.0Q9163.8 -128 9192.8 -149.5Q9221.8 -171 9247.8 -192Q9308.8 -240 9365.8 -295L9410.8 -246Q9377.8 -215 9337.3 -180.0Q9296.8 -145 9252.8 -111.0Q9208.8 -77 9163.3 -46.0Q9117.8 -15 9075.8 9.0Q9033.8 33 8997.8 47.0Q8961.8 61 8935.8 61Q8917.8 61 8890.8 50.5Q8863.8 40 8838.3 25.0Q8812.8 10 8794.3 -7.5Q8775.8 -25 8775.8 -39Q8775.8 -47 8786.3 -53.5Q8796.8 -60 8816.8 -77.0Q8836.8 -94 8864.8 -128.0Q8892.8 -162 8927.8 -227Q8946.8 -263 8963.8 -301.5Q8980.8 -340 8993.8 -373.5Q9006.8 -407 9014.3 -431.5Q9021.8 -456 9021.8 -465Q9021.8 -474 9019.3 -477.5Q9016.8 -481 9014.8 -483Q9010.8 -484 9007.8 -483Q8993.8 -479 8951.3 -438.5Q8908.8 -398 8851.8 -322.5Q8794.8 -247 8730.8 -138.5Q8666.8 -30 8609.8 109Q8562.8 243 8515.8 380.0Q8468.8 517 8423.8 653L8169.799999999999 713Q8227.8 548 8290.3 385.5Q8352.8 223 8414.3 60.5Q8475.8 -102 8535.3 -265.0Q8594.8 -428 8648.8 -592Q8651.8 -603 8654.3 -607.5Q8656.8 -612 8664.8 -612Q8680.8 -607 8703.8 -593.5Q8726.8 -580 8749.3 -562.5Q8771.8 -545 8789.8 -527.0Q8807.8 -509 8814.8 -496Q8816.8 -493 8814.3 -485.5Q8811.8 -478 8808.8 -469Z"/><path fill="currentColor" d="M 60 330 C 2300 520, 6300 470, 9450 -10 C 6400 690, 2300 790, 60 330 Z"/></svg>'''


# Böngészőfül- és kezdőképernyő-ikon (az „S” a Yesteryear betűtípusból)
IKON_ECSET = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Cpath fill=%22%23f2f5f9%22 stroke=%22%23f2f5f9%22 stroke-width=%221.6%22 stroke-linejoin=%22round%22 d=%22M52.57 14.05Q52.60 14.11 52.68 14.31Q52.76 14.52 52.85 14.78Q52.93 15.05 53.00 15.31Q53.07 15.57 53.07 15.68Q53.07 16.62 52.68 17.94Q52.29 19.25 51.62 20.71Q50.94 22.18 50.03 23.70Q49.11 25.22 48.09 26.59Q47.07 27.96 45.98 29.08Q44.88 30.20 43.83 30.86Q43.45 30.50 42.91 30.00Q42.37 29.51 41.76 28.94Q41.15 28.37 40.54 27.79Q39.94 27.21 39.41 26.66Q40.21 26.02 41.22 24.99Q42.23 23.95 43.27 22.68Q44.30 21.41 45.30 20.05Q46.29 18.70 47.03 17.44Q47.76 16.18 48.17 15.16Q48.59 14.13 48.48 13.52Q48.40 13.22 48.22 13.10Q48.04 12.97 47.57 12.97Q45.88 12.97 43.67 13.64Q41.46 14.30 39.05 15.56Q36.64 16.82 34.20 18.64Q31.75 20.47 29.61 22.76Q27.47 25.05 25.79 27.79Q24.12 30.53 23.23 33.63Q25.17 33.60 26.91 33.47Q28.65 33.35 30.23 33.21Q31.81 33.07 33.24 32.96Q34.68 32.85 36.04 32.85Q36.76 32.85 37.58 33.63Q38.41 34.40 39.13 35.48Q39.85 36.56 40.32 37.69Q40.79 38.82 40.79 39.52Q40.79 41.48 39.78 43.46Q38.77 45.43 37.07 47.27Q35.37 49.11 33.13 50.70Q30.89 52.29 28.42 53.47Q25.94 54.64 23.37 55.32Q20.80 56.0 18.48 56.0Q18.17 55.75 17.68 55.23Q17.18 54.72 16.61 54.10Q16.05 53.48 15.45 52.79Q14.86 52.10 14.35 51.46Q13.83 50.82 13.45 50.30Q13.06 49.77 12.92 49.50Q14.44 49.72 16.58 49.61Q18.73 49.50 21.11 49.00Q23.48 48.50 25.90 47.58Q28.32 46.65 30.42 45.24Q32.52 43.83 34.13 41.88Q35.73 39.93 36.45 37.36Q34.85 37.39 33.55 37.51Q32.25 37.64 31.03 37.79Q29.82 37.94 28.54 38.05Q27.27 38.16 25.72 38.16Q25.23 38.16 24.64 38.15Q24.06 38.13 23.48 38.06Q22.90 38.0 22.38 37.83Q21.85 37.66 21.52 37.36Q20.77 36.72 20.08 36.02Q19.39 35.31 18.84 34.58Q18.29 33.85 17.95 33.10Q17.62 32.35 17.62 31.66Q17.62 29.56 18.67 26.98Q19.72 24.39 21.59 21.76Q23.46 19.14 26.00 16.66Q28.54 14.19 31.54 12.25Q34.54 10.32 37.86 9.16Q41.18 8.0 44.58 8.0Q45.77 8.0 46.75 8.35Q47.73 8.71 48.53 9.29Q49.34 9.88 49.97 10.58Q50.61 11.29 51.11 11.95Q51.60 12.61 51.96 13.18Q52.32 13.75 52.57 14.05Z%22/%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,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">'
    '<link rel="apple-touch-icon" href="data:image/png;base64,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">'
    '<meta name="theme-color" content="#16202f"><meta name="apple-mobile-web-app-title" content="Saját Címlap">'
)

# Pesti Hírlap-szerű fejléc: keskeny, vastag talpatlan kiskapitális (Oswald betűtípus, SIL Open Font License)
LOGO_HIRLAP = '''<svg class="logo logo-hirlap" xmlns="http://www.w3.org/2000/svg" viewBox="32 -847 4855 873" role="img" aria-label="Saját Címlap"><path fill="currentColor" d="M272.7 11.6Q197.4 11.6 146.8 -15.6Q96.3 -42.9 70.5 -99.0Q44.7 -155.2 41.6 -243.0L189.0 -266.5Q190.2 -215.4 198.9 -182.6Q207.6 -149.7 224.0 -134.5Q240.4 -119.3 264.7 -119.3Q294.4 -119.3 304.4 -138.9Q314.4 -158.4 314.4 -182.1Q314.4 -232.6 290.2 -267.0Q266.1 -301.4 225.5 -336.3L148.4 -403.6Q103.1 -442.2 72.9 -490.8Q42.7 -539.5 42.7 -611.2Q42.7 -712.7 102.7 -766.6Q162.6 -820.5 265.8 -820.5Q329.0 -820.5 368.2 -799.5Q407.4 -778.5 428.3 -744.1Q449.2 -709.6 457.4 -670.1Q465.7 -630.6 467.1 -592.8L319.3 -573.0Q318.1 -609.4 314.0 -636.7Q309.9 -663.9 297.7 -679.1Q285.5 -694.2 260.4 -694.2Q233.6 -694.2 220.9 -673.0Q208.1 -651.9 208.1 -628.4Q208.1 -584.3 228.6 -556.3Q249.2 -528.2 283.4 -497.9L357.9 -432.4Q410.1 -387.9 446.5 -331.1Q482.8 -274.2 482.8 -191.1Q482.8 -133.8 456.9 -87.6Q431.0 -41.4 383.9 -14.8Q336.9 11.6 272.7 11.6ZM538.2 0.0 659.1 -631.8H810.3L929.9 0.0H806.0L784.1 -136.1H687.5L664.8 0.0ZM699.9 -217.4H771.1L735.4 -467.9ZM968.7 16.2V-77.6Q990.6 -77.6 1005.7 -80.1Q1020.9 -82.6 1029.6 -89.6Q1038.3 -96.6 1042.2 -110.1Q1046.1 -123.7 1046.1 -144.9V-631.8H1180.2V-139.2Q1180.2 -87.4 1167.4 -52.7Q1154.7 -18.0 1122.9 -0.9Q1091.2 16.2 1033.8 16.2ZM1255.6 0.0 1376.5 -631.8H1527.7L1647.3 0.0H1523.5L1501.6 -136.1H1404.9L1382.2 0.0ZM1417.3 -217.4H1488.5L1452.9 -467.9ZM1412.2 -680.3 1461.0 -836.9H1585.6L1488.5 -680.3ZM1781.8 0.0V-532.1H1685.4V-631.8H2012.0V-532.1H1916.6V0.0ZM2587.5 11.6Q2496.1 11.6 2443.2 -22.0Q2390.4 -55.8 2368.8 -116.4Q2347.1 -176.9 2347.1 -258.2V-549.5Q2347.1 -633.3 2368.8 -693.9Q2390.4 -754.4 2443.2 -787.5Q2496.1 -820.5 2587.5 -820.5Q2676.0 -820.5 2725.8 -791.2Q2775.7 -761.8 2796.5 -709.0Q2817.4 -656.1 2817.4 -586.6V-517.3H2649.6V-595.7Q2649.6 -622.0 2646.9 -645.3Q2644.2 -668.6 2631.3 -683.6Q2618.5 -698.6 2587.6 -698.6Q2557.4 -698.6 2543.0 -683.0Q2528.6 -667.4 2524.8 -643.3Q2521.0 -619.2 2521.0 -591.5V-216.9Q2521.0 -186.3 2526.1 -162.4Q2531.2 -138.5 2545.7 -124.9Q2560.2 -111.2 2587.6 -111.2Q2617.5 -111.2 2630.3 -126.4Q2643.2 -141.5 2646.4 -165.9Q2649.6 -190.3 2649.6 -216.8V-299.2H2817.4V-230.2Q2817.4 -160.7 2797.1 -106.0Q2776.9 -51.3 2727.1 -19.8Q2677.3 11.6 2587.5 11.6ZM2916.5 0.0V-631.8H3050.4V0.0ZM2943.1 -680.3 2991.9 -836.9H3116.5L3019.4 -680.3ZM3155.1 0.0 3166.1 -631.8H3309.2L3383.1 -248.9L3461.9 -631.8H3600.0L3611.0 0.0H3514.9L3503.7 -424.4L3426.8 0.0H3342.8L3261.8 -427.2L3252.7 0.0ZM3714.6 0.0V-631.8H3848.7V-91.0H3995.6V0.0ZM4037.4 0.0 4158.3 -631.8H4309.6L4429.2 0.0H4305.3L4283.4 -136.1H4186.7L4164.0 0.0ZM4199.1 -217.4H4270.3L4234.7 -467.9ZM4505.3 0.0V-631.8H4711.6Q4768.5 -631.8 4804.9 -610.4Q4841.3 -589.0 4859.0 -548.3Q4876.7 -507.5 4876.7 -449.4Q4876.7 -378.5 4853.2 -340.1Q4829.7 -301.7 4789.1 -286.4Q4748.5 -271.0 4697.1 -271.0H4639.5V0.0ZM4639.5 -364.9H4685.6Q4712.7 -364.9 4726.9 -374.1Q4741.0 -383.4 4745.9 -402.4Q4750.7 -421.4 4750.7 -451.4Q4750.7 -477.8 4746.8 -496.7Q4743.0 -515.7 4728.9 -526.5Q4714.8 -537.2 4685.0 -537.2H4639.5Z"/></svg>'''

IKON_HIRLAP = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Cpath fill=%22%23f2f5f9%22 d=%22M18.89 51.99Q15.27 51.99 12.84 50.68Q10.41 49.37 9.17 46.67Q7.93 43.97 7.78 39.75L14.87 38.62Q14.93 41.08 15.34 42.66Q15.76 44.24 16.55 44.97Q17.34 45.70 18.50 45.70Q19.93 45.70 20.41 44.76Q20.89 43.82 20.89 42.68Q20.89 40.25 19.73 38.60Q18.58 36.94 16.62 35.27L12.92 32.03Q10.74 30.18 9.29 27.84Q7.84 25.50 7.84 22.06Q7.84 17.18 10.72 14.59Q13.60 12.0 18.56 12.0Q21.60 12.0 23.48 13.00Q25.36 14.01 26.37 15.67Q27.37 17.33 27.77 19.23Q28.17 21.12 28.23 22.94L21.13 23.89Q21.07 22.14 20.87 20.83Q20.68 19.52 20.09 18.79Q19.50 18.07 18.30 18.07Q17.01 18.07 16.40 19.08Q15.78 20.10 15.78 21.23Q15.78 23.35 16.77 24.70Q17.76 26.04 19.41 27.50L22.98 30.65Q25.49 32.79 27.24 35.52Q28.99 38.25 28.99 42.25Q28.99 45.00 27.74 47.22Q26.50 49.44 24.24 50.72Q21.97 51.99 18.89 51.99ZM45.16 51.99Q40.76 51.99 38.22 50.37Q35.68 48.75 34.64 45.84Q33.60 42.93 33.60 39.02V25.02Q33.60 20.99 34.64 18.08Q35.68 15.17 38.22 13.58Q40.76 12.0 45.16 12.0Q49.41 12.0 51.81 13.41Q54.20 14.82 55.20 17.36Q56.21 19.90 56.21 23.24V26.57H48.14V22.80Q48.14 21.54 48.01 20.42Q47.88 19.30 47.26 18.58Q46.65 17.86 45.16 17.86Q43.71 17.86 43.02 18.61Q42.33 19.35 42.14 20.51Q41.96 21.67 41.96 23.00V41.01Q41.96 42.48 42.21 43.62Q42.45 44.77 43.15 45.43Q43.84 46.09 45.16 46.09Q46.60 46.09 47.22 45.36Q47.83 44.63 47.99 43.46Q48.14 42.29 48.14 41.01V37.05H56.21V40.37Q56.21 43.71 55.23 46.34Q54.26 48.97 51.87 50.48Q49.47 51.99 45.16 51.99Z%22/%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,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">'
    '<link rel="apple-touch-icon" href="data:image/png;base64,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">'
    '<meta name="theme-color" content="#16202f"><meta name="apple-mobile-web-app-title" content="Saját Címlap">'
)

# Gót betűs fejléc (Pirata One betűtípus, SIL Open Font License)
LOGO_GOT = '''<svg class="logo logo-got" xmlns="http://www.w3.org/2000/svg" viewBox="28 -816 4132 1006" role="img" aria-label="Saját Címlap"><path fill="currentColor" d="M111 -319Q86 -319 68.0 -357.0Q50 -395 50 -450V-658Q50 -677 155.0 -723.5Q260 -770 276 -770Q288 -770 310 -730Q315 -720 324.5 -703.0Q334 -686 340 -675Q353 -651 373 -651V-631Q357 -624 330 -610Q267 -575 262 -575Q247 -575 224.5 -634.0Q202 -693 188 -693Q170 -693 170 -663V-440Q170 -400 188 -400Q192 -400 250.0 -426.0Q308 -452 320 -452Q344 -452 362.5 -413.5Q381 -375 381 -315V-102Q381 -83 266.5 -36.5Q152 10 135 10Q128 10 119.0 -7.0Q110 -24 95.5 -55.5Q81 -87 69.5 -108.0Q58 -129 38 -129V-149Q54 -156 80 -170Q144 -205 149 -205Q164 -205 191.5 -136.0Q219 -67 233 -67Q243 -67 252.0 -75.0Q261 -83 261 -97V-322Q261 -367 236 -367Q228 -367 173.0 -343.0Q118 -319 111 -319ZM785.0 -438V-155Q785.0 -118 797.0 -103.5Q809.0 -89 834.0 -83V-61L725.0 0Q703.0 -9 684.0 -29.5Q665.0 -50 665.0 -67L540.0 0Q537.0 -2 531.0 -5.5Q525.0 -9 510.5 -20.5Q496.0 -32 485.0 -45Q455.0 -78 455.0 -120V-284Q455.0 -300 507.0 -325.0Q559.0 -350 611.0 -366L664.0 -383Q664.0 -440 634.0 -462.5Q604.0 -485 518.0 -485V-515Q673.0 -570 692.0 -570Q725.0 -570 755.0 -527.0Q785.0 -484 785.0 -438ZM665.0 -112V-338Q640.0 -329 627.5 -323.0Q615.0 -317 601.0 -304Q575.0 -280 575.0 -221V-156Q575.0 -130 590.5 -113.5Q606.0 -97 625.0 -97.0Q644.0 -97 665.0 -112ZM885.0 -113V-422Q885.0 -483 844.0 -491V-513L945.0 -570Q967.0 -561 986.0 -540.5Q1005.0 -520 1005.0 -502V-106Q1005.0 -28 996.0 39.5Q987.0 107 974.0 143.5Q961.0 180 947.0 180Q922.0 180 882.5 165.0Q843.0 150 816.0 135L789.0 120V90H823.0Q858.0 90 871.5 44.0Q885.0 -2 885.0 -113ZM870.0 -673V-734Q870.0 -737 911.5 -752.5Q953.0 -768 959.5 -768.0Q966.0 -768 978.5 -751.0Q991.0 -734 991.0 -716V-643Q991.0 -640 951.0 -624.5Q911.0 -609 902.0 -609.0Q893.0 -609 881.5 -626.5Q870.0 -644 870.0 -673ZM1425.0 -438V-155Q1425.0 -118 1437.0 -103.5Q1449.0 -89 1474.0 -83V-61L1365.0 0Q1343.0 -9 1324.0 -29.5Q1305.0 -50 1305.0 -67L1180.0 0Q1177.0 -2 1171.0 -5.5Q1165.0 -9 1150.5 -20.5Q1136.0 -32 1125.0 -45Q1095.0 -78 1095.0 -120V-284Q1095.0 -300 1147.0 -325.0Q1199.0 -350 1251.0 -366L1304.0 -383Q1304.0 -440 1274.0 -462.5Q1244.0 -485 1158.0 -485V-515Q1313.0 -570 1332.0 -570Q1365.0 -570 1395.0 -527.0Q1425.0 -484 1425.0 -438ZM1305.0 -112V-338Q1280.0 -329 1267.5 -323.0Q1255.0 -317 1241.0 -304Q1215.0 -280 1215.0 -221V-156Q1215.0 -130 1230.5 -113.5Q1246.0 -97 1265.0 -97.0Q1284.0 -97 1305.0 -112ZM1331.0 -796Q1337.0 -806 1380.0 -806.0Q1423.0 -806 1423.0 -787Q1423.0 -759 1333.0 -643Q1323.0 -631 1298.0 -631Q1291.0 -631 1286.5 -647.0Q1282.0 -663 1282.0 -678.0Q1282.0 -693 1298.0 -731.0Q1314.0 -769 1331.0 -796ZM1635.0 -674V-555H1686.0L1676.0 -506H1635.0V-148Q1635.0 -87 1676.0 -79V-57L1575.0 0Q1553.0 -9 1534.0 -29.5Q1515.0 -50 1515.0 -68V-505H1474.0L1484.0 -547Q1514.0 -547 1556.5 -587.5Q1599.0 -628 1607.0 -674ZM2063.0 -673Q2053.0 -673 2045.0 -663V-250Q2045.0 -208 2050.0 -182.5Q2055.0 -157 2072.0 -132Q2105.0 -84 2223.0 -84V-55Q2218.0 -55 2149.0 -27.5Q2080.0 0 2065.0 0Q2015.0 0 1970.0 -57.5Q1925.0 -115 1925.0 -179V-678Q1925.0 -685 2039.5 -727.5Q2154.0 -770 2171.0 -770Q2177.0 -770 2185.0 -741.0Q2193.0 -712 2203.5 -671.0Q2214.0 -630 2225.5 -610.5Q2237.0 -591 2269.0 -591V-571Q2247.0 -561 2194.5 -535.5Q2142.0 -510 2138.0 -510Q2123.0 -510 2100.0 -591.5Q2077.0 -673 2063.0 -673ZM2449.0 -502V-148Q2449.0 -87 2490.0 -79V-57L2389.0 0Q2367.0 -9 2348.0 -29.5Q2329.0 -50 2329.0 -68V-422Q2329.0 -483 2288.0 -491V-513L2389.0 -570Q2411.0 -561 2430.0 -540.5Q2449.0 -520 2449.0 -502ZM2377.0 -796Q2383.0 -806 2426.0 -806.0Q2469.0 -806 2469.0 -787Q2469.0 -759 2379.0 -643Q2369.0 -631 2344.0 -631Q2337.0 -631 2332.5 -647.0Q2328.0 -663 2328.0 -678.0Q2328.0 -693 2344.0 -731.0Q2360.0 -769 2377.0 -796ZM2549.0 -68V-407Q2549.0 -440 2535.0 -464.0Q2521.0 -488 2500.0 -488V-510Q2507.0 -511 2609.0 -571Q2616.0 -569 2626.0 -565.5Q2636.0 -562 2652.5 -546.0Q2669.0 -530 2669.0 -509Q2675.0 -512 2703.0 -528Q2777.0 -570 2794.5 -570.0Q2812.0 -570 2833.5 -552.0Q2855.0 -534 2866.0 -509Q2868.0 -510 2907.0 -530Q2985.0 -570 3001.0 -570Q3033.0 -570 3061.0 -528.0Q3089.0 -486 3089.0 -451V-148Q3089.0 -93 3138.0 -83V-61L3029.0 0Q3007.0 -9 2988.0 -29.5Q2969.0 -50 2969.0 -68V-363Q2969.0 -428 2952.0 -449.5Q2935.0 -471 2908.0 -471L2879.0 -465V-148Q2879.0 -93 2928.0 -83V-61L2819.0 0Q2797.0 -9 2778.0 -29.5Q2759.0 -50 2759.0 -68V-358Q2759.0 -473 2704.0 -473L2669.0 -467V-148Q2669.0 -87 2710.0 -79V-57L2609.0 0Q2587.0 -9 2568.0 -29.5Q2549.0 -50 2549.0 -68ZM3309.0 -701V-148Q3309.0 -87 3350.0 -79V-57L3249.0 0Q3227.0 -9 3208.0 -29.5Q3189.0 -50 3189.0 -68V-621Q3189.0 -682 3148.0 -690V-712L3249.0 -769Q3271.0 -760 3290.0 -739.5Q3309.0 -719 3309.0 -701ZM3719.0 -438V-155Q3719.0 -118 3731.0 -103.5Q3743.0 -89 3768.0 -83V-61L3659.0 0Q3637.0 -9 3618.0 -29.5Q3599.0 -50 3599.0 -67L3474.0 0Q3471.0 -2 3465.0 -5.5Q3459.0 -9 3444.5 -20.5Q3430.0 -32 3419.0 -45Q3389.0 -78 3389.0 -120V-284Q3389.0 -300 3441.0 -325.0Q3493.0 -350 3545.0 -366L3598.0 -383Q3598.0 -440 3568.0 -462.5Q3538.0 -485 3452.0 -485V-515Q3607.0 -570 3626.0 -570Q3659.0 -570 3689.0 -527.0Q3719.0 -484 3719.0 -438ZM3599.0 -112V-338Q3574.0 -329 3561.5 -323.0Q3549.0 -317 3535.0 -304Q3509.0 -280 3509.0 -221V-156Q3509.0 -130 3524.5 -113.5Q3540.0 -97 3559.0 -97.0Q3578.0 -97 3599.0 -112ZM3979.0 -477Q3960.0 -477 3940.0 -461V-64Q3966.0 -64 3998.0 -81.0Q4030.0 -98 4030.0 -117V-403Q4030.0 -477 3979.0 -477ZM3819.0 110V-416Q3819.0 -439 3806.5 -461.0Q3794.0 -483 3771.0 -486V-508Q3778.0 -509 3880.0 -569Q3903.0 -559 3921.5 -539.5Q3940.0 -520 3940.0 -503Q4047.0 -569 4065.0 -569Q4072.0 -569 4091.5 -553.5Q4111.0 -538 4130.5 -508.5Q4150.0 -479 4150.0 -448V-92Q4150.0 -85 4061.5 -50.5Q3973.0 -16 3939.0 -7V30Q3939.0 91 3980.0 99V121L3879.0 178Q3857.0 169 3838.0 148.5Q3819.0 128 3819.0 110Z"/></svg>'''

IKON_GOT = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Cpath fill=%22%23f2f5f9%22 d=%22M26.44 35.44Q25.03 35.44 24.01 33.29Q23.00 31.15 23.00 28.05V16.31Q23.00 15.24 28.92 12.62Q34.84 10.0 35.75 10.0Q36.42 10.0 37.66 12.25Q37.95 12.82 38.48 13.77Q39.02 14.73 39.36 15.35Q40.09 16.71 41.22 16.71V17.84Q40.32 18.23 38.79 19.02Q35.24 21.0 34.96 21.0Q34.11 21.0 32.84 17.67Q31.57 14.34 30.78 14.34Q29.77 14.34 29.77 16.03V28.61Q29.77 30.87 30.78 30.87Q31.01 30.87 34.28 29.40Q37.55 27.93 38.23 27.93Q39.58 27.93 40.63 30.11Q41.67 32.28 41.67 35.66V47.68Q41.67 48.75 35.21 51.37Q28.75 54.0 27.79 54.0Q27.40 54.0 26.89 53.04Q26.38 52.08 25.56 50.30Q24.75 48.52 24.10 47.34Q23.45 46.15 22.32 46.15V45.03Q23.22 44.63 24.69 43.84Q28.30 41.87 28.58 41.87Q29.43 41.87 30.98 45.76Q32.53 49.65 33.32 49.65Q33.88 49.65 34.39 49.20Q34.90 48.75 34.90 47.96V35.27Q34.90 32.73 33.49 32.73Q33.04 32.73 29.94 34.08Q26.83 35.44 26.44 35.44Z%22/%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAADbklEQVR4AdRWaUhUURT+7iPLTM0WXwuRQ5thmEUGLRAFYXtqthttE9SPiigiKLOMknZyIBU1WwTTKJfcUoIoSzTExiyKKFIzyQmXstyZ1ztvmIeQxr2mghfOPd8979zv++6dx2MkdBpuhllussE7zN3gkysbZtbIBh+ld2JmjY3TO4w00GlIduzu4b3CQVHeAlIoA/wAJqPXBpNtnFIoaZCWnVozIBt8ljEmZTOG8fYHfZVJg7Tc1QOThjR8ovcIRcFNWvRvSHGuE7xGSg4S26G6Gte/4oCqOX7woEHbJQY2Dz0cntMm42J4KIqe5WJdwGphFtJWDUDYwMrlS5GcEIus1LtY578ao0aNwLHDB+Di4ixkggHz6CX04N3l6uqCnPQkmK6Ew8vLE6bIOH0rmTDuDNbXnMCDDHD2AsGbgjB1yiStv7auDqbrMXj+olBb07R/nxF7dm0jyB1CBhwGO+jEDEzDpqgbWv7xsxFXrkUi9WGWtuadhAx0RVryqhQHjxzHYj9/RMXeQm1tfVdt3dZ6bIAx2w0Qc/ajx2hs/EVQOHpsQFipmw0Dy0DT7yb9HE5OThoe6uiI9YFrkXQnBpkpiXB2HqbVeSehG/hmsei8bsNdEWm6iKL8XJw/GwLfObMw3XMKNm8I1Ht4gJCBgsJitLW167yy+2iEnbuMj58+67W5c2brmAcIGairq8exE2FIvp+GNUHbsH7LbjxIy8DL4hJdS5ZH65gHCBkgwozsPJw4FY537z/QUovm5lYt0+Q4ZAgl7hA2wM3M2TjwDIwbOwYXzp1EfEyEfkZF/UtlX3SC9tI/s9ANnA45iie5qQgKWINFC+djrq/YG9+VEyEDaRk5sFoVnWevcbuO7aCltcUOubKQAXPpG6SkZ+rEixctRKD/KiyY76vVSsyvcehoiIZ5J9WAUsPbTH3RsbfR0dFBUItL4afQ2toG475D2Bi8B5WVVVqdb1JqJPVCzXzNtq6qr9WIiU/AFzUnJN7D8rWbsGGrEU/zC2wNAjNpqzfAhAwQ/9WIKCzxC/jrM0zPxIKZpXawaNVJA/p5kCZpSw3l5nKmYHc/68MK607SVn8CwFJRmqoo1pXqR6S6r42QBmnVlpelk5ZmgMD3irKcdsZmANYz6vXkAYqF6v8btv2KxcZpPUMapGWrA38AAAD//9hpipQAAAAGSURBVAMAOBkg1IQwEbAAAAAASUVORK5CYII=">'
    '<link rel="apple-touch-icon" href="data:image/png;base64,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">'
    '<meta name="theme-color" content="#16202f"><meta name="apple-mobile-web-app-title" content="Saját Címlap">'
)

IKON_SVG = {"ecset": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" stroke=\"#f2f5f9\" stroke-width=\"1.6\" stroke-linejoin=\"round\" d=\"M52.57 14.05Q52.60 14.11 52.68 14.31Q52.76 14.52 52.85 14.78Q52.93 15.05 53.00 15.31Q53.07 15.57 53.07 15.68Q53.07 16.62 52.68 17.94Q52.29 19.25 51.62 20.71Q50.94 22.18 50.03 23.70Q49.11 25.22 48.09 26.59Q47.07 27.96 45.98 29.08Q44.88 30.20 43.83 30.86Q43.45 30.50 42.91 30.00Q42.37 29.51 41.76 28.94Q41.15 28.37 40.54 27.79Q39.94 27.21 39.41 26.66Q40.21 26.02 41.22 24.99Q42.23 23.95 43.27 22.68Q44.30 21.41 45.30 20.05Q46.29 18.70 47.03 17.44Q47.76 16.18 48.17 15.16Q48.59 14.13 48.48 13.52Q48.40 13.22 48.22 13.10Q48.04 12.97 47.57 12.97Q45.88 12.97 43.67 13.64Q41.46 14.30 39.05 15.56Q36.64 16.82 34.20 18.64Q31.75 20.47 29.61 22.76Q27.47 25.05 25.79 27.79Q24.12 30.53 23.23 33.63Q25.17 33.60 26.91 33.47Q28.65 33.35 30.23 33.21Q31.81 33.07 33.24 32.96Q34.68 32.85 36.04 32.85Q36.76 32.85 37.58 33.63Q38.41 34.40 39.13 35.48Q39.85 36.56 40.32 37.69Q40.79 38.82 40.79 39.52Q40.79 41.48 39.78 43.46Q38.77 45.43 37.07 47.27Q35.37 49.11 33.13 50.70Q30.89 52.29 28.42 53.47Q25.94 54.64 23.37 55.32Q20.80 56.0 18.48 56.0Q18.17 55.75 17.68 55.23Q17.18 54.72 16.61 54.10Q16.05 53.48 15.45 52.79Q14.86 52.10 14.35 51.46Q13.83 50.82 13.45 50.30Q13.06 49.77 12.92 49.50Q14.44 49.72 16.58 49.61Q18.73 49.50 21.11 49.00Q23.48 48.50 25.90 47.58Q28.32 46.65 30.42 45.24Q32.52 43.83 34.13 41.88Q35.73 39.93 36.45 37.36Q34.85 37.39 33.55 37.51Q32.25 37.64 31.03 37.79Q29.82 37.94 28.54 38.05Q27.27 38.16 25.72 38.16Q25.23 38.16 24.64 38.15Q24.06 38.13 23.48 38.06Q22.90 38.0 22.38 37.83Q21.85 37.66 21.52 37.36Q20.77 36.72 20.08 36.02Q19.39 35.31 18.84 34.58Q18.29 33.85 17.95 33.10Q17.62 32.35 17.62 31.66Q17.62 29.56 18.67 26.98Q19.72 24.39 21.59 21.76Q23.46 19.14 26.00 16.66Q28.54 14.19 31.54 12.25Q34.54 10.32 37.86 9.16Q41.18 8.0 44.58 8.0Q45.77 8.0 46.75 8.35Q47.73 8.71 48.53 9.29Q49.34 9.88 49.97 10.58Q50.61 11.29 51.11 11.95Q51.60 12.61 51.96 13.18Q52.32 13.75 52.57 14.05Z\"/></svg>", "hirlap": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" d=\"M18.89 51.99Q15.27 51.99 12.84 50.68Q10.41 49.37 9.17 46.67Q7.93 43.97 7.78 39.75L14.87 38.62Q14.93 41.08 15.34 42.66Q15.76 44.24 16.55 44.97Q17.34 45.70 18.50 45.70Q19.93 45.70 20.41 44.76Q20.89 43.82 20.89 42.68Q20.89 40.25 19.73 38.60Q18.58 36.94 16.62 35.27L12.92 32.03Q10.74 30.18 9.29 27.84Q7.84 25.50 7.84 22.06Q7.84 17.18 10.72 14.59Q13.60 12.0 18.56 12.0Q21.60 12.0 23.48 13.00Q25.36 14.01 26.37 15.67Q27.37 17.33 27.77 19.23Q28.17 21.12 28.23 22.94L21.13 23.89Q21.07 22.14 20.87 20.83Q20.68 19.52 20.09 18.79Q19.50 18.07 18.30 18.07Q17.01 18.07 16.40 19.08Q15.78 20.10 15.78 21.23Q15.78 23.35 16.77 24.70Q17.76 26.04 19.41 27.50L22.98 30.65Q25.49 32.79 27.24 35.52Q28.99 38.25 28.99 42.25Q28.99 45.00 27.74 47.22Q26.50 49.44 24.24 50.72Q21.97 51.99 18.89 51.99ZM45.16 51.99Q40.76 51.99 38.22 50.37Q35.68 48.75 34.64 45.84Q33.60 42.93 33.60 39.02V25.02Q33.60 20.99 34.64 18.08Q35.68 15.17 38.22 13.58Q40.76 12.0 45.16 12.0Q49.41 12.0 51.81 13.41Q54.20 14.82 55.20 17.36Q56.21 19.90 56.21 23.24V26.57H48.14V22.80Q48.14 21.54 48.01 20.42Q47.88 19.30 47.26 18.58Q46.65 17.86 45.16 17.86Q43.71 17.86 43.02 18.61Q42.33 19.35 42.14 20.51Q41.96 21.67 41.96 23.00V41.01Q41.96 42.48 42.21 43.62Q42.45 44.77 43.15 45.43Q43.84 46.09 45.16 46.09Q46.60 46.09 47.22 45.36Q47.83 44.63 47.99 43.46Q48.14 42.29 48.14 41.01V37.05H56.21V40.37Q56.21 43.71 55.23 46.34Q54.26 48.97 51.87 50.48Q49.47 51.99 45.16 51.99Z\"/></svg>", "got": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" d=\"M26.44 35.44Q25.03 35.44 24.01 33.29Q23.00 31.15 23.00 28.05V16.31Q23.00 15.24 28.92 12.62Q34.84 10.0 35.75 10.0Q36.42 10.0 37.66 12.25Q37.95 12.82 38.48 13.77Q39.02 14.73 39.36 15.35Q40.09 16.71 41.22 16.71V17.84Q40.32 18.23 38.79 19.02Q35.24 21.0 34.96 21.0Q34.11 21.0 32.84 17.67Q31.57 14.34 30.78 14.34Q29.77 14.34 29.77 16.03V28.61Q29.77 30.87 30.78 30.87Q31.01 30.87 34.28 29.40Q37.55 27.93 38.23 27.93Q39.58 27.93 40.63 30.11Q41.67 32.28 41.67 35.66V47.68Q41.67 48.75 35.21 51.37Q28.75 54.0 27.79 54.0Q27.40 54.0 26.89 53.04Q26.38 52.08 25.56 50.30Q24.75 48.52 24.10 47.34Q23.45 46.15 22.32 46.15V45.03Q23.22 44.63 24.69 43.84Q28.30 41.87 28.58 41.87Q29.43 41.87 30.98 45.76Q32.53 49.65 33.32 49.65Q33.88 49.65 34.39 49.20Q34.90 48.75 34.90 47.96V35.27Q34.90 32.73 33.49 32.73Q33.04 32.73 29.94 34.08Q26.83 35.44 26.44 35.44Z\"/></svg>"}

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
    gy.setProperty('--cim-suly', bt.suly); gy.setProperty('--cim-suly-nagy', bt.nagy); gy.setProperty('--cim-ter', bt.ter);
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

LOGO = {"hirlap": LOGO_HIRLAP, "got": LOGO_GOT, "ecset": LOGO_ECSET}
IKON_LINKEK = {"hirlap": IKON_HIRLAP, "got": IKON_GOT, "ecset": IKON_ECSET}


# ── Fő program ─────────────────────────────────────────────────────

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
            h["ido"] = min(h["ido"], tarolt[h["id"]]["ido"])
        tarolt[h["id"]] = h
    hatar = (MOST - timedelta(hours=MEGORZES_ORA)).isoformat()
    ervenyes = [h for h in tarolt.values() if h["ido"] >= hatar and h["forras"] in FN
                # a Google Hírek tartalékból mentett cikkek törlése, ha a lapnak már nincs tartaléka
                and not ("news.google.com" in h["link"] and not FN[h["forras"]].get("tartalek"))]
    # ugyanannak a lapnak azonos című cikkei közül csak egy maradjon (amelyiknek van képe/leadje)
    egyedi = {}
    for h in sorted(ervenyes, key=lambda h: (bool(h["kep"]), len(h["lead"])), reverse=True):
        egyedi.setdefault((h["forras"], h["cim"].strip().lower()), h)
    ervenyes = list(egyedi.values())
    for h in ervenyes:
        h["rovat"] = rovat(h)

    allapot_ut.write_text(json.dumps(ervenyes, ensure_ascii=False), "utf-8")
    orszagos = osszevon([h for h in ervenyes if h["rovat"] not in ("helyi", "idojaras")])
    helyi_cs = osszevon([h for h in ervenyes if h["rovat"] == "helyi"])
    Path(a.kimenet).parent.mkdir(parents=True, exist_ok=True)
    idokep = [h for h in ervenyes if h["rovat"] == "idojaras"]
    Path(a.kimenet).write_text(oldal(orszagos, helyi_cs, hibak, idojaras_letolt(), idokep), "utf-8")
    print(f"{len(uj)} letöltött, {len(ervenyes)} megőrzött hír → {a.kimenet}")
    for x in hibak:
        print("  hiba:", x)


if __name__ == "__main__":
    main()
