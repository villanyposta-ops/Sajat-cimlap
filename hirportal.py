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
# vagy rögzítve: "talpas" (vastag, két sorban), "klasszikus" (Times-szerű, középen), "ecset" (kézírásos)
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
FEJLEC_SZIN = None        # ha egy állandó színt szeretnél, pl. "#16202f"; None = napszak szerint

# Országos lapok. Az „alap” rovat akkor számít, ha a cikkről semmi más nem derül ki.
FORRASOK = [
    {"id": "telex",  "nev": "Telex",         "szin": "#2563eb", "feeds": ["https://telex.hu/rss"],
     # ha a Telex nem engedi be a GitHub gépét, a Google Hírek Telexre szűrt csatornája a tartalék
     "tartalek": "https://news.google.com/rss/search?q=site:telex.hu+when:2d&hl=hu&gl=HU&ceid=HU:hu"},
    {"id": "444",    "nev": "444",           "szin": "#e11d48", "feeds": ["https://444.hu/feed"]},
    {"id": "24",     "nev": "24.hu",         "szin": "#ea580c", "feeds": ["https://24.hu/feed/"]},
    {"id": "hvg",    "nev": "HVG",           "szin": "#7c3aed", "feeds": ["https://hvg.hu/rss"]},
    {"id": "mhang",  "nev": "Magyar Hang",   "szin": "#0d9488", "feeds": ["https://hang.hu/rss/"]},
    {"id": "atlatszo", "nev": "Átlátszó",    "szin": "#65a30d", "feeds": ["https://atlatszo.hu/feed/"],
     "alap": "belfold"},
    {"id": "valasz", "nev": "Válasz Online", "szin": "#a16207", "feeds": ["https://www.valaszonline.hu/feed/"]},
    {"id": "infostart", "nev": "Infostart", "szin": "#4f46e5", "feeds": ["https://infostart.hu/24ora/rss/"]},
    {"id": "portfolio", "nev": "Portfolio",  "szin": "#c026d3", "feeds": ["https://www.portfolio.hu/rss/all.xml"],
     "alap": "gazdasag"},
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

FO_HIREK_SZAMA = 9        # ennyi kerül a lap tetejére
FRISS_ORA = 24            # ennél frissebb hír dobozban, régebbi listában jelenik meg
MEGORZES_ORA = 72         # ennél régebbi hír kiesik
DOBOZ_ROVATONKENT = 6     # rovatonként legfeljebb ennyi doboz, a többi listába megy
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

def letolt(url):
    if url.startswith(("/", "./")) or url.startswith("file:"):
        return Path(url.replace("file://", "")).read_bytes()
    req = urllib.request.Request(url, headers=FEJLECEK)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


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


def rovat(h):
    forras = FN[h["forras"]]
    if forras.get("panel"):
        return forras["panel"]
    if forras.get("hely"):
        return "helyi"
    szakaszok = [ekezet_nelkul(s.lower()) for s in urlparse(h["link"]).path.split("/") if s]
    for sz in szakaszok[:3]:
        for r in ROVATOK:
            if sz in r["utvonal"]:
                return r["id"]
    kat = ekezet_nelkul(" ".join(h["kategoriak"]).lower())
    for r in ROVATOK:
        if any(re.search(rf"\b{re.escape(u)}\b", kat) for u in r["utvonal"]) or \
                ekezet_nelkul(r["nev"].lower()) in kat:
            return r["id"]
    szoveg_ = " " + (h["cim"] + " " + h["lead"]).lower() + " "
    pontok = {r["id"]: sum(szoveg_.count(k) for k in r["kulcsszavak"]) for r in ROVATOK}
    legjobb = max(pontok, key=pontok.get)
    return legjobb if pontok[legjobb] > 0 else forras.get("alap", "egyeb")


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
        rovatok = [h["rovat"] for h in hk]
        nem_egyeb = [r for r in rovatok if r != "egyeb"] or rovatok
        cs["rovat"] = max(set(nem_egyeb), key=nem_egyeb.count)
        kor_ora = (MOST - datetime.fromisoformat(cs["ido"])).total_seconds() / 3600
        # fontosság: hány lap írt róla + frissesség
        cs["pont"] = 3 * len(cs["forrasok"]) + 0.5 * len(hk) + max(0, 3 - kor_ora / 4)
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
    tobbi = [h for h in cs["hirek"] if h is not cs["fo"]]
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
    tipus = "nyito" if i == 0 else ("masod" if i < 3 else "kis")
    kep = kep_html(h, "kep") if tipus != "kis" else ""
    lead = f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] and tipus != "kis" else ""
    return f'''<article class="fo-hir {tipus}" data-srcs="{' '.join(cs['forrasok'])}">
  {kep}<div class="szoveg">
  <div class="meta">{forras_nev(h["forras"])}<time>{ido_szoveg(cs["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3>
  {lead}{lefedettseg(cs)}{tovabbi(cs) if tipus != "kis" else ""}</div>
</article>'''


def doboz(cs, lead=True):
    h = cs["fo"]
    return f'''<article class="doboz" data-srcs="{' '.join(cs['forrasok'])}">
  <div class="szoveg">
  <div class="meta">{forras_nev(h["forras"])}<time>{ido_szoveg(cs["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3>
  {f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] and lead else ''}
  {lefedettseg(cs)}{tovabbi(cs)}</div>
  {kep_html(h, "belyeg")}
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
    friss = [c for c in orszagos if c["kor_ora"] < 18]
    fo = sorted(friss, key=lambda c: c["pont"], reverse=True)[:FO_HIREK_SZAMA]
    # a legnagyobb dobozba lehetőleg képes hír kerüljön
    kepes = next((c for c in fo[:3] if c["fo"]["kep"]), None)
    if kepes and fo[0] is not kepes:
        fo.remove(kepes)
        fo.insert(0, kepes)
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
            nav.append(f'<a href="#{r["id"]}" style="--r:{r["szin"]}">{e(r["nev"])}</a>')
            reszek.append(f'''<section class="rovat" id="{r["id"]}" style="--r:{r["szin"]}">
  {rovatfej(r["id"], r["nev"], r["szin"], len(cs))}
  <div class="tartalom">
  {f'<div class="racs">{"".join(doboz(c) for c in dobozok)}</div>' if dobozok else ''}
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
    kezdo = fix or "talpas"
    logok = "".join(f'<span class="logo-v" data-stilus="{st}"{"" if st == kezdo else " hidden"}>{LOGO[st]}</span>'
                    for st in stilusok if fix is None or st == fix)
    fej_js = FEJ_JS.replace("__BEALLITAS__", json.dumps({
        "stilusok": stilusok, "fix_stilus": fix, "fix_szin": FEJLEC_SZIN,
        "szinek": NAPSZAK_SZINEK, "ikonok": IKON_SVG}).replace("</", "<\\/"))

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
<meta http-equiv="refresh" content="600"><meta name="robots" content="noindex">
<title>{e(OLDAL_CIM)}, {most.month}. {most.day}.</title>
{IKON_LINKEK.get(kezdo, IKON_TALPAS)}
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400..800&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body>
<header class="fej fej-{e(kezdo)}" id="fej">
  <div class="fejsor">
    <a class="logo-link" href="#" aria-label="{e(OLDAL_CIM)}">{logok if OLDAL_CIM == "Saját Címlap" else f'<span class="nev">{e(OLDAL_CIM)}</span>'}</a>
    <div class="datum"><span class="nap">{most.year}. {HONAPOK[most.month-1]} {most.day}., {NAPOK[most.weekday()]}</span>
      <span class="frissitve">frissítve {most:%H:%M}</span></div>
    <details class="forrasmenu"><summary>Források</summary>
      <div class="panel">
        <p>A kikapcsolt lapok hírei elrejtődnek. A böngésző megjegyzi a beállítást.</p>
        <h4>Országos</h4><div class="szurok">{orsz}</div>
        {f'<h4>Helyi</h4><div class="szurok">{helyi_sz}</div>' if helyi_sz else ''}
      </div>
    </details>
  </div>
</header>
<script>{fej_js}</script>
<nav class="rovatnav"><div class="nav-belso">{"".join(nav)}</div></nav>
{hiba_html}
<section class="elso" aria-label="A legfontosabb hírek">
  <div class="elso-belso"><h2>Amiről most a legtöbben írnak</h2>
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
@media (prefers-color-scheme:dark){:root{
  --lap:#0e141d;--felulet:#18212d;--tinta:#e6ebf2;--halvany:#97a3b5;--vonal:#2a3545;
  --sav:#1c2636;--sav-felulet:#263347}}
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
.fejsor{max-width:1280px;margin:0 auto;padding:1.2rem 1.5rem 1rem;display:flex;align-items:flex-end;gap:2rem}
.logo-link{display:block;color:#fff;margin-right:auto;line-height:0}
.logo-v{display:block}.logo-v[hidden]{display:none}
.logo{height:4.6rem;width:auto;display:block}
.logo-talpas{height:4.4rem}
.fej .nap{color:#fff}
.fej .frissitve{color:rgb(255 255 255/.72)}
.fej .forrasmenu summary{background:rgb(255 255 255/.12);border-color:rgb(255 255 255/.35);color:#fff}
.fej .forrasmenu summary::after{color:rgb(255 255 255/.7)}
.fej .forrasmenu summary:hover{background:rgb(255 255 255/.2)}
.fej .panel{color:var(--tinta)}
/* klasszikus (Times-szerű) fejléc: középre zárt cím, alatta kettős vonal */
.fej-klasszikus .fejsor{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:1.5rem;padding:1.4rem 1.5rem 1.2rem;border-bottom:4px double rgb(255 255 255/.55)}
.fej-klasszikus{padding-bottom:.5rem}
.fej-klasszikus .logo-link{grid-column:2;grid-row:1;margin:0}
.fej-klasszikus .datum{grid-column:1;grid-row:1;align-items:flex-start;padding:0}
.fej-klasszikus .forrasmenu{grid-column:3;grid-row:1;justify-self:end;padding:0}
.fej-klasszikus .logo{height:2.8rem}
@media (max-width:900px){
  .fej-klasszikus .fejsor{grid-template-columns:1fr auto;gap:.9rem 1rem}
  .fej-klasszikus .logo-link{grid-column:1/-1;justify-self:center;width:100%;max-width:26rem}
  .fej-klasszikus .logo{width:100%;height:auto}
  .fej-klasszikus .datum{grid-column:1;grid-row:2}
  .fej-klasszikus .forrasmenu{grid-column:2;grid-row:2}}
.nev{font:800 2.6rem/1 var(--cimbetu);letter-spacing:-.03em}
.datum{display:flex;flex-direction:column;align-items:flex-end;gap:.15rem;padding-bottom:.4rem}
.nap{font:650 1.05rem/1.2 var(--cimbetu)}
.frissitve{color:var(--halvany);font-size:.85rem}
.rovatnav{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--lap) 92%,transparent);backdrop-filter:blur(8px);border-bottom:1px solid var(--vonal)}
.nav-belso{max-width:1280px;margin:0 auto;padding:.55rem 1.5rem;display:flex;gap:.4rem;overflow-x:auto;scrollbar-width:none}
.nav-belso a{flex-shrink:0;font-size:.86rem;font-weight:600;padding:.22rem .7rem;border-radius:99px;background:var(--felulet);border:1px solid var(--vonal);display:flex;align-items:center;gap:.4rem}
.nav-belso a::before{content:"";width:.5rem;height:.5rem;border-radius:50%;background:var(--r)}
.nav-belso a:hover{border-color:var(--r)}

/* forrásválasztó */
.forrasmenu{position:relative;padding-bottom:.4rem;z-index:20}
.forrasmenu summary{list-style:none;cursor:pointer;font-size:.86rem;font-weight:600;padding:.3rem .8rem;border:1px solid var(--vonal);border-radius:6px;background:var(--felulet)}
.forrasmenu summary::-webkit-details-marker{display:none}
.forrasmenu summary::after{content:" ▾";color:var(--halvany)}
.panel{position:absolute;right:0;top:calc(100% + .2rem);width:min(22rem,90vw);background:var(--felulet);border:1px solid var(--vonal);border-radius:10px;padding:1rem;box-shadow:0 12px 32px rgb(15 23 42/.18)}
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
.elso-belso{max-width:1280px;margin:0 auto;padding:1.6rem 1.5rem 2rem}
.elso h2{font-size:1.05rem;font-weight:600;color:var(--sav-halvany);margin-bottom:1rem}
.fo-racs{display:grid;grid-template-columns:repeat(6,1fr);gap:1.1rem}
.fo-hir{background:var(--sav-felulet);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.fo-hir .szoveg{padding:1rem 1.1rem 1.1rem}
.fo-hir .meta{color:var(--sav-halvany)}
.fo-hir .forras{color:color-mix(in srgb,var(--c) 45%,#fff)}
.fo-hir .lead{color:var(--sav-halvany)}
.fo-hir .tovabbi{color:var(--sav-halvany)}
.fo-hir .lefed{color:var(--sav-szoveg)}
.nyito{grid-column:span 4;grid-row:span 2;flex-direction:row}
.nyito .kep{flex:1.3;min-height:100%}
.nyito .szoveg{flex:1;padding:1.5rem;display:flex;flex-direction:column;justify-content:flex-end}
.nyito h3{font-size:clamp(1.5rem,2.4vw,2.15rem);line-height:1.12;letter-spacing:-.02em;font-weight:750}
.nyito .lead{font-size:1rem;-webkit-line-clamp:5}
.masod{grid-column:span 2}
.masod .kep{aspect-ratio:2/1}
.masod h3{font-size:1.15rem;line-height:1.22}
.masod .lead{-webkit-line-clamp:2}
.kis{grid-column:span 2}
.kis h3{font-size:1.05rem;line-height:1.25;font-weight:650}
.kep{background:#2b384c;overflow:hidden}.kep img{width:100%;height:100%;object-fit:cover;display:block}

/* közös szövegelemek */
h3{font-weight:650;letter-spacing:-.01em}
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
.csuk{all:unset;cursor:pointer;font:750 1.25rem/1 var(--cimbetu);color:#fff;background:var(--r);padding:.42rem .9rem .48rem;border-radius:8px;letter-spacing:-.01em}
.csuk::after{content:"";display:inline-block;width:.42rem;height:.42rem;border-right:2px solid;border-bottom:2px solid;transform:translateY(-.2rem) rotate(45deg);margin-left:.6rem;opacity:.8}
.rovat.csukva .csuk::after{transform:translateY(-.05rem) rotate(-45deg)}
.rovat.csukva .tartalom{display:none}
.db{font-size:.8rem;color:var(--halvany);order:4}
.racs{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:.9rem}
.doboz{background:var(--felulet);border:1px solid var(--vonal);border-radius:10px;padding:.9rem 1rem;display:flex;gap:.9rem;align-items:flex-start}
.doboz .szoveg{flex:1;min-width:0}
.doboz h3{font-size:1.04rem;line-height:1.28}
.belyeg{width:84px;height:84px;flex-shrink:0;border-radius:6px;overflow:hidden;background:var(--vonal)}
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
.ido-gomb{margin-top:.8rem;font:600 .88rem system-ui,sans-serif;color:#fff;background:var(--r);border:0;border-radius:8px;padding:.55rem .9rem;cursor:pointer}
.ido-gomb:hover{filter:brightness(1.1)}
.ido-helyek{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:.9rem}
.ido-hely{background:var(--felulet);border:1px solid var(--vonal);border-radius:12px;padding:1rem 1.1rem}
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
.ido-hirek{background:color-mix(in srgb,var(--r) 6%,var(--lap));border-radius:14px;padding:1rem 1.1rem}
.ido-hirek h3{font-size:1.05rem;font-weight:700;color:var(--r);margin-bottom:.4rem}
.ido-hirek ul{list-style:none;margin:0;padding:0}
.ido-hirek li{padding:.45rem 0;border-bottom:1px solid var(--vonal);font-size:.9rem;line-height:1.35}
.ido-hirek li:last-child{border-bottom:0}
.ido-hirek time{display:block;color:var(--halvany);font-size:.74rem}
.ido-hirek a:hover{text-decoration:underline}

@media (prefers-color-scheme:dark){.ido-hirek h3,.helynev{color:color-mix(in srgb,var(--r) 45%,#fff)}}

/* helyi rovat */
.helyek{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:1.4rem}
.hely{background:color-mix(in srgb,var(--r) 6%,var(--lap));border-radius:14px;padding:1rem 1.1rem 1.1rem;display:flex;flex-direction:column;gap:.7rem}
.helynev{font-size:1.5rem;font-weight:800;letter-spacing:-.02em;color:var(--r)}
.hely .lista{columns:1;margin:0}
.hely details{margin-top:0}

.hibak{max-width:1280px;margin:.8rem auto 0;padding:.6rem 1rem;font-size:.85rem;background:var(--felulet);border:1px solid var(--vonal);border-radius:8px}
.rejtett{display:none!important}
footer{text-align:center;color:var(--halvany);font-size:.8rem;padding:1rem 1.5rem 2.5rem;max-width:60ch;margin:0 auto}

@media (max-width:1000px){
  .fo-racs{grid-template-columns:repeat(2,1fr)}
  .nyito{grid-column:span 2;grid-row:auto;flex-direction:column}
  .nyito .kep{aspect-ratio:16/9;min-height:0}
  .masod,.kis{grid-column:span 1}}
@media (max-width:640px){
  .fejsor{flex-wrap:wrap;gap:.6rem 1rem;padding:.9rem 1rem .7rem;align-items:center}
  .logo-link{width:100%}.logo{height:3.4rem}
  .datum{align-items:flex-start;margin-right:auto;padding:0}
  .nap{font-size:.95rem}
  .forrasmenu{padding:0}
  .nav-belso,main,.elso-belso{padding-left:1rem;padding-right:1rem}
  .fo-racs{grid-template-columns:1fr}.nyito,.masod,.kis{grid-column:auto}
  .nyito .szoveg{padding:1.1rem}
  .belyeg{width:64px;height:64px}
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
const menu=document.querySelector('.forrasmenu');
document.addEventListener('click',ev=>{if(menu.open&&!menu.contains(ev.target))menu.open=false;});
szur();
"""


# Kézírásos fejléc (a Yesteryear betűtípusból rajzolt útvonal, SIL Open Font License)
LOGO_ECSET = '''<svg class="logo" xmlns="http://www.w3.org/2000/svg" viewBox="-148 -1601 9819 2401" role="img" aria-label="Saját Címlap"><path fill="currentColor" d="M1346 -1337Q1347 -1335 1350.0 -1327.5Q1353 -1320 1356.0 -1310.5Q1359 -1301 1361.5 -1291.5Q1364 -1282 1364 -1278Q1364 -1244 1350.0 -1196.5Q1336 -1149 1311.5 -1096.0Q1287 -1043 1254.0 -988.0Q1221 -933 1184.0 -883.5Q1147 -834 1107.5 -793.5Q1068 -753 1030 -729Q1016 -742 996.5 -760.0Q977 -778 955.0 -798.5Q933 -819 911.0 -840.0Q889 -861 870 -881Q899 -904 935.5 -941.5Q972 -979 1009.5 -1025.0Q1047 -1071 1083.0 -1120.0Q1119 -1169 1145.5 -1214.5Q1172 -1260 1187.0 -1297.0Q1202 -1334 1198 -1356Q1195 -1367 1188.5 -1371.5Q1182 -1376 1165 -1376Q1104 -1376 1024.0 -1352.0Q944 -1328 857.0 -1282.5Q770 -1237 681.5 -1171.0Q593 -1105 515.5 -1022.0Q438 -939 377.5 -840.0Q317 -741 285 -629Q355 -630 418.0 -634.5Q481 -639 538.0 -644.0Q595 -649 647.0 -653.0Q699 -657 748 -657Q774 -657 804.0 -629.0Q834 -601 860.0 -562.0Q886 -523 903.0 -482.0Q920 -441 920 -416Q920 -345 883.5 -273.5Q847 -202 785.5 -135.5Q724 -69 643.0 -11.5Q562 46 472.5 88.5Q383 131 290.0 155.5Q197 180 113 180Q102 171 84.0 152.5Q66 134 45.5 111.5Q25 89 3.5 64.0Q-18 39 -36.5 16.0Q-55 -7 -69.0 -26.0Q-83 -45 -88 -55Q-33 -47 44.5 -51.0Q122 -55 208.0 -73.0Q294 -91 381.5 -124.5Q469 -158 545.0 -209.0Q621 -260 679.0 -330.5Q737 -401 763 -494Q705 -493 658.0 -488.5Q611 -484 567.0 -478.5Q523 -473 477.0 -469.0Q431 -465 375 -465Q357 -465 336.0 -465.5Q315 -466 294.0 -468.5Q273 -471 254.0 -477.0Q235 -483 223 -494Q196 -517 171.0 -542.5Q146 -568 126.0 -594.5Q106 -621 94.0 -648.0Q82 -675 82 -700Q82 -776 120.0 -869.5Q158 -963 225.5 -1058.0Q293 -1153 385.0 -1242.5Q477 -1332 585.5 -1402.0Q694 -1472 814.0 -1514.0Q934 -1556 1057 -1556Q1100 -1556 1135.5 -1543.0Q1171 -1530 1200.0 -1509.0Q1229 -1488 1252.0 -1462.5Q1275 -1437 1293.0 -1413.0Q1311 -1389 1324.0 -1368.5Q1337 -1348 1346 -1337ZM1751.0 -88Q1751.0 -82 1757.0 -82Q1766.0 -82 1787.0 -95.0Q1808.0 -108 1835.0 -128.0Q1862.0 -148 1892.5 -173.0Q1923.0 -198 1951.0 -222.0Q1979.0 -246 2001.5 -266.0Q2024.0 -286 2036.0 -297Q2045.0 -288 2058.5 -273.0Q2072.0 -258 2083.0 -246Q2059.0 -224 2025.5 -192.5Q1992.0 -161 1953.5 -127.0Q1915.0 -93 1874.0 -59.5Q1833.0 -26 1794.0 0.0Q1755.0 26 1720.0 42.5Q1685.0 59 1659.0 59Q1641.0 59 1619.5 52.0Q1598.0 45 1580.0 32.5Q1562.0 20 1550.0 3.0Q1538.0 -14 1538.0 -35Q1538.0 -43 1537.5 -54.5Q1537.0 -66 1539.0 -84.5Q1541.0 -103 1547.0 -129.5Q1553.0 -156 1565.0 -195Q1532.0 -160 1487.5 -122.0Q1443.0 -84 1394.0 -50.0Q1345.0 -16 1295.5 11.5Q1246.0 39 1204.0 53Q1199.0 55 1196.0 55.0Q1193.0 55 1190.0 55Q1180.0 55 1163.5 45.0Q1147.0 35 1131.0 18.5Q1115.0 2 1103.5 -19.0Q1092.0 -40 1092.0 -61Q1092.0 -67 1098.0 -101.0Q1104.0 -135 1126.5 -187.5Q1149.0 -240 1193.0 -305.5Q1237.0 -371 1313.0 -440Q1352.0 -475 1397.0 -506Q1435.0 -532 1484.0 -559.0Q1533.0 -586 1587.0 -602Q1589.0 -602 1590.5 -603.0Q1592.0 -604 1595.0 -602Q1607.0 -598 1623.5 -589.5Q1640.0 -581 1656.5 -571.5Q1673.0 -562 1685.5 -552.5Q1698.0 -543 1702.0 -537Q1703.0 -535 1702.0 -534L1700.0 -532Q1655.0 -508 1615.0 -478.5Q1575.0 -449 1544.0 -423Q1508.0 -392 1477.0 -362Q1404.0 -290 1376.0 -237.5Q1348.0 -185 1348.0 -162Q1348.0 -145 1362.0 -145Q1376.0 -145 1397.5 -156.5Q1419.0 -168 1446.0 -187.5Q1473.0 -207 1505.5 -233.5Q1538.0 -260 1573.0 -289Q1604.0 -314 1633.0 -343Q1658.0 -367 1685.0 -398.0Q1712.0 -429 1731.0 -461Q1731.0 -462 1732.0 -463Q1733.0 -464 1735.0 -463Q1746.0 -455 1760.0 -436.5Q1774.0 -418 1786.0 -396.0Q1798.0 -374 1806.5 -353.5Q1815.0 -333 1815.0 -322Q1792.0 -275 1779.5 -236.5Q1767.0 -198 1760.5 -169.0Q1754.0 -140 1752.5 -119.5Q1751.0 -99 1751.0 -88ZM2532.0 -246Q2441.0 -184 2345.5 -124.0Q2250.0 -64 2156.0 -4Q2139.0 48 2118.5 105.5Q2098.0 163 2065.0 240Q2021.0 340 1961.5 424.5Q1902.0 509 1838.5 570.5Q1775.0 632 1713.0 667.0Q1651.0 702 1602.0 702Q1586.0 702 1564.5 685.5Q1543.0 669 1523.5 646.5Q1504.0 624 1490.5 600.0Q1477.0 576 1477.0 561Q1477.0 502 1511.0 440.5Q1545.0 379 1605.0 316.0Q1665.0 253 1747.0 188.5Q1829.0 124 1925.0 58Q1951.0 -16 1976.0 -93.5Q2001.0 -171 2023.5 -244.5Q2046.0 -318 2065.5 -383.5Q2085.0 -449 2100.0 -498.0Q2115.0 -547 2125.0 -575.5Q2135.0 -604 2138.0 -604Q2144.0 -604 2169.5 -589.0Q2195.0 -574 2222.5 -553.5Q2250.0 -533 2272.0 -512.5Q2294.0 -492 2294.0 -481Q2294.0 -476 2293.0 -472.0Q2292.0 -468 2290.0 -463Q2270.0 -403 2255.0 -353.0Q2240.0 -303 2228.0 -260.5Q2216.0 -218 2206.0 -181.5Q2196.0 -145 2187.0 -112Q2262.0 -159 2337.0 -205.0Q2412.0 -251 2487.0 -299ZM2452.0 -889Q2451.0 -885 2444.5 -869.5Q2438.0 -854 2429.0 -833.5Q2420.0 -813 2408.5 -790.5Q2397.0 -768 2386.0 -749.0Q2375.0 -730 2365.0 -717.5Q2355.0 -705 2349.0 -705Q2331.0 -706 2305.5 -716.0Q2280.0 -726 2256.5 -738.5Q2233.0 -751 2216.5 -763.0Q2200.0 -775 2200.0 -780Q2200.0 -802 2212.0 -836.0Q2224.0 -870 2240.5 -901.0Q2257.0 -932 2273.5 -954.5Q2290.0 -977 2298.0 -977Q2316.0 -977 2342.0 -965.5Q2368.0 -954 2392.5 -939.0Q2417.0 -924 2434.5 -909.5Q2452.0 -895 2452.0 -889ZM1839.0 274Q1849.0 254 1858.0 232.0Q1867.0 210 1877.0 186Q1813.0 234 1760.0 283.0Q1707.0 332 1668.5 381.0Q1630.0 430 1608.5 479.5Q1587.0 529 1587.0 580Q1587.0 594 1598.0 594Q1616.0 594 1646.5 565.5Q1677.0 537 1711.0 491.0Q1745.0 445 1779.0 388.0Q1813.0 331 1839.0 274ZM3039.0 -88Q3039.0 -82 3045.0 -82Q3054.0 -82 3075.0 -95.0Q3096.0 -108 3123.0 -128.0Q3150.0 -148 3180.5 -173.0Q3211.0 -198 3239.0 -222.0Q3267.0 -246 3289.5 -266.0Q3312.0 -286 3324.0 -297Q3333.0 -288 3346.5 -273.0Q3360.0 -258 3371.0 -246Q3347.0 -224 3313.5 -192.5Q3280.0 -161 3241.5 -127.0Q3203.0 -93 3162.0 -59.5Q3121.0 -26 3082.0 0.0Q3043.0 26 3008.0 42.5Q2973.0 59 2947.0 59Q2929.0 59 2907.5 52.0Q2886.0 45 2868.0 32.5Q2850.0 20 2838.0 3.0Q2826.0 -14 2826.0 -35Q2826.0 -43 2825.5 -54.5Q2825.0 -66 2827.0 -84.5Q2829.0 -103 2835.0 -129.5Q2841.0 -156 2853.0 -195Q2820.0 -160 2775.5 -122.0Q2731.0 -84 2682.0 -50.0Q2633.0 -16 2583.5 11.5Q2534.0 39 2492.0 53Q2487.0 55 2484.0 55.0Q2481.0 55 2478.0 55Q2468.0 55 2451.5 45.0Q2435.0 35 2419.0 18.5Q2403.0 2 2391.5 -19.0Q2380.0 -40 2380.0 -61Q2380.0 -67 2386.0 -101.0Q2392.0 -135 2414.5 -187.5Q2437.0 -240 2481.0 -305.5Q2525.0 -371 2601.0 -440Q2640.0 -475 2685.0 -506Q2723.0 -532 2772.0 -559.0Q2821.0 -586 2875.0 -602Q2877.0 -602 2878.5 -603.0Q2880.0 -604 2883.0 -602Q2895.0 -598 2911.5 -589.5Q2928.0 -581 2944.5 -571.5Q2961.0 -562 2973.5 -552.5Q2986.0 -543 2990.0 -537Q2991.0 -535 2990.0 -534L2988.0 -532Q2943.0 -508 2903.0 -478.5Q2863.0 -449 2832.0 -423Q2796.0 -392 2765.0 -362Q2692.0 -290 2664.0 -237.5Q2636.0 -185 2636.0 -162Q2636.0 -145 2650.0 -145Q2664.0 -145 2685.5 -156.5Q2707.0 -168 2734.0 -187.5Q2761.0 -207 2793.5 -233.5Q2826.0 -260 2861.0 -289Q2892.0 -314 2921.0 -343Q2946.0 -367 2973.0 -398.0Q3000.0 -429 3019.0 -461Q3019.0 -462 3020.0 -463Q3021.0 -464 3023.0 -463Q3034.0 -455 3048.0 -436.5Q3062.0 -418 3074.0 -396.0Q3086.0 -374 3094.5 -353.5Q3103.0 -333 3103.0 -322Q3080.0 -275 3067.5 -236.5Q3055.0 -198 3048.5 -169.0Q3042.0 -140 3040.5 -119.5Q3039.0 -99 3039.0 -88ZM3232.0 -930Q3176.0 -861 3111.0 -799.0Q3046.0 -737 2980.0 -684Q2975.0 -687 2964.5 -695.0Q2954.0 -703 2943.0 -713.0Q2932.0 -723 2922.0 -731.5Q2912.0 -740 2908.0 -745Q2933.0 -776 2964.0 -818.0Q2995.0 -860 3024.0 -901.5Q3053.0 -943 3076.0 -977.5Q3099.0 -1012 3109.0 -1030Q3130.0 -1020 3149.5 -1004.5Q3169.0 -989 3185.5 -974.0Q3202.0 -959 3214.0 -946.5Q3226.0 -934 3232.0 -930ZM3420.0 55Q3403.0 55 3375.5 39.5Q3348.0 24 3322.0 1.5Q3296.0 -21 3277.0 -45.0Q3258.0 -69 3258.0 -86Q3258.0 -116 3274.5 -181.0Q3291.0 -246 3318.0 -329.5Q3345.0 -413 3379.5 -508.0Q3414.0 -603 3449.0 -694H3308.0H3307.0Q3306.0 -694 3306.0 -696Q3306.0 -702 3310.5 -713.5Q3315.0 -725 3321.5 -737.5Q3328.0 -750 3336.5 -760.5Q3345.0 -771 3353.0 -774L3483.0 -780Q3511.0 -848 3536.5 -905.5Q3562.0 -963 3582.0 -1004Q3587.0 -1015 3592.5 -1027.5Q3598.0 -1040 3608.5 -1052.5Q3619.0 -1065 3636.5 -1078.5Q3654.0 -1092 3684.0 -1106Q3706.0 -1117 3737.5 -1130.0Q3769.0 -1143 3799.5 -1154.0Q3830.0 -1165 3855.0 -1172.5Q3880.0 -1180 3891.0 -1180Q3901.0 -1180 3901.0 -1176V-1174Q3901.0 -1173 3899.0 -1169Q3893.0 -1159 3877.0 -1125.5Q3861.0 -1092 3837.5 -1041.5Q3814.0 -991 3785.5 -926.5Q3757.0 -862 3727.0 -790L3967.0 -799Q3977.0 -799 3973.0 -787.0Q3969.0 -775 3958.0 -759.0Q3947.0 -743 3932.5 -726.0Q3918.0 -709 3908.0 -700Q3906.0 -698 3900.0 -696.0Q3894.0 -694 3885.0 -694H3687.0Q3651.0 -607 3618.0 -518.0Q3585.0 -429 3559.0 -349.0Q3533.0 -269 3517.5 -202.0Q3502.0 -135 3502.0 -92Q3502.0 -86 3508.0 -86Q3519.0 -86 3541.0 -98.5Q3563.0 -111 3590.5 -131.0Q3618.0 -151 3649.0 -175.0Q3680.0 -199 3708.0 -222.5Q3736.0 -246 3758.5 -265.5Q3781.0 -285 3793.0 -295Q3802.0 -286 3814.5 -272.0Q3827.0 -258 3838.0 -246Q3816.0 -224 3783.0 -192.5Q3750.0 -161 3711.5 -128.0Q3673.0 -95 3631.5 -62.0Q3590.0 -29 3550.5 -3.0Q3511.0 23 3477.0 39.0Q3443.0 55 3420.0 55ZM5289.8 -893Q5319.8 -912 5353.3 -941.5Q5386.8 -971 5420.3 -1008.5Q5453.8 -1046 5484.8 -1089.5Q5515.8 -1133 5539.3 -1177.5Q5562.8 -1222 5576.8 -1266.5Q5590.8 -1311 5590.8 -1352Q5590.8 -1383 5585.8 -1402.0Q5580.8 -1421 5572.3 -1431.5Q5563.8 -1442 5552.8 -1446.0Q5541.8 -1450 5529.8 -1450Q5481.8 -1450 5420.8 -1409.5Q5359.8 -1369 5292.3 -1299.5Q5224.8 -1230 5154.3 -1137.0Q5083.8 -1044 5017.8 -937.5Q4951.8 -831 4893.3 -718.0Q4834.8 -605 4791.3 -495.0Q4747.8 -385 4722.3 -284.5Q4696.8 -184 4696.8 -104Q4696.8 -74 4701.3 -54.5Q4705.8 -35 4714.3 -23.5Q4722.8 -12 4735.8 -8.0Q4748.8 -4 4765.8 -4Q4794.8 -4 4834.3 -28.5Q4873.8 -53 4918.3 -92.0Q4962.8 -131 5008.3 -179.0Q5053.8 -227 5093.8 -273.5Q5133.8 -320 5165.8 -359.5Q5197.8 -399 5214.8 -422Q5214.8 -421 5239.3 -401.0Q5263.8 -381 5296.8 -353.0Q5329.8 -325 5362.8 -296.5Q5395.8 -268 5412.8 -250Q5388.8 -223 5351.8 -181.0Q5314.8 -139 5268.3 -92.5Q5221.8 -46 5168.8 1.0Q5115.8 48 5059.8 85.5Q5003.8 123 4946.8 146.5Q4889.8 170 4835.8 170Q4796.8 170 4752.8 146.5Q4708.8 123 4665.8 85.5Q4622.8 48 4585.8 2.5Q4548.8 -43 4523.8 -88Q4493.8 -144 4488.8 -191.0Q4483.8 -238 4483.8 -266Q4483.8 -343 4505.8 -440.0Q4527.8 -537 4568.8 -642.5Q4609.8 -748 4667.8 -857.0Q4725.8 -966 4796.3 -1068.0Q4866.8 -1170 4949.3 -1260.0Q5031.8 -1350 5122.3 -1417.0Q5212.8 -1484 5309.3 -1522.5Q5405.8 -1561 5504.8 -1561Q5532.8 -1561 5563.8 -1546.5Q5594.8 -1532 5624.8 -1508.5Q5654.8 -1485 5682.8 -1454.0Q5710.8 -1423 5731.8 -1390.5Q5752.8 -1358 5765.3 -1325.5Q5777.8 -1293 5777.8 -1266Q5777.8 -1206 5753.8 -1133.5Q5729.8 -1061 5688.8 -987.5Q5647.8 -914 5591.3 -844.0Q5534.8 -774 5470.8 -719ZM5641.8 55Q5626.8 55 5603.8 44.5Q5580.8 34 5559.8 18.5Q5538.8 3 5523.8 -16.0Q5508.8 -35 5508.8 -53Q5518.8 -97 5535.8 -154.0Q5552.8 -211 5572.3 -270.0Q5591.8 -329 5613.3 -385.5Q5634.8 -442 5652.3 -486.0Q5669.8 -530 5682.8 -557.0Q5695.8 -584 5699.8 -584Q5705.8 -584 5729.8 -571.0Q5753.8 -558 5780.3 -539.5Q5806.8 -521 5827.8 -500.5Q5848.8 -480 5848.8 -465Q5848.8 -462 5847.8 -457.5Q5846.8 -453 5844.8 -446Q5836.8 -421 5824.3 -387.0Q5811.8 -353 5797.8 -316.5Q5783.8 -280 5769.8 -243.5Q5755.8 -207 5744.8 -175.0Q5733.8 -143 5726.8 -119.0Q5719.8 -95 5719.8 -84Q5719.8 -78 5725.8 -78Q5736.8 -78 5758.8 -91.0Q5780.8 -104 5809.3 -125.0Q5837.8 -146 5868.8 -171.0Q5899.8 -196 5928.3 -220.5Q5956.8 -245 5979.8 -265.0Q6002.8 -285 6014.8 -295Q6023.8 -286 6036.3 -272.0Q6048.8 -258 6059.8 -246Q6037.8 -224 6004.8 -192.5Q5971.8 -161 5933.3 -128.0Q5894.8 -95 5853.3 -62.0Q5811.8 -29 5772.8 -3.0Q5733.8 23 5699.8 39.0Q5665.8 55 5641.8 55ZM6104.8 -930Q6048.8 -861 5983.8 -799.0Q5918.8 -737 5852.8 -684Q5847.8 -687 5837.3 -695.0Q5826.8 -703 5815.8 -713.0Q5804.8 -723 5794.8 -731.5Q5784.8 -740 5780.8 -745Q5805.8 -776 5836.8 -818.0Q5867.8 -860 5896.8 -901.5Q5925.8 -943 5948.8 -977.5Q5971.8 -1012 5981.8 -1030Q6002.8 -1020 6022.3 -1004.5Q6041.8 -989 6058.3 -974.0Q6074.8 -959 6086.8 -946.5Q6098.8 -934 6104.8 -930ZM6274.8 -483Q6262.8 -452 6250.8 -419.0Q6238.8 -386 6227.8 -352Q6290.8 -427 6332.3 -472.0Q6373.8 -517 6401.3 -542.0Q6428.8 -567 6446.8 -577.0Q6464.8 -587 6479.8 -592Q6488.8 -592 6510.3 -582.0Q6531.8 -572 6556.3 -557.5Q6580.8 -543 6602.8 -527.5Q6624.8 -512 6633.8 -502Q6641.8 -494 6635.8 -479Q6623.8 -447 6612.3 -414.5Q6600.8 -382 6589.8 -348Q6656.8 -425 6701.8 -471.5Q6746.8 -518 6775.3 -543.0Q6803.8 -568 6819.8 -576.0Q6835.8 -584 6844.8 -584Q6857.8 -584 6883.8 -571.0Q6909.8 -558 6936.3 -539.0Q6962.8 -520 6982.3 -500.0Q7001.8 -480 7001.8 -465Q7001.8 -462 7000.8 -457.5Q6999.8 -453 6997.8 -446Q6989.8 -421 6977.3 -387.0Q6964.8 -353 6950.8 -316.5Q6936.8 -280 6922.8 -243.5Q6908.8 -207 6897.8 -175.0Q6886.8 -143 6879.8 -119.0Q6872.8 -95 6872.8 -84Q6872.8 -78 6878.8 -78Q6889.8 -78 6911.8 -91.0Q6933.8 -104 6962.3 -125.0Q6990.8 -146 7021.8 -171.0Q7052.8 -196 7081.3 -220.5Q7109.8 -245 7132.8 -265.0Q7155.8 -285 7167.8 -295Q7176.8 -286 7189.3 -272.0Q7201.8 -258 7212.8 -246Q7190.8 -224 7157.8 -192.5Q7124.8 -161 7086.3 -128.0Q7047.8 -95 7006.3 -62.0Q6964.8 -29 6925.8 -3.0Q6886.8 23 6852.8 39.0Q6818.8 55 6794.8 55Q6779.8 55 6756.8 44.5Q6733.8 34 6712.8 18.5Q6691.8 3 6676.8 -16.0Q6661.8 -35 6661.8 -53Q6670.8 -94 6685.8 -146.0Q6700.8 -198 6718.8 -252.5Q6736.8 -307 6756.3 -360.0Q6775.8 -413 6793.8 -458Q6773.8 -434 6740.8 -400.0Q6707.8 -366 6665.8 -310.5Q6623.8 -255 6575.8 -173.0Q6527.8 -91 6479.8 27Q6473.8 47 6450.8 47Q6438.8 47 6421.3 42.5Q6403.8 38 6384.3 28.5Q6364.8 19 6344.3 5.0Q6323.8 -9 6305.8 -29Q6305.8 -36 6313.3 -71.5Q6320.8 -107 6335.3 -162.0Q6349.8 -217 6369.8 -285.0Q6389.8 -353 6414.8 -425Q6390.8 -399 6354.8 -358.5Q6318.8 -318 6277.8 -260.5Q6236.8 -203 6193.8 -127.0Q6150.8 -51 6112.8 45Q6106.8 66 6084.8 66Q6067.8 66 6046.3 56.5Q6024.8 47 6002.3 31.5Q5979.8 16 5957.3 -3.0Q5934.8 -22 5916.8 -41Q5916.8 -47 5922.3 -75.0Q5927.8 -103 5937.8 -145.5Q5947.8 -188 5962.3 -242.5Q5976.8 -297 5995.8 -357.0Q6014.8 -417 6037.8 -479.5Q6060.8 -542 6086.8 -600Q6128.8 -580 6180.3 -551.0Q6231.8 -522 6274.8 -483ZM7376.8 -70Q7392.8 -70 7420.3 -84.0Q7447.8 -98 7480.8 -119.5Q7513.8 -141 7549.8 -167.0Q7585.8 -193 7617.3 -218.0Q7648.8 -243 7673.8 -264.0Q7698.8 -285 7710.8 -295Q7719.8 -286 7732.3 -272.0Q7744.8 -258 7755.8 -246Q7733.8 -224 7700.8 -192.5Q7667.8 -161 7629.3 -128.0Q7590.8 -95 7549.3 -62.0Q7507.8 -29 7468.3 -3.0Q7428.8 23 7394.8 39.0Q7360.8 55 7337.8 55Q7306.8 55 7272.3 33.0Q7237.8 11 7207.8 -18.5Q7177.8 -48 7156.8 -78.0Q7135.8 -108 7132.8 -123Q7130.8 -129 7130.8 -137.5Q7130.8 -146 7130.8 -156Q7130.8 -206 7143.3 -282.5Q7155.8 -359 7176.8 -444.5Q7197.8 -530 7225.3 -614.5Q7252.8 -699 7282.8 -764Q7343.8 -897 7408.3 -1000.0Q7472.8 -1103 7534.8 -1179.5Q7596.8 -1256 7653.8 -1308.0Q7710.8 -1360 7756.3 -1392.0Q7801.8 -1424 7833.3 -1438.0Q7864.8 -1452 7876.8 -1452Q7895.8 -1445 7915.3 -1431.5Q7934.8 -1418 7951.8 -1403.0Q7968.8 -1388 7981.8 -1373.5Q7994.8 -1359 8000.8 -1352Q8008.8 -1344 8011.3 -1336.5Q8013.8 -1329 8013.8 -1317Q8013.8 -1293 8001.8 -1251.0Q7989.8 -1209 7956.8 -1144.5Q7923.8 -1080 7865.3 -990.0Q7806.8 -900 7712.8 -780Q7668.8 -724 7619.8 -666Q7577.8 -616 7522.8 -557.0Q7467.8 -498 7409.8 -444Q7380.8 -334 7369.3 -250.0Q7357.8 -166 7357.8 -119Q7357.8 -88 7362.8 -79.0Q7367.8 -70 7376.8 -70ZM7536.8 -803Q7519.8 -765 7505.3 -729.0Q7490.8 -693 7477.8 -658Q7508.8 -693 7534.3 -725.0Q7559.8 -757 7577.8 -780Q7598.8 -807 7615.8 -831Q7685.8 -929 7736.8 -1011.0Q7787.8 -1093 7820.8 -1155.5Q7853.8 -1218 7869.3 -1259.5Q7884.8 -1301 7884.8 -1319Q7884.8 -1324 7884.3 -1326.5Q7883.8 -1329 7878.8 -1329Q7869.8 -1329 7837.8 -1299.5Q7805.8 -1270 7759.3 -1206.0Q7712.8 -1142 7655.3 -1042.5Q7597.8 -943 7536.8 -803ZM8271.8 -88Q8271.8 -82 8277.8 -82Q8286.8 -82 8307.8 -95.0Q8328.8 -108 8355.8 -128.0Q8382.8 -148 8413.3 -173.0Q8443.8 -198 8471.8 -222.0Q8499.8 -246 8522.3 -266.0Q8544.8 -286 8556.8 -297Q8565.8 -288 8579.3 -273.0Q8592.8 -258 8603.8 -246Q8579.8 -224 8546.3 -192.5Q8512.8 -161 8474.3 -127.0Q8435.8 -93 8394.8 -59.5Q8353.8 -26 8314.8 0.0Q8275.8 26 8240.8 42.5Q8205.8 59 8179.8 59Q8161.8 59 8140.3 52.0Q8118.8 45 8100.8 32.5Q8082.8 20 8070.8 3.0Q8058.8 -14 8058.8 -35Q8058.8 -43 8058.3 -54.5Q8057.8 -66 8059.8 -84.5Q8061.8 -103 8067.8 -129.5Q8073.8 -156 8085.8 -195Q8052.8 -160 8008.3 -122.0Q7963.8 -84 7914.8 -50.0Q7865.8 -16 7816.3 11.5Q7766.8 39 7724.8 53Q7719.8 55 7716.8 55.0Q7713.8 55 7710.8 55Q7700.8 55 7684.3 45.0Q7667.8 35 7651.8 18.5Q7635.8 2 7624.3 -19.0Q7612.8 -40 7612.8 -61Q7612.8 -67 7618.8 -101.0Q7624.8 -135 7647.3 -187.5Q7669.8 -240 7713.8 -305.5Q7757.8 -371 7833.8 -440Q7872.8 -475 7917.8 -506Q7955.8 -532 8004.8 -559.0Q8053.8 -586 8107.8 -602Q8109.8 -602 8111.3 -603.0Q8112.8 -604 8115.8 -602Q8127.8 -598 8144.3 -589.5Q8160.8 -581 8177.299999999999 -571.5Q8193.8 -562 8206.3 -552.5Q8218.8 -543 8222.8 -537Q8223.8 -535 8222.8 -534L8220.8 -532Q8175.8 -508 8135.8 -478.5Q8095.8 -449 8064.8 -423Q8028.8 -392 7997.8 -362Q7924.8 -290 7896.8 -237.5Q7868.8 -185 7868.8 -162Q7868.8 -145 7882.8 -145Q7896.8 -145 7918.3 -156.5Q7939.8 -168 7966.8 -187.5Q7993.8 -207 8026.3 -233.5Q8058.8 -260 8093.8 -289Q8124.8 -314 8153.8 -343Q8178.8 -367 8205.8 -398.0Q8232.8 -429 8251.8 -461Q8251.8 -462 8252.8 -463Q8253.8 -464 8255.8 -463Q8266.8 -455 8280.8 -436.5Q8294.8 -418 8306.8 -396.0Q8318.8 -374 8327.3 -353.5Q8335.8 -333 8335.8 -322Q8312.8 -275 8300.3 -236.5Q8287.8 -198 8281.3 -169.0Q8274.8 -140 8273.3 -119.5Q8271.8 -99 8271.8 -88ZM8774.8 -369Q8828.8 -436 8873.8 -479.5Q8918.8 -523 8953.3 -548.5Q8987.8 -574 9011.8 -584.0Q9035.8 -594 9047.8 -594Q9063.8 -594 9082.8 -582.0Q9101.8 -570 9120.3 -553.0Q9138.8 -536 9154.8 -518.0Q9170.8 -500 9181.8 -489Q9192.8 -478 9195.3 -465.5Q9197.8 -453 9197.8 -440Q9197.8 -393 9181.8 -337.5Q9165.8 -282 9139.8 -227.5Q9113.8 -173 9080.8 -124.0Q9047.8 -75 9013.8 -41Q9042.8 -53 9073.3 -70.5Q9103.8 -88 9133.8 -108.0Q9163.8 -128 9192.8 -149.5Q9221.8 -171 9247.8 -192Q9308.8 -240 9365.8 -295L9410.8 -246Q9377.8 -215 9337.3 -180.0Q9296.8 -145 9252.8 -111.0Q9208.8 -77 9163.3 -46.0Q9117.8 -15 9075.8 9.0Q9033.8 33 8997.8 47.0Q8961.8 61 8935.8 61Q8917.8 61 8890.8 50.5Q8863.8 40 8838.3 25.0Q8812.8 10 8794.3 -7.5Q8775.8 -25 8775.8 -39Q8775.8 -47 8786.3 -53.5Q8796.8 -60 8816.8 -77.0Q8836.8 -94 8864.8 -128.0Q8892.8 -162 8927.8 -227Q8946.8 -263 8963.8 -301.5Q8980.8 -340 8993.8 -373.5Q9006.8 -407 9014.3 -431.5Q9021.8 -456 9021.8 -465Q9021.8 -474 9019.3 -477.5Q9016.8 -481 9014.8 -483Q9010.8 -484 9007.8 -483Q8993.8 -479 8951.3 -438.5Q8908.8 -398 8851.8 -322.5Q8794.8 -247 8730.8 -138.5Q8666.8 -30 8609.8 109Q8562.8 243 8515.8 380.0Q8468.8 517 8423.8 653L8169.799999999999 713Q8227.8 548 8290.3 385.5Q8352.8 223 8414.3 60.5Q8475.8 -102 8535.3 -265.0Q8594.8 -428 8648.8 -592Q8651.8 -603 8654.3 -607.5Q8656.8 -612 8664.8 -612Q8680.8 -607 8703.8 -593.5Q8726.8 -580 8749.3 -562.5Q8771.8 -545 8789.8 -527.0Q8807.8 -509 8814.8 -496Q8816.8 -493 8814.3 -485.5Q8811.8 -478 8808.8 -469Z"/><path fill="currentColor" d="M 60 330 C 2300 520, 6300 470, 9450 -10 C 6400 690, 2300 790, 60 330 Z"/></svg>'''


# Böngészőfül- és kezdőképernyő-ikon (az „S” a Yesteryear betűtípusból)
IKON_ECSET = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Cpath fill=%22%23f2f5f9%22 stroke=%22%23f2f5f9%22 stroke-width=%221.6%22 stroke-linejoin=%22round%22 d=%22M52.57 14.05Q52.60 14.11 52.68 14.31Q52.76 14.52 52.85 14.78Q52.93 15.05 53.00 15.31Q53.07 15.57 53.07 15.68Q53.07 16.62 52.68 17.94Q52.29 19.25 51.62 20.71Q50.94 22.18 50.03 23.70Q49.11 25.22 48.09 26.59Q47.07 27.96 45.98 29.08Q44.88 30.20 43.83 30.86Q43.45 30.50 42.91 30.00Q42.37 29.51 41.76 28.94Q41.15 28.37 40.54 27.79Q39.94 27.21 39.41 26.66Q40.21 26.02 41.22 24.99Q42.23 23.95 43.27 22.68Q44.30 21.41 45.30 20.05Q46.29 18.70 47.03 17.44Q47.76 16.18 48.17 15.16Q48.59 14.13 48.48 13.52Q48.40 13.22 48.22 13.10Q48.04 12.97 47.57 12.97Q45.88 12.97 43.67 13.64Q41.46 14.30 39.05 15.56Q36.64 16.82 34.20 18.64Q31.75 20.47 29.61 22.76Q27.47 25.05 25.79 27.79Q24.12 30.53 23.23 33.63Q25.17 33.60 26.91 33.47Q28.65 33.35 30.23 33.21Q31.81 33.07 33.24 32.96Q34.68 32.85 36.04 32.85Q36.76 32.85 37.58 33.63Q38.41 34.40 39.13 35.48Q39.85 36.56 40.32 37.69Q40.79 38.82 40.79 39.52Q40.79 41.48 39.78 43.46Q38.77 45.43 37.07 47.27Q35.37 49.11 33.13 50.70Q30.89 52.29 28.42 53.47Q25.94 54.64 23.37 55.32Q20.80 56.0 18.48 56.0Q18.17 55.75 17.68 55.23Q17.18 54.72 16.61 54.10Q16.05 53.48 15.45 52.79Q14.86 52.10 14.35 51.46Q13.83 50.82 13.45 50.30Q13.06 49.77 12.92 49.50Q14.44 49.72 16.58 49.61Q18.73 49.50 21.11 49.00Q23.48 48.50 25.90 47.58Q28.32 46.65 30.42 45.24Q32.52 43.83 34.13 41.88Q35.73 39.93 36.45 37.36Q34.85 37.39 33.55 37.51Q32.25 37.64 31.03 37.79Q29.82 37.94 28.54 38.05Q27.27 38.16 25.72 38.16Q25.23 38.16 24.64 38.15Q24.06 38.13 23.48 38.06Q22.90 38.0 22.38 37.83Q21.85 37.66 21.52 37.36Q20.77 36.72 20.08 36.02Q19.39 35.31 18.84 34.58Q18.29 33.85 17.95 33.10Q17.62 32.35 17.62 31.66Q17.62 29.56 18.67 26.98Q19.72 24.39 21.59 21.76Q23.46 19.14 26.00 16.66Q28.54 14.19 31.54 12.25Q34.54 10.32 37.86 9.16Q41.18 8.0 44.58 8.0Q45.77 8.0 46.75 8.35Q47.73 8.71 48.53 9.29Q49.34 9.88 49.97 10.58Q50.61 11.29 51.11 11.95Q51.60 12.61 51.96 13.18Q52.32 13.75 52.57 14.05Z%22/%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,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">'
    '<link rel="apple-touch-icon" href="data:image/png;base64,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">'
    '<meta name="theme-color" content="#16202f"><meta name="apple-mobile-web-app-title" content="Saját Címlap">'
)

# Talpas, kétsoros fejléc (a Literata betűtípusból rajzolt útvonal, SIL Open Font License)
LOGO_TALPAS = '''<svg class="logo logo-talpas" xmlns="http://www.w3.org/2000/svg" viewBox="21 -838 3615 1924" role="img" aria-label="Saját Címlap"><path fill="currentColor" d="M323.2 14.0Q267.5 14.0 217.0 -2.5Q166.5 -19.0 127.8 -47.0L111.8 10.3H42.6L31.0 -223.0L103.1 -236.3Q113.4 -204.6 130.9 -175.4Q148.4 -146.2 172.0 -123.8Q195.5 -101.4 225.7 -88.5Q255.9 -75.7 291.6 -75.7Q322.6 -75.7 343.5 -84.7Q364.3 -93.7 375.2 -109.9Q386.0 -126.1 386.0 -147.9Q386.0 -175.6 369.7 -193.3Q353.3 -211.0 321.3 -224.5Q289.3 -238.0 241.9 -252.7Q211.0 -262.3 174.8 -277.7Q138.6 -293.0 106.5 -318.8Q74.3 -344.7 53.9 -384.8Q33.6 -425.0 33.6 -484.7Q33.6 -515.4 43.3 -548.4Q53.0 -581.4 73.2 -612.8Q93.4 -644.1 124.9 -669.3Q156.4 -694.4 200.7 -709.2Q245.0 -724.0 302.6 -724.0Q359.6 -724.0 397.3 -711.2Q435.0 -698.3 470.3 -674.7L481.0 -724.0H539.5L566.9 -498.4L493.1 -483.1Q470.7 -541.1 441.4 -574.0Q412.1 -606.9 380.5 -620.4Q349.0 -634.0 318.0 -634.0Q281.9 -634.0 257.2 -616.9Q232.5 -599.9 232.5 -564.2Q232.5 -531.4 251.2 -512.1Q269.9 -492.7 300.4 -480.7Q330.9 -468.7 365.2 -458.0Q405.6 -446.3 446.6 -429.6Q487.6 -413.0 521.9 -387.3Q556.2 -361.6 576.9 -322.0Q597.6 -282.3 597.6 -224.0Q597.6 -162.3 572.6 -117.1Q547.6 -72.0 506.9 -42.8Q466.2 -13.6 418.0 0.1Q369.9 14.0 323.2 14.0ZM769.6 14.0Q692.3 14.0 650.5 -22.6Q608.6 -59.3 608.6 -123.6Q608.6 -184.6 640.3 -217.8Q671.9 -250.9 736.4 -265.6Q800.9 -280.3 901.0 -284.6V-372.1Q901.0 -406.5 895.8 -428.1Q890.6 -449.8 876.1 -460.1Q861.6 -470.5 833.9 -470.5Q814.6 -470.5 792.0 -464.4Q769.5 -458.4 754.5 -448.1Q771.6 -426.4 780.3 -411.2Q788.9 -396.0 791.9 -385.8Q795.0 -375.6 795.0 -367Q795.0 -342 772.6 -324.9Q750.3 -307.9 704.3 -307.9Q661.3 -307.9 641.3 -328.0Q621.3 -348.0 621.3 -379.0Q621.3 -419.6 651.0 -454.6Q680.6 -489.6 737.8 -511.0Q794.9 -532.3 877.3 -532.3Q959.6 -532.3 1014.6 -512.0Q1069.6 -491.7 1097.6 -446.5Q1125.6 -401.3 1125.6 -327.3V-132.8Q1125.6 -109.5 1132.1 -98.3Q1138.6 -87.1 1156.3 -87.1Q1166.0 -87.1 1174.1 -89.1Q1182.3 -91.1 1190.3 -93.8L1202.6 -39.3Q1177.6 -15.6 1137.9 -0.8Q1098.2 14.0 1054.9 14.0Q998.3 14.0 965.6 -10.0Q932.9 -34.0 921.9 -68.3Q912.9 -47.6 893.9 -28.6Q874.9 -9.6 844.6 2.1Q814.2 14.0 769.6 14.0ZM861.0 -80.9Q875.0 -80.9 883.6 -86.7Q892.3 -92.5 896.6 -104.5Q901.0 -116.6 901.0 -133.2V-224.8Q874.0 -224.1 857.1 -216.2Q840.3 -208.4 833.0 -191.5Q825.6 -174.7 825.6 -145.6Q825.6 -113.5 833.1 -97.2Q840.6 -80.9 861.0 -80.9ZM1221.5 230.0 1170.6 166.5Q1191.3 144.2 1205.1 123.3Q1219.0 102.5 1226.9 80.3Q1234.9 58.2 1238.4 32.0Q1241.9 5.8 1241.9 -25.4V-377.9Q1241.9 -409.5 1235.4 -421.2Q1228.9 -432.9 1206.6 -436.2L1177.6 -440.5L1182.6 -513.6L1451.6 -527.0L1466.6 -517.0V-93.3Q1466.6 -29.6 1451.0 20.6Q1435.3 70.9 1404.3 109.6Q1373.3 148.3 1327.8 177.9Q1282.3 207.6 1221.5 230.0ZM1341.3 -588.6Q1279.6 -588.6 1246.3 -615.5Q1213.0 -642.3 1213.0 -692.3Q1213.0 -740.3 1245.0 -767.1Q1277.0 -794.0 1341.3 -794.0Q1407.0 -794.0 1437.8 -767.6Q1468.6 -741.3 1468.6 -691.3Q1468.6 -642.3 1435.0 -615.5Q1401.3 -588.6 1341.3 -588.6ZM1677.6 14.0Q1600.3 14.0 1558.5 -22.6Q1516.6 -59.3 1516.6 -123.6Q1516.6 -184.6 1548.3 -217.8Q1579.9 -250.9 1644.4 -265.6Q1708.9 -280.3 1809.0 -284.6V-372.1Q1809.0 -406.5 1803.8 -428.1Q1798.6 -449.8 1784.1 -460.1Q1769.6 -470.5 1741.9 -470.5Q1722.6 -470.5 1700.0 -464.4Q1677.5 -458.4 1662.5 -448.1Q1679.6 -426.4 1688.3 -411.2Q1696.9 -396.0 1699.9 -385.8Q1703.0 -375.6 1703.0 -367Q1703.0 -342 1680.6 -324.9Q1658.3 -307.9 1612.3 -307.9Q1569.3 -307.9 1549.3 -328.0Q1529.3 -348.0 1529.3 -379.0Q1529.3 -419.6 1559.0 -454.6Q1588.6 -489.6 1645.8 -511.0Q1702.9 -532.3 1785.3 -532.3Q1867.6 -532.3 1922.6 -512.0Q1977.6 -491.7 2005.6 -446.5Q2033.6 -401.3 2033.6 -327.3V-132.8Q2033.6 -109.5 2040.1 -98.3Q2046.6 -87.1 2064.3 -87.1Q2074.0 -87.1 2082.1 -89.1Q2090.3 -91.1 2098.3 -93.8L2110.6 -39.3Q2085.6 -15.6 2045.9 -0.8Q2006.2 14.0 1962.9 14.0Q1906.3 14.0 1873.6 -10.0Q1840.9 -34.0 1829.9 -68.3Q1820.9 -47.6 1801.9 -28.6Q1782.9 -9.6 1752.6 2.1Q1722.2 14.0 1677.6 14.0ZM1769.0 -80.9Q1783.0 -80.9 1791.6 -86.7Q1800.3 -92.5 1804.6 -104.5Q1809.0 -116.6 1809.0 -133.2V-224.8Q1782.0 -224.1 1765.1 -216.2Q1748.3 -208.4 1741.0 -191.5Q1733.6 -174.7 1733.6 -145.6Q1733.6 -113.5 1741.1 -97.2Q1748.6 -80.9 1769.0 -80.9ZM1737.2 -572.0 1702.6 -606.9 1813.9 -790.6Q1827.9 -813.9 1841.8 -820.9Q1855.6 -827.9 1873.3 -827.9Q1900.2 -827.9 1917.1 -813.6Q1933.9 -799.3 1941.8 -780.0Q1949.6 -760.6 1949.6 -744.6Q1949.6 -726.6 1939.4 -709.9Q1929.2 -693.3 1906.9 -678.9ZM2327.2 14.0Q2272.2 14.0 2232.6 -2.9Q2192.9 -19.9 2171.8 -55.4Q2150.6 -90.9 2150.6 -144.6V-437.2H2085.3V-506.3Q2140.6 -515.0 2169.8 -535.6Q2199.0 -556.3 2212.6 -592.3Q2226.3 -628.3 2234.0 -680.6L2354.3 -697.0L2374.9 -686.0V-519.0H2500.6V-437.2H2374.9V-187.6Q2374.9 -154.2 2384.6 -134.0Q2394.3 -113.8 2424.6 -113.8Q2443.6 -113.8 2462.5 -121.2Q2481.3 -128.5 2498.3 -140.9L2530.5 -79.8Q2497.2 -39.3 2448.9 -12.6Q2400.5 14.0 2327.2 14.0ZM385.7 874.0Q276.0 874.0 197.1 832.1Q118.3 790.3 76.1 708.3Q33.9 626.2 33.9 505.9Q33.9 421.9 59.6 353.7Q85.3 285.5 133.3 236.9Q181.3 188.2 247.1 162.0Q313.0 135.9 393.0 135.9Q447.7 135.9 486.5 151.6Q525.4 167.3 556.7 190.6L570.7 135.9H631.5L671.2 389.9L590.8 405.8Q568.1 339.4 540.6 301.4Q513.0 263.3 481.3 246.6Q449.6 229.9 414.6 229.9Q365.3 229.9 335.1 260.6Q304.9 291.3 291.3 347.9Q277.6 404.5 277.6 481.6Q277.6 563.0 292.0 615.7Q306.3 668.4 330.8 698.5Q355.3 728.5 385.6 740.4Q416.0 752.2 448.0 752.2Q488.3 752.2 523.8 739.6Q559.3 726.9 587.5 706.7Q615.7 686.5 633.4 664.1L681.9 731.2Q666.9 750.5 643.1 775.2Q619.3 799.9 584.3 822.3Q549.3 844.6 500.2 859.3Q451.0 874.0 385.7 874.0ZM703.6 860.0V792.8L736.6 788.2Q751.6 785.8 756.6 776.8Q761.6 767.8 761.6 741.8V484.0Q761.6 451.7 755.4 439.4Q749.3 427.0 726.3 423.7L697.3 419.4L702.3 346.3L972.0 332.9L986.3 342.9V741.5Q986.3 767.5 991.3 776.2Q996.3 784.8 1012.9 788.2L1043.6 793.5V860.0ZM792.9 287.9 758.4 253.0 869.7 69.3Q883.7 46.0 897.5 39.0Q911.3 32.0 929.0 32.0Q956.0 32.0 972.8 46.3Q989.6 60.6 997.5 79.9Q1005.3 99.3 1005.3 115.3Q1005.3 133.3 995.1 150.0Q985.0 166.6 962.6 181.0ZM1059.3 860.0V792.8L1090.9 788.2Q1106.3 785.8 1111.1 777.0Q1115.9 768.2 1115.9 741.8V480.0Q1115.9 451.0 1109.8 439.0Q1103.6 427.0 1080.6 423.7L1051.6 419.4L1057.9 346.3L1305.0 332.9L1320.0 342.9L1324.9 439.3H1327.9Q1343.3 403.3 1366.6 378.1Q1389.9 352.9 1422.8 339.9Q1455.6 326.9 1496.9 326.9Q1562.3 326.9 1600.8 355.6Q1639.3 384.3 1653.6 433.3Q1676.0 382.0 1717.0 354.4Q1758.0 326.9 1819.6 326.9Q1877.0 326.9 1913.8 349.8Q1950.6 372.6 1968.2 412.9Q1985.7 453.3 1985.7 506.3V747.5Q1985.7 769.5 1991.3 778.2Q1997.0 786.8 2015.7 789.5L2041.7 792.8V860.0H1727.0V793.5L1738.0 792.2Q1753.3 790.2 1757.3 780.2Q1761.3 770.2 1761.3 748.2V529.5Q1761.3 494.4 1757.0 475.9Q1752.7 457.4 1744.0 450.7Q1735.3 444.0 1722.0 444.0Q1708.0 444.0 1694.5 453.7Q1681.0 463.4 1672.0 484.5Q1663.0 505.5 1663.0 539.2V746.8Q1663.0 768.2 1666.1 779.2Q1669.3 790.2 1685.6 792.2L1696.6 793.5V860.0H1404.3V793.5L1415.3 792.2Q1430.6 790.2 1434.6 780.2Q1438.6 770.2 1438.6 748.2V529.5Q1438.6 493.1 1434.3 474.9Q1430.0 456.7 1421.3 450.4Q1412.6 444.0 1399.3 444.0Q1385.3 444.0 1371.6 453.9Q1357.9 463.8 1349.1 484.3Q1340.2 504.8 1340.2 537.1V746.8Q1340.2 768.2 1343.4 779.2Q1346.6 790.2 1362.9 792.2L1373.9 793.5V860.0ZM2043.3 860.0V792.8L2075.6 788.2Q2091.6 785.8 2096.4 777.3Q2101.3 768.8 2101.3 741.8V241.4Q2101.3 209.7 2094.8 197.4Q2088.3 185.0 2065.3 181.7L2038.3 178.0L2043.3 104.9L2310.3 91.6L2325.9 102.9V742.2Q2325.9 769.5 2331.6 777.5Q2337.3 785.5 2355.3 788.8L2383.9 793.5V860.0ZM2555.6 874.0Q2478.3 874.0 2436.5 837.3Q2394.6 800.6 2394.6 736.3Q2394.6 675.3 2426.3 642.1Q2457.9 609.0 2522.4 594.3Q2586.9 579.6 2687.0 575.3V487.8Q2687.0 453.4 2681.8 431.8Q2676.6 410.1 2662.1 399.8Q2647.6 389.4 2619.9 389.4Q2600.6 389.4 2578.0 395.5Q2555.5 401.5 2540.5 411.8Q2557.6 433.5 2566.3 448.7Q2574.9 463.9 2577.9 474.1Q2581.0 484.3 2581.0 493.0Q2581.0 518.0 2558.6 535.0Q2536.3 552.0 2490.3 552.0Q2447.3 552.0 2427.3 532.0Q2407.3 511.9 2407.3 480.9Q2407.3 440.3 2437.0 405.3Q2466.6 370.3 2523.8 348.9Q2580.9 327.6 2663.3 327.6Q2745.6 327.6 2800.6 347.9Q2855.6 368.2 2883.6 413.4Q2911.6 458.6 2911.6 532.6V727.1Q2911.6 750.4 2918.1 761.6Q2924.6 772.8 2942.3 772.8Q2952.0 772.8 2960.1 770.8Q2968.3 768.8 2976.3 766.1L2988.6 820.6Q2963.6 844.3 2923.9 859.1Q2884.2 874.0 2840.9 874.0Q2784.3 874.0 2751.6 849.9Q2718.9 825.9 2707.9 791.6Q2698.9 812.3 2679.9 831.3Q2660.9 850.3 2630.6 862.1Q2600.2 874.0 2555.6 874.0ZM2647.0 779.0Q2661.0 779.0 2669.6 773.2Q2678.3 767.4 2682.6 755.4Q2687.0 743.3 2687.0 726.7V635.1Q2660.0 635.8 2643.1 643.7Q2626.3 651.5 2619.0 668.4Q2611.6 685.2 2611.6 714.3Q2611.6 746.4 2619.1 762.7Q2626.6 779.0 2647.0 779.0ZM2974.3 1076.0V1008.8L3004.3 1004.8Q3018.9 1003.2 3024.4 994.3Q3029.9 985.5 3029.9 957.8V484.0Q3029.9 450.4 3023.1 438.4Q3016.3 426.4 2996.6 423.7L2967.6 419.4L2972.6 346.3L3219.7 332.9L3234.6 342.9L3240.0 426.3H3242.3Q3257.3 401.0 3278.9 378.4Q3300.6 355.9 3332.6 341.4Q3364.6 326.9 3409.6 326.9Q3476.3 326.9 3524.8 354.8Q3573.3 382.6 3599.8 438.6Q3626.3 494.6 3626.3 581.6Q3626.3 669.0 3598.1 734.8Q3569.9 800.6 3511.7 837.3Q3453.6 874.0 3364.6 874.0Q3331.6 874.0 3304.9 868.1Q3278.3 862.3 3254.6 850.3V959.5Q3254.6 984.2 3260.4 993.5Q3266.3 1002.8 3289.6 1004.8L3344.2 1010.2V1076.0ZM3321.6 781.9Q3347.6 781.9 3362.8 765.0Q3378.0 748.2 3384.6 707.8Q3391.3 667.4 3391.3 598.7Q3391.3 540.2 3382.8 504.7Q3374.3 469.2 3358.4 453.1Q3342.6 437.1 3319.6 437.1Q3299.6 437.1 3284.8 448.6Q3270.0 460.1 3262.3 482.3Q3254.6 504.4 3254.6 536.8V758.2Q3267.3 767.9 3286.1 774.9Q3305.0 781.9 3321.6 781.9Z"/></svg>'''

IKON_TALPAS = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Cpath fill=%22%23f2f5f9%22 d=%22M32.53 54.0Q29.21 54.0 26.20 53.01Q23.19 52.03 20.88 50.36L19.92 53.78H15.80L15.11 39.86L19.41 39.07Q20.02 40.96 21.07 42.70Q22.11 44.44 23.51 45.78Q24.91 47.11 26.71 47.88Q28.51 48.65 30.64 48.65Q32.49 48.65 33.74 48.11Q34.98 47.57 35.63 46.60Q36.27 45.64 36.27 44.34Q36.27 42.69 35.30 41.64Q34.32 40.58 32.41 39.78Q30.51 38.97 27.68 38.09Q25.84 37.52 23.68 36.60Q21.52 35.69 19.61 34.15Q17.69 32.61 16.48 30.22Q15.26 27.82 15.26 24.26Q15.26 22.43 15.84 20.46Q16.42 18.49 17.62 16.63Q18.83 14.76 20.71 13.26Q22.58 11.76 25.23 10.88Q27.87 10.0 31.30 10.0Q34.70 10.0 36.95 10.76Q39.19 11.53 41.30 12.94L41.93 10.0H45.42L47.05 23.45L42.65 24.36Q41.32 20.90 39.57 18.94Q37.82 16.98 35.95 16.17Q34.07 15.36 32.21 15.36Q30.07 15.36 28.59 16.38Q27.12 17.39 27.12 19.53Q27.12 21.48 28.23 22.63Q29.35 23.79 31.17 24.50Q32.99 25.22 35.03 25.86Q37.44 26.55 39.88 27.54Q42.33 28.54 44.37 30.07Q46.42 31.60 47.65 33.96Q48.88 36.33 48.88 39.80Q48.88 43.48 47.39 46.17Q45.90 48.87 43.48 50.61Q41.05 52.35 38.18 53.17Q35.31 54.0 32.53 54.0Z%22/%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAEz0lEQVR4AbxWe1BUVRj/3SvKa+LRDCsgwc5UPuNhIVBKRAwiopWDAw7yiiAHIkbBB1HBAFHoYGCZmOGgjoROFDC0YuhEODAaoULEVFN/oOMg76V0FxZhb/e7K/uQ3bs4KWfmO/c73+v323O/Peey0BsOUh8HidQz30nq/YNE6jUgkXpzj0a8BjQ1PfMJA3qDndGdPDzDF3JcD8DmMsB6gJHgkQ1GoqnJ5hIGYc2UFghIpN5hDMOeYxi4zjge15MwCMuJ/8GEwdq7ezpyHCppMb/CVti5rXySXcgyCTwrl/kFB3hM10UWFvEsAyYAcxyOjg5ISojBpwcK0fDdaXRfvYSLshocLi1G8puxsLe3g7u7G/7uaceZU8fMVmV4bJ4A5kQgIjwULU31yNm7E69FhGHFsqWwtrKCVOqODetfRfbuDLQ1y1CYmy0Au7gsFp5iEwMEUBN6iAWRb0dyPA6VFMHGxpqWWhkeHkF3z++4e1ch2KwsLbH2RT9BX+LqggULFgi6yORBBET8QEhwIPbsSjeIGZ+YQHRcCgKCwrElKgE+/sFIScuEQqE0iHNxNr8LZgm8m5ZiUJQWssYmXL3WRapWmltasSN9t3ZNirOzhB6iIkrAbYkrnlu5fFYBWxvbWTYyXGnvwMnTZ0kVxI1/DYIiMokSWLH8WaOp4WEhQtcbcx4+ehz0ilSTk3wPWBgLMbCJErC0XGQQrL+grpfVViM1JRF2dk9oXXL5GCK2xMDLNwjf1jVo7aYUUQJy+T+m8gT7sqVPI2tnGlp/lKEgdx/CQoNha2uDmzdvYXp6WogxN4kS6Pz1N/x75465GrCxtkJMdCS+KNuPyy3nhV1hGP5fbjYTECVAf6uDZeVzKKMLITK0K5XHPhOI6TzGNVEClFJ1pgbfN14g9aFk3Uv+SE9NNptjlgBVyNz7Icq/OjHn90o5JHHbo2adnmTXlzkRUKvVOFh2BEGhr4N2ZHRUrl/DpE53he/zPib95BAlYGFhgdXenti0MZRi0T8wiLzCA/ALDENcUhrOnb8Ijv+YEJwmJomTkwmPxixKIHrrG/jm6+Mo+SQfiyWGx+rlnzuQkZWDzZGxaG27oqlmZFZzaiNWnUmUgEKhueVoJ4oK3tNl6Wl//PkX3krdBeX4hJ5Vp/b3D+gWRjRRAkrluDbllcC12L5tq3atr9Cho1Kp9E2CPjg0hPaO64JuahIloFAqDfJyc7KQ8U4KHjyiE+O3wdHB3iCWFh8Vl2JqaopUk/JQBOgDI4O/nn9pvYDqk1+i6kQ5mpvq8MG+zFkAFZVVQpPOcjxg4AlwJl8SnYQz8fT3u9XXJyxt+C+jNb6r4b/mBTzFX9mC8f4kl49hT04+iksO3beIPbgBlgM6YWLoE/j8SAVCNkQimW+4ylPVQuff5huMmu/a9S6cralDVnYe1oVsQm29zERFQzNh8zvAmCTQd7sfz6zyE2R4ZFQ4CX+61Iai/aVIfDsDgSGb+Wv3ZUTFpuD9vI9R39AIlWrSEEV0xXSy98Ac5ZmMYZ4HYRI2O9bb2ctwSJpnfKihTiRs/hUAgze6ajlOvZE/VTVd9hjZEAZhjfR21xOMQICUoRvdjfcYZhWgLuC3pwngBsn+f0WTzw1qaqoLCIOwNHbgPwAAAP//9hReqAAAAAZJREFUAwDL15/U6I+hxAAAAABJRU5ErkJggg==">'
    '<link rel="apple-touch-icon" href="data:image/png;base64,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">'
    '<meta name="theme-color" content="#16202f"><meta name="apple-mobile-web-app-title" content="Saját Címlap">'
)

# Klasszikus, egysoros fejléc (a Tinos betűtípusból rajzolt útvonal, Apache License 2.0)
LOGO_KLASSZIKUS = '''<svg class="logo logo-klasszikus" xmlns="http://www.w3.org/2000/svg" viewBox="99 -1767 16570 1797" role="img" aria-label="Saját Címlap"><path fill="currentColor" d="M109 -411H197L242 -196Q284 -147 369.0 -114.0Q454 -81 545 -81Q832 -81 832 -317Q832 -391 777.5 -442.0Q723 -493 606 -533Q434 -590 358.5 -625.5Q283 -661 231.0 -709.0Q179 -757 148.0 -825.5Q117 -894 117 -994Q117 -1171 237.5 -1263.5Q358 -1356 590 -1356Q758 -1356 962 -1313V-994H873L828 -1178Q726 -1252 590 -1252Q467 -1252 401.0 -1206.5Q335 -1161 335 -1067Q335 -1000 390.0 -952.5Q445 -905 562 -868Q791 -793 876.5 -737.5Q962 -682 1007.0 -600.0Q1052 -518 1052 -407Q1052 20 549 20Q434 20 314.5 0.5Q195 -19 109 -51ZM1689.8 -73V0H1281.8V-73L1381.8 -100L1858.8 -1352H2148.8L2623.8 -100L2725.8 -73V0H2128.8V-73L2283.8 -100L2155.8 -447H1640.8L1517.8 -100ZM1902.8 -1150 1681.8 -557H2117.8ZM3364.7 -1242 3192.7 -1268V-1341H3831.7V-1268L3679.7 -1242V-432Q3679.7 -215 3559.7 -97.5Q3439.7 20 3217.7 20Q3142.7 20 3070.2 11.5Q2997.7 3 2954.7 -10V-314H3043.7L3073.7 -135Q3092.7 -112 3127.7 -99.0Q3162.7 -86 3204.7 -86Q3273.7 -86 3319.2 -134.0Q3364.7 -182 3364.7 -276ZM4438.6 -73V0H4030.6V-73L4130.6 -100L4607.6 -1352H4897.6L5372.6 -100L5474.6 -73V0H4877.6V-73L5032.6 -100L4904.6 -447H4389.6L4266.6 -100ZM4651.6 -1150 4430.6 -557H4866.6ZM4612.6 -1452V-1485L4778.6 -1757H5063.6V-1720L4698.6 -1452ZM5922.5 0V-73L6135.5 -100V-1235H6084.5Q5855.5 -1235 5762.5 -1215L5735.5 -966H5644.5V-1341H6947.5V-966H6855.5L6828.5 -1215Q6745.5 -1233 6500.5 -1233H6451.5V-100L6664.5 -73V0ZM8612.7 20Q8275.7 20 8086.7 -159.0Q7897.7 -338 7897.7 -655Q7897.7 -999 8078.2 -1177.5Q8258.7 -1356 8611.7 -1356Q8844.7 -1356 9094.7 -1289L9100.7 -967H9010.7L8982.7 -1161Q8850.7 -1251 8675.7 -1251Q8443.7 -1251 8336.7 -1106.5Q8229.7 -962 8229.7 -658Q8229.7 -377 8341.7 -230.0Q8453.7 -83 8667.7 -83Q8780.7 -83 8865.2 -113.0Q8949.7 -143 8997.7 -184L9029.7 -404H9120.7L9114.7 -64Q9024.7 -29 8880.7 -4.5Q8736.7 20 8612.7 20ZM9955.6 -100 10127.6 -74V0H9468.6V-74L9640.6 -100V-1241L9468.6 -1268V-1341H10127.6V-1268L9955.6 -1241ZM9637.6 -1452V-1485L9803.6 -1757H10088.6V-1720L9723.6 -1452ZM11201.4 0H11146.4L10651.4 -1133V-100L10831.4 -73V0H10354.4V-73L10526.4 -100V-1242L10354.4 -1268V-1341H10881.4L11264.4 -459L11655.4 -1341H12193.4V-1268L12021.4 -1242V-100L12193.4 -73V0H11526.4V-73L11706.4 -100V-1133ZM13104.3 -1268 12897.3 -1242V-106H13170.3Q13383.3 -106 13483.3 -126L13565.3 -405H13655.3L13617.3 0H12410.3V-73L12582.3 -100V-1242L12411.3 -1268V-1341H13104.3ZM14292.2 -73V0H13884.2V-73L13984.2 -100L14461.2 -1352H14751.2L15226.2 -100L15328.2 -73V0H14731.2V-73L14886.2 -100L14758.2 -447H14243.2L14120.2 -100ZM14505.2 -1150 14284.2 -557H14720.2ZM16337.1 -944Q16337.1 -1104 16277.6 -1167.5Q16218.1 -1231 16068.1 -1231H15989.1V-636H16072.1Q16211.1 -636 16274.1 -706.0Q16337.1 -776 16337.1 -944ZM15989.1 -526V-100L16212.1 -73V0H15514.1V-73L15673.1 -100V-1242L15501.1 -1268V-1341H16092.1Q16377.1 -1341 16518.1 -1245.5Q16659.1 -1150 16659.1 -946Q16659.1 -526 16169.1 -526Z"/></svg>'''

IKON_KLASSZIKUS = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Cpath fill=%22%23f2f5f9%22 d=%22M17.60 39.84H20.29L21.66 46.40Q22.94 47.90 25.54 48.90Q28.13 49.91 30.91 49.91Q39.67 49.91 39.67 42.71Q39.67 40.45 38.01 38.89Q36.34 37.34 32.77 36.12Q27.52 34.38 25.22 33.29Q22.91 32.21 21.33 30.74Q19.74 29.28 18.79 27.19Q17.85 25.10 17.85 22.04Q17.85 16.64 21.53 13.82Q25.20 11.0 32.28 11.0Q37.41 11.0 43.64 12.31V22.04H40.92L39.55 16.43Q36.44 14.17 32.28 14.17Q28.53 14.17 26.52 15.56Q24.50 16.95 24.50 19.82Q24.50 21.86 26.18 23.31Q27.86 24.76 31.43 25.89Q38.42 28.18 41.03 29.87Q43.64 31.57 45.01 34.07Q46.39 36.57 46.39 39.96Q46.39 53.0 31.03 53.0Q27.52 53.0 23.88 52.40Q20.23 51.80 17.60 50.83Z%22/%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAEe0lEQVR4AbxWe1BUVRz+zuURajwMdkuHYEcz4rFgkxWEkX/ZiIg8NJDkZaJZaqYxDNWsQlAGThRJkZKbjGmviYk0RIYcE5PBpgjChNEGZ0pjechjJkGavd7f3blXVnbv3mWUM3vu+T3O7/u+vefeew6HCc1Ht9BHq9MXaHQR9VpdeI9WF8HfmR7eY8HUFxAHJjROsjWB+mVuPN8BcAYGLAWYFnesMa0FkzMQB3FJ0KIArS7iWca4HxjDXClxt0biIC6N8IeJg/MO0M/meRjJmd7OVXn5h9zHuXEsU1A1Z3rJAYFzrrurawbHwCLhRJs1ayaejo5E4srleCErDRvXZyApIQ6Lo55EQIC/E0gAcQsCEAkVjeM4pKUk48e6Ghj3laP07Z3Iz92G3Fc3o6TYgM+qPhRy36Ljtya8v6dYBSIEAYikhzAQKlpJ8U4UGvLg6zsb9Q0nkbl+M6KeiUX4ohjEJaah/KP9uHFjHPe4u2P+PFWQxBpIAshQ7GkpSUiIXybOaTx5Gi9vy8OZsy3o7evDf9dHcaHrIsor9iM1PUec48xFlQBabwm09thxyZw0tv1xHtcGh8B4NilnL+BQAGMMYSHBcr0Lp1zScu5Xea4aQxlNQKA1dXNzFSzLb9OGbHh7eVocG1danrjk521kbIccChgdG8PwyIhcveCheaj//iukrEqAt7eXHJ+q4VAAAdcetV53Pz9fFBe8jnNNJ3Cwai9SVyfC0/Nemup0VyWgotKIgYFrk8Dp2xAd9QSKduWj+afjyNuxBUrLMwlACKgSQK/bmsyNuHL1X6HE9o+elZx16air/QKPBC2wPclGVJUAqrv0VzdWJK/FsboGcu12rUaDQ8aPofazrFoAMQ4NDeOV197A8sQ1OHTka5y/0Amz2Uwpq+7j7QVD/g6rmD3HKQESSGfXJewqKkV8cjqeWhILY/URjI//L6XFcUlMNGZ4eIi20mVKAiYC9vUPoPjdMiQ+l4Eek2liChqtn5Vvy3EoQB8ajIsdLXh80aO26uUY7Qe7S8tln4zR62M0KHaHAqTq8LBbn2Mpdvv49z9X5dDQ8AhMvb2yb89QLUAfFmoPQ46HBAfJdpewQ8qOgqFaQMziSAQ9PN8u1MwZHngxJ1POV1YdlG0lQ7UAL09P1HxZjXfeehMLI8JETBcXF9D7viJ2KY7WHMacB+4X41XGz3Hq9M+i7egiCOB7lCaZevvR3PILeOHo7O7uhtVJ8fjm8AHxwexsOysew8pKixDwoD+azjRj09Zc7N7zgRLkhBzfw/FAKxQavVprs1+C/rEYrFyVji3b81Hy3l7QEaysvBLb8wxISs0Wj2ZZG7aiofGUApp1iriFO8AUBUgltC13/NmJuvpG7Pu0WjyCVXxyALRTtrV3iEczaa76kbVy42CVgpJBTHMjTuLmBrtbuxmPddPMDzPMWcQtLAFguvx7Dc+bY4Xn7MrdFkIcxNXf3f4dcYkCyOi93F43zlgoYC4Ubs8JgLf+sNOkKXRLCW+yYJoLiYO4LHHgJgAAAP//dsF3iAAAAAZJREFUAwAMHHbUEVNGfwAAAABJRU5ErkJggg==">'
    '<link rel="apple-touch-icon" href="data:image/png;base64,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">'
    '<meta name="theme-color" content="#16202f"><meta name="apple-mobile-web-app-title" content="Saját Címlap">'
)

IKON_SVG = {"ecset": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" stroke=\"#f2f5f9\" stroke-width=\"1.6\" stroke-linejoin=\"round\" d=\"M52.57 14.05Q52.60 14.11 52.68 14.31Q52.76 14.52 52.85 14.78Q52.93 15.05 53.00 15.31Q53.07 15.57 53.07 15.68Q53.07 16.62 52.68 17.94Q52.29 19.25 51.62 20.71Q50.94 22.18 50.03 23.70Q49.11 25.22 48.09 26.59Q47.07 27.96 45.98 29.08Q44.88 30.20 43.83 30.86Q43.45 30.50 42.91 30.00Q42.37 29.51 41.76 28.94Q41.15 28.37 40.54 27.79Q39.94 27.21 39.41 26.66Q40.21 26.02 41.22 24.99Q42.23 23.95 43.27 22.68Q44.30 21.41 45.30 20.05Q46.29 18.70 47.03 17.44Q47.76 16.18 48.17 15.16Q48.59 14.13 48.48 13.52Q48.40 13.22 48.22 13.10Q48.04 12.97 47.57 12.97Q45.88 12.97 43.67 13.64Q41.46 14.30 39.05 15.56Q36.64 16.82 34.20 18.64Q31.75 20.47 29.61 22.76Q27.47 25.05 25.79 27.79Q24.12 30.53 23.23 33.63Q25.17 33.60 26.91 33.47Q28.65 33.35 30.23 33.21Q31.81 33.07 33.24 32.96Q34.68 32.85 36.04 32.85Q36.76 32.85 37.58 33.63Q38.41 34.40 39.13 35.48Q39.85 36.56 40.32 37.69Q40.79 38.82 40.79 39.52Q40.79 41.48 39.78 43.46Q38.77 45.43 37.07 47.27Q35.37 49.11 33.13 50.70Q30.89 52.29 28.42 53.47Q25.94 54.64 23.37 55.32Q20.80 56.0 18.48 56.0Q18.17 55.75 17.68 55.23Q17.18 54.72 16.61 54.10Q16.05 53.48 15.45 52.79Q14.86 52.10 14.35 51.46Q13.83 50.82 13.45 50.30Q13.06 49.77 12.92 49.50Q14.44 49.72 16.58 49.61Q18.73 49.50 21.11 49.00Q23.48 48.50 25.90 47.58Q28.32 46.65 30.42 45.24Q32.52 43.83 34.13 41.88Q35.73 39.93 36.45 37.36Q34.85 37.39 33.55 37.51Q32.25 37.64 31.03 37.79Q29.82 37.94 28.54 38.05Q27.27 38.16 25.72 38.16Q25.23 38.16 24.64 38.15Q24.06 38.13 23.48 38.06Q22.90 38.0 22.38 37.83Q21.85 37.66 21.52 37.36Q20.77 36.72 20.08 36.02Q19.39 35.31 18.84 34.58Q18.29 33.85 17.95 33.10Q17.62 32.35 17.62 31.66Q17.62 29.56 18.67 26.98Q19.72 24.39 21.59 21.76Q23.46 19.14 26.00 16.66Q28.54 14.19 31.54 12.25Q34.54 10.32 37.86 9.16Q41.18 8.0 44.58 8.0Q45.77 8.0 46.75 8.35Q47.73 8.71 48.53 9.29Q49.34 9.88 49.97 10.58Q50.61 11.29 51.11 11.95Q51.60 12.61 51.96 13.18Q52.32 13.75 52.57 14.05Z\"/></svg>", "talpas": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" d=\"M32.53 54.0Q29.21 54.0 26.20 53.01Q23.19 52.03 20.88 50.36L19.92 53.78H15.80L15.11 39.86L19.41 39.07Q20.02 40.96 21.07 42.70Q22.11 44.44 23.51 45.78Q24.91 47.11 26.71 47.88Q28.51 48.65 30.64 48.65Q32.49 48.65 33.74 48.11Q34.98 47.57 35.63 46.60Q36.27 45.64 36.27 44.34Q36.27 42.69 35.30 41.64Q34.32 40.58 32.41 39.78Q30.51 38.97 27.68 38.09Q25.84 37.52 23.68 36.60Q21.52 35.69 19.61 34.15Q17.69 32.61 16.48 30.22Q15.26 27.82 15.26 24.26Q15.26 22.43 15.84 20.46Q16.42 18.49 17.62 16.63Q18.83 14.76 20.71 13.26Q22.58 11.76 25.23 10.88Q27.87 10.0 31.30 10.0Q34.70 10.0 36.95 10.76Q39.19 11.53 41.30 12.94L41.93 10.0H45.42L47.05 23.45L42.65 24.36Q41.32 20.90 39.57 18.94Q37.82 16.98 35.95 16.17Q34.07 15.36 32.21 15.36Q30.07 15.36 28.59 16.38Q27.12 17.39 27.12 19.53Q27.12 21.48 28.23 22.63Q29.35 23.79 31.17 24.50Q32.99 25.22 35.03 25.86Q37.44 26.55 39.88 27.54Q42.33 28.54 44.37 30.07Q46.42 31.60 47.65 33.96Q48.88 36.33 48.88 39.80Q48.88 43.48 47.39 46.17Q45.90 48.87 43.48 50.61Q41.05 52.35 38.18 53.17Q35.31 54.0 32.53 54.0Z\"/></svg>", "klasszikus": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" d=\"M17.60 39.84H20.29L21.66 46.40Q22.94 47.90 25.54 48.90Q28.13 49.91 30.91 49.91Q39.67 49.91 39.67 42.71Q39.67 40.45 38.01 38.89Q36.34 37.34 32.77 36.12Q27.52 34.38 25.22 33.29Q22.91 32.21 21.33 30.74Q19.74 29.28 18.79 27.19Q17.85 25.10 17.85 22.04Q17.85 16.64 21.53 13.82Q25.20 11.0 32.28 11.0Q37.41 11.0 43.64 12.31V22.04H40.92L39.55 16.43Q36.44 14.17 32.28 14.17Q28.53 14.17 26.52 15.56Q24.50 16.95 24.50 19.82Q24.50 21.86 26.18 23.31Q27.86 24.76 31.43 25.89Q38.42 28.18 41.03 29.87Q43.64 31.57 45.01 34.07Q46.39 36.57 46.39 39.96Q46.39 53.0 31.03 53.0Q27.52 53.0 23.88 52.40Q20.23 51.80 17.60 50.83Z\"/></svg>"}

FEJ_JS = r"""(function(){
  var B = __BEALLITAS__;
  var fej = document.getElementById('fej'); if (!fej) return;
  // napszak szerinti háttérszín (a néző saját órája szerint)
  var ora = new Date().getHours(), szin = B.szinek[B.szinek.length - 1][2];
  for (var i = 0; i < B.szinek.length; i++) {
    var s = B.szinek[i]; if (ora >= s[0] && ora < s[1]) { szin = s[2]; break; }
  }
  if (B.fix_szin) szin = B.fix_szin;
  fej.style.setProperty('--fejszin', szin);
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

LOGO = {"ecset": LOGO_ECSET, "talpas": LOGO_TALPAS, "klasszikus": LOGO_KLASSZIKUS}
IKON_LINKEK = {"ecset": IKON_ECSET, "talpas": IKON_TALPAS, "klasszikus": IKON_KLASSZIKUS}


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
    ervenyes = [h for h in tarolt.values() if h["ido"] >= hatar and h["forras"] in FN]
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
