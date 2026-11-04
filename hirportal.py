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
# vagy rögzítve: "hirlap" (Pesti Hírlap-szerű), "nemzeti" (reformkori, a Nemzeti Dal címe nyomán), "got" (gót betűs, a Le Monde nyomán)
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
    "got":    {"csalad": "Source Serif 4", "url": "Source+Serif+4:opsz,wght@8..60,400..800", "suly": 650, "nagy": 700, "ter": "-.005em", "tartalek": "Georgia,'Times New Roman',serif"},
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
     "alap": "belfold", "alap_elony": 1},
    {"id": "valasz", "nev": "Válasz Online", "szin": "#a16207", "feeds": ["https://www.valaszonline.hu/feed/"]},
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

FO_HIREK_SZAMA = 11       # ennyi kerül a lap tetejére (1 nyitó + 4 képes + 6 kép nélküli)
FO_MAX_ORA = 8            # a kiemelt részbe ennél frissebb hír kerülhet (a hír legutóbbi cikke számít)
NYITO_MAX_ORA = 3         # a nyitóhír ennél frissebb legyen; ha nincs ilyen, fokozatosan enged (5, 8, 12 óra)
FO_TARTALEK_ORA = 12      # ha (pl. hajnalban) nincs elég 8 óránál frissebb hír, legfeljebb eddig nyúl vissza
FO_STILUS = "arnyalt"     # a kiemelt rész háttere: "arnyalt" (a fejléc színének halvány árnyalata), "vilagos", "sotet"
FRISS_ORA = 24            # ennél frissebb hír dobozban, régebbi listában jelenik meg
HELYI_LISTA = 8           # a helyi oszlopokban a kiemeltek alatt ennyi friss hír látszik; a többi a napokra bontott listában
MEGORZES_ORA = 72         # ennél régebbi hír kiesik
DOBOZ_ROVATONKENT = 8     # rovatonként legfeljebb ennyi doboz (az első kiemelt, dupla széles), a többi listába megy
# Ugyanarról szóló cikkek összevonása (betű-n-gramos hasonlóság, 0–1 között)
OSSZEVON_ABLAK_ORA = 12   # csak az ennyi órán belül megjelent cikkek vethetők össze
OSSZEVON_EROS = 0.30      # legalább egy csoporttaghoz ennyi hasonlóság kell (kisebb: többet von össze)
OSSZEVON_ATLAG = 0.18     # és a csoport közeli tagjaihoz átlagosan ennyi (a láncolódás ellen)
KIEMELT_ORA = 4           # a rovat kiemelt dobozánál ennyi óránként ér egy lappal kevesebbet egy téma
ROVAT_VALTOZATOS = True   # a rovatok kiemelt kártyája óránként sorsolt elrendezést kap (széles/magas, bal/jobb)
SZINES_ESELY = 0.35       # ekkora eséllyel színes (a rovat színében) a kiemelt kártya; 0 = soha
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
                     "izrael", "gáza", "kína", "német", "nato", "szlovák", "román", "lengyel", "francia",
                     "egyesült államok", "washington", "fehér ház", "kongresszus", "szenátus", "republikánus",
                     "demokrata párt", "kreml", "moszkva", "kijev", "zelenszkij", "irán", "brit", "olasz", "spanyol",
                     "török", "osztrák", "szerb", "horvát", "ensz", "európai bizottság", "von der leyen",
                     "pozsony", "bécs", "prága", "varsó", "bukarest", "belgrád", "zágráb", "berlin", "párizs", "london",
                     "fico", "vučić", "vucic", "netanjahu", "macron", "merz", "meloni", "starmer", "erdoğan", "erdogan"]},
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
    {"id": "egyeb", "nev": "Egyéb", "szin": "#64748b",
     "utvonal": ["kozlemeny", "promo", "x-promo", "szponzoralt", "partner", "hirdetes", "pr"], "kulcsszavak": []},
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
            "spanyol", "török", "osztrák", "szerb", "horvát"}


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
    eredeti = h["cim"] + ". " + h["lead"]
    szoveg_ = eredeti.lower()
    pontok = {r["id"]: sum(kulcsszo_talalat(szoveg_, k, eredeti) for k in r["kulcsszavak"]) for r in ROVATOK}
    if sum(pontok.values()) > 0:
        # lapfüggő előny: pl. az Euronews cikkei a Külföld javára indulnak, hogy egy-egy belföldes szó
        # (pl. „választás”) ne vigye el őket; csak egyértelmű belpolitikai cikk kerül máshova
        if forras.get("alap") in pontok:
            pontok[forras["alap"]] += forras.get("alap_elony", 0)
        legjobb = max(pontok, key=pontok.get)
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
        jeloltek = {hova[j] for j, s in acc.items() if s >= OSSZEVON_EROS}
        legjobb, legjobb_atl = None, 0
        for k in jeloltek:
            kozeli = [j for j in csoportok[k]["idx"] if T[i] - T[j] <= ablak]
            atl = sum(acc.get(j, 0) for j in kozeli) / len(kozeli)
            if atl >= OSSZEVON_ATLAG and atl > legjobb_atl:
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
  <div class="meta">{forras_nev(h["forras"])}<time>{ido_szoveg(h["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3>
  {lead}{lefedettseg(cs)}{tovabbi(cs) if tipus != "kis" else ""}</div>
</article>'''


def doboz(cs, lead=True, kiemelt=False, extra=""):
    h = cs["fo"]
    kep = kep_html(h, "kiemelt-kep") if kiemelt else kep_html(h, "belyeg")
    return f'''<article class="doboz{' kiemelt' if kiemelt else ''}{extra}" data-srcs="{' '.join(cs['forrasok'])}">
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
    return (f'<li data-srcs="{" ".join(cs["forrasok"])}"><time>{ido}</time>'
            f'<span class="sor-cim"><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a> '
            f'{forras_nev(h["forras"])}</span></li>')


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


def oldal(orszagos, helyi_cs, hibak, idojaras=None, idokep=None):
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
            dobozok = [c for c in cs if c["kor_ora"] < FRISS_ORA][:DOBOZ_ROVATONKENT]
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
                # annyi doboz, hogy a három oszlopos rács tele legyen (2, 5 vagy 8); a többi a listába kerül
                teli = max(x for x in (2, 5, 8) if x <= len(dobozok))
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
.tema-gomb{width:2.1rem;height:2.1rem;border-radius:5px;display:grid;place-items:center;cursor:pointer;color:#fff;background:rgb(255 255 255/.12);border:1px solid rgb(255 255 255/.35);padding:0}
.tema-gomb:hover{background:rgb(255 255 255/.2)}
.tema-gomb svg{width:1.1rem;height:1.1rem;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;display:none}
:root:not([data-tema]) .t-auto,:root[data-tema="vilagos"] .t-vilagos,:root[data-tema="sotet"] .t-sotet{display:block}
.logo-v{display:block}.logo-v[hidden]{display:none}
.logo{height:4.6rem;width:auto;display:block}
.logo-hirlap{height:3.5rem}
.logo-got{height:4.4rem}
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
.nap{font:650 1.05rem/1.2 var(--cimbetu)}
.frissitve{color:var(--halvany);font-size:.85rem}
/* menüsáv: a fejléc színének sötétebb árnyalata (kétszínű, újságos fejléc) */
.rovatnav{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--fejszin,#16202f) 68%,#000);box-shadow:0 1px 0 rgb(0 0 0/.25)}
.nav-belso{max-width:1280px;margin:0 auto;padding:.55rem 1.5rem;display:flex;align-items:center;gap:.4rem;overflow-x:auto;scrollbar-width:none}
/* görgetéskor a menüsáv elején megjelenik a lap neve kicsiben (az éppen kisorsolt fejléc stílusában) */
.nav-logo{display:flex;align-items:center;flex-shrink:0;color:#fff;max-width:0;opacity:0;overflow:hidden;margin-right:0;
  transition:max-width .3s ease,opacity .25s ease,margin .3s ease;line-height:0}
.nav-logo svg{height:1.45rem;width:auto;display:block}
.nav-logo .logo-got{height:1.75rem}
.fej-elrejtve .nav-logo{max-width:14rem;opacity:1;margin-right:.7rem}
@media (prefers-reduced-motion:reduce){.nav-logo{transition:none}}
/* rovatgombok: enyhén lekerekített sarok, mint a Források gombon, elöl a rovat színes pöttyével */
.nav-belso a:not(.nav-logo){flex-shrink:0;font-size:.86rem;font-weight:600;padding:.22rem .7rem;border-radius:5px;color:#fff;background:rgb(255 255 255/.1);border:1px solid rgb(255 255 255/.24);display:flex;align-items:center;gap:.45rem;transition:background .15s}
.nav-belso a:not(.nav-logo)::before{content:"";width:.5rem;height:.5rem;border-radius:50%;background:var(--r);box-shadow:0 0 0 1.5px rgb(255 255 255/.9)}
.nav-belso a:not(.nav-logo):hover{background:rgb(255 255 255/.2);border-color:rgb(255 255 255/.45)}
.nav-belso a:not(.nav-logo):focus-visible{outline-color:#fff}

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
  .racs{grid-template-columns:repeat(2,minmax(0,1fr))}
  .elr-bal-szeles .doboz.kiemelt,.elr-jobb-szeles .doboz.kiemelt{grid-column:1/-1;grid-row:auto}
  .elr-bal-magas .doboz.kiemelt{grid-column:1;grid-row:1/span 2}
  .elr-jobb-magas .doboz.kiemelt{grid-column:2;grid-row:1/span 2}}
@media (max-width:640px){
  .fejsor{padding:.9rem 1rem .8rem}
  .logo{height:3.4rem}
  .logo-got{width:100%;max-width:19rem;height:auto}
  .nap{font-size:.95rem}
  .forrasmenu{padding:0}
  .nav-belso,main,.elso-belso{padding-left:1rem;padding-right:1rem}
  .fo-racs{grid-template-columns:1fr}.nyito,.masod,.kis{grid-column:auto}
  .nyito .szoveg{padding:1.1rem}
  .belyeg{width:64px;height:64px}
  .racs{grid-template-columns:1fr}.racs .doboz.kiemelt{grid-column:auto;grid-row:auto;flex-direction:column}
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


# Gót betűs fejléc a Le Monde nyomán, balra lent eltolt árnyékkontúrral (UnifrakturCook betűtípus, SIL Open Font License; az „ő” kettős ékezete saját rajz)
LOGO_GOT = '''<svg class="logo logo-got" xmlns="http://www.w3.org/2000/svg" viewBox="-117 -1638 9202 2159" role="img" aria-label="A Kezdőlap"><path class="arnyek" fill="#ffffff" fill-opacity=".42" fill-rule="evenodd" d="M5876 -1151 L5911 -1188 L5873 -1188 L5871 -1188 L5868 -1188 L5866 -1189 L5864 -1190 L5862 -1191 L5860 -1192 L5858 -1193 L5856 -1195 L5854 -1197 L5853 -1198 L5852 -1201 L5851 -1203 L5850 -1205 L5849 -1207 L5849 -1210 L5849 -1212 L5849 -1214 L5849 -1217 L5849 -1219 L5850 -1221 L5851 -1223 L5997 -1515 L5983 -1515 L5801 -1151ZM2009 -1156 L2028 -1158 L2046 -1160 L2062 -1162 L2076 -1163 L2089 -1164 L2099 -1164 L2108 -1165 L2114 -1165 L2119 -1165 L2124 -1164 L2128 -1164 L2133 -1164 L2137 -1164 L2141 -1164 L2145 -1164 L2149 -1164 L2153 -1164 L2156 -1164 L2159 -1164 L2159 -1162 L2159 -1161 L2159 -1160 L2159 -1158 L2159 -1157 L2159 -1156 L2159 -1154 L2159 -1153 L2159 -1152 L2159 -1150 L2159 -1149 L2159 -1148 L2159 -1095 L2158 -1042 L2157 -990 L2155 -938 L2152 -886 L2149 -834 L2146 -783 L2142 -732 L2137 -681 L2132 -630 L2126 -579 L2120 -529 L2112 -480 L2102 -432 L2090 -386 L2075 -342 L2059 -299 L2039 -258 L2018 -220 L2021 -223 L2046 -257 L2069 -293 L2089 -331 L2108 -370 L2124 -412 L2138 -454 L2150 -499 L2160 -546 L2167 -594 L2173 -644 L2179 -694 L2184 -745 L2189 -795 L2193 -846 L2197 -897 L2200 -949 L2202 -1000 L2204 -1052 L2205 -1104 L2206 -1157 L2206 -1201 L2204 -1201 L2200 -1201 L2195 -1201 L2190 -1201 L2185 -1201 L2180 -1201 L2172 -1201 L2162 -1201 L2150 -1200 L2136 -1199 L2120 -1197 L2103 -1195 L2083 -1193 L2062 -1190 L2040 -1187 L2015 -1184 L1989 -1180 L1961 -1176 L1958 -1175 L1956 -1175 L1953 -1176 L1951 -1176 L1949 -1177 L1946 -1178 L1944 -1179 L1942 -1180 L1940 -1182 L1939 -1184 L1937 -1186 L1936 -1188 L1935 -1190 L1934 -1192 L1933 -1194 L1933 -1197 L1932 -1199 L1932 -1202 L1933 -1204 L1933 -1207 L1937 -1219 L1941 -1235 L1946 -1253 L1952 -1274 L1958 -1298 L1965 -1324 L1973 -1353 L1981 -1385 L1990 -1420 L2000 -1458 L2004 -1471 L1998 -1472 L1974 -1474 L1963 -1430 L1952 -1390 L1943 -1352 L1933 -1318 L1925 -1286 L1917 -1256 L1910 -1230 L1904 -1206 L1898 -1185 L1893 -1167 L1889 -1151 L1885 -1139 L1913 -1143 L1940 -1147 L1965 -1150 L1987 -1153ZM1910 -80 L1877 -50 L1929 -47 L1979 -43 L2028 -39 L2076 -34 L2122 -28 L2167 -22 L2211 -15 L2253 -7 L2295 1 L2334 10 L2373 20 L2410 30 L2437 -18 L2400 -27 L2361 -36 L2320 -44 L2278 -52 L2235 -59 L2190 -65 L2145 -71 L2097 -76 L2049 -80 L1999 -83 L1948 -86 L1945 -87 L1943 -87 L1941 -88 L1938 -89 L1936 -90 L1934 -91 L1932 -93 L1931 -95 L1929 -96 L1928 -98 L1928 -99ZM2612 -664 L2638 -613 L2664 -561 L2690 -508 L2716 -453 L2741 -397 L2767 -340 L2793 -282 L2819 -222 L2845 -161 L2871 -98 L2896 -35 L2922 30 L2940 20 L2959 8 L2980 -4 L2986 -8 L2984 -9 L2982 -10 L2980 -11 L2978 -12 L2976 -14 L2975 -16 L2973 -18 L2972 -20 L2971 -22 L2945 -87 L2920 -150 L2894 -213 L2868 -274 L2842 -333 L2817 -391 L2791 -448 L2765 -504 L2739 -559 L2714 -612 L2688 -663 L2673 -693 L2553 -635 L2553 -635ZM3244 -963 L3244 -963 L2995 -845 L2995 -845ZM2858 -1474 L2842 -1448 L2825 -1423 L2808 -1397 L2792 -1372 L2775 -1347 L2760 -1324 L2760 -1325 L2776 -1349 L2793 -1373 L2810 -1398 L2826 -1422 L2843 -1447 L2859 -1472ZM2764 -1034 L2773 -1020 L2783 -1006 L2792 -994 L2802 -981 L2812 -970 L2823 -959 L2826 -956 L2855 -974 L2885 -992 L2886 -994 L2877 -1003 L2877 -1003 L2866 -1014 L2866 -1015 L2855 -1027 L2855 -1027 L2845 -1039 L2844 -1040 L2835 -1053 L2834 -1054 L2825 -1067 L2824 -1068 L2815 -1082 L2815 -1082 L2806 -1097 L2805 -1098 L2759 -1041ZM4922 -828 L4883 -811 L4884 -785 L4884 -760 L4884 -735 L4885 -709 L4885 -684 L4885 -659 L4886 -633 L4886 -608 L4886 -583 L4887 -557 L4887 -532 L4887 -507 L4888 -481 L4888 -475 L4894 -456 L4894 -455 L4900 -436 L4900 -435 L4905 -416 L4905 -415 L4910 -396 L4910 -395 L4913 -376 L4913 -375 L4916 -356 L4916 -355 L4918 -336 L4918 -335 L4919 -316 L4919 -315 L4920 -296 L4920 -295 L4918 -254 L4917 -252 L4913 -212 L4912 -210 L4909 -190 L4909 -190 L4924 -176 L4940 -164 L4957 -151 L4973 -139 L4990 -128 L5007 -117 L5024 -106 L5041 -96 L5059 -87 L5077 -78 L5095 -69 L5113 -61 L5131 -52 L5150 -44 L5168 -36 L5186 -27 L5204 -19 L5223 -11 L5241 -2 L5259 6 L5278 14 L5296 22 L5314 30 L5324 11 L5334 -8 L5342 -24 L5339 -25 L5339 -25 L5321 -33 L5321 -33 L5303 -41 L5303 -41 L5284 -50 L5284 -50 L5266 -58 L5266 -58 L5248 -66 L5248 -66 L5229 -75 L5229 -75 L5211 -83 L5211 -83 L5193 -91 L5193 -91 L5174 -100 L5174 -100 L5156 -108 L5156 -108 L5138 -117 L5137 -117 L5119 -126 L5119 -127 L5101 -136 L5101 -137 L5083 -147 L5083 -147 L5065 -157 L5065 -158 L5048 -169 L5047 -169 L5031 -181 L5030 -181 L5014 -193 L5013 -193 L4997 -206 L4997 -206 L4981 -219 L4980 -219 L4965 -232 L4964 -233 L4949 -247 L4947 -248 L4945 -250 L4944 -252 L4943 -255 L4942 -257 L4941 -259 L4941 -262 L4940 -264 L4940 -290 L4940 -290 L4939 -315 L4939 -315 L4939 -340 L4939 -340 L4938 -365 L4938 -365 L4937 -390 L4937 -391 L4937 -416 L4937 -416 L4936 -441 L4936 -441 L4936 -466 L4936 -466 L4936 -492 L4936 -492 L4935 -517 L4935 -517 L4935 -542 L4935 -542 L4934 -568 L4934 -618 L4933 -644 L4933 -669 L4933 -694 L4932 -720 L4932 -745 L4932 -770 L4931 -821 L4931 -832ZM5003 -1240 L5024 -1215 L5044 -1192 L5063 -1168 L5082 -1144 L5100 -1121 L5117 -1098 L5134 -1075 L5150 -1052 L5165 -1030 L5179 -1008 L5181 -1005 L5195 -1011 L5216 -1021 L5235 -1031 L5243 -1035 L5230 -1056 L5216 -1078 L5201 -1100 L5185 -1122 L5169 -1145 L5152 -1168 L5134 -1191 L5116 -1214 L5097 -1237 L5077 -1261 L5067 -1273ZM5279 -834 L5302 -783 L5322 -731 L5341 -679 L5358 -626 L5372 -573 L5384 -520 L5394 -466 L5401 -411 L5407 -357 L5409 -327 L5422 -323 L5443 -317 L5458 -314 L5457 -362 L5454 -416 L5449 -470 L5441 -523 L5431 -576 L5420 -629 L5406 -681 L5389 -733 L5371 -784 L5351 -834 L5328 -885 L5314 -912 L5305 -906 L5292 -896 L5280 -885 L5268 -872 L5263 -865ZM225 -919 L184 -899 L196 -874 L207 -849 L216 -824 L225 -799 L232 -773 L239 -747 L245 -721 L249 -695 L253 -668 L255 -641 L257 -614 L257 -586 L257 -558 L256 -530 L255 -502 L253 -474 L250 -446 L247 -418 L243 -389 L239 -361 L234 -332 L229 -304 L223 -275 L216 -246 L209 -217 L202 -189 L195 -160 L187 -132 L179 -105 L171 -77 L163 -50 L154 -23 L146 4 L137 31 L127 57 L118 83 L108 109 L97 134 L85 159 L73 184 L60 207 L53 218 L67 200 L82 178 L97 156 L110 134 L123 111 L135 87 L146 63 L157 38 L166 13 L176 -12 L185 -38 L194 -65 L203 -91 L211 -118 L219 -145 L227 -173 L235 -200 L243 -228 L250 -256 L257 -285 L264 -313 L271 -342 L276 -370 L282 -398 L287 -426 L291 -454 L294 -482 L297 -510 L300 -538 L302 -565 L303 -593 L304 -620 L304 -647 L304 -674 L302 -701 L300 -727 L297 -753 L292 -778 L287 -803 L280 -828 L273 -853 L265 -877 L256 -901 L245 -925 L244 -929ZM-19 319 L-37 340 L-56 360 L-50 364 L-44 367 L-38 371 L-32 375 L-26 379 L-20 383 L-13 387 L-7 392 L-0 396 L7 401 L13 406 L20 410 L44 387 L68 362 L64 359 L58 355 L51 350 L45 346 L38 342 L32 338 L26 334 L20 330 L15 327 L9 323 L3 320 L1 319 L-0 317 L-2 316 L-4 314 L-5 312 L-6 309 L-7 307 L-8 306ZM932 -575 L932 -533 L932 -493 L931 -454 L930 -416 L929 -380 L927 -345 L925 -311 L923 -279 L920 -248 L917 -219 L914 -190 L910 -164 L1139 30 L1180 -25 L966 -206 L964 -208 L963 -210 L961 -212 L960 -214 L959 -216 L958 -218 L958 -221 L957 -223 L957 -226 L958 -228 L961 -255 L964 -283 L967 -312 L970 -342 L972 -374 L974 -408 L976 -442 L977 -478 L978 -516 L979 -554 L979 -595 L979 -611 L652 -611 L647 -575 L647 -575ZM29 -1171 L-15 -1136 L-15 -1134 L-15 -1131 L-15 -1129 L-15 -1125 L-15 -1122 L-14 -1117 L-14 -1113 L-13 -1108 L-12 -1102 L-11 -1096 L-10 -1090 L-9 -1083 L-8 -1076 L-7 -1070 L-6 -1064 L-5 -1058 L-4 -1053 L-4 -1049 L-3 -1045 L-3 -1041 L-3 -1038 L-3 -1035 L-3 -1032 L-3 -1031 L30 -1056 L54 -1073 L53 -1074 L51 -1075 L49 -1077 L48 -1079 L47 -1081 L46 -1084 L45 -1086 L45 -1089 L44 -1091 L44 -1093 L45 -1095 L45 -1096 L44 -1098 L44 -1100 L44 -1103 L43 -1107 L43 -1111 L42 -1116 L41 -1121 L41 -1127 L39 -1134 L38 -1140 L37 -1147 L37 -1147 L36 -1154 L36 -1154 L35 -1160 L35 -1160 L34 -1166 L34 -1166 L34 -1171 L33 -1171 L33 -1174ZM202 -1151 L238 -1163 L275 -1173 L312 -1181 L350 -1186 L388 -1189 L427 -1191 L443 -1190 L459 -1190 L476 -1189 L492 -1188 L508 -1186 L525 -1184 L541 -1182 L558 -1179 L575 -1176 L580 -1175 L582 -1175 L616 -1199 L638 -1214 L625 -1216 L609 -1219 L593 -1221 L577 -1223 L562 -1225 L546 -1226 L530 -1227 L514 -1227 L499 -1227 L461 -1226 L424 -1223 L388 -1218 L352 -1210 L317 -1201 L282 -1189 L248 -1175 L214 -1159 L190 -1146ZM755 -1118 L773 -1109 L791 -1101 L808 -1092 L826 -1082 L843 -1073 L860 -1062 L877 -1052 L894 -1041 L911 -1030 L927 -1019 L927 -1013 L927 -1007 L927 -1002 L927 -996 L927 -991 L927 -985 L926 -980 L926 -975 L926 -970 L926 -965 L926 -961 L926 -956 L926 -951 L926 -944 L926 -937 L927 -928 L927 -917 L927 -906 L927 -896 L975 -896 L975 -908 L975 -924 L974 -940 L974 -954 L974 -967 L974 -978 L974 -978 L974 -989 L974 -989 L974 -998 L974 -998 L973 -1006 L973 -1006 L973 -1012 L973 -1012 L973 -1017 L973 -1018 L973 -1022 L973 -1022 L973 -1027 L973 -1027 L973 -1032 L973 -1032 L974 -1037 L974 -1037 L974 -1042 L974 -1042 L974 -1047 L974 -1047 L974 -1053 L974 -1053 L974 -1058 L974 -1058 L974 -1063 L974 -1064 L974 -1067 L969 -1071 L952 -1082 L936 -1093 L919 -1103 L902 -1113 L886 -1122 L869 -1131 L851 -1140 L834 -1149 L817 -1157 L799 -1165 L795 -1167 L784 -1158 L774 -1149 L765 -1140 L756 -1131 L747 -1122 L747 -1122ZM3316 -813 L3321 -783 L3326 -754 L3330 -725 L3334 -696 L3337 -668 L3340 -639 L3342 -611 L3344 -583 L3345 -555 L3346 -528 L3347 -500 L3347 -473 L3347 -446 L3347 -419 L3346 -393 L3344 -366 L3343 -340 L3341 -314 L3338 -288 L3335 -262 L3333 -241 L3335 -236 L3337 -234 L3337 -232 L3338 -229 L3338 -227 L3339 -225 L3339 -222 L3338 -220 L3338 -218 L3337 -215 L3336 -213 L3335 -211 L3334 -209 L3332 -207 L3330 -206 L3329 -204 L3327 -203 L3325 -186 L3320 -161 L3799 30 L3808 18 L3817 6 L3825 -6 L3834 -18 L3835 -19 L3383 -199 L3381 -200 L3379 -201 L3377 -203 L3375 -204 L3373 -206 L3372 -208 L3371 -210 L3369 -212 L3369 -214 L3368 -217 L3368 -219 L3367 -221 L3367 -224 L3368 -226 L3372 -251 L3376 -276 L3379 -301 L3383 -326 L3385 -352 L3388 -377 L3390 -403 L3391 -429 L3393 -455 L3394 -481 L3394 -508 L3394 -534 L3394 -561 L3394 -588 L3393 -616 L3391 -643 L3389 -671 L3387 -698 L3384 -726 L3381 -755 L3377 -783 L3373 -812 L3369 -841 L3368 -843ZM3685 -730 L3694 -724 L3703 -718 L3713 -710 L3723 -702 L3733 -694 L3736 -690 L3739 -687 L3742 -684 L3745 -680 L3748 -677 L3751 -673 L3754 -669 L3757 -665 L3760 -661 L3763 -657 L3766 -652 L3769 -648 L3772 -643 L3774 -639 L3776 -634 L3779 -630 L3780 -626 L3782 -622 L3783 -618 L3784 -615 L3785 -611 L3786 -608 L3786 -605 L3823 -644 L3825 -646 L3827 -648 L3828 -650 L3830 -652 L3831 -654 L3832 -655 L3832 -657 L3833 -658 L3833 -660 L3833 -661 L3833 -662 L3833 -663 L3833 -664 L3833 -666 L3833 -668 L3832 -670 L3832 -672 L3831 -675 L3829 -678 L3828 -681 L3826 -684 L3824 -688 L3822 -692 L3820 -696 L3817 -700 L3814 -704 L3812 -708 L3809 -712 L3806 -716 L3803 -719 L3801 -723 L3798 -726 L3795 -729 L3793 -732 L3790 -735 L3788 -737 L3779 -745 L3770 -752 L3761 -759 L3752 -765 L3744 -770 L3737 -775 L3730 -778 L3723 -781 L3718 -783 L3713 -784 L3709 -785 L3706 -785 L3702 -785 L3699 -784 L3696 -782 L3693 -778 L3689 -771 L3685 -762 L3681 -750 L3678 -735 L3677 -734ZM4228 -189 L4209 -160 L4191 -131 L4176 -101 L4162 -71 L4155 -50 L4148 -30 L4141 -8 L4136 13 L4131 36 L4126 59 L4123 83 L4120 107 L4117 132 L4116 157 L4115 183 L4114 209 L4115 231 L4116 251 L4119 269 L4122 286 L4126 302 L4131 315 L4137 328 L4144 339 L4152 348 L4161 356 L4171 363 L4182 367 L4193 371 L4204 375 L4215 378 L4226 381 L4237 384 L4247 386 L4258 388 L4268 390 L4279 391 L4289 392 L4299 392 L4309 392 L4336 391 L4362 388 L4389 382 L4415 375 L4441 364 L4466 352 L4492 338 L4493 337 L4493 337 L4467 344 L4465 345 L4439 351 L4437 351 L4411 354 L4409 354 L4382 356 L4381 356 L4371 355 L4370 355 L4360 355 L4359 355 L4348 354 L4348 354 L4337 353 L4337 353 L4326 351 L4325 351 L4315 349 L4314 349 L4303 347 L4303 346 L4292 344 L4291 344 L4280 341 L4280 340 L4269 337 L4268 337 L4257 333 L4256 333 L4245 329 L4243 328 L4233 323 L4231 323 L4229 322 L4219 315 L4218 314 L4217 313 L4208 305 L4205 303 L4197 293 L4195 291 L4188 280 L4187 277 L4181 265 L4180 263 L4175 249 L4174 247 L4170 231 L4169 229 L4166 212 L4166 211 L4163 192 L4163 191 L4162 171 L4162 170 L4161 149 L4161 148 L4162 121 L4162 121 L4163 95 L4163 94 L4164 69 L4165 68 L4167 43 L4167 42 L4170 18 L4170 17 L4174 -6 L4174 -7 L4178 -30 L4178 -31 L4183 -53 L4184 -54 L4189 -76 L4189 -77 L4196 -98 L4196 -99 L4203 -120 L4203 -121 L4211 -141 L4211 -142 L4225 -172 L4225 -174 L4241 -204 L4242 -205 L4244 -210ZM4228 -1035 L4208 -1043 L4149 -724 L4177 -732 L4204 -739 L4230 -746 L4256 -752 L4281 -757 L4306 -762 L4330 -766 L4353 -769 L4376 -771 L4398 -773 L4419 -774 L4440 -775 L4448 -775 L4456 -774 L4464 -774 L4472 -774 L4479 -773 L4487 -773 L4494 -772 L4501 -771 L4508 -771 L4512 -770 L4550 -810 L4549 -810 L4542 -811 L4535 -811 L4527 -811 L4520 -811 L4512 -811 L4492 -811 L4471 -810 L4450 -808 L4428 -806 L4405 -803 L4381 -799 L4358 -795 L4333 -789 L4308 -783 L4282 -777 L4255 -770 L4228 -761 L4226 -761 L4223 -761 L4221 -760 L4219 -761 L4216 -761 L4214 -761 L4212 -762 L4209 -763 L4207 -765 L4205 -766 L4204 -768 L4202 -769 L4201 -771 L4199 -773 L4198 -775 L4198 -778 L4197 -780 L4197 -782 L4196 -785 L4197 -787 L4197 -789 L4241 -1031ZM4283 -506 L4303 -496 L4323 -487 L4341 -478 L4358 -469 L4375 -460 L4390 -452 L4405 -443 L4418 -434 L4431 -425 L4437 -421 L4449 -431 L4450 -432 L4485 -457 L4485 -457 L4494 -463 L4489 -467 L4477 -475 L4463 -483 L4449 -492 L4434 -500 L4418 -509 L4401 -518 L4383 -527 L4365 -536 L4345 -545 L4343 -546 L4341 -547 L4339 -549 L4337 -550 L4335 -552 L4334 -554 L4333 -556 L4332 -558ZM4474 -250 L4482 -252 L4491 -254 L4499 -255 L4506 -256 L4513 -257 L4520 -257 L4527 -256 L4533 -256 L4539 -256 L4545 -256 L4551 -255 L4557 -255 L4562 -254 L4567 -253 L4572 -252 L4576 -252 L4580 -251 L4589 -233 L4597 -217 L4604 -200 L4610 -183 L4615 -167 L4620 -151 L4624 -134 L4627 -119 L4630 -103 L4632 -87 L4633 -72 L4633 -57 L4632 -31 L4629 -6 L4624 18 L4617 41 L4607 64 L4605 68 L4620 52 L4634 33 L4646 13 L4657 -7 L4665 -28 L4672 -50 L4677 -72 L4679 -95 L4680 -118 L4680 -132 L4679 -146 L4677 -161 L4675 -176 L4672 -190 L4668 -206 L4664 -221 L4658 -236 L4652 -252 L4646 -268 L4638 -284 L4635 -290 L4635 -290 L4630 -291 L4626 -292 L4621 -292 L4615 -292 L4610 -293 L4604 -293 L4598 -293 L4592 -293 L4585 -293 L4580 -293 L4574 -293 L4567 -291 L4560 -290 L4552 -288 L4544 -285 L4536 -282 L4527 -278 L4517 -274 L4507 -269 L4497 -263 L4486 -257 L4475 -251 L4473 -249ZM6198 -1188 L6160 -1188 L6158 -1188 L6155 -1188 L6153 -1189 L6151 -1190 L6149 -1191 L6147 -1192 L6145 -1193 L6143 -1195 L6141 -1197 L6140 -1198 L6139 -1201 L6138 -1203 L6137 -1205 L6136 -1207 L6136 -1210 L6136 -1212 L6136 -1214 L6136 -1217 L6136 -1219 L6137 -1221 L6138 -1223 L6284 -1515 L6270 -1515 L6088 -1151 L6163 -1151ZM6642 -1295 L6649 -1284 L6654 -1273 L6660 -1262 L6665 -1250 L6669 -1238 L6673 -1226 L6676 -1213 L6679 -1199 L6682 -1186 L6684 -1172 L6686 -1157 L6687 -1143 L6688 -1128 L6689 -1113 L6689 -1099 L6690 -1085 L6690 -1072 L6691 -1059 L6691 -1046 L6692 -1034 L6692 -1022 L6692 -1010 L6692 -999 L6692 -988 L6687 -383 L6687 -375 L6686 -367 L6686 -360 L6684 -352 L6683 -344 L6681 -336 L6679 -328 L6677 -320 L6675 -312 L6672 -304 L6669 -296 L6667 -292 L6670 -295 L6678 -304 L6685 -312 L6692 -321 L6698 -329 L6703 -336 L6707 -344 L6711 -351 L6714 -359 L6717 -366 L6720 -374 L6723 -381 L6725 -389 L6727 -396 L6729 -403 L6731 -410 L6732 -417 L6733 -424 L6734 -431 L6734 -438 L6734 -445 L6739 -1049 L6739 -1060 L6739 -1071 L6739 -1083 L6739 -1094 L6738 -1107 L6738 -1119 L6738 -1132 L6737 -1146 L6736 -1159 L6736 -1173 L6735 -1188 L6734 -1202 L6733 -1216 L6731 -1230 L6729 -1243 L6727 -1256 L6724 -1268 L6721 -1280 L6717 -1292 L6713 -1303 L6709 -1313 L6704 -1324 L6702 -1327ZM6625 -226 L6616 -217 L6607 -207 L6598 -198 L6587 -188 L6576 -179 L6588 -169 L6601 -160 L6613 -151 L6625 -141 L6637 -132 L6649 -122 L6662 -113 L6674 -104 L6686 -94 L6698 -85 L6711 -75 L6723 -66 L6735 -57 L6747 -47 L6760 -38 L6772 -28 L6784 -19 L6796 -10 L6809 -0 L6821 9 L6833 18 L6846 28 L6858 37 L6870 46 L6880 36 L6890 25 L6903 11 L6916 -4 L6915 -5 L6902 -14 L6890 -23 L6878 -33 L6866 -42 L6853 -52 L6841 -61 L6829 -70 L6816 -80 L6804 -89 L6792 -99 L6780 -108 L6767 -117 L6755 -127 L6743 -136 L6731 -146 L6718 -155 L6706 -164 L6694 -174 L6682 -183 L6670 -192 L6657 -202 L6645 -211 L6633 -221 L6631 -222 L6630 -224 L6628 -226 L6627 -228 L6626 -228ZM7175 -654 L7178 -645 L7181 -635 L7184 -626 L7186 -617 L7187 -607 L7188 -597 L7188 -588 L7188 -587 L7188 -586 L7188 -586 L7188 -585 L7188 -584 L7188 -584 L7188 -583 L7188 -582 L7188 -582 L7188 -581 L7188 -580 L7188 -580 L7191 -278 L7190 -263 L7188 -249 L7186 -235 L7184 -222 L7181 -210 L7178 -198 L7174 -187 L7170 -177 L7165 -167 L7160 -157 L7154 -149 L7148 -141 L7170 -121 L7192 -103 L7215 -86 L7237 -69 L7260 -54 L7283 -39 L7307 -25 L7331 -12 L7355 1 L7379 12 L7403 23 L7428 32 L7436 26 L7444 19 L7452 12 L7460 5 L7468 -1 L7476 -8 L7480 -10 L7466 -16 L7465 -16 L7441 -27 L7440 -27 L7416 -39 L7415 -39 L7391 -51 L7390 -52 L7367 -65 L7366 -65 L7342 -79 L7342 -80 L7319 -94 L7318 -95 L7295 -110 L7294 -111 L7272 -127 L7271 -128 L7249 -145 L7248 -146 L7226 -164 L7226 -164 L7204 -184 L7202 -185 L7200 -187 L7199 -189 L7198 -191 L7197 -193 L7196 -196 L7196 -198 L7195 -201 L7195 -203 L7196 -205 L7196 -208 L7197 -210 L7198 -212 L7199 -214 L7200 -217 L7206 -224 L7210 -231 L7215 -239 L7219 -248 L7222 -257 L7226 -267 L7229 -278 L7232 -289 L7234 -301 L7236 -313 L7237 -327 L7238 -340 L7235 -641 L7235 -641 L7235 -642 L7235 -642 L7235 -643 L7235 -644 L7235 -644 L7235 -645 L7235 -646 L7235 -646 L7235 -647 L7235 -649 L7235 -657 L7234 -666 L7233 -674 L7232 -682 L7230 -690 L7227 -698 L7224 -705 L7220 -713 L7216 -721 L7211 -729 L7206 -736 L7202 -742 L7148 -698 L7155 -689 L7161 -680 L7166 -672 L7171 -663ZM7798 30 L7847 -20 L7713 -157 L7711 -156 L7711 -156 L7702 -149 L7702 -148 L7692 -141 L7692 -141 L7683 -134 L7683 -134 L7673 -127 L7673 -127 L7664 -120 L7664 -120 L7657 -115ZM7647 -690 L7647 -270 L7694 -304 L7694 -736 L7571 -803 L7567 -795 L7559 -782 L7553 -769 L7547 -757 L7543 -746ZM5782 -693 L5783 -684 L5784 -674 L5785 -663 L5786 -652 L5786 -641 L5788 -408 L5788 -390 L5786 -372 L5784 -354 L5781 -335 L5777 -315 L5771 -295 L5765 -274 L5758 -253 L5750 -232 L5741 -210 L5731 -187 L5720 -164 L6148 47 L6188 -3 L5781 -203 L5779 -204 L5777 -205 L5775 -207 L5774 -209 L5772 -211 L5771 -213 L5770 -215 L5769 -217 L5768 -219 L5768 -221 L5767 -224 L5767 -226 L5768 -229 L5768 -231 L5769 -233 L5770 -235 L5780 -258 L5790 -280 L5799 -302 L5807 -323 L5813 -343 L5819 -363 L5824 -382 L5828 -401 L5831 -419 L5834 -436 L5835 -453 L5835 -469 L5833 -701 L5833 -712 L5832 -723 L5831 -733 L5831 -742 L5830 -751 L5828 -759 L5827 -766 L5826 -773 L5825 -776 L5810 -765 L5771 -740 L5773 -735 L5775 -729 L5777 -723 L5778 -717 L5780 -709 L5781 -701ZM6138 -717 L6151 -706 L6163 -694 L6175 -682 L6186 -669 L6197 -655 L6207 -640 L6217 -625 L6227 -608 L6236 -590 L6245 -571 L6254 -550 L6262 -529 L6269 -507 L6276 -483 L6283 -458 L6289 -433 L6295 -406 L6296 -401 L6297 -397 L6298 -393 L6298 -389 L6299 -385 L6299 -381 L6299 -377 L6300 -373 L6300 -369 L6300 -366 L6300 -362 L6300 -359 L6300 -344 L6299 -339 L6301 -340 L6310 -345 L6318 -350 L6325 -356 L6331 -362 L6336 -369 L6340 -377 L6343 -386 L6345 -396 L6347 -407 L6347 -420 L6347 -423 L6347 -426 L6347 -429 L6347 -433 L6347 -436 L6346 -440 L6346 -443 L6345 -447 L6345 -451 L6344 -454 L6344 -458 L6343 -462 L6337 -488 L6331 -514 L6324 -538 L6318 -561 L6310 -582 L6303 -603 L6294 -622 L6286 -640 L6277 -657 L6268 -673 L6259 -688 L6249 -702 L6239 -715 L6228 -727 L6217 -738 L6206 -749 L6194 -760 L6182 -769 L6170 -778 L6157 -786 L6144 -794 L6136 -798 L6136 -719ZM8582 47 L8620 -1 L8537 -34 L8537 -31 L8537 -30 L8537 -28 L8537 -25 L8537 -23 L8537 -22 L8537 -20 L8537 -18 L8537 -16 L8537 -10 L8537 -6 L8537 1 L8537 11 L8538 24 L8538 30ZM8035 -793 L8040 -789 L8045 -786 L8051 -782 L8057 -777 L8064 -773 L8071 -767 L8079 -762 L8088 -756 L8097 -749 L8106 -742 L8116 -735 L8127 -728 L8137 -719 L8147 -711 L8155 -702 L8163 -693 L8170 -683 L8176 -672 L8181 -661 L8185 -650 L8188 -638 L8191 -626 L8192 -614 L8192 -601 L8191 -396 L8186 -385 L8181 -375 L8176 -365 L8171 -355 L8168 -351 L8174 -358 L8181 -366 L8187 -375 L8193 -383 L8199 -392 L8205 -401 L8210 -410 L8216 -419 L8221 -428 L8226 -438 L8231 -447 L8236 -457 L8238 -463 L8239 -662 L8239 -673 L8238 -684 L8236 -694 L8233 -704 L8230 -714 L8226 -722 L8221 -731 L8215 -739 L8209 -747 L8201 -755 L8193 -762 L8184 -769 L8174 -777 L8163 -784 L8154 -791 L8145 -797 L8137 -803 L8129 -809 L8121 -814 L8115 -819 L8108 -823 L8103 -827 L8098 -831 L8093 -834 L8091 -835 L8089 -837 L8088 -839 L8086 -841 L8085 -843 L8085 -843ZM8122 -282 L8115 -273 L8109 -264 L8102 -255 L8096 -246 L8096 -244 L8096 -241 L8097 -239 L8096 -237 L8096 -234 L8096 -233 L8096 -234 L8097 -236 L8103 -247 L8103 -248 L8109 -258 L8109 -259 L8115 -269 L8115 -270 L8121 -280 L8121 -280 L8122 -282ZM8065 -196 L8059 -185 L8053 -175 L8047 -164 L8189 -108 L8186 97 L8186 111 L8185 126 L8183 140 L8180 155 L8177 170 L8173 186 L8168 201 L8163 217 L8157 233 L8150 250 L8142 267 L8134 284 L8125 301 L8117 316 L8115 319 L8117 318 L8121 313 L8126 307 L8132 300 L8138 291 L8145 281 L8152 269 L8159 257 L8167 243 L8175 228 L8184 211 L8192 195 L8199 179 L8206 163 L8212 147 L8217 132 L8221 117 L8225 103 L8228 89 L8230 75 L8232 62 L8233 49 L8233 35 L8236 -152 L8110 -202 L8108 -203 L8106 -204 L8104 -206 L8102 -207 L8100 -209 L8099 -211 L8098 -213 L8096 -215 L8096 -218 L8095 -220 L8095 -222 L8094 -225 L8095 -227 L8095 -229 L8095 -231 L8095 -230 L8094 -228 L8093 -226 L8091 -224 L8090 -222 L8065 -196ZM8094 355 L8087 366 L8080 376 L8074 384 L8068 391 L8062 397 L8056 402 L8051 405 L8101 459 L8131 440 L8161 422 L8164 421 L8163 420 L8161 419 L8159 418 L8157 416 L8155 415 L8105 361 L8103 359 L8102 357 L8101 355 L8100 352 L8099 350 L8099 348ZM8308 351 L8337 341 L8366 332 L8395 324 L8424 318 L8452 312 L8451 292 L8445 294 L8417 303 L8389 313 L8360 324 L8332 336 L8303 350 L8280 362ZM8560 -700 L8572 -690 L8584 -678 L8595 -667 L8606 -655 L8617 -642 L8628 -629 L8639 -616 L8650 -601 L8660 -587 L8669 -571 L8678 -555 L8687 -539 L8694 -522 L8702 -504 L8708 -485 L8714 -466 L8720 -447 L8725 -426 L8729 -406 L8730 -401 L8731 -397 L8732 -393 L8732 -389 L8733 -385 L8733 -381 L8733 -377 L8734 -373 L8734 -369 L8734 -365 L8734 -361 L8734 -358 L8734 -343 L8733 -339 L8736 -340 L8745 -345 L8753 -350 L8759 -355 L8765 -362 L8770 -369 L8774 -377 L8777 -385 L8779 -395 L8781 -407 L8781 -419 L8781 -422 L8781 -426 L8781 -429 L8781 -432 L8781 -436 L8780 -439 L8780 -443 L8779 -447 L8779 -451 L8778 -454 L8778 -458 L8777 -462 L8773 -482 L8768 -502 L8762 -521 L8757 -539 L8750 -556 L8743 -573 L8736 -590 L8728 -605 L8720 -620 L8711 -635 L8702 -649 L8692 -662 L8681 -675 L8670 -688 L8660 -700 L8649 -711 L8638 -722 L8627 -733 L8616 -743 L8605 -753 L8593 -762 L8582 -770 L8580 -772 L8578 -768 L8575 -763 L8573 -759 L8570 -754 L8568 -750 L8565 -745 L8563 -741 L8561 -737 L8559 -733 L8558 -729 L8556 -725 L8555 -720 L8554 -715 L8553 -710 L8552 -707Z"/><path fill="currentColor" d="M697 -1226Q595 -1252 499 -1252Q263 -1252 69 -1092Q70 -1102 62.5 -1144.5Q55 -1187 57 -1197Q320 -1413 629 -1535Q1033 -1415 1372 -1205Q1350 -1169 1317.0 -1142.0Q1284 -1115 1262 -1080Q1255 -1061 1255 -1026V-972L1262 -436Q1265 -371 1286 -355L1387 -272L1211 -31L982 -225Q1004 -382 1004 -636H630Q592 -282 413.5 -36.0Q235 210 92 349Q50 319 16 299Q132 178 189.5 22.0Q247 -134 288.0 -307.5Q329 -481 329.0 -647.5Q329 -814 256 -960Q499 -1081 697 -1226ZM1000 -871Q998 -990 998.0 -1017.5Q998 -1045 999 -1080Q900 -1150 791 -1195Q720 -1142 683.0 -1073.5Q646 -1005 639 -871ZM1957.0 -1200Q1976.0 -1268 2046.0 -1535Q2190.0 -1524 2334.5 -1512.0Q2479.0 -1500 2624.0 -1489Q2550.0 -1360 2537.5 -1253.5Q2525.0 -1147 2525.0 -1086.5Q2525.0 -1026 2527.0 -991Q2630.0 -1097 2730.0 -1239.0Q2830.0 -1381 2930.0 -1535Q3128.0 -1223 3316.0 -1024L2961.0 -856L3314.0 -224Q3094.0 -92 2994.0 -31Q2839.0 -425 2684.0 -725L2531.0 -652Q2522.0 -585 2501.0 -528.5Q2480.0 -472 2425.0 -431.5Q2370.0 -391 2326.0 -345Q2394.0 -322 2435.0 -322Q2544.0 -322 2652.0 -342L2482.0 -31Q2262.0 -95 1949.0 -111Q2152.0 -289 2191.5 -590.5Q2231.0 -892 2231.0 -1209Q2231.0 -1217 2231.0 -1225Q2212.0 -1226 2180.0 -1226Q2131.0 -1226 1957.0 -1200ZM2537.0 -780Q2754.0 -875 2927.0 -991Q2860.0 -1044 2809.0 -1141L2536.0 -807Q2536.0 -790 2537.0 -780ZM3871.0 -31 3392.0 -222Q3419.0 -372 3419.0 -534.5Q3419.0 -697 3388.0 -874L3733.0 -1077V-1080L3735.0 -1079H3736.0L4125.0 -800L3720.0 -374Q3761.0 -325 3811.0 -320.0Q3861.0 -315 3893.5 -315.0Q3926.0 -315 3970.5 -318.5Q4015.0 -322 4080.0 -324Q4027.0 -250 3975.0 -177.0Q3923.0 -104 3871.0 -31ZM3705.0 -810Q3647.0 -810 3647.0 -652Q3647.0 -562 3677.0 -472Q3677.0 -463 3680.0 -457L3841.0 -627Q3858.0 -645 3858.0 -663.0Q3858.0 -681 3840.5 -709.0Q3823.0 -737 3805.0 -755Q3742.0 -810 3705.0 -810ZM4536.0 -461Q4478.0 -512 4355.0 -567L4601.0 -829Q4560.0 -836 4512.0 -836Q4388.0 -836 4221.0 -785L4280.0 -1104Q4399.0 -1058 4535.0 -1058Q4552.0 -1058 4638.0 -1061.5Q4724.0 -1065 4880.0 -1070L4629.0 -767Q4786.0 -642 4840.5 -525.5Q4895.0 -409 4895.0 -296Q4891.0 -49 4689.0 170Q4541.0 331 4381.0 331Q4321.0 331 4253.5 306.0Q4186.0 281 4186.0 148Q4186.0 -13 4234.0 -132Q4307.0 -316 4536.0 -461ZM4390.0 141Q4433.0 172 4474.5 172.0Q4516.0 172 4556.0 144Q4705.0 37 4705.0 -118Q4705.0 -209 4652.0 -312Q4627.0 -318 4585.0 -318.0Q4543.0 -318 4473.5 -278.5Q4404.0 -239 4365.0 -186.5Q4326.0 -134 4305.0 -34Q4302.0 -18 4302.0 -2Q4302.0 78 4390.0 141ZM5278.0 -1025Q5201.0 -1156 5075.0 -1301L5340.0 -1440Q5700.0 -1001 5740.0 -404Q5615.0 -310 5528.5 -230.0Q5442.0 -150 5386.0 -31Q5276.0 -80 5166.5 -130.5Q5057.0 -181 4965.0 -265Q4961.0 -416 4959.0 -568.0Q4957.0 -720 4955.0 -872Q5195.0 -982 5278.0 -1025ZM5483.0 -286Q5483.0 -296 5483.0 -307Q5483.0 -643 5325.0 -946Q5216.0 -894 5195.0 -758Q5188.0 -705 5188.0 -623.0Q5188.0 -541 5208.0 -411Q5348.0 -300 5483.0 -286ZM5843.0 -801Q6076.0 -957 6230.0 -1084Q6307.0 -1051 6385.0 -966Q6554.0 -785 6585.0 -588Q6589.0 -565 6589.0 -544Q6589.0 -478 6552.0 -437L6220.0 -14L5792.0 -225Q5860.0 -366 5860.0 -469L5858.0 -702Q5855.0 -774 5843.0 -801ZM6112.0 -836 6109.0 -411Q6118.0 -362 6178.0 -334.5Q6238.0 -307 6257.5 -307.0Q6277.0 -307 6284.0 -309Q6372.0 -329 6372.0 -420Q6372.0 -441 6367.0 -467Q6333.0 -632 6268.5 -716.5Q6204.0 -801 6112.0 -836ZM5873.1 -1212.4H5948.0L6290.4 -1576.2H6055.0ZM6160.0 -1212.4H6234.9L6577.3 -1576.2H6341.9ZM7164.0 -259Q6992.0 -69 6942.0 -15Q6868.0 -71 6794.5 -127.5Q6721.0 -184 6648.0 -240Q6716.0 -299 6737.0 -349.0Q6758.0 -399 6759.0 -444L6764.0 -1049Q6764.0 -1114 6758.5 -1204.0Q6753.0 -1294 6714.0 -1356L7044.0 -1535Q7017.0 -1415 7012.5 -1286.0Q7008.0 -1157 7008.0 -1024L7004.0 -437Q7004.0 -362 7018.0 -352.0Q7032.0 -342 7164.0 -259ZM7220.0 -759 7610.0 -1076 8052.0 -869Q7974.0 -801 7973.0 -716L7969.0 -385Q7969.0 -351 8004.5 -308.0Q8040.0 -265 8072.0 -239L7870.0 -31L7715.0 -190Q7659.0 -145 7602.0 -107.5Q7545.0 -70 7500.0 -29Q7350.0 -85 7220.0 -202Q7257.0 -249 7263.0 -339L7260.0 -641Q7260.0 -645 7260.0 -649Q7260.0 -708 7220.0 -759ZM7563.0 -836Q7505.0 -749 7505.0 -681V-436Q7505.0 -361 7532.0 -329Q7555.0 -296 7574.5 -270.0Q7594.0 -244 7637.0 -232L7719.0 -291V-751ZM8524.0 251Q8353.0 278 8173.0 398L8123.0 344Q8153.0 326 8205.5 222.5Q8258.0 119 8258.0 36L8261.0 -169L8119.0 -225Q8153.0 -292 8193.5 -343.0Q8234.0 -394 8263.0 -457L8264.0 -662Q8264.0 -742 8198.5 -789.0Q8133.0 -836 8107.0 -854L8331.0 -1084L8523.0 -909L8664.0 -1084Q8741.0 -1051 8819.0 -966Q8988.0 -785 9019.0 -588Q9023.0 -565 9023.0 -544Q9023.0 -478 8986.0 -437L8654.0 -14L8513.0 -70L8512.0 -31Q8512.0 -23 8512.0 -9.5Q8512.0 4 8524.0 251ZM8519.0 -371Q8657.0 -307 8705.0 -307Q8713.0 -307 8719.0 -309Q8806.0 -329 8806.0 -419Q8806.0 -441 8801.0 -467Q8776.0 -594 8711.0 -677.0Q8646.0 -760 8572.0 -807Q8556.0 -780 8541.0 -752.0Q8526.0 -724 8526.0 -682Z"/></svg>'''

# Böngészőfül- és kezdőképernyő-ikon (az „AK” a UnifrakturCook betűtípusból, árnyékkontúrral)
IKON_GOT = (
    '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Crect width=%2264%22 height=%2264%22 rx=%2214%22 fill=%22%2316202f%22/%3E%3Csvg x=%227.0%22 y=%2214.8%22 width=%2250.0%22 height=%2234.2%22 viewBox=%22-148 -1596 3066 2099%22%3E%3Cpath class=%22arnyek%22 fill=%22%23ffffff%22 fill-opacity=%22.42%22 fill-rule=%22evenodd%22 d=%22M1475 -1119 L1498 -1122 L1519 -1125 L1538 -1127 L1556 -1129 L1572 -1131 L1586 -1132 L1599 -1133 L1610 -1134 L1619 -1134 L1624 -1134 L1629 -1134 L1634 -1134 L1638 -1134 L1643 -1134 L1647 -1134 L1651 -1133 L1655 -1133 L1659 -1133 L1663 -1133 L1666 -1133 L1670 -1133 L1670 -1132 L1670 -1130 L1670 -1129 L1670 -1128 L1670 -1126 L1670 -1125 L1670 -1124 L1670 -1122 L1670 -1121 L1670 -1120 L1670 -1118 L1670 -1117 L1669 -1064 L1668 -1012 L1667 -959 L1665 -907 L1663 -855 L1660 -804 L1656 -752 L1652 -701 L1647 -650 L1642 -599 L1636 -549 L1630 -498 L1622 -449 L1612 -401 L1600 -355 L1586 -311 L1569 -269 L1550 -228 L1549 -227 L1552 -230 L1577 -264 L1599 -299 L1619 -337 L1637 -375 L1653 -416 L1667 -458 L1679 -502 L1688 -548 L1696 -596 L1702 -645 L1708 -695 L1713 -746 L1718 -796 L1722 -847 L1725 -898 L1728 -949 L1731 -1001 L1733 -1053 L1734 -1105 L1735 -1157 L1735 -1189 L1731 -1189 L1726 -1189 L1721 -1189 L1713 -1189 L1703 -1188 L1692 -1188 L1678 -1186 L1662 -1185 L1645 -1183 L1626 -1181 L1605 -1178 L1582 -1175 L1558 -1171 L1531 -1168 L1503 -1164 L1500 -1163 L1496 -1163 L1492 -1164 L1489 -1164 L1485 -1165 L1482 -1167 L1479 -1169 L1476 -1171 L1473 -1173 L1470 -1176 L1468 -1178 L1466 -1182 L1464 -1185 L1463 -1188 L1462 -1192 L1461 -1195 L1461 -1199 L1461 -1203 L1462 -1206 L1462 -1210 L1466 -1222 L1470 -1238 L1475 -1256 L1481 -1277 L1487 -1301 L1494 -1327 L1502 -1356 L1510 -1389 L1520 -1423 L1524 -1440 L1509 -1441 L1485 -1443 L1473 -1400 L1463 -1359 L1453 -1322 L1444 -1287 L1435 -1255 L1427 -1226 L1420 -1199 L1414 -1175 L1408 -1154 L1403 -1136 L1399 -1121 L1396 -1108 L1424 -1112 L1450 -1116ZM1451 -82 L1420 -49 L1388 -19 L1439 -16 L1489 -12 L1538 -8 L1586 -3 L1632 3 L1677 9 L1721 16 L1764 24 L1805 32 L1845 41 L1883 51 L1921 61 L1959 -10 L1939 -15 L1899 -24 L1859 -32 L1817 -40 L1774 -47 L1730 -53 L1684 -58 L1637 -63 L1589 -68 L1539 -71 L1488 -74 L1484 -75 L1481 -75 L1477 -76 L1474 -78 L1471 -79 L1468 -81 L1465 -84 L1463 -86 L1460 -89 L1459 -91ZM2123 -633 L2148 -582 L2174 -530 L2200 -477 L2226 -422 L2252 -367 L2278 -309 L2303 -251 L2329 -191 L2355 -130 L2381 -68 L2407 -4 L2433 61 L2450 51 L2469 39 L2490 26 L2513 13 L2526 5 L2523 4 L2520 3 L2517 1 L2514 -1 L2511 -3 L2508 -5 L2506 -8 L2504 -11 L2502 -14 L2501 -17 L2475 -82 L2449 -146 L2423 -208 L2398 -269 L2372 -328 L2346 -386 L2321 -443 L2295 -499 L2269 -553 L2244 -606 L2218 -658 L2208 -676 L2106 -627 L2105 -625ZM2755 -932 L2753 -934 L2553 -840 L2555 -837ZM2283 -989 L2293 -976 L2303 -963 L2313 -951 L2320 -942 L2329 -948 L2360 -966 L2390 -984 L2408 -996 L2398 -1006 L2397 -1007 L2387 -1018 L2386 -1020 L2376 -1032 L2376 -1033 L2366 -1046 L2365 -1047 L2356 -1060 L2355 -1061 L2346 -1075 L2345 -1076 L2345 -1076 L2279 -996ZM233 -909 L194 -888 L154 -868 L165 -843 L176 -819 L186 -794 L194 -768 L202 -743 L208 -717 L214 -691 L218 -664 L222 -637 L225 -610 L226 -583 L227 -555 L226 -528 L225 -500 L224 -472 L222 -444 L219 -415 L216 -387 L213 -359 L208 -330 L204 -302 L198 -273 L192 -244 L186 -215 L179 -187 L171 -158 L164 -130 L156 -102 L149 -74 L140 -46 L132 -19 L124 8 L115 35 L106 62 L97 88 L87 114 L77 140 L66 165 L55 190 L45 208 L57 193 L72 171 L86 150 L100 127 L112 105 L124 82 L135 58 L145 34 L155 9 L164 -16 L173 -42 L182 -69 L191 -95 L199 -122 L208 -149 L216 -176 L223 -204 L231 -231 L238 -259 L245 -288 L252 -316 L259 -344 L264 -372 L270 -400 L274 -428 L279 -456 L282 -484 L285 -511 L288 -539 L290 -566 L291 -593 L292 -620 L292 -647 L292 -674 L290 -700 L288 -725 L284 -751 L280 -776 L275 -800 L269 -825 L261 -849 L253 -873 L244 -897 L238 -911ZM-32 328 L-49 350 L-67 371 L-86 391 L-81 395 L-75 398 L-69 402 L-63 406 L-57 410 L-50 414 L-44 418 L-38 422 L-31 427 L-24 431 L-17 436 L-10 441 L14 417 L38 392 L58 370 L57 370 L51 365 L44 361 L38 356 L32 352 L26 348 L20 345 L14 341 L8 337 L3 334 L-3 331 L-6 329 L-9 326 L-11 324 L-14 321 L-16 318 L-17 315 L-19 311 L-19 311ZM902 -544 L901 -502 L901 -462 L900 -423 L899 -385 L898 -349 L896 -314 L894 -281 L892 -248 L889 -217 L886 -188 L883 -160 L880 -133 L1109 61 L1168 -20 L958 -197 L955 -199 L953 -202 L951 -205 L949 -208 L948 -212 L946 -215 L946 -219 L945 -222 L945 -226 L945 -230 L949 -256 L952 -284 L955 -313 L957 -343 L960 -375 L962 -408 L963 -443 L965 -479 L966 -516 L967 -555 L967 -595 L967 -599 L663 -599 L659 -573 L659 -572 L655 -544ZM-1 -1140 L-45 -1105 L-46 -1103 L-46 -1101 L-46 -1098 L-46 -1095 L-45 -1091 L-45 -1087 L-45 -1082 L-44 -1077 L-43 -1072 L-42 -1066 L-41 -1059 L-40 -1052 L-39 -1045 L-38 -1039 L-37 -1033 L-36 -1028 L-35 -1023 L-35 -1018 L-34 -1014 L-34 -1010 L-33 -1007 L-33 -1004 L-33 -1002 L-33 -1000 L-1 -1025 L32 -1049 L51 -1060 L50 -1060 L47 -1062 L44 -1065 L42 -1067 L39 -1070 L37 -1073 L36 -1076 L34 -1080 L33 -1083 L33 -1087 L32 -1090 L32 -1094 L32 -1095 L32 -1095 L32 -1097 L32 -1099 L32 -1102 L31 -1106 L31 -1110 L30 -1114 L29 -1120 L28 -1125 L27 -1131 L26 -1138 L25 -1145 L25 -1145 L24 -1152 L24 -1152 L23 -1158 L23 -1158 L23 -1159ZM171 -1120 L207 -1132 L244 -1142 L281 -1150 L319 -1155 L358 -1159 L397 -1160 L413 -1160 L429 -1159 L445 -1158 L461 -1157 L478 -1155 L494 -1153 L511 -1151 L519 -1150 L540 -1163 L575 -1186 L606 -1207 L592 -1209 L576 -1211 L560 -1212 L545 -1214 L530 -1214 L514 -1215 L499 -1215 L462 -1214 L426 -1211 L390 -1206 L355 -1198 L320 -1189 L286 -1177 L253 -1164 L220 -1148 L187 -1130 L170 -1119ZM742 -1079 L760 -1070 L778 -1061 L795 -1052 L812 -1042 L829 -1032 L846 -1021 L863 -1010 L880 -999 L897 -988 L896 -982 L896 -976 L896 -971 L896 -965 L896 -960 L896 -955 L896 -950 L896 -945 L896 -940 L896 -935 L896 -930 L896 -925 L896 -920 L896 -914 L896 -908 L963 -908 L962 -924 L962 -940 L962 -954 L962 -967 L961 -978 L961 -978 L961 -989 L961 -989 L961 -998 L961 -998 L961 -1005 L961 -1006 L961 -1012 L961 -1012 L961 -1017 L961 -1018 L961 -1022 L961 -1022 L961 -1027 L961 -1027 L961 -1032 L961 -1032 L961 -1037 L961 -1037 L961 -1042 L961 -1042 L961 -1047 L961 -1048 L961 -1053 L961 -1053 L962 -1058 L962 -1058 L962 -1061 L946 -1072 L929 -1082 L913 -1092 L896 -1102 L880 -1111 L863 -1121 L846 -1129 L829 -1138 L812 -1146 L797 -1153 L792 -1149 L782 -1140 L773 -1132 L764 -1123 L756 -1114 L748 -1104 L741 -1095 L734 -1086 L732 -1084Z%22/%3E%3Cpath fill=%22%23f2f5f9%22 d=%22M697 -1226Q595 -1252 499 -1252Q263 -1252 69 -1092Q70 -1102 62.5 -1144.5Q55 -1187 57 -1197Q320 -1413 629 -1535Q1033 -1415 1372 -1205Q1350 -1169 1317.0 -1142.0Q1284 -1115 1262 -1080Q1255 -1061 1255 -1026V-972L1262 -436Q1265 -371 1286 -355L1387 -272L1211 -31L982 -225Q1004 -382 1004 -636H630Q592 -282 413.5 -36.0Q235 210 92 349Q50 319 16 299Q132 178 189.5 22.0Q247 -134 288.0 -307.5Q329 -481 329.0 -647.5Q329 -814 256 -960Q499 -1081 697 -1226ZM1000 -871Q998 -990 998.0 -1017.5Q998 -1045 999 -1080Q900 -1150 791 -1195Q720 -1142 683.0 -1073.5Q646 -1005 639 -871ZM1497.9 -1200Q1516.9 -1268 1586.9 -1535Q1730.9 -1524 1875.4 -1512.0Q2019.9 -1500 2164.9 -1489Q2090.9 -1360 2078.4 -1253.5Q2065.9 -1147 2065.9 -1086.5Q2065.9 -1026 2067.9 -991Q2170.9 -1097 2270.9 -1239.0Q2370.9 -1381 2470.9 -1535Q2668.9 -1223 2856.9 -1024L2501.9 -856L2854.9 -224Q2634.9 -92 2534.9 -31Q2379.9 -425 2224.9 -725L2071.9 -652Q2062.9 -585 2041.9 -528.5Q2020.9 -472 1965.9 -431.5Q1910.9 -391 1866.9 -345Q1934.9 -322 1975.9 -322Q2084.9 -322 2192.9 -342L2022.9 -31Q1802.9 -95 1489.9 -111Q1692.9 -289 1732.4 -590.5Q1771.9 -892 1771.9 -1209Q1771.9 -1217 1771.9 -1225Q1752.9 -1226 1720.9 -1226Q1671.9 -1226 1497.9 -1200ZM2077.9 -780Q2294.9 -875 2467.9 -991Q2400.9 -1044 2349.9 -1141L2076.9 -807Q2076.9 -790 2077.9 -780Z%22/%3E%3C/svg%3E%3C/svg%3E">'
    '<link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAFJElEQVR4AcRXeVCVVRT/fZ88ljQEk2dZwTOdEGQRkVD/aCadJC1HzcamNHTIhnF0LFFGLUHBJUCWiU1JhxxAllCZjDIWWUQLgQQEAlkGpDTZZJNN4H3dc1+PgPfeH40Id+ace75z7j2/37v33MMgYtQwUSw2kStsfcwU9mlyhV2TXGEvTYzYNaly2voQBkYNUW2bWdiukUlSBSB6C8BqQJBjwoYgV+UUvQmDsNSpOQG5wt5FEMSfBQFz1YFnNRMGYZmxH0wY4kxzW1NJwnf0MbkinjN+xXqWKBOFbYzVS5MLDjDMufp6eq6iAGEZpmgQNiOAKSSAZVSEFlN0AARrQQTI0CnvrF6J1MvxiI2OxPp1azTWyWR62OXuhtqKAi6lBTlIT03Gy3NVZeW+wxU21gs19qkdOgnMf02By0nnER7ih4WWC7DceSmC/HxQkJcGT4/deHGOqk3QvGK5kzofpk9/Dk8GnuD+g79hu8gKnnt3IyTg+Eh8vKGVgOvWzfA6tA8512/ghF8wkxC0d3Ti0aN2zJplCvdPXXEjKxV7dn0GmUyGzs5uxFz4nq8hgPLKSsye/QJOh52iT8ybZ45APx9uj1caBOxsrFB6pwI7du5FaMQ55Bfexvsb30VMXBJO+ocgMip6JEdCUgoeP+6B78lAZGbloLe3l8d6e/sRFR6IuIRkVN6t5r4N7Pr2793F7dFKg0BFZTWIgJGRIS4mROPHS3Gwsnwd0THxuJqRheraWr6/q7sbLa2taG5pxcOmZvT19cPI0JDHXLdsRn1DIyOViz9YPnKW3ClH3s18MseIBoHh4WG+YIm9DUyMjblN6tuIIERHfYPtn2yhTw2hfXQ9FKivb0RxSRnOhAZi04b34O3rjw8+csOtgt8pPEY0CKijQ0PDqKmrV3/C2cmRy2K7Rdynpyfjs1pNnzFDbfI7X+JgB4XCnPuogLmhRekk0NnVjZ7eHr6lqqoGh49+jSPH/OFzIoD7RGph3FKp2aw4VRYQGBKBjMwsZFzL5a41LqvgtNSB2+OVTgK00GTmTJrwW0EREpNTcCHxElpb27jP0MCAz6SmTZsGaytLMlkh9uHKT2n89DKv5XAfKa9DHjRpiE4C/QP9mPfvERbdLhnZaGigKjRyrFimev+mJsZYbGdDLgSHnYYjO34jQyMolcMoLCrmfuuFlti8aT23RyudBAz09WFh/iqv8Nzrv/I9oihicHiI26Q8Pt9JE5Y6Oowc8eEDHgg5dRwPm1tQVV2LzGzVNdDCVW+9SdMY0UqAjpSKiFYGBIejf2CATPaLlJCUShz0OsabkueXR2Fs/Dyc33BEWkY27lbX8XXnYxP5VdXUNaCx8S/2VFvgHxQG9937eHy00krAlB2py9srkc7u8ErqL6PXIy0zFx3tHXBZx946e2767G9BfMJFnI2OYY0qGBs/3IbQyLN8z9DQEOsVbVi74WMWj+W+8UorgQUL5mOOmRz7Dx4Zvx6UNDM7D9SaKdja1s4LroR1z5v5hSgrr0QXe0EUIyF/B2vjZGsTrQS+OvAF3Nz38IrWtmkifRoEnJ0csHX7Tvx5/8FE4ujMpUHgVmExqAnp3DHBAUZAaprgnP8jndQkSsB/XQaTOwibnYAwZQQAoUQchHCGMenAJA/CJGyxo6GkQZDgNsn4UEK5nbDZFQDN90pTJEm5lv2L9szfHmEQVltD2Q/0ozkBMlrulV0dFIRFgNKXHU86IDWT/2lFtV9qVuVU+hIGYan8wD8AAAD//yqvcakAAAAGSURBVAMAYN/w1J6TGRAAAAAASUVORK5CYII=">'
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

IKON_SVG = {"hirlap": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" d=\"M9.0 48.87 15.45 15.12H23.54L29.91 48.87H23.29L22.12 41.58H16.95L15.75 48.87ZM17.62 37.25H21.41L19.54 23.87ZM34.58 48.87V15.12H41.75V29.12L47.66 15.12H54.62L48.33 30.33L55.0 48.87H47.75L42.70 33.87L41.75 35.45V48.87Z\"/></svg>", "nemzeti": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><path fill=\"#f2f5f9\" d=\"M22.09 43.83 19.50 36.18H12.49L9.76 43.83H12.42V44.50H7.0V43.83H8.66L17.70 19.49H20.60L29.53 43.83H31.16V44.50H19.57V43.83ZM16.14 26.11 12.81 35.40H19.22ZM32.26 19.70H43.07V20.37H41.12V31.57L41.40 31.50L51.40 20.37H48.60V19.70H54.80V20.37H52.67L46.58 27.18L55.08 43.83H57.0V44.50H45.62V43.83H47.29L41.44 32.53L41.12 32.60V43.83H43.07V44.50H32.26V43.83H34.21V20.37H32.26Z\"/></svg>", "got": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><rect width=\"64\" height=\"64\" rx=\"14\" fill=\"#16202f\"/><svg x=\"7.0\" y=\"14.8\" width=\"50.0\" height=\"34.2\" viewBox=\"-148 -1596 3066 2099\"><path class=\"arnyek\" fill=\"#ffffff\" fill-opacity=\".42\" fill-rule=\"evenodd\" d=\"M1475 -1119 L1498 -1122 L1519 -1125 L1538 -1127 L1556 -1129 L1572 -1131 L1586 -1132 L1599 -1133 L1610 -1134 L1619 -1134 L1624 -1134 L1629 -1134 L1634 -1134 L1638 -1134 L1643 -1134 L1647 -1134 L1651 -1133 L1655 -1133 L1659 -1133 L1663 -1133 L1666 -1133 L1670 -1133 L1670 -1132 L1670 -1130 L1670 -1129 L1670 -1128 L1670 -1126 L1670 -1125 L1670 -1124 L1670 -1122 L1670 -1121 L1670 -1120 L1670 -1118 L1670 -1117 L1669 -1064 L1668 -1012 L1667 -959 L1665 -907 L1663 -855 L1660 -804 L1656 -752 L1652 -701 L1647 -650 L1642 -599 L1636 -549 L1630 -498 L1622 -449 L1612 -401 L1600 -355 L1586 -311 L1569 -269 L1550 -228 L1549 -227 L1552 -230 L1577 -264 L1599 -299 L1619 -337 L1637 -375 L1653 -416 L1667 -458 L1679 -502 L1688 -548 L1696 -596 L1702 -645 L1708 -695 L1713 -746 L1718 -796 L1722 -847 L1725 -898 L1728 -949 L1731 -1001 L1733 -1053 L1734 -1105 L1735 -1157 L1735 -1189 L1731 -1189 L1726 -1189 L1721 -1189 L1713 -1189 L1703 -1188 L1692 -1188 L1678 -1186 L1662 -1185 L1645 -1183 L1626 -1181 L1605 -1178 L1582 -1175 L1558 -1171 L1531 -1168 L1503 -1164 L1500 -1163 L1496 -1163 L1492 -1164 L1489 -1164 L1485 -1165 L1482 -1167 L1479 -1169 L1476 -1171 L1473 -1173 L1470 -1176 L1468 -1178 L1466 -1182 L1464 -1185 L1463 -1188 L1462 -1192 L1461 -1195 L1461 -1199 L1461 -1203 L1462 -1206 L1462 -1210 L1466 -1222 L1470 -1238 L1475 -1256 L1481 -1277 L1487 -1301 L1494 -1327 L1502 -1356 L1510 -1389 L1520 -1423 L1524 -1440 L1509 -1441 L1485 -1443 L1473 -1400 L1463 -1359 L1453 -1322 L1444 -1287 L1435 -1255 L1427 -1226 L1420 -1199 L1414 -1175 L1408 -1154 L1403 -1136 L1399 -1121 L1396 -1108 L1424 -1112 L1450 -1116ZM1451 -82 L1420 -49 L1388 -19 L1439 -16 L1489 -12 L1538 -8 L1586 -3 L1632 3 L1677 9 L1721 16 L1764 24 L1805 32 L1845 41 L1883 51 L1921 61 L1959 -10 L1939 -15 L1899 -24 L1859 -32 L1817 -40 L1774 -47 L1730 -53 L1684 -58 L1637 -63 L1589 -68 L1539 -71 L1488 -74 L1484 -75 L1481 -75 L1477 -76 L1474 -78 L1471 -79 L1468 -81 L1465 -84 L1463 -86 L1460 -89 L1459 -91ZM2123 -633 L2148 -582 L2174 -530 L2200 -477 L2226 -422 L2252 -367 L2278 -309 L2303 -251 L2329 -191 L2355 -130 L2381 -68 L2407 -4 L2433 61 L2450 51 L2469 39 L2490 26 L2513 13 L2526 5 L2523 4 L2520 3 L2517 1 L2514 -1 L2511 -3 L2508 -5 L2506 -8 L2504 -11 L2502 -14 L2501 -17 L2475 -82 L2449 -146 L2423 -208 L2398 -269 L2372 -328 L2346 -386 L2321 -443 L2295 -499 L2269 -553 L2244 -606 L2218 -658 L2208 -676 L2106 -627 L2105 -625ZM2755 -932 L2753 -934 L2553 -840 L2555 -837ZM2283 -989 L2293 -976 L2303 -963 L2313 -951 L2320 -942 L2329 -948 L2360 -966 L2390 -984 L2408 -996 L2398 -1006 L2397 -1007 L2387 -1018 L2386 -1020 L2376 -1032 L2376 -1033 L2366 -1046 L2365 -1047 L2356 -1060 L2355 -1061 L2346 -1075 L2345 -1076 L2345 -1076 L2279 -996ZM233 -909 L194 -888 L154 -868 L165 -843 L176 -819 L186 -794 L194 -768 L202 -743 L208 -717 L214 -691 L218 -664 L222 -637 L225 -610 L226 -583 L227 -555 L226 -528 L225 -500 L224 -472 L222 -444 L219 -415 L216 -387 L213 -359 L208 -330 L204 -302 L198 -273 L192 -244 L186 -215 L179 -187 L171 -158 L164 -130 L156 -102 L149 -74 L140 -46 L132 -19 L124 8 L115 35 L106 62 L97 88 L87 114 L77 140 L66 165 L55 190 L45 208 L57 193 L72 171 L86 150 L100 127 L112 105 L124 82 L135 58 L145 34 L155 9 L164 -16 L173 -42 L182 -69 L191 -95 L199 -122 L208 -149 L216 -176 L223 -204 L231 -231 L238 -259 L245 -288 L252 -316 L259 -344 L264 -372 L270 -400 L274 -428 L279 -456 L282 -484 L285 -511 L288 -539 L290 -566 L291 -593 L292 -620 L292 -647 L292 -674 L290 -700 L288 -725 L284 -751 L280 -776 L275 -800 L269 -825 L261 -849 L253 -873 L244 -897 L238 -911ZM-32 328 L-49 350 L-67 371 L-86 391 L-81 395 L-75 398 L-69 402 L-63 406 L-57 410 L-50 414 L-44 418 L-38 422 L-31 427 L-24 431 L-17 436 L-10 441 L14 417 L38 392 L58 370 L57 370 L51 365 L44 361 L38 356 L32 352 L26 348 L20 345 L14 341 L8 337 L3 334 L-3 331 L-6 329 L-9 326 L-11 324 L-14 321 L-16 318 L-17 315 L-19 311 L-19 311ZM902 -544 L901 -502 L901 -462 L900 -423 L899 -385 L898 -349 L896 -314 L894 -281 L892 -248 L889 -217 L886 -188 L883 -160 L880 -133 L1109 61 L1168 -20 L958 -197 L955 -199 L953 -202 L951 -205 L949 -208 L948 -212 L946 -215 L946 -219 L945 -222 L945 -226 L945 -230 L949 -256 L952 -284 L955 -313 L957 -343 L960 -375 L962 -408 L963 -443 L965 -479 L966 -516 L967 -555 L967 -595 L967 -599 L663 -599 L659 -573 L659 -572 L655 -544ZM-1 -1140 L-45 -1105 L-46 -1103 L-46 -1101 L-46 -1098 L-46 -1095 L-45 -1091 L-45 -1087 L-45 -1082 L-44 -1077 L-43 -1072 L-42 -1066 L-41 -1059 L-40 -1052 L-39 -1045 L-38 -1039 L-37 -1033 L-36 -1028 L-35 -1023 L-35 -1018 L-34 -1014 L-34 -1010 L-33 -1007 L-33 -1004 L-33 -1002 L-33 -1000 L-1 -1025 L32 -1049 L51 -1060 L50 -1060 L47 -1062 L44 -1065 L42 -1067 L39 -1070 L37 -1073 L36 -1076 L34 -1080 L33 -1083 L33 -1087 L32 -1090 L32 -1094 L32 -1095 L32 -1095 L32 -1097 L32 -1099 L32 -1102 L31 -1106 L31 -1110 L30 -1114 L29 -1120 L28 -1125 L27 -1131 L26 -1138 L25 -1145 L25 -1145 L24 -1152 L24 -1152 L23 -1158 L23 -1158 L23 -1159ZM171 -1120 L207 -1132 L244 -1142 L281 -1150 L319 -1155 L358 -1159 L397 -1160 L413 -1160 L429 -1159 L445 -1158 L461 -1157 L478 -1155 L494 -1153 L511 -1151 L519 -1150 L540 -1163 L575 -1186 L606 -1207 L592 -1209 L576 -1211 L560 -1212 L545 -1214 L530 -1214 L514 -1215 L499 -1215 L462 -1214 L426 -1211 L390 -1206 L355 -1198 L320 -1189 L286 -1177 L253 -1164 L220 -1148 L187 -1130 L170 -1119ZM742 -1079 L760 -1070 L778 -1061 L795 -1052 L812 -1042 L829 -1032 L846 -1021 L863 -1010 L880 -999 L897 -988 L896 -982 L896 -976 L896 -971 L896 -965 L896 -960 L896 -955 L896 -950 L896 -945 L896 -940 L896 -935 L896 -930 L896 -925 L896 -920 L896 -914 L896 -908 L963 -908 L962 -924 L962 -940 L962 -954 L962 -967 L961 -978 L961 -978 L961 -989 L961 -989 L961 -998 L961 -998 L961 -1005 L961 -1006 L961 -1012 L961 -1012 L961 -1017 L961 -1018 L961 -1022 L961 -1022 L961 -1027 L961 -1027 L961 -1032 L961 -1032 L961 -1037 L961 -1037 L961 -1042 L961 -1042 L961 -1047 L961 -1048 L961 -1053 L961 -1053 L962 -1058 L962 -1058 L962 -1061 L946 -1072 L929 -1082 L913 -1092 L896 -1102 L880 -1111 L863 -1121 L846 -1129 L829 -1138 L812 -1146 L797 -1153 L792 -1149 L782 -1140 L773 -1132 L764 -1123 L756 -1114 L748 -1104 L741 -1095 L734 -1086 L732 -1084Z\"/><path fill=\"#f2f5f9\" d=\"M697 -1226Q595 -1252 499 -1252Q263 -1252 69 -1092Q70 -1102 62.5 -1144.5Q55 -1187 57 -1197Q320 -1413 629 -1535Q1033 -1415 1372 -1205Q1350 -1169 1317.0 -1142.0Q1284 -1115 1262 -1080Q1255 -1061 1255 -1026V-972L1262 -436Q1265 -371 1286 -355L1387 -272L1211 -31L982 -225Q1004 -382 1004 -636H630Q592 -282 413.5 -36.0Q235 210 92 349Q50 319 16 299Q132 178 189.5 22.0Q247 -134 288.0 -307.5Q329 -481 329.0 -647.5Q329 -814 256 -960Q499 -1081 697 -1226ZM1000 -871Q998 -990 998.0 -1017.5Q998 -1045 999 -1080Q900 -1150 791 -1195Q720 -1142 683.0 -1073.5Q646 -1005 639 -871ZM1497.9 -1200Q1516.9 -1268 1586.9 -1535Q1730.9 -1524 1875.4 -1512.0Q2019.9 -1500 2164.9 -1489Q2090.9 -1360 2078.4 -1253.5Q2065.9 -1147 2065.9 -1086.5Q2065.9 -1026 2067.9 -991Q2170.9 -1097 2270.9 -1239.0Q2370.9 -1381 2470.9 -1535Q2668.9 -1223 2856.9 -1024L2501.9 -856L2854.9 -224Q2634.9 -92 2534.9 -31Q2379.9 -425 2224.9 -725L2071.9 -652Q2062.9 -585 2041.9 -528.5Q2020.9 -472 1965.9 -431.5Q1910.9 -391 1866.9 -345Q1934.9 -322 1975.9 -322Q2084.9 -322 2192.9 -342L2022.9 -31Q1802.9 -95 1489.9 -111Q1692.9 -289 1732.4 -590.5Q1771.9 -892 1771.9 -1209Q1771.9 -1217 1771.9 -1225Q1752.9 -1226 1720.9 -1226Q1671.9 -1226 1497.9 -1200ZM2077.9 -780Q2294.9 -875 2467.9 -991Q2400.9 -1044 2349.9 -1141L2076.9 -807Q2076.9 -790 2077.9 -780Z\"/></svg></svg>"}

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

LOGO = {"hirlap": LOGO_HIRLAP, "nemzeti": LOGO_NEMZETI, "got": LOGO_GOT}
IKON_LINKEK = {"hirlap": IKON_HIRLAP, "nemzeti": IKON_NEMZETI, "got": IKON_GOT}


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
        # a mentett hírekre is (pl. egy korábban tévesen frissnek látszó, újra kiadott cikkre)
        h["ido"] = webcim_korlat(h["link"], datetime.fromisoformat(h["ido"])).isoformat()
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
