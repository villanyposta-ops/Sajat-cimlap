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
     "panel": "idojaras"},
]

# Időjárás-előrejelzés (Open-Meteo, ingyenes, regisztráció nélkül). Helyszín: név + koordináták.
IDOJARAS_HELYEK = [
    {"nev": "Budapest", "lat": 47.4979, "lon": 19.0402},
    {"nev": "Pécs",     "lat": 46.0727, "lon": 18.2323},
    {"nev": "Balaton (Siófok)", "lat": 46.9041, "lon": 18.0580},
]
IDOJARAS_NAPOK = 5

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
                 "business"],
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


def idojaras_panel(helyek, idokep_hirek):
    if not helyek and not idokep_hirek:
        return ""
    kartyak = "".join(ido_kartya(h) for h in helyek)
    hirek = sorted(idokep_hirek, key=lambda x: x["ido"], reverse=True)[:8]
    hirek_html = "".join(
        f'<li><time>{ido_szoveg(x["ido"])}</time>'
        f'<a href="{e(x["link"])}" target="_blank" rel="noopener">{e(x["cim"])}</a></li>' for x in hirek)
    oldalsav = (f'<aside class="ido-hirek"><h3>Az Időkép hírei</h3><ul>{hirek_html}</ul></aside>'
                if hirek else "")
    return (f'<section class="rovat idojaras" id="idojaras" style="--r:{IDOJARAS_SZIN}">'
            f'{rovatfej("idojaras", "Időjárás", IDOJARAS_SZIN, 0)}'
            f'<div class="tartalom ido-racs"><div class="ido-helyek">{kartyak}</div>{oldalsav}</div>'
            f'</section>')


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
{IKON_LINKEK}
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400..800&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body>
<header class="fej">
  <div class="fejsor">
    <a class="logo-link" href="#" aria-label="{e(OLDAL_CIM)}">{FEJLEC_SVG if OLDAL_CIM == "Saját Címlap" else f'<span class="nev">{e(OLDAL_CIM)}</span>'}</a>
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

/* fejléc */
.fej{border-bottom:1px solid var(--vonal)}
.fejsor{max-width:1280px;margin:0 auto;padding:1.1rem 1.5rem .9rem;display:flex;align-items:flex-end;gap:2rem}
.logo-link{display:block;color:var(--tinta);margin-right:auto;line-height:0}
.logo{height:4.6rem;width:auto;display:block}
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
.ido-racs{display:grid;grid-template-columns:minmax(0,2.2fr) minmax(0,1fr);gap:1.4rem;align-items:start}
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
@media (max-width:1000px){.ido-racs{grid-template-columns:1fr}}
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


# A fejléc grafikája (a Yesteryear betűtípusból rajzolt útvonal, SIL Open Font License)
FEJLEC_SVG = '''<svg class="logo" xmlns="http://www.w3.org/2000/svg" viewBox="-148 -1601 9819 2401" role="img" aria-label="Saját Címlap"><path fill="currentColor" d="M1346 -1337Q1347 -1335 1350.0 -1327.5Q1353 -1320 1356.0 -1310.5Q1359 -1301 1361.5 -1291.5Q1364 -1282 1364 -1278Q1364 -1244 1350.0 -1196.5Q1336 -1149 1311.5 -1096.0Q1287 -1043 1254.0 -988.0Q1221 -933 1184.0 -883.5Q1147 -834 1107.5 -793.5Q1068 -753 1030 -729Q1016 -742 996.5 -760.0Q977 -778 955.0 -798.5Q933 -819 911.0 -840.0Q889 -861 870 -881Q899 -904 935.5 -941.5Q972 -979 1009.5 -1025.0Q1047 -1071 1083.0 -1120.0Q1119 -1169 1145.5 -1214.5Q1172 -1260 1187.0 -1297.0Q1202 -1334 1198 -1356Q1195 -1367 1188.5 -1371.5Q1182 -1376 1165 -1376Q1104 -1376 1024.0 -1352.0Q944 -1328 857.0 -1282.5Q770 -1237 681.5 -1171.0Q593 -1105 515.5 -1022.0Q438 -939 377.5 -840.0Q317 -741 285 -629Q355 -630 418.0 -634.5Q481 -639 538.0 -644.0Q595 -649 647.0 -653.0Q699 -657 748 -657Q774 -657 804.0 -629.0Q834 -601 860.0 -562.0Q886 -523 903.0 -482.0Q920 -441 920 -416Q920 -345 883.5 -273.5Q847 -202 785.5 -135.5Q724 -69 643.0 -11.5Q562 46 472.5 88.5Q383 131 290.0 155.5Q197 180 113 180Q102 171 84.0 152.5Q66 134 45.5 111.5Q25 89 3.5 64.0Q-18 39 -36.5 16.0Q-55 -7 -69.0 -26.0Q-83 -45 -88 -55Q-33 -47 44.5 -51.0Q122 -55 208.0 -73.0Q294 -91 381.5 -124.5Q469 -158 545.0 -209.0Q621 -260 679.0 -330.5Q737 -401 763 -494Q705 -493 658.0 -488.5Q611 -484 567.0 -478.5Q523 -473 477.0 -469.0Q431 -465 375 -465Q357 -465 336.0 -465.5Q315 -466 294.0 -468.5Q273 -471 254.0 -477.0Q235 -483 223 -494Q196 -517 171.0 -542.5Q146 -568 126.0 -594.5Q106 -621 94.0 -648.0Q82 -675 82 -700Q82 -776 120.0 -869.5Q158 -963 225.5 -1058.0Q293 -1153 385.0 -1242.5Q477 -1332 585.5 -1402.0Q694 -1472 814.0 -1514.0Q934 -1556 1057 -1556Q1100 -1556 1135.5 -1543.0Q1171 -1530 1200.0 -1509.0Q1229 -1488 1252.0 -1462.5Q1275 -1437 1293.0 -1413.0Q1311 -1389 1324.0 -1368.5Q1337 -1348 1346 -1337ZM1751.0 -88Q1751.0 -82 1757.0 -82Q1766.0 -82 1787.0 -95.0Q1808.0 -108 1835.0 -128.0Q1862.0 -148 1892.5 -173.0Q1923.0 -198 1951.0 -222.0Q1979.0 -246 2001.5 -266.0Q2024.0 -286 2036.0 -297Q2045.0 -288 2058.5 -273.0Q2072.0 -258 2083.0 -246Q2059.0 -224 2025.5 -192.5Q1992.0 -161 1953.5 -127.0Q1915.0 -93 1874.0 -59.5Q1833.0 -26 1794.0 0.0Q1755.0 26 1720.0 42.5Q1685.0 59 1659.0 59Q1641.0 59 1619.5 52.0Q1598.0 45 1580.0 32.5Q1562.0 20 1550.0 3.0Q1538.0 -14 1538.0 -35Q1538.0 -43 1537.5 -54.5Q1537.0 -66 1539.0 -84.5Q1541.0 -103 1547.0 -129.5Q1553.0 -156 1565.0 -195Q1532.0 -160 1487.5 -122.0Q1443.0 -84 1394.0 -50.0Q1345.0 -16 1295.5 11.5Q1246.0 39 1204.0 53Q1199.0 55 1196.0 55.0Q1193.0 55 1190.0 55Q1180.0 55 1163.5 45.0Q1147.0 35 1131.0 18.5Q1115.0 2 1103.5 -19.0Q1092.0 -40 1092.0 -61Q1092.0 -67 1098.0 -101.0Q1104.0 -135 1126.5 -187.5Q1149.0 -240 1193.0 -305.5Q1237.0 -371 1313.0 -440Q1352.0 -475 1397.0 -506Q1435.0 -532 1484.0 -559.0Q1533.0 -586 1587.0 -602Q1589.0 -602 1590.5 -603.0Q1592.0 -604 1595.0 -602Q1607.0 -598 1623.5 -589.5Q1640.0 -581 1656.5 -571.5Q1673.0 -562 1685.5 -552.5Q1698.0 -543 1702.0 -537Q1703.0 -535 1702.0 -534L1700.0 -532Q1655.0 -508 1615.0 -478.5Q1575.0 -449 1544.0 -423Q1508.0 -392 1477.0 -362Q1404.0 -290 1376.0 -237.5Q1348.0 -185 1348.0 -162Q1348.0 -145 1362.0 -145Q1376.0 -145 1397.5 -156.5Q1419.0 -168 1446.0 -187.5Q1473.0 -207 1505.5 -233.5Q1538.0 -260 1573.0 -289Q1604.0 -314 1633.0 -343Q1658.0 -367 1685.0 -398.0Q1712.0 -429 1731.0 -461Q1731.0 -462 1732.0 -463Q1733.0 -464 1735.0 -463Q1746.0 -455 1760.0 -436.5Q1774.0 -418 1786.0 -396.0Q1798.0 -374 1806.5 -353.5Q1815.0 -333 1815.0 -322Q1792.0 -275 1779.5 -236.5Q1767.0 -198 1760.5 -169.0Q1754.0 -140 1752.5 -119.5Q1751.0 -99 1751.0 -88ZM2532.0 -246Q2441.0 -184 2345.5 -124.0Q2250.0 -64 2156.0 -4Q2139.0 48 2118.5 105.5Q2098.0 163 2065.0 240Q2021.0 340 1961.5 424.5Q1902.0 509 1838.5 570.5Q1775.0 632 1713.0 667.0Q1651.0 702 1602.0 702Q1586.0 702 1564.5 685.5Q1543.0 669 1523.5 646.5Q1504.0 624 1490.5 600.0Q1477.0 576 1477.0 561Q1477.0 502 1511.0 440.5Q1545.0 379 1605.0 316.0Q1665.0 253 1747.0 188.5Q1829.0 124 1925.0 58Q1951.0 -16 1976.0 -93.5Q2001.0 -171 2023.5 -244.5Q2046.0 -318 2065.5 -383.5Q2085.0 -449 2100.0 -498.0Q2115.0 -547 2125.0 -575.5Q2135.0 -604 2138.0 -604Q2144.0 -604 2169.5 -589.0Q2195.0 -574 2222.5 -553.5Q2250.0 -533 2272.0 -512.5Q2294.0 -492 2294.0 -481Q2294.0 -476 2293.0 -472.0Q2292.0 -468 2290.0 -463Q2270.0 -403 2255.0 -353.0Q2240.0 -303 2228.0 -260.5Q2216.0 -218 2206.0 -181.5Q2196.0 -145 2187.0 -112Q2262.0 -159 2337.0 -205.0Q2412.0 -251 2487.0 -299ZM2452.0 -889Q2451.0 -885 2444.5 -869.5Q2438.0 -854 2429.0 -833.5Q2420.0 -813 2408.5 -790.5Q2397.0 -768 2386.0 -749.0Q2375.0 -730 2365.0 -717.5Q2355.0 -705 2349.0 -705Q2331.0 -706 2305.5 -716.0Q2280.0 -726 2256.5 -738.5Q2233.0 -751 2216.5 -763.0Q2200.0 -775 2200.0 -780Q2200.0 -802 2212.0 -836.0Q2224.0 -870 2240.5 -901.0Q2257.0 -932 2273.5 -954.5Q2290.0 -977 2298.0 -977Q2316.0 -977 2342.0 -965.5Q2368.0 -954 2392.5 -939.0Q2417.0 -924 2434.5 -909.5Q2452.0 -895 2452.0 -889ZM1839.0 274Q1849.0 254 1858.0 232.0Q1867.0 210 1877.0 186Q1813.0 234 1760.0 283.0Q1707.0 332 1668.5 381.0Q1630.0 430 1608.5 479.5Q1587.0 529 1587.0 580Q1587.0 594 1598.0 594Q1616.0 594 1646.5 565.5Q1677.0 537 1711.0 491.0Q1745.0 445 1779.0 388.0Q1813.0 331 1839.0 274ZM3039.0 -88Q3039.0 -82 3045.0 -82Q3054.0 -82 3075.0 -95.0Q3096.0 -108 3123.0 -128.0Q3150.0 -148 3180.5 -173.0Q3211.0 -198 3239.0 -222.0Q3267.0 -246 3289.5 -266.0Q3312.0 -286 3324.0 -297Q3333.0 -288 3346.5 -273.0Q3360.0 -258 3371.0 -246Q3347.0 -224 3313.5 -192.5Q3280.0 -161 3241.5 -127.0Q3203.0 -93 3162.0 -59.5Q3121.0 -26 3082.0 0.0Q3043.0 26 3008.0 42.5Q2973.0 59 2947.0 59Q2929.0 59 2907.5 52.0Q2886.0 45 2868.0 32.5Q2850.0 20 2838.0 3.0Q2826.0 -14 2826.0 -35Q2826.0 -43 2825.5 -54.5Q2825.0 -66 2827.0 -84.5Q2829.0 -103 2835.0 -129.5Q2841.0 -156 2853.0 -195Q2820.0 -160 2775.5 -122.0Q2731.0 -84 2682.0 -50.0Q2633.0 -16 2583.5 11.5Q2534.0 39 2492.0 53Q2487.0 55 2484.0 55.0Q2481.0 55 2478.0 55Q2468.0 55 2451.5 45.0Q2435.0 35 2419.0 18.5Q2403.0 2 2391.5 -19.0Q2380.0 -40 2380.0 -61Q2380.0 -67 2386.0 -101.0Q2392.0 -135 2414.5 -187.5Q2437.0 -240 2481.0 -305.5Q2525.0 -371 2601.0 -440Q2640.0 -475 2685.0 -506Q2723.0 -532 2772.0 -559.0Q2821.0 -586 2875.0 -602Q2877.0 -602 2878.5 -603.0Q2880.0 -604 2883.0 -602Q2895.0 -598 2911.5 -589.5Q2928.0 -581 2944.5 -571.5Q2961.0 -562 2973.5 -552.5Q2986.0 -543 2990.0 -537Q2991.0 -535 2990.0 -534L2988.0 -532Q2943.0 -508 2903.0 -478.5Q2863.0 -449 2832.0 -423Q2796.0 -392 2765.0 -362Q2692.0 -290 2664.0 -237.5Q2636.0 -185 2636.0 -162Q2636.0 -145 2650.0 -145Q2664.0 -145 2685.5 -156.5Q2707.0 -168 2734.0 -187.5Q2761.0 -207 2793.5 -233.5Q2826.0 -260 2861.0 -289Q2892.0 -314 2921.0 -343Q2946.0 -367 2973.0 -398.0Q3000.0 -429 3019.0 -461Q3019.0 -462 3020.0 -463Q3021.0 -464 3023.0 -463Q3034.0 -455 3048.0 -436.5Q3062.0 -418 3074.0 -396.0Q3086.0 -374 3094.5 -353.5Q3103.0 -333 3103.0 -322Q3080.0 -275 3067.5 -236.5Q3055.0 -198 3048.5 -169.0Q3042.0 -140 3040.5 -119.5Q3039.0 -99 3039.0 -88ZM3232.0 -930Q3176.0 -861 3111.0 -799.0Q3046.0 -737 2980.0 -684Q2975.0 -687 2964.5 -695.0Q2954.0 -703 2943.0 -713.0Q2932.0 -723 2922.0 -731.5Q2912.0 -740 2908.0 -745Q2933.0 -776 2964.0 -818.0Q2995.0 -860 3024.0 -901.5Q3053.0 -943 3076.0 -977.5Q3099.0 -1012 3109.0 -1030Q3130.0 -1020 3149.5 -1004.5Q3169.0 -989 3185.5 -974.0Q3202.0 -959 3214.0 -946.5Q3226.0 -934 3232.0 -930ZM3420.0 55Q3403.0 55 3375.5 39.5Q3348.0 24 3322.0 1.5Q3296.0 -21 3277.0 -45.0Q3258.0 -69 3258.0 -86Q3258.0 -116 3274.5 -181.0Q3291.0 -246 3318.0 -329.5Q3345.0 -413 3379.5 -508.0Q3414.0 -603 3449.0 -694H3308.0H3307.0Q3306.0 -694 3306.0 -696Q3306.0 -702 3310.5 -713.5Q3315.0 -725 3321.5 -737.5Q3328.0 -750 3336.5 -760.5Q3345.0 -771 3353.0 -774L3483.0 -780Q3511.0 -848 3536.5 -905.5Q3562.0 -963 3582.0 -1004Q3587.0 -1015 3592.5 -1027.5Q3598.0 -1040 3608.5 -1052.5Q3619.0 -1065 3636.5 -1078.5Q3654.0 -1092 3684.0 -1106Q3706.0 -1117 3737.5 -1130.0Q3769.0 -1143 3799.5 -1154.0Q3830.0 -1165 3855.0 -1172.5Q3880.0 -1180 3891.0 -1180Q3901.0 -1180 3901.0 -1176V-1174Q3901.0 -1173 3899.0 -1169Q3893.0 -1159 3877.0 -1125.5Q3861.0 -1092 3837.5 -1041.5Q3814.0 -991 3785.5 -926.5Q3757.0 -862 3727.0 -790L3967.0 -799Q3977.0 -799 3973.0 -787.0Q3969.0 -775 3958.0 -759.0Q3947.0 -743 3932.5 -726.0Q3918.0 -709 3908.0 -700Q3906.0 -698 3900.0 -696.0Q3894.0 -694 3885.0 -694H3687.0Q3651.0 -607 3618.0 -518.0Q3585.0 -429 3559.0 -349.0Q3533.0 -269 3517.5 -202.0Q3502.0 -135 3502.0 -92Q3502.0 -86 3508.0 -86Q3519.0 -86 3541.0 -98.5Q3563.0 -111 3590.5 -131.0Q3618.0 -151 3649.0 -175.0Q3680.0 -199 3708.0 -222.5Q3736.0 -246 3758.5 -265.5Q3781.0 -285 3793.0 -295Q3802.0 -286 3814.5 -272.0Q3827.0 -258 3838.0 -246Q3816.0 -224 3783.0 -192.5Q3750.0 -161 3711.5 -128.0Q3673.0 -95 3631.5 -62.0Q3590.0 -29 3550.5 -3.0Q3511.0 23 3477.0 39.0Q3443.0 55 3420.0 55ZM5289.8 -893Q5319.8 -912 5353.3 -941.5Q5386.8 -971 5420.3 -1008.5Q5453.8 -1046 5484.8 -1089.5Q5515.8 -1133 5539.3 -1177.5Q5562.8 -1222 5576.8 -1266.5Q5590.8 -1311 5590.8 -1352Q5590.8 -1383 5585.8 -1402.0Q5580.8 -1421 5572.3 -1431.5Q5563.8 -1442 5552.8 -1446.0Q5541.8 -1450 5529.8 -1450Q5481.8 -1450 5420.8 -1409.5Q5359.8 -1369 5292.3 -1299.5Q5224.8 -1230 5154.3 -1137.0Q5083.8 -1044 5017.8 -937.5Q4951.8 -831 4893.3 -718.0Q4834.8 -605 4791.3 -495.0Q4747.8 -385 4722.3 -284.5Q4696.8 -184 4696.8 -104Q4696.8 -74 4701.3 -54.5Q4705.8 -35 4714.3 -23.5Q4722.8 -12 4735.8 -8.0Q4748.8 -4 4765.8 -4Q4794.8 -4 4834.3 -28.5Q4873.8 -53 4918.3 -92.0Q4962.8 -131 5008.3 -179.0Q5053.8 -227 5093.8 -273.5Q5133.8 -320 5165.8 -359.5Q5197.8 -399 5214.8 -422Q5214.8 -421 5239.3 -401.0Q5263.8 -381 5296.8 -353.0Q5329.8 -325 5362.8 -296.5Q5395.8 -268 5412.8 -250Q5388.8 -223 5351.8 -181.0Q5314.8 -139 5268.3 -92.5Q5221.8 -46 5168.8 1.0Q5115.8 48 5059.8 85.5Q5003.8 123 4946.8 146.5Q4889.8 170 4835.8 170Q4796.8 170 4752.8 146.5Q4708.8 123 4665.8 85.5Q4622.8 48 4585.8 2.5Q4548.8 -43 4523.8 -88Q4493.8 -144 4488.8 -191.0Q4483.8 -238 4483.8 -266Q4483.8 -343 4505.8 -440.0Q4527.8 -537 4568.8 -642.5Q4609.8 -748 4667.8 -857.0Q4725.8 -966 4796.3 -1068.0Q4866.8 -1170 4949.3 -1260.0Q5031.8 -1350 5122.3 -1417.0Q5212.8 -1484 5309.3 -1522.5Q5405.8 -1561 5504.8 -1561Q5532.8 -1561 5563.8 -1546.5Q5594.8 -1532 5624.8 -1508.5Q5654.8 -1485 5682.8 -1454.0Q5710.8 -1423 5731.8 -1390.5Q5752.8 -1358 5765.3 -1325.5Q5777.8 -1293 5777.8 -1266Q5777.8 -1206 5753.8 -1133.5Q5729.8 -1061 5688.8 -987.5Q5647.8 -914 5591.3 -844.0Q5534.8 -774 5470.8 -719ZM5641.8 55Q5626.8 55 5603.8 44.5Q5580.8 34 5559.8 18.5Q5538.8 3 5523.8 -16.0Q5508.8 -35 5508.8 -53Q5518.8 -97 5535.8 -154.0Q5552.8 -211 5572.3 -270.0Q5591.8 -329 5613.3 -385.5Q5634.8 -442 5652.3 -486.0Q5669.8 -530 5682.8 -557.0Q5695.8 -584 5699.8 -584Q5705.8 -584 5729.8 -571.0Q5753.8 -558 5780.3 -539.5Q5806.8 -521 5827.8 -500.5Q5848.8 -480 5848.8 -465Q5848.8 -462 5847.8 -457.5Q5846.8 -453 5844.8 -446Q5836.8 -421 5824.3 -387.0Q5811.8 -353 5797.8 -316.5Q5783.8 -280 5769.8 -243.5Q5755.8 -207 5744.8 -175.0Q5733.8 -143 5726.8 -119.0Q5719.8 -95 5719.8 -84Q5719.8 -78 5725.8 -78Q5736.8 -78 5758.8 -91.0Q5780.8 -104 5809.3 -125.0Q5837.8 -146 5868.8 -171.0Q5899.8 -196 5928.3 -220.5Q5956.8 -245 5979.8 -265.0Q6002.8 -285 6014.8 -295Q6023.8 -286 6036.3 -272.0Q6048.8 -258 6059.8 -246Q6037.8 -224 6004.8 -192.5Q5971.8 -161 5933.3 -128.0Q5894.8 -95 5853.3 -62.0Q5811.8 -29 5772.8 -3.0Q5733.8 23 5699.8 39.0Q5665.8 55 5641.8 55ZM6104.8 -930Q6048.8 -861 5983.8 -799.0Q5918.8 -737 5852.8 -684Q5847.8 -687 5837.3 -695.0Q5826.8 -703 5815.8 -713.0Q5804.8 -723 5794.8 -731.5Q5784.8 -740 5780.8 -745Q5805.8 -776 5836.8 -818.0Q5867.8 -860 5896.8 -901.5Q5925.8 -943 5948.8 -977.5Q5971.8 -1012 5981.8 -1030Q6002.8 -1020 6022.3 -1004.5Q6041.8 -989 6058.3 -974.0Q6074.8 -959 6086.8 -946.5Q6098.8 -934 6104.8 -930ZM6274.8 -483Q6262.8 -452 6250.8 -419.0Q6238.8 -386 6227.8 -352Q6290.8 -427 6332.3 -472.0Q6373.8 -517 6401.3 -542.0Q6428.8 -567 6446.8 -577.0Q6464.8 -587 6479.8 -592Q6488.8 -592 6510.3 -582.0Q6531.8 -572 6556.3 -557.5Q6580.8 -543 6602.8 -527.5Q6624.8 -512 6633.8 -502Q6641.8 -494 6635.8 -479Q6623.8 -447 6612.3 -414.5Q6600.8 -382 6589.8 -348Q6656.8 -425 6701.8 -471.5Q6746.8 -518 6775.3 -543.0Q6803.8 -568 6819.8 -576.0Q6835.8 -584 6844.8 -584Q6857.8 -584 6883.8 -571.0Q6909.8 -558 6936.3 -539.0Q6962.8 -520 6982.3 -500.0Q7001.8 -480 7001.8 -465Q7001.8 -462 7000.8 -457.5Q6999.8 -453 6997.8 -446Q6989.8 -421 6977.3 -387.0Q6964.8 -353 6950.8 -316.5Q6936.8 -280 6922.8 -243.5Q6908.8 -207 6897.8 -175.0Q6886.8 -143 6879.8 -119.0Q6872.8 -95 6872.8 -84Q6872.8 -78 6878.8 -78Q6889.8 -78 6911.8 -91.0Q6933.8 -104 6962.3 -125.0Q6990.8 -146 7021.8 -171.0Q7052.8 -196 7081.3 -220.5Q7109.8 -245 7132.8 -265.0Q7155.8 -285 7167.8 -295Q7176.8 -286 7189.3 -272.0Q7201.8 -258 7212.8 -246Q7190.8 -224 7157.8 -192.5Q7124.8 -161 7086.3 -128.0Q7047.8 -95 7006.3 -62.0Q6964.8 -29 6925.8 -3.0Q6886.8 23 6852.8 39.0Q6818.8 55 6794.8 55Q6779.8 55 6756.8 44.5Q6733.8 34 6712.8 18.5Q6691.8 3 6676.8 -16.0Q6661.8 -35 6661.8 -53Q6670.8 -94 6685.8 -146.0Q6700.8 -198 6718.8 -252.5Q6736.8 -307 6756.3 -360.0Q6775.8 -413 6793.8 -458Q6773.8 -434 6740.8 -400.0Q6707.8 -366 6665.8 -310.5Q6623.8 -255 6575.8 -173.0Q6527.8 -91 6479.8 27Q6473.8 47 6450.8 47Q6438.8 47 6421.3 42.5Q6403.8 38 6384.3 28.5Q6364.8 19 6344.3 5.0Q6323.8 -9 6305.8 -29Q6305.8 -36 6313.3 -71.5Q6320.8 -107 6335.3 -162.0Q6349.8 -217 6369.8 -285.0Q6389.8 -353 6414.8 -425Q6390.8 -399 6354.8 -358.5Q6318.8 -318 6277.8 -260.5Q6236.8 -203 6193.8 -127.0Q6150.8 -51 6112.8 45Q6106.8 66 6084.8 66Q6067.8 66 6046.3 56.5Q6024.8 47 6002.3 31.5Q5979.8 16 5957.3 -3.0Q5934.8 -22 5916.8 -41Q5916.8 -47 5922.3 -75.0Q5927.8 -103 5937.8 -145.5Q5947.8 -188 5962.3 -242.5Q5976.8 -297 5995.8 -357.0Q6014.8 -417 6037.8 -479.5Q6060.8 -542 6086.8 -600Q6128.8 -580 6180.3 -551.0Q6231.8 -522 6274.8 -483ZM7376.8 -70Q7392.8 -70 7420.3 -84.0Q7447.8 -98 7480.8 -119.5Q7513.8 -141 7549.8 -167.0Q7585.8 -193 7617.3 -218.0Q7648.8 -243 7673.8 -264.0Q7698.8 -285 7710.8 -295Q7719.8 -286 7732.3 -272.0Q7744.8 -258 7755.8 -246Q7733.8 -224 7700.8 -192.5Q7667.8 -161 7629.3 -128.0Q7590.8 -95 7549.3 -62.0Q7507.8 -29 7468.3 -3.0Q7428.8 23 7394.8 39.0Q7360.8 55 7337.8 55Q7306.8 55 7272.3 33.0Q7237.8 11 7207.8 -18.5Q7177.8 -48 7156.8 -78.0Q7135.8 -108 7132.8 -123Q7130.8 -129 7130.8 -137.5Q7130.8 -146 7130.8 -156Q7130.8 -206 7143.3 -282.5Q7155.8 -359 7176.8 -444.5Q7197.8 -530 7225.3 -614.5Q7252.8 -699 7282.8 -764Q7343.8 -897 7408.3 -1000.0Q7472.8 -1103 7534.8 -1179.5Q7596.8 -1256 7653.8 -1308.0Q7710.8 -1360 7756.3 -1392.0Q7801.8 -1424 7833.3 -1438.0Q7864.8 -1452 7876.8 -1452Q7895.8 -1445 7915.3 -1431.5Q7934.8 -1418 7951.8 -1403.0Q7968.8 -1388 7981.8 -1373.5Q7994.8 -1359 8000.8 -1352Q8008.8 -1344 8011.3 -1336.5Q8013.8 -1329 8013.8 -1317Q8013.8 -1293 8001.8 -1251.0Q7989.8 -1209 7956.8 -1144.5Q7923.8 -1080 7865.3 -990.0Q7806.8 -900 7712.8 -780Q7668.8 -724 7619.8 -666Q7577.8 -616 7522.8 -557.0Q7467.8 -498 7409.8 -444Q7380.8 -334 7369.3 -250.0Q7357.8 -166 7357.8 -119Q7357.8 -88 7362.8 -79.0Q7367.8 -70 7376.8 -70ZM7536.8 -803Q7519.8 -765 7505.3 -729.0Q7490.8 -693 7477.8 -658Q7508.8 -693 7534.3 -725.0Q7559.8 -757 7577.8 -780Q7598.8 -807 7615.8 -831Q7685.8 -929 7736.8 -1011.0Q7787.8 -1093 7820.8 -1155.5Q7853.8 -1218 7869.3 -1259.5Q7884.8 -1301 7884.8 -1319Q7884.8 -1324 7884.3 -1326.5Q7883.8 -1329 7878.8 -1329Q7869.8 -1329 7837.8 -1299.5Q7805.8 -1270 7759.3 -1206.0Q7712.8 -1142 7655.3 -1042.5Q7597.8 -943 7536.8 -803ZM8271.8 -88Q8271.8 -82 8277.8 -82Q8286.8 -82 8307.8 -95.0Q8328.8 -108 8355.8 -128.0Q8382.8 -148 8413.3 -173.0Q8443.8 -198 8471.8 -222.0Q8499.8 -246 8522.3 -266.0Q8544.8 -286 8556.8 -297Q8565.8 -288 8579.3 -273.0Q8592.8 -258 8603.8 -246Q8579.8 -224 8546.3 -192.5Q8512.8 -161 8474.3 -127.0Q8435.8 -93 8394.8 -59.5Q8353.8 -26 8314.8 0.0Q8275.8 26 8240.8 42.5Q8205.8 59 8179.8 59Q8161.8 59 8140.3 52.0Q8118.8 45 8100.8 32.5Q8082.8 20 8070.8 3.0Q8058.8 -14 8058.8 -35Q8058.8 -43 8058.3 -54.5Q8057.8 -66 8059.8 -84.5Q8061.8 -103 8067.8 -129.5Q8073.8 -156 8085.8 -195Q8052.8 -160 8008.3 -122.0Q7963.8 -84 7914.8 -50.0Q7865.8 -16 7816.3 11.5Q7766.8 39 7724.8 53Q7719.8 55 7716.8 55.0Q7713.8 55 7710.8 55Q7700.8 55 7684.3 45.0Q7667.8 35 7651.8 18.5Q7635.8 2 7624.3 -19.0Q7612.8 -40 7612.8 -61Q7612.8 -67 7618.8 -101.0Q7624.8 -135 7647.3 -187.5Q7669.8 -240 7713.8 -305.5Q7757.8 -371 7833.8 -440Q7872.8 -475 7917.8 -506Q7955.8 -532 8004.8 -559.0Q8053.8 -586 8107.8 -602Q8109.8 -602 8111.3 -603.0Q8112.8 -604 8115.8 -602Q8127.8 -598 8144.3 -589.5Q8160.8 -581 8177.299999999999 -571.5Q8193.8 -562 8206.3 -552.5Q8218.8 -543 8222.8 -537Q8223.8 -535 8222.8 -534L8220.8 -532Q8175.8 -508 8135.8 -478.5Q8095.8 -449 8064.8 -423Q8028.8 -392 7997.8 -362Q7924.8 -290 7896.8 -237.5Q7868.8 -185 7868.8 -162Q7868.8 -145 7882.8 -145Q7896.8 -145 7918.3 -156.5Q7939.8 -168 7966.8 -187.5Q7993.8 -207 8026.3 -233.5Q8058.8 -260 8093.8 -289Q8124.8 -314 8153.8 -343Q8178.8 -367 8205.8 -398.0Q8232.8 -429 8251.8 -461Q8251.8 -462 8252.8 -463Q8253.8 -464 8255.8 -463Q8266.8 -455 8280.8 -436.5Q8294.8 -418 8306.8 -396.0Q8318.8 -374 8327.3 -353.5Q8335.8 -333 8335.8 -322Q8312.8 -275 8300.3 -236.5Q8287.8 -198 8281.3 -169.0Q8274.8 -140 8273.3 -119.5Q8271.8 -99 8271.8 -88ZM8774.8 -369Q8828.8 -436 8873.8 -479.5Q8918.8 -523 8953.3 -548.5Q8987.8 -574 9011.8 -584.0Q9035.8 -594 9047.8 -594Q9063.8 -594 9082.8 -582.0Q9101.8 -570 9120.3 -553.0Q9138.8 -536 9154.8 -518.0Q9170.8 -500 9181.8 -489Q9192.8 -478 9195.3 -465.5Q9197.8 -453 9197.8 -440Q9197.8 -393 9181.8 -337.5Q9165.8 -282 9139.8 -227.5Q9113.8 -173 9080.8 -124.0Q9047.8 -75 9013.8 -41Q9042.8 -53 9073.3 -70.5Q9103.8 -88 9133.8 -108.0Q9163.8 -128 9192.8 -149.5Q9221.8 -171 9247.8 -192Q9308.8 -240 9365.8 -295L9410.8 -246Q9377.8 -215 9337.3 -180.0Q9296.8 -145 9252.8 -111.0Q9208.8 -77 9163.3 -46.0Q9117.8 -15 9075.8 9.0Q9033.8 33 8997.8 47.0Q8961.8 61 8935.8 61Q8917.8 61 8890.8 50.5Q8863.8 40 8838.3 25.0Q8812.8 10 8794.3 -7.5Q8775.8 -25 8775.8 -39Q8775.8 -47 8786.3 -53.5Q8796.8 -60 8816.8 -77.0Q8836.8 -94 8864.8 -128.0Q8892.8 -162 8927.8 -227Q8946.8 -263 8963.8 -301.5Q8980.8 -340 8993.8 -373.5Q9006.8 -407 9014.3 -431.5Q9021.8 -456 9021.8 -465Q9021.8 -474 9019.3 -477.5Q9016.8 -481 9014.8 -483Q9010.8 -484 9007.8 -483Q8993.8 -479 8951.3 -438.5Q8908.8 -398 8851.8 -322.5Q8794.8 -247 8730.8 -138.5Q8666.8 -30 8609.8 109Q8562.8 243 8515.8 380.0Q8468.8 517 8423.8 653L8169.799999999999 713Q8227.8 548 8290.3 385.5Q8352.8 223 8414.3 60.5Q8475.8 -102 8535.3 -265.0Q8594.8 -428 8648.8 -592Q8651.8 -603 8654.3 -607.5Q8656.8 -612 8664.8 -612Q8680.8 -607 8703.8 -593.5Q8726.8 -580 8749.3 -562.5Q8771.8 -545 8789.8 -527.0Q8807.8 -509 8814.8 -496Q8816.8 -493 8814.3 -485.5Q8811.8 -478 8808.8 -469Z"/><path fill="currentColor" d="M 60 330 C 2300 520, 6300 470, 9450 -10 C 6400 690, 2300 790, 60 330 Z"/></svg>'''


# Böngészőfül- és kezdőképernyő-ikon (az „S” a Yesteryear betűtípusból)
IKON_LINKEK = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Cpath fill=%22%23f2f5f9%22 stroke=%22%23f2f5f9%22 stroke-width=%221.6%22 stroke-linejoin=%22round%22 d=%22M52.57 14.05Q52.60 14.11 52.68 14.31Q52.76 14.52 52.85 14.78Q52.93 15.05 53.00 15.31Q53.07 15.57 53.07 15.68Q53.07 16.62 52.68 17.94Q52.29 19.25 51.62 20.71Q50.94 22.18 50.03 23.70Q49.11 25.22 48.09 26.59Q47.07 27.96 45.98 29.08Q44.88 30.20 43.83 30.86Q43.45 30.50 42.91 30.00Q42.37 29.51 41.76 28.94Q41.15 28.37 40.54 27.79Q39.94 27.21 39.41 26.66Q40.21 26.02 41.22 24.99Q42.23 23.95 43.27 22.68Q44.30 21.41 45.30 20.05Q46.29 18.70 47.03 17.44Q47.76 16.18 48.17 15.16Q48.59 14.13 48.48 13.52Q48.40 13.22 48.22 13.10Q48.04 12.97 47.57 12.97Q45.88 12.97 43.67 13.64Q41.46 14.30 39.05 15.56Q36.64 16.82 34.20 18.64Q31.75 20.47 29.61 22.76Q27.47 25.05 25.79 27.79Q24.12 30.53 23.23 33.63Q25.17 33.60 26.91 33.47Q28.65 33.35 30.23 33.21Q31.81 33.07 33.24 32.96Q34.68 32.85 36.04 32.85Q36.76 32.85 37.58 33.63Q38.41 34.40 39.13 35.48Q39.85 36.56 40.32 37.69Q40.79 38.82 40.79 39.52Q40.79 41.48 39.78 43.46Q38.77 45.43 37.07 47.27Q35.37 49.11 33.13 50.70Q30.89 52.29 28.42 53.47Q25.94 54.64 23.37 55.32Q20.80 56.0 18.48 56.0Q18.17 55.75 17.68 55.23Q17.18 54.72 16.61 54.10Q16.05 53.48 15.45 52.79Q14.86 52.10 14.35 51.46Q13.83 50.82 13.45 50.30Q13.06 49.77 12.92 49.50Q14.44 49.72 16.58 49.61Q18.73 49.50 21.11 49.00Q23.48 48.50 25.90 47.58Q28.32 46.65 30.42 45.24Q32.52 43.83 34.13 41.88Q35.73 39.93 36.45 37.36Q34.85 37.39 33.55 37.51Q32.25 37.64 31.03 37.79Q29.82 37.94 28.54 38.05Q27.27 38.16 25.72 38.16Q25.23 38.16 24.64 38.15Q24.06 38.13 23.48 38.06Q22.90 38.0 22.38 37.83Q21.85 37.66 21.52 37.36Q20.77 36.72 20.08 36.02Q19.39 35.31 18.84 34.58Q18.29 33.85 17.95 33.10Q17.62 32.35 17.62 31.66Q17.62 29.56 18.67 26.98Q19.72 24.39 21.59 21.76Q23.46 19.14 26.00 16.66Q28.54 14.19 31.54 12.25Q34.54 10.32 37.86 9.16Q41.18 8.0 44.58 8.0Q45.77 8.0 46.75 8.35Q47.73 8.71 48.53 9.29Q49.34 9.88 49.97 10.58Q50.61 11.29 51.11 11.95Q51.60 12.61 51.96 13.18Q52.32 13.75 52.57 14.05Z%22/%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,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">'
    '<link rel="apple-touch-icon" href="data:image/png;base64,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">'
    '<meta name="theme-color" content="#16202f"><meta name="apple-mobile-web-app-title" content="Saját Címlap">'
)


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
