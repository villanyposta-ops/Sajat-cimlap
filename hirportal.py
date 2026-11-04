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
    '<link rel="apple-touch-icon" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAALQAAAC0CAIAAACyr5FlAAAQAElEQVR4AexdCVxN2R+nRy9ERYVQJEok+6CkxVYjmhjrMGMsYxuy78PY9yVrYxdZsxZK9n1fU8hShEoSlRL+/+97p27XW+6793Xf69Gdz2+Oc8/5ne13vu93fud3zr3pmVd1FEiQgEIJ6BUR/hMkoEQCAjiUCEZILlJEAIeAAqUSEMChVDRChgAOAQNKJSCAQ6lohAwBHGpgoLAUEcBRWGZajXEK4FBDaIWliACOwjLTaoxTAIcaQissRQRwFJaZVmOcAjjUEFphKSKAo7DMtBrj5BUcarQvFNFhCQjg0OHJKeiuCeAo6BnQ4fYFcOjw5BR01wRwFPQM6HD7Ajh0eHIKumsCOAp6BnS4fQEcBT05Oty+AA4dnpyC7poAjoKeAR1uXwCHDk9OQXdNAEdBz4AOty+AQ4cnp6C7JoCjoGdAh9sXwKHDk1PQXdNdcBS0ZIT2hXdlBQwol4CgOZTLptDnCOAo9BBQLgABHMplU+hzBHAUeggoF4AADuWyKfQ5Ajh+KAjwOxgBHPzK84eqTQDHDzWd/A5GAAe/8vyhahPA8UNNJ7+DEcDBrzx/qNoEcPxQ08nvYL4DcFSyqPhb918rlDfnd+Rca+vSqWOPrr6ebT2aNmlkV9OmvLmW+lPDxpprV/ni12lwNGncYOXSeaePHZg2ecy5EyF7d24a0Le3RcUK3Aef3xIlDAxmT580/Z/xyxfP2bpxVci+oPMnQ2Iir9y5ehrd2797y6b//JcsmPnPxNHDBvfr1fNXb682zs2b1rG3A7JLliyhdvNisX7AioX4bahdQ34K6ig4fLw9DwYHBm1a07a1GzW8unXsx44ceibi4L5dm4GSypUsqCxNRxo1rKewCUx8JYuKAIGzU1MAonfPLsOGDJg6cQyAsmmtP0AD6ABAgBHAFLI3CMBasWTu9Knj6eNSWDNJHDvib8sqlcePGYZWSIo2Q90Ch1GZ0oMH9LlwKnTh3H/t7WyVCcKhdi2g5FT4fkj/r369tYCS5k0bK+sMy3QsQ3a2NliS2rVx79HF9/37DyoLNqjv+HuvrmAzEIsXzf0XES2TroAD44dCPns8ZOTwQeZmZiylgJ/smBFDgZLtmwO82rViWYolG7QCDB10DPzNmzVByBfdux998fI15tqwoCyenwcIqK5uv/7CXIT3XJ0AB2y9E2F7oZAxH+qNsHGj+v6LZkPlAGGmpuXUq0SmVOOG9TzbuBkYiMuUKV27llI1JlOKzePyVWtVso32G1LZ4pt1c8KY4VA/KgvyyFDA4HBxbha6bztsPfbagmHwqAQIu3T6yLKFs/BTY+Bkk1WlUsW0tLR3qe+deFUbz57FHT95lrkDsK769O4uw1OqVMn5s/+RSdToY4GBw8zUdO2qxRsCltnWrM77CH/2bL1jy3/BOzZ6uLVQr/JyZU2KFy8e/yoBxZvl2+BAJRT5q1IbJUsYLF04k+KnRwBTbKfpKRqNFww4unfxDQvZ6dbSWaNjc3SoHbBiUUjwNjgnuDZU3twURV4nJCF04hUcB0PDUCcDTRjrhx2KMoaJ40ZUq2apLJfXdK3fPreyqrI7aP2MqePLlC7N70iU1WZnVwPOibBDuzp6eyrjkU+HKYrEV68S4FaxsqyCOC909vwl5nrgHcEvh4EHBvLyRXMZGHjM0qrmwDb1+OHg+o4OPA6AZVXVratiN3j8SPCvvh3YFLGoWB67zY+ZmdDkbPhZ8jBvUrCTRydVVoUt8cSxfirZ8s+gJXDg93dgTyC2qfnvcX5qgA6YM2PyiaN727VxZ6jH3MwUBsfL1xKDw6n5TwycXLMuMe5g58+eVq6cCZs6//y9h3Pzpmw488OjDXBgSxKybxu/u8H8jBkrOtyUcMbDmaawnkoWEg/9S6k16uLM2xxkZHy8c+++whaR6OvTnpP5DB1TtiwrJKFy9Ujj4Bg/ahi2JMwWRmZW1vP4lzdu3QmPOLVtx57lq9dvCtwRvP9Q2LGT5y5cfhYbp97YmEthuwg3PLwjVeTc8LngeA0vLXPPmZuQyb19N1ImhXqsZFFx6sTR1CObCHTM/Fma3dlqEBzmZma7tq3r9+dvCoeanJxy+GjE5GlzWrfvXKdBC7c2Pl169hs8fOzUGfOXrQiYOXfxuEkzhviN+6P/3628OtvUbtLet+foCdNQSmFtaifCr3oyfP+4UX/D00UqgWvSzLRcQkJSdvbnFs7frCkZHzNv3Yk8cercvgOhgO+q/zYCwQxTTiqkwsSkN1RcJrJ43nS4MWQSVT66ujjhjEklm9oMmgIHVsTQfdsa1Ksr07P09Iw16za38f71J5e2w0ZNPBp+XK9o0fqOdVxdmnm2cfNq4/7rLz/36dW1s8/PeAS5tWwuEolQSfFixQb2+x0/F8R5p/5/9jp+ZC+WG9RM9imvExMRF4sNNm7ZPn3Owj//Gu7e9pe6jVw6d+8zYMjIMRP/BXwXL1sNBC/xDwAnG0pMlGyM5TkBzYYNHOXT2aTgjMm52TcIZlOKJY+mwDF5/AgTE2N6J1Lff/Bftc6llffCJSvfJqc0rO/QqaPXLx08XZya1qtbu5qVJWalfHkzQ0NDlCpd2hCPX79+vXz15pcvX8aMHIolwKZ6NWQRAsj+6D/Mb8ykh4+ekJR8hqfPXoh7/gKVEA9HWRMTxP1X/jdr3pItW3edOXfxTXIyumRTvWq9urWbNJJ41oFd8BgY6CNkQwo1B+wMQJNNcWU8yxbPll8ZlTFzSucfHMWKFUMPXklNfURAKSnvlvivASwga6gB95ZOwIRVlcoZHz++Tkh8Fvf87r2oe5HRiIPeJL9FkbT09NPnLoVFnLauahUesvuvvr2RSFFyckq33gPOXbgUcviYl0+3v4aOgranctWI3Lh5e9LUWaQgEIBIlcoW6Gej+nVbu7v06OIDZda1c0egoUXzn6DnYFyDDaSnpycWi8HPhuTBAVNj8bwZbMoy8GADvG7N0hIGBgw86mXxDA7ogJZS8/7xk2ekQxEnzrTy6rQyYINYX79xA0f88qIePNoYuHPvwSNHwk+CTp6+cO3mnas3biMOOnT4GHJ37w2Jf/lqwhg/HMpbV7MiVZEQ6b/+1jcq+qFzs8a+HTwdatvhqALafvjoSW/eJBMeTiF2EH8O9MvK+oRScF3DcY4IyMqyskOdWpUrVSTTD7wCu4SQSwi6TV+freZIffeelKLClcvmyZsaj2KeUAwsI/DiLJgzjSUzezY+weHiJPlVGRuVQfNPnsYi/Hf2goF/j8aCAlxjdQACrly79eq1ZDlHLgPVc6wTujeo7x895Hl+7/93XNwLIKOGjbWRUZlGDRwBEQAo9MgxD69OMBHkizCkXLh09bc+g9LS0glP+fLmyW9Toh/G3Lx979KV60fCTwYfOAywgoBXPBL69EmCJFIELksSURl+zMqk8/w7ZWwdezt6CuITpswcMmIcIlypXRt3LL5cSzHz8wYOKFvgF40Rs/9+9IP2v/QI3LYbKSC4GgERRFSSWKw/fvTwPUEbqlZVcIKwYMmKZ8/iatvbAhlUVYAI1JW3V2sYrTARPDt2u3rtJpXLEImNe479EdwPFM/TZ3EHQ8MvXr6OdSrqQQz0BPykVC4VwWpCxYvrS5ZR6pEhkvkxDxw9unbq2a2zDPPmrTt37z345EmsysM5mYLkEYuv/FkuyVIv5A0cUAxUD8zNTG/dvoffH5XCMuLj7XksdE+/Pj0V8kMbBazbgqykpOR371IRoZNpubIwZRwd7KGWu//+1+Wr1+m5CuNzFiyjdIZCBmWJxaR21efPn8FgIOa82OMgcPo/surh/MUrM+YsKomFTSz2X7mWWpfRBHuaNG6ETwcv9vzMnLyBIyV3trKzs2EiMbcqn9ukcQP41xfO/ReOdvlckgLvAonAstt36CgsVnmINKjnACsSbNmfshEy0527UcwMzLkwOMBgIGZrcxB907RJIxwEoiCd4l7EDxs5AZhr4+HSvGkjZA0YOurNG4ltjjgnWjhnmhtPx928gYOoX2xSMG2PHj9lPx5ra6t1a5YEbVqDLQBzqUOHw+kMUCRo68atu/RExPVEkkEV1ZOEeGSgtPQ0hlyVWZ+/fAGPPuvdCuberqZNwIqFKEWn9PSMfoP8sOy6t2xuYmwMQ9i+Vs3Y2Oc9+wzERo/OyTK+fMkcy8qVWDIzsKmWIENhelbq+w/Yhhw9dgpDpaczxOHRmj51fPih3a4tnBjYSNaNW3dexL8kcXp4++592IwJSvxLdE75OH0plM9VmfLlsxQcxYur5CQMsJM2r1spvz3BPgt2hlPTxtjZEs6fGtUHRLCy9Oo7BKAhiSxDHEF07dkPqoglPwMbb+BAG5eu3EDIhgzE4mGD+50MO9Cjiy8bfvBcvX4LoUKC0jocduJ+9EN67v/+9z/6o8J40SJFFaYzJ0IBEIbPX6Q2hwFbP8fs6ZPweyBlqXDeouWnzpzHo4mJEUJCsGb0pJovNfX9o0ePSaLKMHj/IZxFwMSOjHqgkpkNA5/gYNMeeNp4uJ4Ik14nLsHBlIOaRVkGepfrRSCT9+Wr5GfNwK92lkiUI7RPWRKzxoA1OORbPBgatnZDIEk/cfo8MIF49MMYbJuxbxrU/4+zxw+xuQy7dfvulq07wpf/9Cmfh5Q540SftEampuXMWb98QPUq7nk8FWeOFJXqg69fvjKzIVdUTHJqgwgn0iuaI7QsqbdDX5+t5pBpBbv9kWOnUInYUR+NOLVnfyg20vXrORw/EjzKbzCVqzCCrVbA+i1NWrSdNnMBfIMKefKTmDPO/FTBtSw2He8/qH6lR6Za+CRkUpQ9Fi0qyfkstRYlMeX/FxOpA45ixYuRKrEvQwSOGYRc6e3blP6DRsmUwhbdsFSpFUvmBm5YZcV4N/Fd6vtlKwJatPJesHgFlh71+iDTuvyj9sABJylsLhAcYnv2HpLvCnMK9kHMDM9fxBP35d3IaHCy2cqy2dGgKhmiIEU0h3oT02/QiATpwS+9cqwjYSG74eukJ8rEIYeZ8xY7u/28fPV6OGpxQlmsmIj4/mU48/+oDXDACMCZS+dffq5Zw7q6tRUe9x08zKnr8FT+1KQhMzk41LayqgKys6sJTmPjPPtOWVuU9aCMQWE6JoOkE82hxm2g0eOn4kCHVELCrp19Th3bj3UEPjCSIh8+i40bP3lmCw/vTVt2YEE0My2LBe7DhzQNIQMd0AY4jI1Kw7kOTKA9kUhkXdUyKvqh/O8GucoI55/bNq7mRM1ZvE8g0stZIJS1qzCdDARZnz5JdivmZqaIs6d1G7buP3SE4u/exfdMxMFZ/06Ueb+NYiAR/1XrWnl13rPvII6uzMzKwaJKevMWOpjkaijUBjjeJKck0c5LLatI/DMRJ85oaEjsqxXl7jvYFwEnTnAQgqDVETK4dJErQxcvX5u7yJ8kdunU8WT4/hlTx7OpoUkjyWv+8JFAT8A00TQsSA+1AQ60dPdeTRSGwwAAEABJREFUnqO6YgXJZ0+On1TxSiBKaZpEahmkxfWLk45lZmayWbwIM0JsKAYNG4NIJx/vE2H74PaoInd9FbkKCU73li2aw8GoHViQPmgJHLHP47OyskiTUMum5Uzg8SSPBRjqqaU5xLn+8o9ZWeZmHF7afvo0tl1rD2iLebOmqOHenjBmuJZlpSVwYFRPY58jJFSubFns0WFmkseCCkVqgcNAnHPSht+xuSnbr0VgjM5OTefOnMxeW6AInWyqV+vRla1DmV5Q7bj2wEH3YhkbSy4ExXA5n1N7hAwFRXrq+DkozQG3lSkXzcHQE5ZZI4YNgkeAJXP+2bQHjoTEvIv5ZPv3OkHySln+x6B2DSL1NIfUX05WSXIbWe0OYCMKE5V9cRNjo8ED/2TPn09O7YEDBwfJb1NId8l5xPv3aeSxoEI9PXU0h4HU5vgovdbFaVmhDxMuzqXLA5w92g/xG4fliZ7FHO/Tu7s598MH5jqV5WoPHOgBtaHVlx5zf2ByooNd40T5Ojm1RBR7xsePKGXGfVkhsGjZusOKNetx6I8j5Y2B21EVSwI0x44cypI5n2xaBUdq7lfSikvBkVrQ4FBvtwJnA4SelpaB0JyLBwywWOK/hsACPw9Xl2bdu/gYlSm9YXMQJ+Xh08FT5cUo9C3/pFVwYIml9/h/qs9Nc9jDjp30X7WWE527cDmnsPJ/RCLOHlIRykgvkKalSy6sm3HRHLPnL10ZsOF/X796uDr7eLerZiW5Qe3oYA/lsSVol/JuKsj5h+OLtQqqYJGkVXCkp0t+bejV/4pIbuKUKS15uQ2PKmn9pm3+K9dyoouXr6qsVk9PeoCrko/GQNQGEtKk4LBiPDsFG53uS+/gZHzMpCpBbnXrqoaGpdZuCEzPFQ4SVVLDBo6t3F1UsuWTQavgyJJegECPsdYiLC198xERlRTz5ClOpDzbuLEhFyfJu6MiPdXGZjHumqNkiZzPEX/4kI5JVdlzOkP0g0fk8ca3914dattBeQRuz3mNg/CoDLXgE9MqOHBcRMacLb3UX8aoNHlkDhOTkiA7I6MyOH5jQ6blyqJC6uwUcWUkEnEevqFhSVLbu9RU25o2JM4mPH/xCsX2Iv4VtXFDol1NG7FYH9qRk2scSqtXjy4orjniLJ38dIW6ukcuW5Rh91mwRzGSu+xwusOjykDUVojcHtUTqdYcenqch29YqhQkgH3sp0/Zjg61EWdJMJvonLfvfPOtjlq2NVJS3gXt2EvnURkfNqS/oaGkPyo51WPgLB31miGlMMEkkpEp2QeWZmdzPH4iee32+YuX5C6PsvDM+RwL9MtXiaFLtUVaVBiKWABIpmCpkhLN8TZF4rCpywkcESfoVeGwCeqQSrG3q4EOb9wSRKWwicAnNuQvuk+MTSEOPFoFBzUZ6ekScJAXJ1V2NubxE5U8YNAj1wOLFCEvHBQvlnN2iixlJBJxHn6pUhKbI0n6ulHdOrWU1SyTvmbd5uRkCZ7o6bfu5ikPsVhsW7M69OK+A6F0HpXx/n/2qsz6aFdlbTIMnKUjU57TI+V0Ih6kMuw0B6dXpNAf8iJaiRKq7/2K9FQvPaiQTsSIxixCn1tWqUzPUhYHLFauXi+f+/hJbFq6ZD9MsurY2yKyau1GhJxolKp7yJxqozNrFxzFc/wKOJJFJ2BSIVRJ92h3QRiYixbN2ZdmZ0suaBEPPQM/svS4aw5oO5wDvHqd2EDuo0WoUJ4+fEjrO3C4MkuT/j4m9kG2Nao/fRp38vQ5+XoYUry92mjIJ6ZVcIhzP2UBcGB/zzBgKgtGvjLJUjwkoqeXMxayFRKzeGOguNSdRYqzCcuaGIPtufTFO3dX1Z9fTnmX2uOPgffuSy48o6A8PXj0GLYtlV63juSLDAHrJS+LU4lsItOmjGXDxpUnR6Bci6nHT6w5lIUf3c62BiIq6cy5Cyp5CIOeXo7m+Cp9KaFErkOC5CoMK1QorzBdWSLZJD+LfQEG347tETIQMNSpe5+ob9/Dk+e/dz/v7TRDQ8OqVlWuXb91m2aOyBeRT6nv6NCO8cuq8kXYpGgXHFJrLj0jA5qZpZPg9NmLbIYBHlj7CEFEcxiWkmwr8MhA5DYrA4NMlplpOZzUP4t93qF9u5IlJZapDAP1ePnqdZ/OveLiJDCiEhVGoh/GfMr1DYKhrtTIhc8DcU40bvQwTvxsmLUKDiyr6BNxSNjVVK05EhITY1hfCKKWlSzpB5yMjFS/mgAPtDj3Whc6ppIsLMo/lH6fjvkb2XBp9PxjELSjygrBgN/J/egczykey5U1KW9udvhoBNdPWFWpZIHTfNTAI2kVHLDw0XVY7wixc0PITCdOcTDNRHo5Y8nMlHxAx8REctksp34l/5iZmo4cpuKVQ6oojk8NS5WKjHpoWblSs58aUekykZ179g/xk/0wiwyPzGNk1ANAhEqsXasm4psCdyDkRMOHDDAyUj1q9nXmCJR9gfxwkq5Dc8Dmpz43wFDhmXNs1xRUUjz3UniW9LMt5uxuxPT9o8fyxXPIFQ1UwkBYg6IePIJ13MnXm4HNraXTwtnT2nu1tqtpAzyB09jYCK4I/BhgGXi4tRjY7/d5s6bsDFyLdpEL+vQpO/ph3qv0VpaVcTK3fdfezNwr2eBhQ/jtDR3Ylw0nSx7tgQM2AVlWEhLf1Kyh+g/wQC1zAoe+9I4Ihg2zAGJChCV5tvXYuW1thfLmzPxVKlvcvnPfxMSY+Q95ApQ+Hb2WLpgVsi/o+sXjMZFXrp0/dip8f+i+7buD1gesWDR6xJBOPt44Vl2zdhPVYuS3Oxp72xoY/gHau08UJ3MEKwtL7wtzPSRXe+AwMTFCkwkJSXBSNawv+2VjZMlQ0M7gLKn1IJOu7BHgI1kZGR/NpX9KhzyyCe3tbA8Fb63nWEcZM3QAHF9QG5PHj0RcGRv79AMhR7GaUPwZHzOf0j7xXrOGtUgk2rhlO8XAPsLjPTHtgcNcemkqMUlyzdiJxReZtwbtYS8RcOrnLivYDVUwV6EGwC9DUAnzZ02VSaQesabcvhvl1KxJx/btqMT8RJb4r5Epfj8qzyzV19evUb0qjHFO149JhdjTNlT3Y9mkBirUHjjMTCUn6QlScKh8kfVI2HFsVahesolAoGDLzs7GEk6AiEdONHz0JIX8BmLxy1cJOLKZMW2CQgauies3Bb2IfylTCj+blHfvqESc0yK+eStnsxSlpkyQ/bgDEtUg7YGjnPRr4lDOLZxU/wUTrtfmMPJS0vNS4nCkn1kgiw3NmLtImcMqMysr+W3KyOGDsE9hUxUzz/sPH1b9t0EhT+T9vC9XwYwFxCNOnIl/+UohM0Mi7F9eLA8tgQPuBOxQXr1OwMGHU1MVf8AX6pTlV2bpAioh/YgUWoGfADLtO9APCzmdgSF+6sz5zYE7GRjcWjoPHtCHgYF91qqAjamp7xXyP34aC2uayrKvJXEFBQZxuyG2K/iAh6dvnPQj/1RV6kSKFNESOCpKHdWvXieil82bN0bIQOs2bmPIVZZVMtdl6dXW3bqa1emzFzp3/4PN2pT05s3Icf8oqxbpDerVXbtqMSL5p9cJiYHK7xLDVMdpC9VKNSvLkiUMgveHUCnMkagHD31+7T3xn1nKwMdcXD5XS+CoUqki2n4R/6p0aUNsDRBXRk+exu7Zd1BZLkN6SdphSkvnpo0bOMKb2aFzL5XnFENHTKDfu5FpooaN9YaAZTKJaj8uXLIyi3ELFkXzlqIVe7uaKSnvDoaGIc5A71Lf/zN9nrfvb/e+3RIzFGGTpSVwWFhUwCYi+W2Ka4vmzN0aN2k6M4PCXNiMMul1att5uDqnpn7o1K0PvNEyudSj/8r/rt+4TT3KRGBkBK5fxclrIlMD/fHBw8f0z7bQs6g4lsInz/K+CIg9LbLgEEOojPBbauXVCTt/ZQxqp2sDHCbGRvhZx0pPodzdmC7U7z0QcvO27BeJ2YwtMysr/PhpuhMapbD//LmdB5abYaMmLl62GikydOXqDf9V62QS6Y9jR/2d+SkznctLA/TiMvE585fKpCh8jH4QQ6WLxeJqVS1hgT1+8oxKpCJYR3DwO37yTPnPfFM8+YloAxxQG+hibFw8ft8eri0QV0gw4+csyPnqjUIG5sT4l68PHT6WRrtbBf6yJsY+7duamZVb9d/GgX+PhhcLiYTQnN+YvC89kkQqhGGL+NAR411b+zg2cbWp3aRpS892Hbp27dV/xJjJ8xYt37p994lT56KjH8GVCU6VdO78pXMXc265MjMnJEpu21M8tjWsEQ/aFYyQorS09GkzF2AdkbmoTDHwEtEKOCqUT0tLgy3m7tYCv2Nl/Z67wB/rq7JcNulYeg+GhkO4dGb8+Nq3a1Xd2gpbmE7d8kzUEWOmJCYp/qNr9rVqduro5dnGzdWlWZNG9Wrb2+Lnq1e0KHZbWIMOHQ5fuyEQczNgyMj2nXo2bObRoJlH99//mjpj/rYde/ArT8n9GwH0bsxewEptkCLRtK8Ww5aHobZ3f97d0uD9h9w9fYFOwqy5UBvgqFDejKyj7T3bKBvJ3cgo7MGU5bJPz8r6dDjsxKOYJzJFXJzyTFT4rTdu2Y7tjAwPebStUf2nRvURx2kL9gu1a9k2aVjPtUUzrFBdfL379OpKqFvnjkgBehrWdzAxKnPnbiSQAXwAJY2dWru3/WX0hGk4oSXLwb4DobCOUSdLQv/hc6OYbW2sP3xIAyZu3bmH/ci4STPe5n6vgOLRRETj4LCoWB6nHs9iX8Csa9PKVeEYoO39xkxWmKVe4rmLV6/JmZkwUVu5tUhOTunSs++seUsU1mxTvWpz6V+0UJhLEkkIt4q5mSnQU7eOvburc48uvwAu2EVjl4QhJ6ek7D94eNLU2W29uzRs3mqmkuZIVfIhnLwxT/KMDOyYwDN73tLO3f/kdz+CahlI4+DAYSbWFOxTWik3RcdMmKby0+YMY1CYdTcyOuLk2ezsbHouuoFHaBeECinm8bONgTtBO4MPhhyNOHnmwpXrt27evge6H/2QFFdYEImAC/xvgGDbVq7QMb9184WNhfTU1PcgRDjR/ag8b6mBgbhR/bosjRtOrTAzaxwc2DI8eiz5EbT/WfGasn3X3qPh37zww9xj9rnPX7wMPXqcMlFxeIE5ZlkcR7tJScnPYp9H3n9w604k6PLVmzBogJs9+0OPHjt5/uJVeFCwaiTRvqJJrxxlMzleyKAXh/2EE2ySAohrHxloWrPgqGRRwbBUKezvLS0ruyr6oyrwlE/5dy76oSGCbXgo9BhgAfmeYn0dlbkzWP7h6n0Y8+TGrXtnzl8OORKxdUcwdi5RDx5BtVC2wo3b95jrUZn7IOYxeOAz3XvgMNeXd1Aw/6SX/yoYaqhmVSXuefzHzMxhg/vLs8HhM2AoP+eH8pVTKfj5Qn+ERZxK58ldQdVMRXBgFPs8/tKVG1AtW4L2IDx+8ix0D8WgXgSGGnZGF02oG08AAAOBSURBVC5dg6DUqyGfpTQLDqwpUMhQGz7envIdHTVuCpv72fIF1UghLzCqUVCNItAfcS9kT+TVqAdK6I3cS5Rq1KN2EQ2Cw7KyxcvXCZDUUEUfwFu9dtOx46fV7rdQUAsS0CA4rK2trt24Y1Gxgvz7P+cvXlm0dJUWhic0kR8J8AiOb7pRwsAAi25aWvrQQbL3oV++eg239DfcwoNOSkBT4LCwKH/95t36jg5dOnWkDxzGab9BfjD46YlCXDcloClwvHyZgBOB1f7zZYY91G88J0eyTHHhUZsS0BQ4oCFWLJljalqOPpjJ0+YoO9GgswlxHZGApsAxdGDfpk2+eWdw3cZtO3bv05FhC91gIwGNgAOmht/ff9GbD484NXchb5ft6DULcc1JgH9wGBsbyZgal65cGzxcI18X0ZxchJohAf7B0aF9W7qpgdOp/oNGoiWBFElAp9P4BIdzs8Zwb2zZuuu3PoNfvJT4j7Ex6TNgGIxTnZaB0DklEuANHA3q1alhY+3b0bO6tRXWkXbtu8JB3rvfkPe5fylBSQeEZN2VAD/gsLKsTD7nq6+v7+LU1KutO85C4SB/o+Sug+7KQ+gZTQI8gANLSUvnb15/fRHP+fVOWpeEqK5IgAdwwKQIizidnpFBxnT52s077L4cSviFUGclwAM4MLaExKRdwYcioyQ36uiXH5El0PcrAX7AQcZ/5ZrkLi6JC+EPIAE+wcGnOIS6dEACAjh0YBJ0tQsCOHR1ZnSgXwI4dGASdLULAjh0dWZ0oF8COHRgEnS1CwI4dHVmdKBfAjh0YBL46gLf9Qjg4FuiP1B9Ajh+oMnkeygCOPiW6A9UnwCOH2gy+R6KAA6+JfoD1SeA4weaTL6HIoCDb4n+QPUVCnD8QPOl1aEI4NCquL+vxgRwfF/zpdXeCuDQqri/r8YEcHxf86XV3grg0Kq4v6/GBHB8X/Ol1d4K4NCquL+jxtBVARwQgkCKJSCAQ7FchFRIQAAHhCCQYgkI4FAsFyEVEhDAASEIpFgCAjgUy0VIhQQEcEAIAimWgAAOxXJRnlqIcgRwFKLJ5jpUARxcJVaI+AVwFKLJ5jpUARxcJVaI+AVwFKLJ5jpUARxcJVaI+AVwFKLJ5jpU/sDBtWWBX+clIIBD56eo4DoogKPgZK/zLQvg0PkpKrgOCuAoONnrfMsCOHR+igqug/8HAAD//zufspwAAAAGSURBVAMAw1PCIQnFH/0AAAAASUVORK5CYII=">'
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

# ── Telepíthető webalkalmazás (PWA) ───────────────────────────────
# A program az index.html mellé kiírja az app leíróját, ikonjait és a háttérszkriptet (service worker);
# így a lap Windowson (Edge/Chrome) és Androidon „alkalmazásként” telepíthető, és térerő nélkül is
# megnyílik a legutóbb letöltött címlap.

APP_IKONOK = {"ikon-192.png": "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", "ikon-512.png": "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", "ikon-maskable.png": "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"}

SW_JS = r"""// A Kezdőlap – háttérszkript: a címlapot mindig a hálózatról kéri, de elmenti, hogy offline is megnyíljon
const TAR = 'kezdolap-v1';
const ALAP = ['./', 'manifest.webmanifest', 'ikon-192.png', 'ikon-512.png', 'ikon-maskable.png'];
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
            {"src": "ikon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "ikon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "ikon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    (mappa / "manifest.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    (mappa / "sw.js").write_text(SW_JS, "utf-8")
    for nev, adat in APP_IKONOK.items():
        (mappa / nev).write_bytes(base64.b64decode(adat))
    (mappa / "frissites.json").write_text(json.dumps({"ido": most}), "utf-8")


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
    pwa_fajlok(Path(a.kimenet).parent, MOST.isoformat())
    print(f"{len(uj)} letöltött, {len(ervenyes)} megőrzött hír → {a.kimenet}")
    for x in hibak:
        print("  hiba:", x)


if __name__ == "__main__":
    main()
