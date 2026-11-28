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
import math
from collections import Counter
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
    "hirlap": {"csalad": "Libre Franklin", "url": "Libre+Franklin:wght@400;500;700;800", "suly": 700, "nagy": 800, "ter": "-.005em",
               # klasszikus újsággroteszk (a Franklin Gothic utóda): széles, nyitott betűk, kis méretben is tiszta
               "lista": "Libre Franklin", "lista_url": "",   # a címbetűvel azonos család: egy letöltés
               "lista_tartalek": "'Franklin Gothic Medium','Arial',ui-sans-serif,system-ui,sans-serif", "lista_suly": 500},
    "nemzeti": {"csalad": "Merriweather", "url": "Merriweather:wght@400;700;900", "suly": 700, "nagy": 900, "ter": "-.005em",
                "tartalek": "Georgia,'Times New Roman',serif",
                "lista": "Source Serif 4", "lista_url": "Source+Serif+4:opsz,wght@8..60,400..700",
                "lista_tartalek": "Georgia,'Times New Roman',serif"},
    "nepszava": {"csalad": "Roboto Serif", "url": "Roboto+Serif:opsz,wdth,wght@8..144,50..150,100..900", "suly": 700, "nagy": 750,
                 "ter": "-.005em", "nyujt": "70%", "tartalek": "Georgia,'Times New Roman',serif",
                 "lista": "Roboto Serif", "lista_nyujt": "85%",   # ugyanaz a család, kis méretre hangolt szélességgel
                 "lista_tartalek": "Georgia,'Times New Roman',serif"},
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
     "hatter_rovatok": {"magyar-hang-plusz": "Plusz", "vendegoldal": "Vendégoldal", "budaihang": "Budai Hang",
                        "publicisztika": "Publicisztika"}},
    {"id": "atlatszo", "nev": "Átlátszó",    "szin": "#65a30d", "feeds": ["https://atlatszo.hu/feed/"],
     "hatter": True},            # a „Háttér” rovatba kerül, kimarad a hírversenyből és az összevonásból
    {"id": "valasz", "nev": "Válasz Online", "rovid": "Válasz", "szin": "#a16207", "feeds": ["https://www.valaszonline.hu/feed/"],
     "hatter": True},
    {"id": "nszo",   "nev": "Népszabadság",  "szin": "#991b1b", "feeds": ["https://nszo.hu/feed/"],
     "kep_oldalrol": True},   # a hírfolyamban többnyire nincs kép: az új cikkeknél a cikkoldalról (og:image) olvassuk ki
    {"id": "infostart", "nev": "Infostart", "szin": "#4f46e5", "feeds": ["https://infostart.hu/24ora/rss/"]},
    {"id": "euronews", "nev": "Euronews",    "szin": "#0369a1",
     "feeds": ["https://hu.euronews.com/rss?level=theme&name=news"], "alap": "kulfold",
     "alap_elony": 2},   # a kulcsszavas pontozásban ennyi előnnyel indul az alapértelmezett rovat
    # BBC News Magyarul: a BBC World Service nyelvi oldalainak szokásos csatornája; ha nem létezik vagy
    # nem elérhető, a Google Hírek tartaléka lép be (ott nincs bevezető és kép)
    {"id": "bbc", "nev": "BBC News Magyarul", "rovid": "BBC", "szin": "#a21caf",
     "feeds": ["https://feeds.bbci.co.uk/magyarul/rss.xml"],
     "tartalek": "https://news.google.com/rss/search?q=site:bbc.com/magyarul+when:2d&hl=hu&gl=HU&ceid=HU:hu",
     "alap": "kulfold", "alap_elony": 1, "hatter": True},

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
MERES_NYUGAT_LON = 18.6     # a mért adatok hasábjai: ettől nyugatra „Nyugat”, MERES_KELET_LON-tól keletre „Kelet”
MERES_KELET_LON = 20.05      # (a Kékestető 20,01°, Szeged 20,09° keleti hosszúságon fekszik)
MERES_HEGYI_M = 500        # ennél magasabban fekvő állomásnál a név mellett a magasság is látszik
MERES_ALLOMASOK = [
    ("Budapest", 44527),      # Pestszentlőrinc
    ("Békéscsaba", 66522),    # repülőtér
    ("Debrecen", 64711),      # repülőtér
    ("Győr", 24423),          # Pér repülőtér (Győrben nincs égképet jelentő állomás)
    ("Eger", 53215),
    ("Kékestető", 53101),     # az ország legmagasabb pontja, 1011 m
    ("Kecskemét", 46400),     # repülőtér
    ("Baja", 48101),          # Csillagvizsgáló
    ("Miskolc", 52744),       # Diósgyőr
    ("Nagy-Hideg-hegy", [33431, "Dobogókő"]),   # Börzsöny, 854 m; ha nem jelent, helyette a Dobogókő (Pilis) a saját nevével
    ("Nagykanizsa", 17809),
    ("Nyíregyháza", "Nyíregyháza repülőtér (észak)", "repülőtér"),   # szám helyett név is megadható; a 3. elem a kiírt rövid név
    ("Paks", 37402),
    ("Pécs", 39115),          # Pogány repülőtér (az Egyetem állomás 2026 novemberében elhallgatott)
    ("Sármellék", 26514),     # repülőtér, a Balaton nyugati végénél
    ("Siófok", 36100),
    ("Szolnok", 55507),       # repülőtér (égképet nem jelent, de az Alföld közepét lefedi)
    ("Szécsény", "Szécsény"),   # Nógrád, a szlovák határ közelében (Salgótarján környékén nincs automata állomás)
    ("Szeged", 58116),        # külterület
    ("Szombathely", 15310),
    ("Veszprém", "Szentkirályszabadja repülőtér", "Szentkirályszabadja"),
]
# csapadékradar: a HungaroMet kompozit radarképei (ötpercenként, PNG); az oszlopmaximum („refl2D”, CMax) változat
RADAR_MAPPA = "https://odp.met.hu/weather/radar/composite/"
RADAR_TIPUS = "refl2D"
RADAR_KEPEK = 12          # ennyi képből áll az animáció (12 × 5 perc = az elmúlt óra)
MERES_10P_URL = "https://odp.met.hu/weather/weather_reports/synoptic/hungary/10_minutes/csv/HABP_10M_SYNOP_LATEST.csv.zip"
MERES_ORAS_URL = "https://odp.met.hu/weather/weather_reports/synoptic/hungary/hourly/csv/HABP_1H_SYNOP_LATEST.csv.zip"

FO_HIREK_SZAMA = 11       # ennyi kerül a lap tetejére (1 nyitó + 4 képes + 6 kép nélküli)
FO_MAX_ORA = 8            # a kiemelt részbe ennél frissebb hír kerülhet (a hír legutóbbi cikke számít)
FEJLEMENY_ORA = 3         # ha egy régebbi témáról ennyi órán belül …
FEJLEMENY_LAP = 2         # … legalább ennyi lap írt, a téma a friss fejlemény idejével számít
NYITO_MAX_ORA = 3         # a nyitóhír ennél frissebb legyen; ha nincs ilyen, fokozatosan enged (5, 8, 12 óra)
FO_TARTALEK_ORA = 12      # ha (pl. hajnalban) nincs elég 8 óránál frissebb hír, legfeljebb eddig nyúl vissza
FO_STILUS = "arnyalt"     # a kiemelt rész háttere: "arnyalt" (a fejléc színének halvány árnyalata), "vilagos", "sotet"
FRISS_SAV_ORA = 3         # a dobozokból kiszorult, ennél frissebb hírek a rovat alján, nyitott sávban látszanak
FRISS_SAV_SZELES = ("belfold", "kulfold")   # ezekben a rovatokban …
FRISS_SAV_SZELES_ORA = 6  # … ennyi óránál frissebb kiszorult hír látszik nyitva a rovat alján …
FRISS_SAV_SZELES_MAX = 12 # … legfeljebb ennyi (három négyes sor)
FRISS_SAV_MAX = 30        # biztonsági felső korlát, rendes napon nem éri el; a többi a „További hírek” közé kerül
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
OSSZEVON_EGYESIT = 0.32   # utólag két csoport egybeolvad, ha a súlypontjuk (összesített szóanyaguk) hasonlósága legalább ennyi
OSSZEVON_KOZELI = 0.25    # két, egymáshoz időben közeli (OSSZEVON_KOZELI_ORA-n belüli) cikknél ennyi hasonlóság is elég
OSSZEVON_KOZELI_ORA = 2
OSSZEVON_KOZELI_MARAD = 0.13  # …de csak ha a legerősebb közös szó nélkül is marad ennyi hasonlóság
RITKA_NEV_MAX = 5         # ritkának számít a (legalább 6 betűs) név, ha legfeljebb ennyi cikk címében szerepel
KIEMELT_ORA = 4           # a rovat kiemelt dobozánál ennyi óránként ér egy lappal kevesebbet egy téma
FO_LEAD_MIN = 60          # ennél rövidebb bevezetőjű cikk csak végső esetben kerül egy téma élére
KIEMELT_ABLAK = 12        # a kiemelt dobozhoz csak az ennyi óránál frissebb cikkek lapjai számítanak
ROVAT_VALTOZATOS = True   # a rovatok kiemelt kártyája óránként sorsolt elrendezést kap (széles/magas, bal/jobb)
HALOM = True              # a rovatok egy-egy sorában egy doboz helyére három kis, kép és bevezető nélküli kártya kerül
HALOM_DB = 3              # ennyi kis kártya egy halomban, ha a sorban van több lapos téma; különben eggyel kevesebb
SZINES_ESELY = 0.35       # ekkora eséllyel színes (a rovat színében) a kiemelt kártya; 0 = soha
DOBOZ_HELYENKENT = 4      # a helyi rovatban helyenként ennyi kiemelt (legfrissebb) hír a bal oldalon

# Országos rovatok a megjelenés sorrendjében. A „Helyi” rovat helyét a HELYI_UTAN adja meg.
#   utvonal: a cikk URL-jének egy szakasza (telex.hu/belfold/... → "belfold") vagy a
#            lap által megadott kategória (WordPress-lapoknál, pl. Magyar Hang, Válasz)
#   kulcsszavak: ha sem az URL, sem a kategória nem árulkodó, a cím és a lead alapján
ROVATOK = [
    {"id": "belfold", "nev": "Belföld", "szin": "#1d4ed8", "dobozok": 11, "ujsag": True,
     "utvonal": ["belfold", "itthon", "kozelet", "politika", "belpol", "orszag", "kozugyek",
                 "orszagszerte", "jogallamisag", "kozpenz", "kozeleti"],
     "kulcsszavak": ["kormány", "orbán", "fidesz", "tisza párt", "magyar péter", "parlament", "országgyűlés",
                     "miniszter", "polgármester", "önkormányzat", "választás", "ellenzék", "budapest",
                     "rendőrség", "bíróság", "ügyészség", "kdnp", "mi hazánk", "ügynök", "állambiztonság"]},
    {"id": "kulfold", "nev": "Külföld", "szin": "#0f766e", "dobozok": 11, "ujsag": True,
     "utvonal": ["kulfold", "vilag", "nagyvilag", "europa", "eu", "kulpolitika", "my-europe", "world",
                 "tudositoink"],  # Infostart: a külföldi tudósítók anyagai
     "kulcsszavak": ["!EP", "!EU", "!ENSZ", "!NATO", "európai parlament", "európai bizottság", "emírség", "florida", "texas", "kalifornia", "new york", "ukrajn", "orosz", "putyin", "trump", "amerikai", "európai unió", "brüsszel",
                     "izrael", "gáza", "kína", "német", "nato", "szlovák", "román", "lengyel", "francia",
                     "egyesült államok", "washington", "fehér ház", "kongresszus", "szenátus", "republikánus",
                     "demokrata párt", "kreml", "moszkva", "kijev", "zelenszkij", "irán", "brit", "olasz", "spanyol",
                     "török", "osztrák", "szerb", "horvát", "ensz", "európai bizottság", "von der leyen",
                     "pozsony", "bécs", "prága", "varsó", "bukarest", "belgrád", "zágráb", "berlin", "párizs", "london",
                     "fico", "vučić", "vucic", "netanjahu", "macron", "merz", "meloni", "starmer", "erdoğan", "erdogan",
                     "belga", "holland", "portugál", "svéd", "finn", "dán", "norvég", "bolgár",
                     "japán", "kínai", "indiai", "brüsszel", "amszterdam", "lisszabon", "madrid", "róma", "athén",
                     "stockholm", "koppenhága", "oslo", "helsinki", "dublin", "szófia"]},
    {"id": "gazdasag", "nev": "Gazdaság", "szin": "#b45309", "dobozok": 11, "ujsag": True,
     "utvonal": ["gazdasag", "fn", "kkv", "penz", "ingatlan", "fogyasztas", "uzlet", "befektetes", "ceg",
                 "business", "bank", "deviza", "tozsde", "unios-forrasok", "krypto", "kriptopenz", "g7"],
     "kulcsszavak": ["infláció", "forint", "jegybank", "mnb", "kamat", "gdp", "költségvetés", "adó",
                     "árak", "tőzsde", "vállalat", "cég", "bér", "nyugdíj", "benzin", "beruházás",
                     "fogyasztás", "büdzsé", "kiskereskedelem", "ksh", "munkanélküli"]},
    {"id": "kultura", "nev": "Kultúra", "szin": "#be185d", "ujsag": True,
     "utvonal": ["kultura", "kult", "kulturalis", "zene", "film", "konyv", "konyveshaz", "hvgkonyvek", "szinhaz", "mediapiac", "media",
                 "culture", "after", "karakter"],
     "kulcsszavak": ["film", "színház", "koncert", "album", "regény", "író", "kiállítás", "múzeum",
                     "rendező", "színész", "fesztivál", "könyv"]},
    {"id": "tech", "nev": "Tudomány és tech", "szin": "#6d28d9", "ujsag": True,
     "utvonal": ["tech", "techtud", "tudomany", "tudomany-technika", "kornyezet", "zold", "mobil", "next",
                 "cegauto", "auto", "autos"],
     "kulcsszavak": ["kutató", "tudós", "mesterséges intelligencia", "űrszonda", "klíma", "apple",
                     "google", "okostelefon", "szoftver", "energia"]},
    {"id": "eletmod", "nev": "Életmód", "szin": "#a16207", "ujsag": True,
     "utvonal": ["eletmod", "elet-stilus", "elet", "egeszseg", "utazas", "gasztro", "otthon",
                 "eletstilus", "szepkilatas"],   # Telex Szépkilátás: kirándulós blog
     "kulcsszavak": ["recept", "egészség", "utazás", "étterem", "kert", "kutya", "macska"]},
    {"id": "sport", "nev": "Sport", "szin": "#15803d", "stadion": True,
     "utvonal": ["sport", "foci", "futball", "sportmenu", "forma-1", "f1"],
     "kulcsszavak": ["meccs", "bajnokság", "válogatott", "gól", "edző", "olimpi",
                     "labdarúg", "focist", "futball", "játékvezet", "tizenegyes", "premier league", "csapatkapitány",
                     "forma-1", "teniszez", "kézilabd", "vízilabd", "úszó", "nb i.", "fradi",
                     "forma–1", "forma-1", "nb i", "bajnokok ligája",
                     "ftc", "európa-liga", "európa liga", "konferencialiga", "ultrá", "szurkol"]},
    {"id": "egyeb", "nev": "Vegyes", "szin": "#64748b", "dobozok": 0, "egyszeru": True,
     "utvonal": ["kozlemeny", "promo", "x-promo", "szponzoralt", "partner", "hirdetes", "pr", "pr-cikk",
                 "tamogatott-tartalom"], "kulcsszavak": []},
]
HELYI_UTAN = "kulfold"    # a Helyi rovat ez után jön (None = rögtön a fő hírek után)
HATTER_UTAN = "gazdasag"  # a Háttér rovat ez után jön
HATTER_SZIN = "#57534e"
HATTER_SORREND = ["atlatszo", "bbc", "mhang", "valasz"]   # a hasábok sorrendje; a többi háttérlap utánuk jön
HATTER_NAP = 7            # a háttéranyagok ennyi napig maradnak meg (a hírek 72 óráig)
HATTER_DOBOZ = 2          # lapokként ennyi legfrissebb anyag kap dobozt (bevezetővel)
HATTER_LISTA = 3          # alattuk ennyi cím látszik, a többi a lenyitható „Korábban” részbe kerül
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


def xml_tisztit(adat):
    """Gyakori hírfolyam-hibák javítása: XML-ben tiltott vezérlőkarakterek és szabálytalan „&” jelek."""
    adat = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", adat)
    return re.sub(rb"&(?!(?:[A-Za-z][A-Za-z0-9]{1,31}|#[0-9]{1,7}|#x[0-9A-Fa-f]{1,6});)", b"&amp;", adat)


def xml_beolvas(adat):
    """XML-elemzés hibatűréssel. Sorban: az eredeti; javított karakterekkel; csonka hírfolyamnál az utolsó teljes
    cikk után lezárva; végül cikkenként, a hibás cikkeket kihagyva (így egy rossz cikk miatt nem esik ki az egész lap)."""
    try:
        return ET.fromstring(adat)
    except ET.ParseError as hiba:
        elso_hiba = hiba
    adat = xml_tisztit(adat)
    try:
        return ET.fromstring(adat)
    except ET.ParseError:
        pass
    vege = adat.rfind(b"</item>")
    if vege >= 0:
        try:
            return ET.fromstring(adat[:vege + 7] + b"</channel></rss>")
        except ET.ParseError:
            pass
    # cikkenként: a fejlécből a névtér-deklarációkat megtartjuk, a hibás <item>-eket eldobjuk
    fej = re.search(rb"<rss\b[^>]*>", adat)
    gyoker = ET.fromstring((fej.group(0) if fej else b"<rss>") + b"<channel></channel></rss>")
    csatorna = gyoker.find("channel")
    jo = 0
    for m in re.finditer(rb"<item\b.*?</item>", adat, re.S):
        try:
            csatorna.append(ET.fromstring((fej.group(0) if fej else b"<rss>") + m.group(0) + b"</rss>")[0])
            jo += 1
        except ET.ParseError:
            continue
    if not jo:
        raise elso_hiba
    return gyoker


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


# kulcsszó → azok a szóalakok, amelyek ugyan vele kezdődnek, de mást jelentenek
# (az „iránti bizalom” nem Irán; a „Mi iránti” típusú cikkek így nem csúsznak a Külföldbe)
KIZART_FOLYTATAS = {"irán": ("iránt",)}


def kulcsszo_talalat(szoveg, kulcsszo, eredeti=None):
    """A kulcsszó csak szó elején számít, utána toldalék állhat: az „adó” illik az „adóemelés” szóra,
    de a „kiadó” szóra nem. A népnevek (pl. „német”) nagy kezdőbetűvel csak mondat elején számítanak,
    így a „Németh” vagy a „Horváth” vezetéknév nem ad külföldi találatot."""
    k = kulcsszo.strip()
    if k.startswith("!"):   # kis- és nagybetűre érzékeny rövidítés (pl. „!EP”: az EP-s igen, az „epizód” nem)
        return len(re.findall(rf"(?<![{BETU}-]){re.escape(k[1:])}(?![A-ZÁÉÍÓÖŐÚÜŰ])", eredeti or ""))
    talalatok = [m for m in re.finditer(rf"(?<![{BETU}-]){re.escape(k)}", szoveg)
                 if not szoveg.startswith(KIZART_FOLYTATAS.get(k, ()), m.start())]
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


LAZA_ROVATOK = ("kultura", "tech", "eletmod")   # a lazább második fél: rács nélküli lista, az összevont hírek képre írt címmel
KEPES_ROVATOK = LAZA_ROVATOK + ("sport",)   # ezekben az összevont hír képes doboz, a cím a képre írva (a Sport kártyái maradnak)
MAGAZIN_ROVATOK = ("kultura", "tech", "eletmod", "sport")   # kiemelt hírük képre írt címmel, rovatszínű panellel
TOBBLAP = 2               # a rovatokban ennyi lapnál felső csíkot kap a sima doboz is
KOTEG_LAP = 5             # ennyi lap felett a doboz mögött „lapköteg” látszik…
KOTEG_NAGY = 7            # …ennyi felett vastagabb köteg
TANULO_SZOMSZED = 15      # a tanuló besorolásnál ennyi leghasonlóbb, biztosan besorolt cikk szavaz
TANULO_EGYEB = 0.60       # besorolatlan (Egyéb) cikk akkor kap rovatot, ha a szavazatok legalább ennyi része egy rovaté
TANULO_KULCSSZO = 0.80    # csak kulcsszó alapján besorolt cikket ennél erősebb egyetértés esetén sorolunk át


def tanulo_besorolas(hirek):
    """A biztosan besorolt cikkekből (webcím vagy a lap kategóriája alapján) tanul: a gyenge jelű cikk
    (Egyéb, vagy csak kulcsszavas tipp) a hozzá szóhasználatban leghasonlóbb biztos cikkek rovatát kapja,
    ha azok elég egyértelműen egy rovat felé húznak. Szavak helyett a szótövek (első 6 betű) számítanak."""
    hirovatok = {r["id"] for r in ROVATOK} - {"egyeb"}
    def tovek(h):
        szoveg_ = h["cim"] + " " + h["cim"] + " " + " ".join((h.get("lead") or "").split()[:60])
        return [ekezet_nelkul(w)[:6] for w in szavak(szoveg_) if w not in STOP and len(w) > 2]
    tanito = [h for h in hirek if h.get("rovat_suly", 0) >= 2 and h["rovat"] in hirovatok
              and "/cegauto/" not in h["link"]]
    cel = [h for h in hirek if (h["rovat"] == "egyeb" and h.get("rovat_suly", 0) < 3)
           or (h["rovat"] in hirovatok and h.get("rovat_suly", 0) == 1)]
    if len(tanito) < 200 or not cel:
        return
    szotar = [Counter(tovek(h)) for h in tanito + cel]
    df = Counter(w for c in szotar for w in c)
    N = len(szotar)
    def vektor(c):
        v = {w: (1 + math.log(n)) * math.log(N / df[w]) for w, n in c.items() if df[w] >= 2}
        hossz = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / hossz for w, x in v.items()}
    tv = [vektor(c) for c in szotar[:len(tanito)]]
    index = {}
    for k, v in enumerate(tv):
        for w in v:
            index.setdefault(w, []).append(k)
    for h, c in zip(cel, szotar[len(tanito):]):
        v = vektor(c)
        hason = {}
        for w, x in v.items():
            for k in index.get(w, ()):
                hason[k] = hason.get(k, 0.0) + x * tv[k][w]
        legk = sorted(hason.items(), key=lambda kv: kv[1], reverse=True)[:TANULO_SZOMSZED]
        szav = Counter()
        for k, x in legk:
            szav[tanito[k]["rovat"]] += x
        if not szav:
            continue
        r, x = szav.most_common(1)[0]
        arany = x / sum(szav.values())
        kuszob = TANULO_EGYEB if h["rovat"] == "egyeb" else TANULO_KULCSSZO
        if arany >= kuszob and r != h["rovat"]:
            h["rovat"], h["rovat_suly"], h["rovat_tanult"] = r, 1, True


def hatter_rovat_nev(h):
    """Ha a cikk háttéranyag, a lapon belüli rovat neve („” ha a lap egésze háttér); ha nem az, None."""
    f = FN.get(h["forras"], {})
    if f.get("hatter"):
        return ""
    elso = urlparse(h["link"]).path.strip("/").split("/")[0]
    return f.get("hatter_rovatok", {}).get(elso)


BALESET = re.compile(r"\b(baleset|karambol|gázol|elgázol|ütköz|felborul|árokba|ámokfut|rendőr|üldöz|"
                     r"halálos|meghalt|életét vesztette|sérült|ittas|részeg|cserbenhagy|száguld|lopott|lopás|"
                     r"büntetőfék|börtön|bíróság)", re.I)
SZERENCSE = re.compile(r"lott[óo]|nyerőszám|szerencsejáték|kenó|puttó|eurojackpot", re.I)
BALESET_CIMKE = re.compile(r"baleset|bűnügy|rendőr|karambol|gázolás|ittas|lopás|bíróság|törvényszék|"
                           r"gyilkos|emberölés|letartóztat", re.I)


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
    # a dátummal kezdődő webcímeknél (pl. nszo.hu/2026/11/22/belfold/…) a dátum utáni tag a rovat
    while szakaszok and szakaszok[0].isdigit():
        szakaszok.pop(0)
    baleset = False
    # szponzorált/közlemény al-oldal bárhol a webcímben elsőbbséget kap (pl. telex.hu/g7/tamogatott-tartalom/…)
    if any(sz in r["utvonal"] for r in ROVATOK if r["id"] == "egyeb" for sz in szakaszok[:3]):
        return ki("egyeb", 3)
    # podcastadások beharangozói (pl. 444 Borízű hang, Belpol): az adás témáinak címkéi (futball, közmédia…)
    # félrevinnék, ezért ha a lap „podcast” címkét tett rá, Egyéb
    if any(k.lower() == "podcast" for k in h["kategoriak"]):
        return ki("egyeb", 3)
    # lottó, nyerőszámok, szerencsejáték: mindig Egyéb, bárhová tette a lap (a 24.hu pl. a Tech rovatban
    # közli a lottószámokat). Kivétel, ha a lap a Gazdaság rovatba tette (pl. a Szerencsejáték Zrt. eredményei).
    if (SZERENCSE.search(h["cim"]) or any(SZERENCSE.search(k) for k in h["kategoriak"])) and not any(
            sz in r["utvonal"] for r in ROVATOK if r["id"] == "gazdasag" for sz in szakaszok[:2]):
        return ki("egyeb", 3)
    # a HVG napi nemzetközi lapszemléje (a külföldi sajtó Magyarországról és a világról): mindig Külföld.
    # A webcím vége nem mindig „nemzetkozi-lapszemle”, de a lap „lapszemle” címkéje mindig rajta van.
    if h["forras"] == "hvg" and ("lapszemle" in h["link"] or any(k.lower() == "lapszemle" for k in h["kategoriak"])):
        return ki("kulfold", 3)
    # a Népszabadság „Mozaik” rovata a mi Egyéb rovatunk megfelelője (lottó, érdekességek, celebhírek).
    # Ide teszi a lap a bűnügyi és bírósági híreket is: ha a címkéi erre utalnak, azok a kulcsszavak
    # szerint Belföldre vagy Külföldre kerülnek, mint a HVG Cégautó baleseti hírei.
    if h["forras"] == "nszo" and ("/mozaik/" in h["link"] or any(k.lower() == "mozaik" for k in h["kategoriak"])):
        if any(BALESET_CIMKE.search(k) for k in h["kategoriak"]):
            baleset = True
        else:
            return ki("egyeb", 3)
    for sz in szakaszok[:3]:
        if baleset:
            break
        # a HVG Cégautó rovatában a közlekedési balesetek, rendőrségi hírek is megjelennek: azok Belföld vagy Külföld
        # ha a lap címkéi megvannak, azoknak is baleseti/bűnügyi hírre kell utalniuk (a „rendőr” szó
        # egy robotaxis cikkben még nem teszi belföldi rendőrségi hírré)
        if sz == "cegauto" and BALESET.search(h["cim"] + " " + (h.get("lead") or "")) and (
                not h["kategoriak"] or any(BALESET_CIMKE.search(k) for k in h["kategoriak"])):
            baleset = True   # a webcím itt nem dönt: a kulcsszavak döntenek Belföld és Külföld közt
            break
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
    if baleset:
        return ki("kulfold" if pontok.get("kulfold", 0) > pontok.get("belfold", 0) else "belfold", 1)
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


def egyesit(csoportok, hason, T, ablak, V):
    """Utólagos egyesítés: az időrendi besorolás az első cikkeknél még kevés adatból dönt, ezért egy sztori
    két csoportba is szakadhat. Két csoport egybeolvad, ha a súlypontjuk (a tagok összesített szóanyaga)
    legalább OSSZEVON_EGYESIT mértékben hasonlít, van köztük OSSZEVON_EROS feletti cikkpár, és időben
    közel vannak. A legjobban illő pár olvad össze először; a kiürült csoport idx-listája üres marad."""
    def sim(a, b):
        return hason[max(a, b)].get(min(a, b), 0.0)

    def kozep(idx):
        k = {}
        for i in idx:
            for g, x in V[i].items():
                k[g] = k.get(g, 0.0) + x
        n = math.sqrt(sum(x * x for x in k.values())) or 1.0
        return {g: x / n for g, x in k.items()}

    def kozep_hason(ia, ib):
        ka, kb = kozep(ia), kozep(ib)
        if len(ka) > len(kb):
            ka, kb = kb, ka
        return sum(x * kb.get(g, 0.0) for g, x in ka.items())

    hova = {i: k for k, cs in enumerate(csoportok) for i in cs["idx"]}
    while True:
        parok = {tuple(sorted((hova[i], hova[j]))) for i, acc in hason.items() for j, x in acc.items()
                 if x >= OSSZEVON_EROS and hova[i] != hova[j]}
        legjobb, legjobb_atl = None, 0.0
        for a, b in parok:
            ia, ib = csoportok[a]["idx"], csoportok[b]["idx"]
            if not any(abs(T[i] - T[j]) <= ablak for i in ia for j in ib):
                continue
            # a két csoport „súlypontjának” hasonlósága: a tagok szóanyagát összegezzük; ez nem hígul fel,
            # ha az egyik csoport nagy (a páronkénti átlag egy egyedül maradt cikknél egyre kisebb lenne)
            atl = kozep_hason(ia, ib)
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


ELO_MIN_RESZ = 3        # ennyi azonos címkéjű cikk egy lapnál már élő sorozat
ELO_RES_PERC = 90       # két egymást követő rész között legfeljebb ennyi perc telhet el
ELO_CIMKE_MIN = 3       # csak legalább ennyi címkés cikkeknél (az egycímkés lapoknál, pl. „Belföld”, ez semmit sem jelent)
ELO_JEL = re.compile(r"élőben|\bélő\b|-elo/|percről percre", re.I)


def elo_sorozatok(hirek):
    """Élő tudósítás felismerése: ha egy lap rövid időn belül legalább ELO_MIN_RESZ cikket ad ki pontosan
    ugyanazzal a (legalább ELO_CIMKE_MIN elemű) címkekészlettel (a 24.hu élő tudósításainak minden része
    ilyen), azokat egyetlen hírré vonjuk össze: elöl a legfrissebb rész, a többi a „Korábbi részek” alatt."""
    from collections import defaultdict
    ido = lambda h: datetime.fromisoformat(h["ido"])
    csop = defaultdict(list)
    for h in hirek:
        k = frozenset(x.strip().lower() for x in h["kategoriak"] if x.strip())
        if len(k) >= ELO_CIMKE_MIN:
            csop[(h["forras"], k)].append(h)
    ki, uj = set(), []
    for lista in csop.values():
        if len(lista) < ELO_MIN_RESZ:
            continue
        lista.sort(key=ido)
        lancok = [[lista[0]]]
        for h in lista[1:]:
            if ido(h) - ido(lancok[-1][-1]) <= timedelta(minutes=ELO_RES_PERC):
                lancok[-1].append(h)
            else:
                lancok.append([h])
        for r in lancok:
            if len(r) < ELO_MIN_RESZ:
                continue
            r = sorted(r, key=ido, reverse=True)
            fo = dict(r[0])
            if not fo["kep"]:
                fo["kep"] = next((h["kep"] for h in r if h["kep"]), "")
            gyujto = next((h for h in r if ELO_JEL.search(h["cim"] + " " + h["link"])), None)
            fo["sorozat"] = [{"c": h["cim"], "l": h["link"], "t": h["ido"]} for h in r[1:]]
            fo["elo_link"] = gyujto["link"] if gyujto else ""
            ki.update(id(h) for h in r)
            uj.append(fo)
    return [h for h in hirek if id(h) not in ki] + uj


def elo_jel(h):
    s = h.get("sorozat")
    if not s:
        return ""
    friss = (MOST - datetime.fromisoformat(h["ido"])) <= timedelta(hours=1)
    return f'<span class="elo-jel{" elo-most" if friss else ""}">{"Élő" if friss else "Tudósítás"} · {len(s) + 1} rész</span>'


def sorozat_html(h):
    s = h.get("sorozat")
    if not s:
        return ""
    sorok = "".join(f'<li><time>{bp(datetime.fromisoformat(x["t"])).strftime("%H:%M")}</time>'
                    f'<a href="{e(x["l"])}" target="_blank" rel="noopener">{e(x["c"])}</a></li>' for x in s)
    gy = (f'<a class="elo-gyujto" href="{e(h["elo_link"])}" target="_blank" rel="noopener">Az egész tudósítás ›</a>'
          if h.get("elo_link") else "")
    return f'<details class="sorozat"><summary>Korábbi részek ({len(s)})</summary><ul>{sorok}</ul>{gy}</details>'


def osszevon(hirek, kulcs=None):
    """Ugyanarról szóló cikkek csoportosítása betű-n-gramos TF-IDF hasonlósággal.
    Minden cikkből súlyozott vektor készül a cím és a bevezető 4 betűs darabjaiból; a ritka darabok többet
    érnek (IDF), a nagyon gyakoriak (a cikkek >10%-ában) kimaradnak. Időrendben haladva egy cikk akkor kerül
    egy meglévő csoportba, ha
    – legalább egy, OSSZEVON_ABLAK_ORA órán belüli tagjához OSSZEVON_EROS feletti a hasonlósága, és
    – az ablakon belüli tagokhoz mért átlagos hasonlósága is eléri az OSSZEVON_ATLAG értéket
      (ez akadályozza meg, hogy a csoport láncszerűen átcsússzon más témákra);
    több jelölt közül az átlagosan leghasonlóbb csoport nyer.
    Ha kulcs is adott (cikk → érték), csak az azonos kulcsú cikkek kerülhetnek egy csoportba (helyi hírek: hely).
    Sportcikk és nem sportcikk sosem kerül egy csoportba, ha mindkettőnek biztos a rovata (a lap jelölte):
    a közös országnév vagy szófordulat (pl. „az Egyesült Államokban”) ott nem jelent közös témát."""
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
    K = [kulcs(h) for h in hirek] if kulcs else None
    # biztos rovatú cikkeknél: sport-e (None, ha a rovat csak tippelt)
    S = [(h.get("rovat") == "sport") if h.get("rovat_suly", 0) >= 2 else None for h in hirek]
    index, hason = {}, {}
    for i, v in enumerate(V):
        acc = {}
        for g, x in v.items():
            for j in index.get(g, ()):
                if T[i] - T[j] <= ablak and (kulcs is None or K[i] == K[j]) and not (
                        S[i] is not None and S[j] is not None and S[i] != S[j]):
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

    def szo_nelkul(i, j):
        """A hasonlóság abból a szóból eredő rész nélkül, amelyik a legtöbbet adja hozzá. Ha két cikket
        szinte csak egy hosszú szó köt össze (pl. „költségvetés”), a maradék kicsi."""
        kozos = {g: x * V[j].get(g, 0.0) for g, x in V[i].items() if g in V[j]}
        if not kozos:
            return 0.0
        h = hirek[i]
        szoveg_ = h["cim"] + " " + " ".join((h.get("lead") or "").split()[:35])
        # egy szó minden alakja együtt számít (költségvetési, költségvetéséről…): az első 6 betű szerint csoportosítunk
        tovek = {}
        for w in set(szavak(szoveg_)):
            w = ekezet_nelkul(w)
            tovek.setdefault(w[:6], set()).update(f"_{w}_"[k:k + 4] for k in range(len(w) - 1))
        legtobb = max((sum(x for g, x in kozos.items() if g in darabok) for darabok in tovek.values()), default=0.0)
        return sum(kozos.values()) - legtobb

    nev_ok = {}
    def nevvel_eleg(i, j):
        if (i, j) not in nev_ok:
            nev_ok[(i, j)] = bool(ritka[i] & ritka[j]) and nev_nelkul(i, j) >= OSSZEVON_NEV_MARAD
        return nev_ok[(i, j)]

    csoportok, hova = [], {}
    for i in range(N):
        acc = hason[i]
        # ugyanarról az eseményről a lapok általában órákon belül írnak: két órán belül kicsit enyhébb a küszöb
        def kuszob(j):
            if nevvel_eleg(i, j):
                return OSSZEVON_NEV
            if T[i] - T[j] <= OSSZEVON_KOZELI_ORA * 3600 and szo_nelkul(i, j) >= OSSZEVON_KOZELI_MARAD:
                return OSSZEVON_KOZELI
            return OSSZEVON_EROS
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
    egyesit(csoportok, hason, T, ablak, V)
    csoportok = [cs for cs in csoportok if cs["idx"]]
    for cs in csoportok:
        hk = cs["hirek"]
        # a fő megjelenítendő változat: a legfrissebb képes, rendes bevezetővel bíró cikk – egy továbbgördülő
        # témánál így a legújabb fejlemény látszik elöl (pl. a bejelentés, nem a reggeli beharangozó);
        # a lapozó a régebbiek felé visz. Ha nincs ilyen, a legfrissebb képes, végül bármelyik.
        hk.sort(key=lambda h: (bool(h["kep"]) and len(h["lead"]) >= FO_LEAD_MIN, bool(h["kep"]),
                               datetime.fromisoformat(h["ido"])), reverse=True)
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
        # Egy továbbgördülő témánál viszont, ha az utóbbi FEJLEMENY_ORA órában legalább FEJLEMENY_LAP lap
        # írt róla, az új fejlemény kezdete számít: így a délelőtt beharangozott, este bejelentett döntés
        # újra friss hírként versenyez a Topért, nem szorul ki pusztán azért, mert reggel kezdődött.
        idok = [(datetime.fromisoformat(h["ido"]), h["forras"]) for h in hk]
        uj = [(t, f) for t, f in idok if MOST - t <= timedelta(hours=FEJLEMENY_ORA)]
        kezdet = min(t for t, _ in uj) if len({f for _, f in uj}) >= FEJLEMENY_LAP else min(t for t, _ in idok)
        cs["kezdet_ora"] = (MOST - kezdet).total_seconds() / 3600
        # fontosság: hány lap írt róla + frissesség
        cs["pont"] = 3 * len(cs["forrasok"]) + 0.5 * len(hk) + 5 * max(0.0, 1 - cs["kezdet_ora"] / FO_MAX_ORA)
        cs["kor_ora"] = kor_ora
    return csoportok



def friss_lapok(cs):
    """Hány különböző lap írt a témáról az utóbbi KIEMELT_ABLAK órában (legalább 1)."""
    hatar = MOST - timedelta(hours=KIEMELT_ABLAK)
    return max(1, len({h["forras"] for h in cs["hirek"] if datetime.fromisoformat(h["ido"]) >= hatar}))

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
                                        "lon": hm_szam(r.get("longitude")), "mag": hm_szam(r.get("elevation")),
                                        "ido": None, "csap": {}})
        a["csap"][ido] = hm_szam(r.get("r"))
        t = hm_szam(r.get("t"))
        if t is not None and (a["ido"] is None or ido > a["ido"]):
            a.update(ido=ido, t=t, szel=hm_szam(r.get("fs")), lokes=hm_szam(r.get("fx")), para=hm_szam(r.get("u")),
                     irany=hm_szam(r.get("fsd")))
    for a in allomasok.values():
        if a["ido"]:   # csapadék az utolsó órában (az utolsó hat tízperces összeg)
            ertek = [v for k, v in a["csap"].items() if a["ido"] - timedelta(minutes=55) <= k <= a["ido"] and v is not None]
            a["csap1h"] = round(sum(ertek), 1) if ertek else None
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


def allomas_utotag(allomas, nev):
    """Az állomás nevének a megjelenített helynév utáni része („Budapest Pestszentlőrinc” → „Pestszentlőrinc”)."""
    if allomas == nev:
        return ""
    return allomas[len(nev) + 1:] if allomas.startswith(nev + " ") else allomas


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
    sorok, idok, helyek = [], [], []
    for nev, szam, *rovid in sorted(MERES_ALLOMASOK, key=lambda x: ekezet_nelkul(x[0].lower())):
        # az állomás megadható számmal vagy névvel, vagy ezek listájával (az első jelentő számít; a tartalék
        # állomás a saját nevén jelenik meg)
        a = None
        for i, sz in enumerate(szam if isinstance(szam, list) else [szam]):
            a = meresek.get(sz) if isinstance(sz, int) else next((v for v in meresek.values() if v["nev"] == sz), None)
            if a:
                if i:
                    nev = a["nev"]
                break
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
            reszlet.append(f'{str(a["csap1h"]).replace(".", ",")} mm/óra')
        leiras = HM_KODOK.get(kod, ("", ""))[0]
        utotag = rovid[0] if rovid else allomas_utotag(a["nev"], nev)
        if a.get("mag") and a["mag"] >= MERES_HEGYI_M:   # hegyi állomás: a magasság is
            utotag = (utotag + " · " if utotag else "") + f'{round(a["mag"])} m'
        sorok.append(
            f'<li data-ido="{a["ido"].isoformat()}"><span class="meres-nev">{e(nev)}'
            f'<small>{e(utotag)}<i class="mi">{" · " if utotag else ""}{bp(a["ido"]):%H:%M}</i></small></span>'
            f'<span title="{e(leiras)}">{hm_ikon(kod, 28, a.get("lat") is not None and nap_magassag(a["lat"], a["lon"], a["ido"]) < -0.833)}</span>'
            f'<span class="meres-fok">{round(a["t"])}°</span>'
            f'<span class="meres-reszlet">{e(", ".join(reszlet))}</span></li>')
        helyek.append((a.get("lat") or 0, a.get("lon") or 0))
    if not sorok:
        return ""
    friss = bp(max(idok))
    # a fejlécben álló időpont a sorokban felesleges: csak az ettől eltérő (késve jelentő) állomásnál marad meg
    sorok = [re.sub(r'<i class="mi">(?: · )?' + f'{friss:%H:%M}' + r'</i>', '', x) for x in sorok]
    # földrajzi rend: három hasáb (Nyugat, Közép, Kelet a hosszúság szerint), hasábon belül északról délre
    regiok = [[], [], []]
    for x, (lat, lon) in zip(sorok, helyek):
        regiok[0 if lon < MERES_NYUGAT_LON else 2 if lon > MERES_KELET_LON else 1].append((-lat, x))
    hasabok = "".join(
        f'<div class="meres-hasab"><h4>{cim}</h4><ul class="meres-lista">{"".join(x for _, x in sorted(r))}</ul></div>'
        for cim, r in zip(("Nyugat", "Közép", "Kelet"), regiok) if r)
    return (f'<div class="ido-hely ido-meres"><h3>Most mért <small>{friss:%H:%M}-kor</small></h3>'
            f'<button class="meres-nyito" type="button" aria-expanded="false" '
            f'onclick="var k=this.parentNode;k.classList.toggle(\'nyitva\');this.setAttribute(\'aria-expanded\',k.classList.contains(\'nyitva\'))">'
            f'{len(sorok)} állomás mért adatai</button>'
            f'<div class="meres-hasabok">{hasabok}</div>'
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


def radar_letolt():
    """A legfrissebb radarképek helye: (mappa URL, [időbélyegek ÉÉÉÉHHNN_ÓÓPP, UTC, növekvő]) vagy None.
    A mappaszerkezetet a szerver könyvtárlistájából derítjük fel (legfeljebb két szint mélyen)."""
    minta = re.compile(r'href="(?:[^"]*/)?radar_composite-' + re.escape(RADAR_TIPUS) + r'-(\d{8}_\d{4})\.png"')
    sor, lekert = [RADAR_MAPPA], 0
    while sor and lekert < 8:
        url = sor.pop(0)
        try:
            lap = letolt(url, probalkozas=2).decode("utf-8", "replace")
        except Exception as ex:
            print(f"  radar ({url}): {ex}")
            lekert += 1
            continue
        lekert += 1
        idok = sorted(set(minta.findall(lap)))
        if idok:
            return url, idok[-RADAR_KEPEK:]
        # almappák: előbb azok, amelyek nevében a típus vagy a „png” szerepel
        al = [h for h in re.findall(r'href="([^"?#/][^"?#]*/)"', lap) if not h.startswith(("..", "http"))]
        al.sort(key=lambda h: (RADAR_TIPUS.lower() + "/" != h.lower(), "png" not in h.lower()))
        sor += [url + h for h in al if h.count("/") == 1]
    return None


def radar_kartya(radar):
    if not radar:
        return ""
    mappa, idok = radar
    adat = json.dumps({"mappa": mappa, "tipus": RADAR_TIPUS, "idok": idok, "db": RADAR_KEPEK})
    return (f'<div class="ido-hely radar" id="radar"><h3>Csapadékradar <small class="radar-ido"></small></h3>'
            f'<div class="radar-kep"><img alt="A HungaroMet kompozit radarképe Magyarországról" decoding="async"></div>'
            f'<div class="radar-sav"><button class="radar-gomb" type="button" aria-label="Lejátszás">▶</button>'
            f'<span class="radar-tol"></span><span class="radar-csik"><i></i></span><span class="radar-ig"></span></div>'
            f'<div class="radar-jel">gyenge <b></b> erős</div>'
            f'<p class="radar-forras">Forrás: <a href="https://odp.met.hu/" target="_blank" rel="noopener">HungaroMet</a>, '
            f'kompozit radarkép (Meteorológiai Adattár)</p></div>'
            f'<script>' + RADAR_JS.replace("__RADAR__", adat) + '</script>')


# A radar: az építéskor ismert legutóbbi képek után a böngésző maga is megpróbálja a frissebbeket (ötpercenként),
# így a kép akkor is friss, ha az oldal negyedóránként épül. Lejátszáskor az utolsó óra képei pörögnek.
RADAR_JS = r"""
(function(){
  var R = __RADAR__, k = document.getElementById('radar'); if (!k) return;
  var img = k.querySelector('img'), gomb = k.querySelector('.radar-gomb'), csik = k.querySelector('.radar-csik i');
  function url(t){ return R.mappa + 'radar_composite-' + R.tipus + '-' + t + '.png'; }
  function datum(t){ return new Date(Date.UTC(+t.slice(0,4), +t.slice(4,6)-1, +t.slice(6,8), +t.slice(9,11), +t.slice(11,13))); }
  function bel(t){ return datum(t).toLocaleTimeString('hu-HU', {hour:'2-digit', minute:'2-digit', timeZone:'Europe/Budapest'}); }
  function kov(t){ var d = new Date(datum(t).getTime() + 300000), p = function(n){ return (n<10?'0':'')+n; };
    return d.getUTCFullYear()+p(d.getUTCMonth()+1)+p(d.getUTCDate())+'_'+p(d.getUTCHours())+p(d.getUTCMinutes()); }
  var idok = R.idok.slice(), i = idok.length - 1, fut = null;
  function mutat(j){ i = j; img.src = url(idok[i]); k.querySelector('.radar-ido').textContent = bel(idok[i]);
    csik.style.width = (idok.length > 1 ? 100 * i / (idok.length - 1) : 100) + '%';
    k.querySelector('.radar-tol').textContent = bel(idok[0]); k.querySelector('.radar-ig').textContent = bel(idok[idok.length-1]); }
  // frissebb képek keresése: ameddig a következő ötperces kép betölthető, hozzávesszük
  function frissit(){ var t = kov(idok[idok.length-1]); if (datum(t) > new Date()) return;
    var p = new Image(); p.onload = function(){ idok.push(t); if (idok.length > R.db) idok.shift(); if (!fut) mutat(idok.length-1); frissit(); };
    p.src = url(t); }
  mutat(i); frissit(); setInterval(frissit, 300000);
  // előtöltés a lejátszáshoz
  function eltolt(){ idok.forEach(function(t){ var p = new Image(); p.src = url(t); }); }
  gomb.addEventListener('click', function(){
    if (fut) { clearInterval(fut); fut = null; gomb.textContent = '▶'; gomb.setAttribute('aria-label','Lejátszás'); mutat(idok.length-1); return; }
    eltolt(); gomb.textContent = '❚❚'; gomb.setAttribute('aria-label','Megállítás'); mutat(0);
    fut = setInterval(function(){ mutat(i < idok.length-1 ? i+1 : 0); }, 650);
  });
  k.querySelector('.radar-csik').addEventListener('click', function(ev){
    var r = this.getBoundingClientRect(), j = Math.round((ev.clientX - r.left) / r.width * (idok.length-1));
    if (fut) gomb.click(); mutat(Math.max(0, Math.min(idok.length-1, j)));
  });
})();
"""


def idojaras_panel(helyek, panel_hirek, meresek=None):
    if not helyek and not panel_hirek and not meresek:
        return ""
    sajat = ('<div class="ido-hely ido-itt" id="ido-itt" hidden></div>' if IDOJARAS_SAJAT_HELY else "")
    kartyak = sajat + (meres_lista(meresek) if meresek else "") + "".join(ido_kartya(h) for h in helyek)
    hirdobozok = [radar_kartya(radar_letolt())]
    hirdobozok = [x for x in hirdobozok if x]
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
    # a Top két alsó sorának kis dobozai kép nélküliek: a cím kiér a doboz széléig
    kep = kep_html(h, "kep") if tipus != "kis" else ""
    lead = f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] else ""
    rszin = next((r["szin"] for r in ROVATOK if r["id"] == cs["rovat"]), "#64748b")
    # képre írt cím: csak a nyitóhírnél, és csak keskeny képernyőn (telefonon)
    rairt = " rairt-kicsi" if i == 0 and h["kep"] else ""
    osztaly = tipus + rairt   # a Topban nincs köteg: ott eleve csak sokat megírt témák vannak
    if tipus == "kis":
        # lapszéli hasáb: színes lapnév a cím előtt, idő a végén; alatta a pöttyök és kicsiben a lapozó
        return f'''<article class="fo-hir kis" style="--r:{rszin}" data-srcs="{' '.join(cs['forrasok'])}"{alt_adat(cs)}>
  <div class="szoveg"><h3><span class="meta">{elo_jel(h)}{forras_nev(h["forras"])}</span><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a> <span class="meta ido"><time>{ido_szoveg(h["ido"])}</time></span></h3>
  <div class="szel-alja">{lefedettseg(cs)}{tovabbi(cs)}</div></div>
</article>'''
    if tipus == "masod":
        # a 2×2-es rácsban: a képen a lap, az idő és a cím; alatta fehéren a bevezető, a lapok és a lapozó
        return f'''<article class="fo-hir masod rairt" style="--r:{rszin}" data-srcs="{' '.join(cs['forrasok'])}"{alt_adat(cs)}>
  <div class="kep-blokk">{kep}<div class="rcim">
  <div class="meta">{elo_jel(h)}{forras_nev(h["forras"])}<time>{ido_szoveg(h["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3></div></div>
  <div class="szoveg">{lead}{sorozat_html(h)}{lefedettseg(cs)}{tovabbi(cs)}</div>
</article>'''
    return f'''<article class="fo-hir {osztaly}" style="--r:{rszin}" data-srcs="{' '.join(cs['forrasok'])}"{alt_adat(cs)}>
  {kep}<div class="szoveg">
  <div class="meta">{forras_nev(h["forras"])}<time>{ido_szoveg(h["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3>
  {lead}{sorozat_html(h)}{lefedettseg(cs)}{tovabbi(cs)}</div>
</article>'''


def koteg(cs):
    """Sokat megírt sztori: „lapköteg” jelölés a doboz mögött (KOTEG_LAP / KOTEG_NAGY lap felett)."""
    n = len(cs["forrasok"])
    return " koteg koteg-nagy" if n >= KOTEG_NAGY else (" koteg" if n >= KOTEG_LAP else "")


def doboz(cs, lead=True, kiemelt=False, extra="", magazin=False, laza=False):
    h = cs["fo"]
    if kiemelt and magazin and h["kep"]:
        # magazinos kiemelt: a képen a lap, az idő és a cím; mellette (fekvő) vagy alatta (álló)
        # rovatszínű panelen alulra igazítva a bevezető, a lapok és a lapozó
        extra = extra.replace(" szines", "")
        fej = (f'<div class="meta">{elo_jel(h)}{forras_nev(h["forras"])}<time>{ido_szoveg(h["ido"])}</time></div>'
               f'<h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3>')
        if "kiemelt-magas" not in extra:
            # fekvő: balra a kép (a doboz teljes magasságában), jobbra a cím, a bevezető, a lapok, a lapozó
            return f'''<article class="doboz kiemelt magazin{extra}" data-srcs="{' '.join(cs['forrasok'])}"{alt_adat(cs, lead=lead)}>
  <div class="kb">{kep_html(h, "kiemelt-kep")}</div>
  <div class="szoveg">{fej}{f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] and lead else ''}{lefedettseg(cs)}{tovabbi(cs)}</div>
</article>'''
        return f'''<article class="doboz kiemelt magazin{extra}" data-srcs="{' '.join(cs['forrasok'])}"{alt_adat(cs, lead=lead)}>
  <div class="kb">{kep_html(h, "kiemelt-kep")}<div class="rcim">
  <div class="meta">{elo_jel(h)}{forras_nev(h["forras"])}<time>{ido_szoveg(h["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3></div></div>
  <div class="szoveg">{f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] and lead else ''}{lefedettseg(cs)}{tovabbi(cs)}</div>
</article>'''
    if laza and not kiemelt and h["kep"] and len(cs["forrasok"]) >= TOBBLAP:
        # a lazább rovatokban az összevont hír képes doboz, a cím a képre írva (a felső vonal helyett)
        return f'''<article class="doboz kepes{extra}" data-srcs="{' '.join(cs['forrasok'])}"{alt_adat(cs, lead=lead)}>
  <div class="szoveg"><div class="kepfej">{kep_html(h, "kep")}<div class="ra">
  <div class="meta">{elo_jel(h)}{forras_nev(h["forras"])}<time>{ido_szoveg(h["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3></div></div>
  {f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] and lead else ''}
  {sorozat_html(h)}{lefedettseg(cs)}{tovabbi(cs)}</div>
</article>'''
    # több lap írta: ugyanaz a rovatszínű felső csík, mint a kiemelt dobozon (a köteg helyett)
    extra += " tobblap" if len(cs["forrasok"]) >= TOBBLAP and not kiemelt else ""
    if kiemelt and not h["kep"] and (h.get("lead") or "").strip():
        extra += " vezer"   # kép nélküli kiemelt: „vezércikk” tipográfia
    kep = kep_html(h, "kiemelt-kep") if kiemelt else kep_html(h, "belyeg")
    return f'''<article class="doboz{' kiemelt' if kiemelt else ''}{extra}" data-srcs="{' '.join(cs['forrasok'])}"{alt_adat(cs, lead=lead)}>
  <div class="szoveg">
  <div class="meta">{elo_jel(h)}{forras_nev(h["forras"])}<time>{ido_szoveg(h["ido"])}</time></div>
  <h3><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a></h3>
  {f'<p class="lead">{e(h["lead"])}</p>' if h["lead"] and lead else ''}
  {sorozat_html(h)}{lefedettseg(cs)}{tovabbi(cs)}</div>
  {kep}
</article>'''


def sor(cs, csak_ora=False, rovid=False):
    h = cs["fo"]
    if rovid:
        ido = rovid_ido(h["ido"])   # ma csak óra:perc, korábban „tegnap 22:14”
    else:
        ido = bp(datetime.fromisoformat(h["ido"])).strftime("%H:%M") if csak_ora else ido_szoveg(h["ido"])
    return (f'<li data-srcs="{" ".join(cs["forrasok"])}"{alt_adat(cs, csak_ora, lead=False, kep=False)}><time>{ido}</time>'
            f'<span class="sor-cim"><a href="{e(h["link"])}" target="_blank" rel="noopener">{e(h["cim"])}</a> '
            f'{elo_jel(h)}{forras_nev(h["forras"])}{cikklista(cs)}</span></li>')


def friss_racs(lista):
    """Újságos rovatokban a dobozokból kiszorult friss hírek a rács folytatásaként: négy hasáb (a fenti háromtól
    eltérő ritmus jelzi, hogy ez ideiglenes rövidhír-zóna), halvány
    vonalak, felül lap és idő, alatta a cím a címbetűvel, de a folyószöveg méretében (bevezető nélkül)."""
    if not lista:
        return ""
    cellak = [
        # a lap neve színesen közvetlenül a cím előtt (sortörés nélkül), az idő kicsiben a cím végén
        f'<article class="fs" data-srcs="{" ".join(c["forrasok"])}"><h4>{elo_jel(c["fo"])}{forras_nev(c["fo"]["forras"])}'
        f'<a href="{e(c["fo"]["link"])}" target="_blank" rel="noopener">{e(c["fo"]["cim"])}</a></h4>'
        f'<time class="fs-ido">{ido_szoveg(c["fo"]["ido"])}</time>'
        + (f' {cikklista(c)}' if len(c["hirek"]) > 1 else "") + "</article>"
        for c in lista]
    cellak += ['<div class="fs ures"></div>'] * (-len(cellak) % 4)   # az utolsó sor is teli legyen (vonalak)
    # telefonon az első négy látszik, a többi egy gomb mögött (asztali gépen minden látszik)
    gomb = (f'<button type="button" class="fs-tobb" onclick="this.parentNode.classList.add(\'nyitva\');this.remove()">'
            f'még {len(lista) - 4} friss hír ▾</button>' if len(lista) > 4 else "")
    return f'<div class="fs-blokk"><div class="fs-racs">{"".join(cellak)}</div>{gomb}</div>'


VEGYES_MAX = 15   # a Vegyes rovat tömör listájában legfeljebb ennyi hír (a többi a „További hírek” alatt)


def vegyes_lista(csoportok):
    """A Vegyes rovat: a nyomtatott lapok „Röviden” hasábjához hasonló sűrű lista három hasábban,
    lapnév, cím és idő, bevezető nélkül, vékony elválasztókkal."""
    if not csoportok:
        return ""
    sorok = "".join(
        f'<li data-srcs="{" ".join(c["forrasok"])}">{elo_jel(c["fo"])}{forras_nev(c["fo"]["forras"])}'
        f'<a href="{e(c["fo"]["link"])}" target="_blank" rel="noopener">{e(c["fo"]["cim"])}</a>'
        f'<time>{ido_szoveg(c["fo"]["ido"])}</time></li>'
        for c in csoportok)
    # telefonon az első hat látszik, a többi egy gomb mögött
    gomb = (f'<button type="button" class="vegyes-tobb" onclick="this.previousElementSibling.classList.add(\'nyitva\');this.remove()">'
            f'még {len(csoportok) - 6} hír ▾</button>' if len(csoportok) > 6 else "")
    return f'<ul class="vegyes-lista" style="--sorok:{(len(csoportok) + 2) // 3}">{sorok}</ul>{gomb}'


def kis_kartyak(csoportok):
    """Az Egyéb rovat friss hírei: három hasábos rácsban kis kártyák (lap, idő, cím, kétsoros bevezető)."""
    if not csoportok:
        return ""
    kartyak = "".join(
        f'<article class="mini" data-srcs="{" ".join(c["forrasok"])}"><div class="meta">{elo_jel(c["fo"])}{forras_nev(c["fo"]["forras"])}'
        f'<time>{ido_szoveg(c["fo"]["ido"])}</time></div>'
        f'<h4><a href="{e(c["fo"]["link"])}" target="_blank" rel="noopener">{e(c["fo"]["cim"])}</a></h4>'
        + (f'<p class="lead">{e(c["fo"]["lead"])}</p>' if c["fo"]["lead"] else "") + "</article>"
        for c in csoportok)
    return f'<div class="kis-kartyak">{kartyak}</div>'


def halom(csoportok):
    """Egy doboz helyén több kis kártya: lap, idő és cím (bevezető és kép nélkül) – egy lapos, régebbi hírekhez."""
    kartyak = "".join(
        f'<article class="mini" data-srcs="{c["fo"]["forras"]}"><div class="meta">{elo_jel(c["fo"])}{forras_nev(c["fo"]["forras"])}'
        f'<time>{ido_szoveg(c["fo"]["ido"])}</time></div>'
        f'<h4><a href="{e(c["fo"]["link"])}" target="_blank" rel="noopener">{e(c["fo"]["cim"])}</a></h4></article>'
        for c in csoportok)
    return f'<div class="halom">{kartyak}</div>'


def halom_helyek(dobozok, lista, sorok, sors):
    """A megadott sorokban (a rács dobozainak indexei szerint) egy-egy sorsolt doboz helyére halom kerül.
    Csak egy lapos, nem összevont hír kerülhet halomba: a helyén álló dobozé és a lista elejéről még kettő.
    Visszaad: {doboz index: [csoportok]} és a megmaradt lista."""
    # az élő tudósítás (sorozat) sem: annak a dobozában kell a „Korábbi részek” lista
    egyszeru = lambda c: len(c["hirek"]) == 1 and not c["fo"].get("sorozat")
    helyek = {}
    for sor in sorok:
        jo = [i for i in sor if i < len(dobozok) and egyszeru(dobozok[i])]
        if not jo:
            continue
        i = sors.choice(jo)
        # a sor magasságát a szomszéd dobozok adják: ha köztük van több lapos (összevont) téma, annak doboza
        # a lefedettség, az „Erről írt” sor, a lapozó és a kis kép miatt magas, így három kis kártya fér mellé;
        # ha nincs, a szomszédok alacsonyabbak, és kettő kerül ide
        magas = any(len(dobozok[k]["forrasok"]) > 1 for k in sor if k != i and k < len(dobozok))
        db = HALOM_DB if magas else HALOM_DB - 1
        tobbi = [c for c in lista if egyszeru(c)]
        if len(tobbi) < db - 1:
            continue
        hozza = tobbi[:db - 1]
        helyek[i] = sorted([dobozok[i]] + hozza, key=lambda c: c["fo"]["ido"], reverse=True)
        lista = [c for c in lista if not any(c is x for x in hozza)]
    return helyek, lista


def friss_sav(lista):
    """A dobozokból kiszorult friss hírek a rovat alján: tömör, két hasábos lista egy külön lapon."""
    if not lista:
        return ""
    return f'<div class="friss-sav"><ul class="lista">{"".join(sor(c, csak_ora=True, rovid=True) for c in lista)}</ul></div>'


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


def korabban(lista, nyitva=False, cimke="További hírek"):
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
    return (f'<details class="tovabbi-hirek"{" open" if nyitva else ""}><summary>{cimke} ({len(lista)})</summary>'
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

    hasabok, fulek = [], []
    sorrend = HATTER_SORREND + [f["id"] for f in FORRASOK if f["id"] not in HATTER_SORREND]
    for f in (FN[x] for x in sorrend):
        cs = sorted([c for c in cs_lista if c["fo"]["forras"] == f["id"]], key=lambda c: c["fo"]["ido"], reverse=True)
        if not cs:
            continue
        dob, lista = cs[:HATTER_DOBOZ], cs[HATTER_DOBOZ:]
        lathato, regi = lista[:HATTER_LISTA], lista[HATTER_LISTA:]
        reszlet = (f'<details class="tovabbi-hirek"><summary>Korábban ({len(regi)})</summary>'
                   f'<ul class="lista">{"".join(sor_(c) for c in regi)}</ul></details>') if regi else ""
        elso = not fulek
        fulek.append(f'<button type="button" class="hely-ful hatter-ful" data-lap="{f["id"]}" style="--r:{f["szin"]}" '
                     f'aria-selected="{str(elso).lower()}">{e(f.get("rovid", f["nev"]))}</button>')
        hasabok.append(f'''<div class="hatter-hasab{" aktiv" if elso else ""}" data-srcs="{f["id"]}" data-lap="{f["id"]}">
  <h3 class="helynev" style="--r:{f["szin"]}">{e(f["nev"])}</h3>
  {"".join(doboz(c) for c in dob)}
  {f'<ul class="lista">{"".join(sor_(c) for c in lathato)}</ul>' if lathato else ""}{reszlet}
</div>''')
    html_ = f'''<section class="rovat" id="hatter" style="--r:{HATTER_SZIN}">
  {rovatfej("hatter", "Háttér", HATTER_SZIN, len(cs_lista))}
  <div class="hely-fulek hatter-fulek" role="tablist">{"".join(fulek)}</div>
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
    html_ = f'''<section class="rovat helyi" id="helyi" style="--r:{HELYI_SZIN}">
  {rovatfej("helyi", "Helyi", HELYI_SZIN, len(helyi_cs))}
  <div class="hely-fulek" role="tablist">{"".join(fulek)}</div>
  <div class="tartalom helyek">{"".join(lapok_)}</div>
</section>'''
    return html_, f'<a href="#helyi" style="--r:{HELYI_SZIN}">Helyi</a>'


def oldal(orszagos, helyi_cs, hibak, idojaras=None, idokep=None, meresek=None, hatter_cs=None):
    # a napi összefoglalók („A nap legfontosabb hírei” stb.) nem kerülnek a Topba és a kiemelt dobozokba
    nem_osszefoglalo = [c for c in orszagos if not OSSZEFOGLALO.search(c["fo"]["cim"])]
    friss = [c for c in nem_osszefoglalo if c["kezdet_ora"] < FO_MAX_ORA]
    if len(friss) < FO_HIREK_SZAMA:           # hajnalban kevés a friss hír: legfeljebb 12 óráig visszanyúlunk
        friss = [c for c in nem_osszefoglalo if c["kezdet_ora"] < FO_TARTALEK_ORA]
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
            kiem = sum(1 for c in dobozok if not OSSZEFOGLALO.search(c["fo"]["cim"])) >= 3   # van-e kiemelt (dupla) kártya
            if kiem:
                # kiemelt: amelyikről a legtöbb lap írt az utóbbi KIEMELT_ABLAK órában, a legfrissebb cikk
                # korával csökkenő súllyal (KIEMELT_ORA óránként egy lappal kevesebbet ér); így a napközben
                # tovább gördülő téma vezethet, a reggeli, azóta elhalt téma viszont este már nem
                k = max((c for c in dobozok if not OSSZEFOGLALO.search(c["fo"]["cim"])), key=lambda c: (round(friss_lapok(c) - c["kor_ora"] / KIEMELT_ORA
                                                      - (0 if c["fo"]["kep"] or (c["fo"].get("lead") or "").strip() else 1.5), 1),
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
            if not r.get("stadion"):
                szines = False   # színes kiemelt kártya csak a Sportban; a közéleti rovatok felül csíkot,
                                 # az újságoldal módú rovatok (Kultúra, Tech, Életmód) halvány rovatszínt kapnak
            elozo_elr, elozo_szines = elr, szines
            # halmok: az alsó sorban (a nagy rovatokban a harmadikban is) egy doboz helyére két-három kis kártya
            halmok = {}
            if HALOM and kiem and ROVAT_VALTOZATOS and len(dobozok) >= 5:
                n = len(dobozok)
                sorok = [range(n - 3, n)]
                if n >= 11:
                    sorok.insert(0, range(5, 8))
                if n == 5 and elr.endswith("magas"):
                    sorok = []          # öt doboznál a magas kiemelt mellett nincs teli sor
                halmok, lista = halom_helyek(dobozok, lista, sorok, sors)
            # a dobozokból kiszorult, néhány óránál frissebb hírek ne tűnjenek el a lenyíló listában
            # (a kiírt cikk saját ideje számít, nem a téma legutóbbi frissülése)
            # (az Egyéb rovatnak nincsenek dobozai: ott az utóbbi egy nap hírei kerülnek a sávba)
            # a forgalmas rovatokban (Belföld, Külföld) hosszabb az ablak, de legfeljebb három teli sor
            szeles = r["id"] in FRISS_SAV_SZELES
            hatar = MOST - timedelta(hours=FRISS_ORA if r.get("egyszeru") else
                                     FRISS_SAV_SZELES_ORA if szeles else FRISS_SAV_ORA)
            sav = sorted([c for c in lista if datetime.fromisoformat(c["fo"]["ido"]) >= hatar],
                         key=lambda c: datetime.fromisoformat(c["fo"]["ido"]),
                         reverse=True)[:FRISS_SAV_SZELES_MAX if szeles else FRISS_SAV_MAX] \
                if dobozok or r.get("egyszeru") else []
            # az újságos rovatok négyhasábos rövidhír-sora ne maradjon csonka: ha megjelenik, a következő
            # legfrissebb hírekkel (akár 3 óránál régebbiekkel) négyesével teli sorokra egészül ki
            if r.get("egyszeru"):   # Vegyes: a legfrissebb VEGYES_MAX hír, kortól függetlenül, három hasábra kerekítve
                sav = sorted(lista, key=lambda c: datetime.fromisoformat(c["fo"]["ido"]), reverse=True)[:VEGYES_MAX]
                if len(sav) > 3 and len(sav) % 3:
                    sav = sav[:len(sav) - len(sav) % 3]
            rovidhir = (r.get("ujsag") or r.get("stadion")) and dobozok   # négyhasábos rövidhír-sor (a Sportban is)
            if sav and rovidhir and len(sav) % 4:
                tobbi = sorted((c for c in lista if not any(c is x for x in sav)),
                               key=lambda c: datetime.fromisoformat(c["fo"]["ido"]), reverse=True)
                sav = sav + tobbi[:-len(sav) % 4]
            lista = [c for c in lista if not any(c is x for x in sav)]
            extra = (" kiemelt-magas" if elr.endswith("magas") else "") + (" szines" if szines else "")
            nav.append(f'<a href="#{r["id"]}" style="--r:{r["szin"]}">{e(r["nev"])}</a>')
            reszek.append(f'''<section class="rovat{' ujsag' if r.get('ujsag') else ''}{' laza' if r['id'] in LAZA_ROVATOK else ''}{' egyszeru' if r.get('egyszeru') else ''}{' stadion' if r.get('stadion') else ''}" id="{r["id"]}" style="--r:{r["szin"]}">
  {rovatfej(r["id"], r["nev"], r["szin"], len(cs))}
  <div class="tartalom">
  {f'<div class="racs elr-{elr}">{"".join(halom(halmok[i]) if i in halmok else doboz(c, kiemelt=(i == 0 and kiem), extra=(extra if i == 0 and kiem else ""), magazin=r["id"] in MAGAZIN_ROVATOK, laza=r["id"] in KEPES_ROVATOK) for i, c in enumerate(dobozok))}</div>' if dobozok else ''}
  {vegyes_lista(sav) if r.get("egyszeru") else friss_racs(sav) if rovidhir else friss_sav(sav)}{korabban(lista, nyitva=not dobozok and not sav, cimke="Korábbi hírek" if sav and rovidhir else "További hírek")}
  </div>
</section>''')
        if r["id"] == HELYI_UTAN:
            reszek.append(helyi_html); nav.append(helyi_nav)
        if r["id"] == HATTER_UTAN and hatter_html:
            reszek.append(hatter_html); nav.append(hatter_nav)

    # a Vegyes rovat (a maradék) az oldal legvégére kerül, az Időjárás után
    vegyes = [i for i, x in enumerate(reszek) if 'id="egyeb"' in x[:400]]
    vegyes_resz = reszek.pop(vegyes[0]) if vegyes else ""
    vnav = [i for i, x in enumerate(nav) if 'href="#egyeb"' in x]
    vegyes_nav = nav.pop(vnav[0]) if vnav else ""
    ido_html = idojaras_panel(idojaras or [], idokep or [], meresek)
    if ido_html:
        reszek.append(ido_html)
        nav.append(f'<a href="#idojaras" style="--r:{IDOJARAS_SZIN}">Időjárás</a>')
    if vegyes_resz:
        reszek.append(vegyes_resz)
        nav.append(vegyes_nav)

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
  <div class="top-keret"><div class="fo-racs">{"".join(fo_doboz(c, i) for i, c in enumerate(fo[:5]))}</div><aside class="top-szel">{"".join(fo_doboz(c, i + 5) for i, c in enumerate(fo[5:]))}</aside></div></div>
</section>
<main>{"".join(reszek)}</main>
<footer>A lap tetején azok a friss hírek állnak, amelyekről a legtöbb lap írt. Forrás: a lapok RSS-csatornái.
Időjárásadatok: <a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo.com</a> (CC BY 4.0).</footer>
<script>{JS}</script>
<script>{APP_JS}</script>
</body></html>'''


CSS = """
:root{
  --lap:#e9edf2;--felulet:#fff;--tinta:#16202f;--halvany:#5a6577;--lead:#2f3a4b;--vonal:#d3dae3;
  --sav:#16202f;--sav-szoveg:#f2f5f9;--sav-halvany:#a9b4c4;--sav-lead:#d5dce6;--sav-felulet:#212d3f;
  --cimbetu:"Bricolage Grotesque",ui-sans-serif,system-ui,sans-serif}
/* sötét mód: a rendszer beállítása szerint (data-tema="auto"), vagy kézzel választva */
@media (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]){
  --lap:#0e141d;--felulet:#18212d;--tinta:#e6ebf2;--halvany:#97a3b5;--lead:#c9d1dc;--vonal:#2a3545;
  --sav:#1c2636;--sav-felulet:#263347;color-scheme:dark}}
:root[data-tema="sotet"]{
  --lap:#0e141d;--felulet:#18212d;--tinta:#e6ebf2;--halvany:#97a3b5;--lead:#c9d1dc;--vonal:#2a3545;
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
.nav-belso{max-width:1280px;margin:0 auto;padding:.55rem 1.5rem;display:flex;align-items:center;gap:.3rem;overflow-x:auto;scrollbar-width:none}
/* görgetéskor a menüsáv elején megjelenik a lap neve kicsiben (az éppen kisorsolt fejléc stílusában) */
.nav-logo{display:flex;align-items:center;flex-shrink:0;color:#fff;max-width:0;opacity:0;overflow:hidden;margin-right:0;
  transition:max-width .3s ease,opacity .25s ease,margin .3s ease;line-height:0}
.nav-logo svg{height:1.45rem;width:auto;display:block}
.nav-logo .logo-nepszava{height:1.5rem}
.fej-elrejtve .nav-logo{max-width:14rem;opacity:1;margin-right:.7rem}
/* ha a logó is megjelenik, a gombok kicsit kisebbek; ha így sem férnek el (széles betű, nagyítás), a „szuk” osztály
   tovább tömörít: elmarad a színes pötty, kisebb a betű */
.fej-elrejtve .nav-belso a:not(.nav-logo){font-size:.84rem}
.nav-belso.szuk a:not(.nav-logo){font-size:.8rem;padding:.2rem .45rem}
.nav-belso.szuk a:not(.nav-logo)::before{display:none}
@media (prefers-reduced-motion:reduce){.nav-logo{transition:none}}
/* rovatgombok: enyhén lekerekített sarok, mint a Források gombon, elöl a rovat színes pöttyével */
.nav-belso a:not(.nav-logo){flex-shrink:0;font-family:var(--cimbetu);font-stretch:var(--cim-nyujt,100%);font-size:.9rem;font-weight:var(--cim-suly,700);letter-spacing:-.005em;padding:.22rem .55rem;border-radius:5px;color:#fff;background:rgb(255 255 255/.1);border:1px solid rgb(255 255 255/.24);display:flex;align-items:center;gap:.38rem;transition:background .15s}
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
.panel{position:absolute;right:0;top:calc(100% + .2rem);width:min(22rem,90vw);background:var(--felulet);border:1px solid var(--vonal);border-radius:6px;padding:1rem;box-shadow:0 12px 32px rgb(15 23 42/.18)}
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
.elso-arnyalt .fo-hir .lead,.elso-vilagos .fo-hir .lead{color:var(--lead)}
.elso-arnyalt .fo-hir .meta,.elso-vilagos .fo-hir .meta,
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
.fo-hir .lead{color:var(--sav-lead)}
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
.masod h3{font-size:1.3rem;line-height:1.2}
@media (max-width:640px){.masod h3{font-size:1.22rem}}
.masod .lead{-webkit-line-clamp:2}
.kis{grid-column:span 2}
.fo-hir.kis{flex-direction:row;align-items:flex-start}
.fo-hir.kis .szoveg{flex:1;min-width:0}
.fo-hir.kis .belyeg{margin:1rem 1.1rem 0 -.2rem}
.belyeg .kep-helyett .logo{width:80%;max-height:60%}
.kis h3{font-size:1.05rem;line-height:1.25}
.kis .lead{display:none}   /* a Top két alsó sora szikár: cím és adatok, bevezető nélkül */
.kep{background:#2b384c;overflow:hidden}.kep img{width:100%;height:100%;object-fit:cover;display:block}

/* közös szövegelemek */
h3{font-weight:var(--cim-suly,650);letter-spacing:var(--cim-ter,-.01em)}
.meta{display:flex;gap:.55rem;align-items:baseline;font-size:.78rem;color:var(--halvany);margin-bottom:.3rem}
.forras{font-weight:700;color:var(--c)}
.lead{margin:.45rem 0 0;font-size:.9rem;line-height:1.45;color:var(--lead);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
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
/* a rovatfej vonala a több lap írta dobozok jeléhez hasonlóan kétharmadig teljes, utána kifut (de vékonyabb) */
.rovatfej::after{content:"";flex:1;height:3px;border-radius:2px;background:linear-gradient(to right,var(--r) 0,var(--r) 66%,transparent);order:3}
.csuk{all:unset;cursor:pointer;font:var(--cim-suly-nagy,750) 1.25rem/1 var(--cimbetu);font-stretch:var(--cim-nyujt,100%);color:#fff;background:var(--r);padding:.42rem .9rem .48rem;border-radius:5px;letter-spacing:-.01em}
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
/* rovatszínű sáv a nem színes kiemelt kártya tetején */
.doboz.kiemelt:not(.szines){border-top:4px solid var(--r);padding-top:calc(1rem - 3px)}
/* több lap írta: erős rovatszínű felső vonal (kétharmadig teljes, utána átlátszóba fut ki), a doboz margóján belül – nem ér a doboz széléhez,
   így nem olvad össze a fölötte lévő kiemelt dobozzal, és a lekerekített kártyákon is jól ül */
.doboz{--bp:1rem}
.rovat.ujsag .doboz{--bp:1.2rem}
.doboz.tobblap{position:relative}
.doboz.tobblap::before{content:"";position:absolute;top:7px;left:var(--bp);right:var(--bp);height:4px;
  background:linear-gradient(to right,var(--r) 0,var(--r) 66%,transparent);border-radius:2px;pointer-events:none;z-index:2}
/* a komoly, újságos rovatokban (Belföld, Külföld, Gazdaság) a vonal a doboz tetején, teljes szélességben fut */
.rovat.ujsag:not(.laza) .doboz.tobblap::before{top:0;left:0;right:0;border-radius:0}
/* „vezércikk”: kép nélküli kiemelt kártya nagy, dőlt címmel */
.doboz.vezer .kiemelt-kep{display:none}
.doboz.vezer h3{font-size:1.75rem;line-height:1.15;font-style:italic;letter-spacing:-.02em}
.doboz.vezer .lead{-webkit-line-clamp:7;font-size:1.02rem;border-top:1px solid var(--r);padding-top:.6rem;margin-top:.5rem}
.doboz.vezer.szines .lead{border-top-color:rgb(255 255 255/.5)}
/* „lapköteg”: sokat megírt sztori mögött eltolt lapok */
.koteg{--kl:var(--felulet);box-shadow:3px 3px 0 -1px var(--kl),3px 3px 0 0 color-mix(in srgb,var(--r) 38%,transparent),
  6px 6px 0 -1px var(--kl),6px 6px 0 0 color-mix(in srgb,var(--r) 24%,transparent)}
.koteg-nagy{box-shadow:3px 3px 0 -1px var(--kl),3px 3px 0 0 color-mix(in srgb,var(--r) 42%,transparent),
  6px 6px 0 -1px var(--kl),6px 6px 0 0 color-mix(in srgb,var(--r) 30%,transparent),
  9px 9px 0 -1px var(--kl),9px 9px 0 0 color-mix(in srgb,var(--r) 20%,transparent)}
.fo-hir.koteg{--kl:var(--sav-felulet);overflow:visible}
.elso-arnyalt .fo-hir.koteg,.elso-vilagos .fo-hir.koteg{--kl:var(--felulet)}
.fo-hir.koteg .kep{border-radius:6px 6px 0 0;overflow:hidden}
/* kis képek csak ott, ahol jelentenek valamit: az egy lapos sima dobozban nincs kép, a cím kiér a szélig és nagyobb;
   a több lapos (csíkos) dobozban marad a bélyeg, de ott is nagyobb a cím (az újságos rovatok kivételek) */
.rovat:not(.ujsag):not(.stadion) .doboz:not(.kiemelt):not(.tobblap) .belyeg{display:none}
.rovat:not(.ujsag) .doboz:not(.kiemelt) h3{font-size:1.24rem;line-height:1.22}
.rovat.stadion .doboz:not(.kiemelt) h3{font-size:1.2rem}
.rovat:not(.ujsag) .doboz.tobblap:not(.kiemelt) h3{font-size:1.2rem}
.fo-racs .fo-hir.kis h3{font-size:1.24rem;line-height:1.22}
.rovat.ujsag .doboz:not(.kiemelt) h3{font-size:1.2rem;line-height:1.22}
.rovat.ujsag .doboz .lead{-webkit-line-clamp:5}   /* az újságos hasábban több fér el a bevezetőből */
@media (max-width:640px){.rovat .doboz:not(.kiemelt) h3,.fo-racs .fo-hir.kis h3{font-size:1.18rem}}
/* halom: egy doboz helyén három kis kártya (lap, idő, cím) */
.halom{display:flex;flex-direction:column;gap:.7rem;min-width:0}
.halom .mini{flex:1;background:var(--felulet);border:1px solid var(--vonal);border-radius:6px;padding:.7rem 1rem .75rem;display:flex;flex-direction:column;justify-content:center}
.halom .mini.rejtett{display:none}
.halom .mini .meta{font-size:.76rem;margin-bottom:.25rem}
.halom .mini h4{margin:0;font-family:var(--cimbetu);font-stretch:var(--cim-nyujt,100%);font-weight:var(--cim-suly,600);letter-spacing:var(--cim-ter,0);font-size:1.04rem;line-height:1.22;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.halom .mini h4 a{color:var(--tinta);text-decoration:none}.halom .mini h4 a:hover{text-decoration:underline}
.rovat.ujsag .halom{gap:0;border-bottom:1px solid var(--vonal)}
.rovat.ujsag .halom .mini{background:transparent;border:0;border-radius:0;border-top:1px solid var(--vonal);padding:.8rem 1.2rem}
@media (max-width:640px){.rovat.ujsag .halom .mini{padding-left:1rem;padding-right:1rem}}
/* újságos rovatok: a dobozokból kiszorult friss rövidhírek a fehér panel alatt, a szürke háttéren
   (jelzi, hogy ideiglenesek); négy hasáb, köztük csak rövid, halvány függőleges elválasztók */
.fs-blokk{margin:.9rem 0 .6rem}
.fs-racs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));row-gap:1rem}
.fs-racs .fs{position:relative;padding:.2rem 1.2rem .4rem;min-width:0}
.fs-racs .fs:nth-child(4n+1){padding-left:0}
.fs-racs .fs:not(:nth-child(4n+1))::before{content:"";position:absolute;left:0;top:.3rem;height:2.6rem;
  border-left:1px solid color-mix(in srgb,var(--halvany) 45%,transparent)}
.fs-racs .fs.ures{display:none}
.fs-racs .fs.rejtett{display:none}
.fs-racs h4{margin:0;font-family:var(--cimbetu);font-stretch:var(--cim-nyujt,100%);font-weight:var(--cim-suly,700);
  letter-spacing:var(--cim-ter,0);font-size:.95rem;line-height:1.3;display:inline}
.fs-racs h4 .forras{font:inherit;margin-right:.3rem}
.fs-racs .fs-ido{font-size:.76rem;color:var(--halvany);margin-left:.4rem;white-space:nowrap}
.fs-racs h4 a{color:var(--tinta);text-decoration:none}.fs-racs h4 a:hover{text-decoration:underline}
.fs-racs .cikkek-gomb{vertical-align:1px}
.fs-racs .cikkek{font-family:var(--listabetu,inherit);font-weight:var(--lista-suly,400);font-size:.84rem;line-height:1.35;list-style:none;padding:0;margin:.45rem 0 0}
.fs-racs .cikkek li{display:flex;gap:.5rem;padding:.2rem 0}.fs-racs .cikkek time{color:var(--halvany);flex:none}.fs-racs .cikkek .forras{margin-right:.3rem}
.fs-tobb{display:none}
/* a Sport zöld hátterén a rövidhír-sor fehér lapon ül */
.rovat.stadion .fs-blokk{background:var(--felulet);border:1px solid var(--vonal);border-radius:6px;padding:.9rem 1.2rem .8rem;margin-top:1.2rem}
.rovat.stadion .fs-racs h4 a,.rovat.stadion .fs-racs .cikkek a,.rovat.stadion .fs-racs .cikkek li{color:var(--tinta)}
.rovat.stadion .fs-racs .fs-ido,.rovat.stadion .fs-racs .cikkek time{color:var(--halvany)}
.rovat.stadion .fs-racs .forras{color:var(--c)}
.rovat.stadion .fs-racs .cikkek-gomb{color:var(--halvany);border-color:var(--vonal)}.rovat.stadion .fs-racs .cikkek-gomb .db{font-weight:400}
.rovat.stadion .fs-tobb{color:var(--tinta)}
@media (max-width:1000px) and (min-width:641px){.fs-racs{grid-template-columns:repeat(2,minmax(0,1fr))}
  .fs-racs .fs:nth-child(4n+1){padding-left:1.2rem}.fs-racs .fs:nth-child(2n+1){padding-left:0}
  .fs-racs .fs::before{display:block}.fs-racs .fs:nth-child(2n+1)::before{display:none}}
@media (max-width:640px){.fs-racs{grid-template-columns:1fr;row-gap:0}
  .fs-racs .fs{padding:.6rem 0;border-top:1px solid color-mix(in srgb,var(--halvany) 30%,transparent)}
  .fs-racs .fs:first-child{border-top:0}.fs-racs .fs::before{display:none!important}
  .fs-blokk:not(.nyitva) .fs-racs .fs:nth-child(n+5){display:none}
  .fs-tobb{display:inline-block;margin:.4rem 0 0;background:none;border:1px solid var(--vonal);border-radius:5px;
    padding:.3rem .8rem;font:inherit;font-size:.85rem;color:var(--halvany);cursor:pointer}}
/* az Egyéb rovat friss hírei kis kártyákon, három hasábban */
.kis-kartyak{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.8rem;margin:.2rem 0 .6rem}
.kis-kartyak .mini{background:var(--felulet);border:1px solid var(--vonal);border-radius:6px;padding:.75rem 1rem .85rem}
.kis-kartyak .mini.rejtett{display:none}
.kis-kartyak .mini .meta{font-size:.76rem;margin-bottom:.25rem}
.kis-kartyak .mini h4{margin:0;font-weight:var(--cim-suly,600);letter-spacing:var(--cim-ter,0);font-size:1.06rem;line-height:1.22}
.kis-kartyak .mini h4 a{color:var(--tinta);text-decoration:none}.kis-kartyak .mini h4 a:hover{text-decoration:underline}
.kis-kartyak .mini .lead{margin-top:.3rem;font-size:.86rem;-webkit-line-clamp:2}
/* széles képernyőn a kis kártyák címe nagyobb (van rá hely); keskenyebb ablakban csonkulnának, ott marad a régi méret */
@media (min-width:1200px){.halom .mini h4,.kis-kartyak .mini h4{font-size:1.14rem;line-height:1.22}}
@media (max-width:1000px){.kis-kartyak{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:640px){.kis-kartyak{grid-template-columns:1fr}}
/* a dobozokból kiszorult friss hírek sávja a rovat alján */
.friss-sav{margin:1.2rem 0 .5rem;background:var(--felulet);border:1px solid var(--vonal);border-radius:6px;padding:.35rem 1.2rem .5rem}
.friss-sav .lista{margin:0}
.friss-sav .lista time{min-width:2.9rem;white-space:nowrap}
.friss-sav .sor-cim>a{font-weight:600}
.rovat.stadion .friss-sav .lista li,.rovat.stadion .friss-sav .lista a{color:var(--tinta)}
.rovat.stadion .friss-sav .lista time{color:var(--halvany)}.rovat.stadion .friss-sav .lista li{border-color:var(--vonal)}
.rovat.stadion .friss-sav .lista .forras{color:var(--c)}
.rovat.stadion .friss-sav .cikkek-gomb{color:var(--halvany);border-color:var(--vonal)}.rovat.stadion .friss-sav .cikkek-gomb .db{font-weight:400}.rovat.stadion .friss-sav .cikkek-gomb:hover{color:var(--tinta);border-color:var(--halvany)}
/* fekvő kiemelések (Top nyitóhír, magazinos rovatok): balra a szöveg tömör színen, jobbra a kép vízszintes átmenettel */
@media (min-width:1001px){
.elso .top-keret .fo-racs .nyito.nyito{position:relative;display:block;min-height:19rem;overflow:hidden}
.elso .top-keret .fo-racs .nyito.nyito .kep{position:absolute;inset:0 0 0 calc((100% - 1px) / 2);margin:0;flex:none;width:auto;border-radius:0}
.elso .top-keret .fo-racs .nyito.nyito .kep img{width:100%;height:100%;object-fit:cover;object-position:center right}
/* a nyitóhírnél éles határ: balra a fejlécszínű szövegpanel, jobbra a kép, pontosan a rács középvonalán */
.elso .top-keret .fo-racs .nyito.nyito .szoveg{position:relative;z-index:2;width:calc((100% - 1px) / 2);padding:1.6rem 1.6rem 1.4rem 1.8rem}   /* a szöveg csak a tömör színen */
.rovat .racs .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas){position:relative;display:block;min-height:17rem}
.rovat .racs .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas) .kb{position:absolute;inset:0 0 0 30%;flex:none;min-height:0}
.rovat .racs .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas) .kiemelt-kep img{object-position:center right}
.rovat .racs .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas)::after{content:"";position:absolute;inset:0;z-index:1;pointer-events:none;
  background:linear-gradient(90deg,var(--mp) 0,var(--mp) 40%,color-mix(in srgb,var(--mp) 70%,transparent) 52%,transparent 72%)}
.rovat .racs .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas) .szoveg{position:relative;z-index:2;width:52%;min-height:17rem;
  display:flex;flex-direction:column;justify-content:flex-end}
}
/* magazinos kiemelt (Kultúra, Életmód, Sport): sötét rovatszínű panel fehér betűkkel;
   fekvőnél balra a kép a doboz teljes magasságában, jobbra a cím és a szöveg; állónál a kép kitölti a dobozt, a cím a képre írva */
.rovat .racs .doboz.kiemelt.magazin.magazin{--mp:color-mix(in srgb,var(--r) 70%,#0b0f17);padding:0!important;gap:0;display:flex;overflow:hidden;border-top:0!important;background:var(--mp);color:#fff}
.rovat .doboz.kiemelt.magazin.magazin .kb{position:relative;overflow:hidden;background:color-mix(in srgb,var(--r) 25%,var(--felulet))}
.rovat .doboz.kiemelt.magazin.magazin .kiemelt-kep{position:absolute;inset:0;width:auto;aspect-ratio:auto;border-radius:0;margin:0;order:0}
.rovat .doboz.kiemelt.magazin.magazin .kiemelt-kep img{width:100%;height:100%;object-fit:cover}
.rovat .doboz.kiemelt.magazin.magazin .szoveg{flex:1;padding:1rem 1.2rem 1rem}
.rovat .doboz.kiemelt.magazin.magazin .lead{-webkit-line-clamp:6}
.rovat .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas){flex-direction:row;align-items:stretch}
.rovat .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas) .kb{flex:0 0 52%;min-height:16rem;align-self:stretch}
.rovat .doboz.kiemelt.magazin.magazin.kiemelt-magas{flex-direction:column}
.rovat .doboz.kiemelt.magazin.magazin.kiemelt-magas .kb{width:100%;flex:1 1 auto;min-height:16rem}   /* a kép kitölti a doboz magasságát */
.rovat .doboz.kiemelt.magazin.magazin.kiemelt-magas .szoveg{flex:0 0 auto}
.rovat .doboz.kiemelt.magazin.magazin.kiemelt-magas .lead{-webkit-line-clamp:4}
.rovat .doboz.kiemelt.magazin.magazin .rcim{position:absolute;left:0;right:0;bottom:0;z-index:1;padding:3rem 1.2rem .9rem;
  background:linear-gradient(to bottom,transparent,rgb(0 0 0/.72) 55%)}
.rovat .doboz.kiemelt.magazin.magazin .rcim h3{color:#fff;margin:0;text-shadow:0 1px 6px rgb(0 0 0/.45)}
.rovat .doboz.kiemelt.magazin.magazin .rcim h3 a,.rovat .doboz.kiemelt.magazin.magazin .rcim .forras{color:#fff}
.rovat .doboz.kiemelt.magazin.magazin .rcim .meta{color:rgb(255 255 255/.82)}
.rovat .doboz.kiemelt.magazin.magazin.kiemelt-magas .lead{margin-top:0}
/* álló kiemelés: a kép alja átmenet nélküli törés helyett a panel rovatszínébe olvad */
.rovat .doboz.kiemelt.magazin.magazin.kiemelt-magas .rcim{padding-bottom:.6rem;
  background:linear-gradient(to bottom,transparent 0,color-mix(in srgb,var(--mp) 55%,transparent) 40%,var(--mp) 100%)}
.rovat .doboz.kiemelt.magazin.magazin.kiemelt-magas .rcim h3{text-shadow:0 1px 8px rgb(0 0 0/.35)}
/* a Sport zöld pályáján fehér marad a doboz */
.rovat .racs .doboz.kiemelt.magazin.magazin .szoveg h3,.rovat .racs .doboz.kiemelt.magazin.magazin .szoveg h3 a,.rovat .racs .doboz.kiemelt.magazin.magazin .szoveg .forras{color:#fff}
.rovat .racs .doboz.kiemelt.magazin.magazin .szoveg .meta{color:rgb(255 255 255/.8)}
.rovat .racs .doboz.kiemelt.magazin.magazin .lead{color:rgb(255 255 255/.9)}
.rovat .racs .doboz.kiemelt.magazin.magazin .lefed{color:#fff}
.rovat .racs .doboz.kiemelt.magazin.magazin .lefed i{box-shadow:0 0 0 1.5px rgb(0 0 0/.35)}
.rovat .racs .doboz.kiemelt.magazin.magazin .tovabbi,.rovat .racs .doboz.kiemelt.magazin.magazin .tovabbi a{color:rgb(255 255 255/.8)}
.rovat .racs .doboz.kiemelt.magazin.magazin .tovabbi a.mutatott{color:#fff}
.rovat .racs .doboz.kiemelt.magazin.magazin .lapoz{background:rgb(255 255 255/.12);border-color:rgb(255 255 255/.45);color:#fff}
@media (max-width:1000px){.rovat .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas) .kb::after{content:"";position:absolute;
  left:0;right:0;bottom:0;height:55%;pointer-events:none;background:linear-gradient(to bottom,transparent,var(--mp))}
  .rovat .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas) .szoveg{margin-top:-1.2rem;position:relative;z-index:1}}
@media (max-width:640px){.rovat .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas){flex-direction:column}
  .rovat .doboz.kiemelt.magazin.magazin:not(.kiemelt-magas) .kb{width:100%;flex:none;aspect-ratio:16/10;min-height:0}}
/* a Helyi rovat dobozaiban nincs kép, a több lapos témáknál sem */
.rovat.helyi .doboz .belyeg{display:none}
/* Top nyitóhír: a cím a képre írva */
@media (max-width:640px){
.fo-racs .fo-hir.rairt-kicsi{position:relative;min-height:21rem;overflow:hidden}
.fo-racs .fo-hir.rairt-kicsi .kep{position:absolute;inset:0;flex:none;min-height:0;height:auto;aspect-ratio:auto}
.fo-racs .fo-hir.rairt-kicsi .kep img{width:100%;height:100%;object-fit:cover}
.fo-racs .fo-hir.rairt-kicsi .szoveg{position:relative;z-index:1;margin-top:auto;align-self:flex-end;width:100%;justify-content:flex-end;
  padding:3.5rem 1.1rem 1.1rem;color:#fff;background:linear-gradient(to top,var(--fejszin,#16202f) 0%,color-mix(in srgb,var(--fejszin,#16202f) 75%,transparent) 55%,transparent 100%)}
.fo-racs .fo-hir.rairt-kicsi h3{max-width:46rem;font-size:clamp(1.55rem,2.4vw,2.2rem);text-shadow:0 1px 8px rgb(0 0 0/.35)}
.fo-racs .fo-hir.rairt-kicsi h3 a,.fo-racs .fo-hir.rairt-kicsi .forras,.fo-racs .fo-hir.rairt-kicsi .lefed,.fo-racs .fo-hir.rairt-kicsi .tovabbi a,.fo-racs .fo-hir.rairt-kicsi .tovabbi a.mutatott{color:#fff}
.fo-racs .fo-hir.rairt-kicsi .meta,.fo-racs .fo-hir.rairt-kicsi .lead,.fo-racs .fo-hir.rairt-kicsi .tovabbi{color:rgb(255 255 255/.85)}
.fo-racs .fo-hir.rairt-kicsi .lead{max-width:46rem}
.fo-racs .fo-hir.rairt-kicsi .lefed i{box-shadow:0 0 0 1.5px rgb(0 0 0/.6)}
.fo-racs .fo-hir.rairt-kicsi .lapoz{background:rgb(255 255 255/.12);border-color:rgb(255 255 255/.45);color:#fff}
.fo-racs .fo-hir.rairt-kicsi .kep-helyett{background:color-mix(in srgb,var(--r) 35%,#111)}}

/* Top: egy közös fehér panel – balra a nyitóhír és alatta 2×2 képre írt című hír, jobbra a lapszéli hasáb */
.top-keret{display:grid;grid-template-columns:3fr 1fr;background:var(--felulet);border:1px solid var(--vonal);border-radius:6px;overflow:hidden}
.elso .top-keret .fo-racs{grid-template-columns:repeat(2,minmax(0,1fr));border:0;border-radius:0;border-right:1px solid var(--vonal)}
.elso .top-keret .nyito{grid-column:1 / -1}
.elso .top-keret .masod{grid-column:auto}
/* 2×2: a képen (alsó részén, sötét átmenettel) a lap, az idő és a cím; alatta fehéren a bevezető, a lapok, a lapozó */
.elso .top-keret .fo-racs .fo-hir.rairt.rairt{background:var(--felulet)}
.elso .top-keret .fo-racs .fo-hir.rairt.rairt .kep-blokk{position:relative;aspect-ratio:2/1;overflow:hidden;
  background:color-mix(in srgb,var(--r) 45%,#0b0f17)}
.elso .top-keret .fo-racs .fo-hir.rairt.rairt .kep{position:absolute;inset:0;margin:0;border-radius:0;aspect-ratio:auto}
.elso .top-keret .fo-racs .fo-hir.rairt.rairt .kep img{width:100%;height:100%;object-fit:cover}
.elso .top-keret .fo-racs .fo-hir.rairt.rairt .rcim{position:absolute;left:0;right:0;bottom:0;z-index:1;padding:3rem 1.2rem .8rem;
  background:linear-gradient(to bottom,transparent,rgb(0 0 0/.75) 55%)}
.elso .top-keret .fo-racs .fo-hir.rairt.rairt .rcim h3 a,.elso .top-keret .fo-racs .fo-hir.rairt.rairt .rcim .forras{color:#fff}
.elso .top-keret .fo-racs .fo-hir.rairt.rairt .rcim h3{color:#fff;text-shadow:0 1px 6px rgb(0 0 0/.45);margin:0}
.elso .top-keret .fo-racs .fo-hir.rairt.rairt .rcim .meta{color:rgb(255 255 255/.82)}
.elso .top-keret .fo-racs .fo-hir.rairt.rairt .szoveg{padding:.7rem 1.2rem 1rem}
.elso .top-keret .fo-racs .fo-hir.rairt.rairt .lead{margin-top:0;-webkit-line-clamp:2}
/* a nyitóhír szövegpanelje: mobilon a képre írt cím alatt a fejléc (napszakonként váltakozó) színe, fehér betűkkel */
.elso .top-keret .fo-racs .nyito.nyito{background:var(--fejszin,#16202f)}
.elso .top-keret .fo-racs .nyito.nyito h3,.elso .top-keret .fo-racs .nyito.nyito h3 a,.elso .top-keret .fo-racs .nyito.nyito .lefed{color:#fff}
.elso .top-keret .fo-racs .nyito.nyito .forras{color:#fff}   /* a lapnév fehér, mint a képre írt címeknél; csak a pöttyök színesek */
.elso .top-keret .fo-racs .nyito.nyito .meta{color:rgb(255 255 255/.75)}
.elso .top-keret .fo-racs .nyito.nyito .lead{color:rgb(255 255 255/.88)}
.elso .top-keret .fo-racs .nyito.nyito .lefed i{box-shadow:0 0 0 1.5px rgb(0 0 0/.3)}
.elso .top-keret .fo-racs .nyito.nyito .tovabbi,.elso .top-keret .fo-racs .nyito.nyito .tovabbi a{color:rgb(255 255 255/.75)}
.elso .top-keret .fo-racs .nyito.nyito .tovabbi a.mutatott{color:#fff}
.elso .top-keret .fo-racs .nyito.nyito .lapoz{background:rgb(255 255 255/.1);border-color:rgb(255 255 255/.4);color:#fff}
/* asztali nézetben (ahol a szöveg nem a képen ül) a fejlécszín halvány árnyalata, sötét betűkkel: napszakonként
   finoman változik, de nem nehéz. Mobilon a képre írt változat marad, ott a fenti fehér betűk kellenek. */
@media (min-width:641px){
.elso .top-keret .fo-racs .nyito.nyito.nyito{background:color-mix(in srgb,var(--fejszin,#16202f) 11%,var(--felulet))}
.elso .top-keret .fo-racs .nyito.nyito.nyito h3,.elso .top-keret .fo-racs .nyito.nyito.nyito h3 a,.elso .top-keret .fo-racs .nyito.nyito.nyito .lefed{color:var(--tinta)}
.elso .top-keret .fo-racs .nyito.nyito.nyito .forras{color:var(--c)}
.elso .top-keret .fo-racs .nyito.nyito.nyito .meta{color:var(--halvany)}
.elso .top-keret .fo-racs .nyito.nyito.nyito .lead{color:var(--lead)}
.elso .top-keret .fo-racs .nyito.nyito.nyito .lefed i{box-shadow:none}
.elso .top-keret .fo-racs .nyito.nyito.nyito .tovabbi,.elso .top-keret .fo-racs .nyito.nyito.nyito .tovabbi a{color:var(--halvany)}
.elso .top-keret .fo-racs .nyito.nyito.nyito .tovabbi a.mutatott{color:var(--tinta)}
.elso .top-keret .fo-racs .nyito.nyito.nyito .lapoz{background:var(--felulet);border-color:var(--vonal);color:var(--tinta)}
}
@media (min-width:641px) and (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]) .elso .top-keret .fo-racs .nyito.nyito.nyito{background:color-mix(in srgb,var(--fejszin,#16202f) 38%,var(--felulet))}}
@media (min-width:641px){:root[data-tema="sotet"] .elso .top-keret .fo-racs .nyito.nyito.nyito{background:color-mix(in srgb,var(--fejszin,#16202f) 38%,var(--felulet))}}
/* élő tudósítás: piros jelzés a lapnév előtt, a korábbi részek lenyitható listában */
.elo-jel{display:inline-flex;align-items:center;gap:.32rem;font-size:.68rem;font-weight:800;letter-spacing:.06em;
  text-transform:uppercase;color:#dc2626;margin-right:.55rem;white-space:nowrap}
.elo-jel::before{content:"";width:.48rem;height:.48rem;border-radius:50%;background:currentColor}
.elo-jel.elo-most::before{animation:elo-pulzus 1.6s ease-in-out infinite}
@keyframes elo-pulzus{0%,100%{box-shadow:0 0 0 0 rgb(220 38 38/.55)}60%{box-shadow:0 0 0 .38rem rgb(220 38 38/0)}}
@media (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]) .elo-jel{color:#f87171}}
:root[data-tema="sotet"] .elo-jel{color:#f87171}
.sorozat{margin-top:.6rem;font-size:.84rem;font-family:var(--listabetu,inherit)}
.sorozat summary{cursor:pointer;color:var(--halvany);font-weight:600;list-style:none}
.sorozat summary::-webkit-details-marker{display:none}
.sorozat summary::after{content:" ▾"}.sorozat[open] summary::after{content:" ▴"}
.sorozat ul{list-style:none;margin:.45rem 0 .3rem;padding:0 0 0 .75rem;border-left:2px solid color-mix(in srgb,#dc2626 45%,var(--vonal))}
.sorozat li{margin:.3rem 0;line-height:1.35}
.sorozat time{color:var(--halvany);margin-right:.45rem;font-variant-numeric:tabular-nums;font-size:.78rem}
.sorozat a{color:var(--tinta);text-decoration:none}.sorozat a:hover{text-decoration:underline}
.sorozat .elo-gyujto{display:inline-block;margin:.15rem 0 0 .75rem;font-weight:700;color:#dc2626}
/* lapszéli hasáb: hat téma tömören, rövid vonallal elválasztva, egyenletesen elosztva */
.top-szel{display:flex;flex-direction:column;justify-content:space-between;padding:.4rem 1.2rem .5rem}
.elso .top-szel .fo-hir.kis{display:block;background:none;border:0;border-radius:0;padding:.8rem 0 .7rem;position:relative;overflow:visible}
.elso .top-szel .fo-hir.kis+.fo-hir.kis::before{content:"";position:absolute;top:0;left:0;width:40%;border-top:1px solid var(--vonal)}
.elso .top-szel .fo-hir.kis .szoveg{padding:0;display:block}
.elso .top-szel .fo-hir.kis h3{display:block;margin:0;font-size:.98rem;line-height:1.32}
/* széles képernyőn a lapszéli hasábban van hely: nagyobb, jobban olvasható címek (keskenyebb ablakban a hasáb már így is teli) */
@media (min-width:1200px){.elso .top-szel .fo-hir.kis h3{font-size:1.12rem;line-height:1.3}}
.elso .top-szel .fo-hir.kis h3 a{color:var(--tinta)}
.elso .top-szel .fo-hir.kis h3 .meta{display:inline;margin:0;font:inherit;height:auto}
.elso .top-szel .fo-hir.kis h3 .forras{font:inherit;color:var(--c);margin-right:.3rem}
.elso .top-szel .fo-hir.kis h3 .ido{font-family:var(--listabetu,inherit);font-weight:400;font-size:.74rem;color:var(--halvany);margin-left:.2rem;white-space:nowrap}
.top-szel .szel-alja{display:grid;grid-template-columns:1fr auto;grid-template-areas:"lefed lapozo" "lapok lapok";align-items:center;column-gap:.5rem;margin-top:.45rem}
.top-szel .szel-alja .lefed{grid-area:lefed}
.elso .top-szel .fo-hir.kis .lefed{margin:0;font-size:.72rem;font-weight:500;color:var(--halvany)}
.elso .top-szel .fo-hir.kis .lefed i{width:.5rem;height:.5rem}
.elso .top-szel .fo-hir.kis .tovabbi{display:contents}
.elso .top-szel .fo-hir.kis .lapozo{grid-area:lapozo;margin:0;position:static;align-self:center}
.elso .top-szel .fo-hir.kis .tovabbi-lapok{grid-area:lapok;font-size:.74rem;line-height:1.4;color:var(--halvany);padding:0;margin-top:.25rem}
.elso .top-szel .fo-hir.kis .tovabbi-lapok a{color:var(--halvany)}.elso .top-szel .fo-hir.kis .tovabbi-lapok a.mutatott{color:var(--tinta)}
.elso .top-szel .fo-hir.kis .lapozo{gap:.25rem}
.elso .top-szel .fo-hir.kis .lapoz{width:1.3rem;height:1.3rem;min-width:0;padding:0;font-size:.8rem;line-height:1;border-radius:4px}
@media (max-width:1000px){.top-keret{grid-template-columns:1fr}.elso .top-keret .fo-racs{border-right:0;border-bottom:1px solid var(--vonal)}
  .top-szel{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:1.4rem}
  .top-szel .fo-hir.kis:nth-child(2)::before{display:none}}
@media (max-width:640px){.elso .top-keret .fo-racs{grid-template-columns:1fr}.top-szel{grid-template-columns:1fr}
  .top-szel .fo-hir.kis:nth-child(2)::before{display:block}.elso .fo-racs .fo-hir.rairt{min-height:15rem}}
/* KÍSÉRLET: a Top is újságoldal – egy fehér lap, a hírek között vékony vonalak (a rács 1 px-es hézaga) */
.elso .fo-racs{gap:1px;background:var(--vonal);border:1px solid var(--vonal);border-radius:6px;overflow:hidden}
.elso .fo-racs .fo-hir{background:var(--felulet);border:0;border-radius:0;box-shadow:none}
.elso .fo-racs .fo-hir:not(.rairt-kicsi) .lead{color:var(--lead)}
.elso .fo-racs .fo-hir:not(.rairt-kicsi) .meta,.elso .fo-racs .fo-hir:not(.rairt-kicsi) .tovabbi{color:var(--halvany)}
.elso .fo-racs .fo-hir:not(.rairt-kicsi) .forras{color:var(--c)}
.elso .fo-racs .fo-hir:not(.rairt-kicsi) .lefed{color:var(--tinta)}
.elso .fo-racs .fo-hir:not(.rairt-kicsi) h3 a{color:var(--tinta)}
.elso .fo-racs .nyito{background:color-mix(in srgb,var(--fejszin,#16202f) 5%,var(--felulet))}
.elso .fo-racs .fo-hir:not(.rairt-kicsi) .kep{margin:1rem 1.1rem 0;border-radius:3px;overflow:hidden}
.elso .fo-racs .nyito:not(.rairt-kicsi) .kep{margin:1.2rem 0 1.2rem 1.2rem}
/* újságoldal: hasábok vonalakkal, kártyák nélkül (Kultúra, Életmód) */
.rovat.ujsag .racs{gap:0;border:1px solid var(--vonal);background-color:var(--felulet);border-radius:6px;overflow:hidden;
  background-image:linear-gradient(var(--vonal),var(--vonal)),linear-gradient(var(--vonal),var(--vonal));
  background-size:1px 100%;background-repeat:no-repeat;background-position:33.333% 0,66.667% 0}
.rovat.ujsag .doboz{background:transparent;border:0;border-radius:0;box-shadow:none;padding:1rem 1.2rem}
.rovat.ujsag .doboz:not(.kiemelt){border-bottom:1px solid var(--vonal)}
.rovat.ujsag .doboz.kiemelt{background:color-mix(in srgb,var(--r) 9%,var(--felulet));border-top:0;border-bottom:1px solid var(--vonal);padding-top:1rem}
.rovat.ujsag .lapoz{background:transparent}
/* lazább második fél (Kultúra, Tech, Életmód): az újságos rács marad, a lapnév előtt rovatszínű négyzet,
   keskeny címbetű; az összevont hírek képes dobozok, a cím a képre írva */
.rovat.laza .doboz.kiemelt{border-radius:6px;border-bottom:0}
.rovat.laza .doboz:not(.kiemelt) .meta .forras::before,.rovat.laza .halom .forras::before{content:"";display:inline-block;width:.55rem;height:.55rem;
  border-radius:2px;background:var(--r);margin-right:.4rem;vertical-align:.05rem}
/* a második fél (Kultúra, Tech, Életmód, Sport, Vegyes) címei stílustól függetlenül közös, keskeny, karakteres betűvel (Saira Semi Condensed) */
:is(.rovat.laza,.rovat.stadion) :is(.doboz h3,.halom h4){font-family:"Saira Semi Condensed","Arial Narrow",var(--cimbetu);font-weight:700;
  font-stretch:100%;letter-spacing:0;line-height:1.16}
.vegyes-lista li a{font-family:"Saira Semi Condensed","Arial Narrow",var(--listabetu,inherit);font-weight:700;font-stretch:100%;font-size:1rem}
.rovat.laza .doboz:not(.kiemelt) h3{font-size:1.28rem}
.rovat.stadion .doboz:not(.kiemelt) h3,.rovat.stadion .doboz.tobblap:not(.kiemelt) h3{font-size:1.24rem}
:is(.rovat.laza,.rovat.stadion) .halom h4{font-size:1.1rem}
:is(.rovat.laza,.rovat.stadion) .doboz.kepes{flex-direction:column}
:is(.rovat.laza,.rovat.stadion) .kepfej{position:relative;aspect-ratio:16/9;border-radius:5px;overflow:hidden;margin-bottom:.7rem;background:var(--vonal)}
:is(.rovat.laza,.rovat.stadion) .kepfej .kep{position:absolute;inset:0;margin:0}
:is(.rovat.laza,.rovat.stadion) .kepfej img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
:is(.rovat.laza,.rovat.stadion) .kepfej::after{content:"";position:absolute;inset:0;background:linear-gradient(to top,rgb(0 0 0/.82),rgb(0 0 0/.35) 45%,transparent 70%);pointer-events:none}
:is(.rovat.laza,.rovat.stadion) .kepfej .ra{position:absolute;left:0;right:0;bottom:0;z-index:1;padding:.8rem 1rem .85rem}
:is(.rovat.laza,.rovat.stadion) .kepfej .meta,:is(.rovat.laza,.rovat.stadion) .kepfej .meta *{color:#fff}
:is(.rovat.laza,.rovat.stadion) .kepfej .forras::before{background:#fff}
:is(.rovat.laza,.rovat.stadion) .kepfej h3{margin:.25rem 0 0;font-size:1.3rem;line-height:1.18}
:is(.rovat.laza,.rovat.stadion) .kepfej h3 a{color:#fff}
:is(.rovat.laza,.rovat.stadion) .doboz.kepes .lead{-webkit-line-clamp:3}
/* Sport: „stadion” – a rovat sötét pályazöld sávon, fűcsíkokkal, félpályavonallal és középkörrel */
.rovat.stadion{--pa:#14532d;position:relative;padding:1.6rem 0 1.8rem;
  background:radial-gradient(circle at 50% 50%,transparent 0 118px,rgb(255 255 255/.10) 119px 121px,transparent 122px),
    linear-gradient(90deg,transparent calc(50% - 1px),rgb(255 255 255/.10) calc(50% - 1px) calc(50% + 1px),transparent calc(50% + 1px)),
    repeating-linear-gradient(90deg,rgb(255 255 255/.035) 0 90px,transparent 90px 180px),var(--pa);
  box-shadow:0 0 0 100vmax var(--pa);clip-path:inset(0 -100vmax)}
@media (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]) .rovat.stadion{--pa:#0d331c}}
:root[data-tema="sotet"] .rovat.stadion{--pa:#0d331c}
.rovat.stadion .rovatfej::after{background:linear-gradient(to right,#fff 0,#fff 66%,transparent);opacity:.75}
.rovat.stadion .rovatfej .csuk{background:#fff;color:var(--pa)}
.rovat.stadion .rovatfej .db{color:rgb(255 255 255/.7)}
.rovat.stadion .doboz{border-color:transparent;box-shadow:0 2px 10px rgb(0 0 0/.25)}
.rovat.stadion .doboz.kiemelt:not(.szines){border-top:1px solid transparent;padding-top:.9rem}
.rovat.stadion .tovabbi-hirek summary,.rovat.stadion .lista li,.rovat.stadion .lista a,.rovat.stadion .lista time{color:rgb(255 255 255/.92)}
.rovat.stadion .lista li{border-color:rgb(255 255 255/.15)}

.rovat.stadion .lista .forras{color:rgb(255 255 255/.65)}
.rovat.stadion .cikkek-gomb{color:rgb(255 255 255/.9);border-color:rgb(255 255 255/.5)}.rovat.stadion .cikkek-gomb .db{color:inherit;font-weight:600;order:0;font-size:inherit}.rovat.stadion .cikkek-gomb:hover{color:#fff;border-color:#fff}
.rovat.stadion .cikkek{border-left-color:rgb(255 255 255/.25)}
/* Időjárás: „égbolt” – mélykék sáv halvány felhőfoltokkal és ferde esőcsíkokkal */
.rovat.idojaras{--eg:#075985;position:relative;padding:1.6rem 0 1.8rem;
  background:
    radial-gradient(ellipse 150px 46px at 8% 14%,rgb(255 255 255/.16),transparent 72%),
    radial-gradient(ellipse 110px 40px at 14% 9%,rgb(255 255 255/.14),transparent 72%),
    radial-gradient(ellipse 190px 52px at 46% 6%,rgb(255 255 255/.12),transparent 72%),
    radial-gradient(ellipse 130px 44px at 52% 2%,rgb(255 255 255/.11),transparent 72%),
    radial-gradient(ellipse 170px 50px at 86% 12%,rgb(255 255 255/.16),transparent 72%),
    radial-gradient(ellipse 120px 42px at 93% 7%,rgb(255 255 255/.14),transparent 72%),
    radial-gradient(ellipse 160px 48px at 28% 92%,rgb(255 255 255/.10),transparent 72%),
    radial-gradient(ellipse 180px 50px at 72% 96%,rgb(255 255 255/.10),transparent 72%),
    repeating-linear-gradient(105deg,transparent 0 18px,rgb(255 255 255/.075) 18px 20px,transparent 20px 40px),
    var(--eg);
  box-shadow:0 0 0 100vmax var(--eg);clip-path:inset(0 -100vmax)}
@media (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]) .rovat.idojaras{--eg:#082f49}}
:root[data-tema="sotet"] .rovat.idojaras{--eg:#082f49}
.rovat.idojaras .rovatfej::after{background:linear-gradient(to right,#fff 0,#fff 66%,transparent);opacity:.75}
.rovat.idojaras .rovatfej .csuk{background:#fff;color:var(--eg)}
.rovat.idojaras .rovatfej .db{color:rgb(255 255 255/.7)}
.rovat.idojaras .ido-hely,.rovat.idojaras .ido-hirek,.rovat.idojaras .doboz{border-color:transparent;box-shadow:0 2px 10px rgb(0 0 0/.25)}
/* Egyéb: egyszerű, kártyák nélküli címlista */
/* Vegyes: sűrű, háromhasábos lista fehér lapon (oszloponként fentről lefelé olvasható) */
.vegyes-lista{list-style:none;margin:.6rem 0 .4rem;padding:.4rem 1.2rem;background:var(--felulet);border:1px solid var(--vonal);border-radius:6px;
  display:grid;grid-template-columns:repeat(3,minmax(0,1fr));grid-template-rows:repeat(var(--sorok),auto);grid-auto-flow:column;column-gap:1.6rem}
.vegyes-lista li{padding:.55rem 0;border-bottom:1px solid var(--vonal);font-family:var(--listabetu,inherit);font-stretch:var(--lista-nyujt,100%);
  font-weight:var(--lista-suly,500);font-size:.93rem;line-height:1.32}
.vegyes-lista li .forras{margin-right:.35rem;font-weight:700}
.vegyes-lista li a{color:var(--tinta)}.vegyes-lista li a:hover{text-decoration:underline}
.vegyes-lista li time{font-size:.74rem;color:var(--halvany);margin-left:.4rem;white-space:nowrap;font-family:system-ui,sans-serif;font-weight:400}
.vegyes-tobb{display:none}
@media (max-width:900px){.vegyes-lista{grid-template-columns:1fr;grid-template-rows:none;grid-auto-flow:row}
  .vegyes-lista:not(.nyitva) li:nth-child(n+7){display:none}
  .vegyes-tobb{all:unset;display:block;cursor:pointer;margin:.1rem 0 .3rem;font:600 .86rem system-ui,sans-serif;color:var(--halvany)}}
.rovat.egyszeru .rovatcim{font-size:1rem;padding:.25rem .7rem}
.rovat.egyszeru .friss-sav{background:var(--felulet);margin-top:.2rem}.rovat.egyszeru .friss-sav .lista time{min-width:5.4rem}
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
.lista li{font-family:var(--listabetu,inherit);font-stretch:var(--lista-nyujt,100%);font-weight:var(--lista-suly,400);break-inside:avoid;padding:.45rem 0;border-bottom:1px solid var(--vonal);font-size:.93rem;display:flex;gap:.8rem;align-items:baseline;line-height:1.35}
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
/* csapadékradar: a HungaroMet 1024×768-as képéből csak Magyarország és közvetlen környéke látszik
   (a kép saját feliratai, jelmagyarázata kívül esnek) */
.radar{padding:.9rem 1rem .8rem}
.radar h3{display:flex;align-items:baseline;gap:.5rem}
.radar h3 small{font-size:.78rem;font-weight:500;color:var(--halvany)}
.radar-kep{position:relative;aspect-ratio:600/380;overflow:hidden;border-radius:4px;background:#cfd8d6;margin-top:.6rem}
.radar-kep img{position:absolute;width:170.67%;left:-35.83%;top:-48.68%;max-width:none;height:auto}
.radar-sav{display:flex;align-items:center;gap:.6rem;margin-top:.55rem;font-size:.74rem;color:var(--halvany);font-variant-numeric:tabular-nums}
.radar-gomb{all:unset;cursor:pointer;width:1.9rem;height:1.9rem;border-radius:50%;background:var(--r);color:#fff;display:grid;place-items:center;font-size:.72rem;flex:none}
.radar-gomb:focus-visible{outline:2px solid var(--r);outline-offset:2px}
.radar-csik{flex:1;height:6px;border-radius:3px;background:var(--vonal);position:relative;cursor:pointer}
.radar-csik i{position:absolute;inset:0 auto 0 0;border-radius:3px;background:var(--r)}
.radar-jel{display:flex;align-items:center;gap:.35rem;margin-top:.45rem;font-size:.72rem;color:var(--halvany)}
.radar-jel b{display:inline-block;width:7rem;height:.5rem;border-radius:2px;background:linear-gradient(90deg,#0a2fb0,#1ea0d6,#22c55e,#a3d613,#f5c400,#f07800,#e00000,#8b0000)}
.radar-forras{font-size:.7rem;color:var(--halvany);margin:.4rem 0 0}.radar-forras a{text-decoration:underline}
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
/* széles képernyőn három, közepesen két oszlop; előbb a bal telik meg fentről lefelé, így az ABC-sorrend egyben olvasható.
   Kompakt sorok, hogy a bal oldali kártya mellett elférjen; telefonon lenyíló lista */
.meres-hasabok{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));column-gap:1.3rem;margin-top:.4rem}
.meres-hasab h4{margin:0;padding:.2rem 0 .25rem;font:700 .68rem/1.2 system-ui,sans-serif;letter-spacing:.08em;text-transform:uppercase;
  color:var(--halvany);border-bottom:2px solid var(--vonal)}
.meres-lista{list-style:none;margin:0;padding:0}
.meres-lista li{display:grid;grid-template-columns:minmax(0,1fr) 22px 2.4rem;grid-template-rows:auto auto;column-gap:.4rem;align-items:center;
  padding:.3rem 0;border-bottom:1px solid var(--vonal)}
.meres-lista li svg{width:22px;height:22px}
.meres-nyito{display:none}
.meres-nev{font-weight:650;font-size:.86rem;line-height:1.2}
/* az állomás neve és (ha eltér) az időpont a helynév mellett, egy sorban: így egy állomás két sor */
.meres-nev small{display:inline;margin-left:.3rem;font-weight:400;font-size:.68rem;color:var(--halvany)}
.meres-nev small i{font-style:normal}
.meres-fok{font:750 1.12rem/1 var(--cimbetu);text-align:right;letter-spacing:-.02em}
.meres-reszlet{grid-column:1/-1;font-size:.68rem;line-height:1.3;color:var(--halvany);min-height:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.meres-reszlet:empty{display:none}
.ido-ikon-ures{display:inline-block}
.meres-forras{font-size:.72rem;color:var(--halvany);margin:.6rem 0 0}.meres-forras a{text-decoration:underline}
/* a hasábok száma a panel tényleges szélességéhez igazodik (nem az ablakéhoz): így a nagyított nézetű
   tableten is három hasáb marad, amíg elfér; az állomásnév kiegészítése szükség esetén „…”-ra rövidül */
.ido-meres{container-type:inline-size}
.meres-nev{display:flex;align-items:baseline;min-width:0;white-space:nowrap}
.meres-nev small{overflow:hidden;text-overflow:ellipsis;min-width:0}
@container (max-width:579px){.meres-hasabok{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:760px){.ido-meres{grid-column:auto}.meres-hasabok{grid-template-columns:1fr;row-gap:.8rem}
  .meres-reszlet{white-space:normal}
  .meres-nyito{all:unset;display:flex;justify-content:space-between;align-items:center;width:100%;box-sizing:border-box;margin-top:.5rem;cursor:pointer;
    font:600 .88rem system-ui,sans-serif;color:var(--tinta);padding:.5rem .7rem;border:1px solid var(--vonal);border-radius:5px}
  .meres-nyito::after{content:"▾";color:var(--halvany)}.ido-meres.nyitva .meres-nyito::after{content:"▴"}
  .ido-meres:not(.nyitva) .meres-hasabok,.ido-meres:not(.nyitva) .meres-forras{display:none}}
.ido-ikon{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}
.i-nap{stroke:#f59e0b}.i-hold{stroke:#94a3b8;fill:rgb(148 163 184/.18)}.i-felho{stroke:var(--halvany)}.i-csepp{stroke:#3b82f6}.i-ho{stroke:#7c8798;stroke-width:2.6}.i-villam{stroke:#eab308}
/* hópehely: világos témán szürke, sötéten fehér (a kék esőcseppektől is jól elkülönül) */
@media (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]) .i-ho{stroke:#f1f5f9}}
:root[data-tema="sotet"] .i-ho{stroke:#f1f5f9}
.ido-hirek{background:color-mix(in srgb,var(--r) 6%,var(--lap));border-radius:6px;padding:1rem 1.1rem}
.ido-hirek h3{font-size:1.05rem;font-weight:700;color:var(--r);margin-bottom:.4rem}
.ido-hirek ul{list-style:none;margin:0;padding:0}
.ido-hirek li{font-family:var(--listabetu,inherit);font-stretch:var(--lista-nyujt,100%);font-weight:var(--lista-suly,400);padding:.45rem 0;border-bottom:1px solid var(--vonal);font-size:.9rem;line-height:1.35}
.ido-hirek li:last-child{border-bottom:0}
.ido-hirek time{display:block;color:var(--halvany);font-size:.74rem}
.ido-hirek a:hover{text-decoration:underline}

@media (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]) .ido-hirek h3,:root:not([data-tema="vilagos"]) .helynev{color:color-mix(in srgb,var(--r) 45%,#fff)}}
:root[data-tema="sotet"] .ido-hirek h3,:root[data-tema="sotet"] .helynev{color:color-mix(in srgb,var(--r) 45%,#fff)}
@media (prefers-color-scheme:dark){:root:not([data-tema="vilagos"]) .hely-ful[aria-selected="false"]{color:#e5e7eb;border-color:color-mix(in srgb,var(--r) 45%,#9ca3af)}}
:root[data-tema="sotet"] .hely-ful[aria-selected="false"]{color:#e5e7eb;border-color:color-mix(in srgb,var(--r) 45%,#9ca3af)}

/* helyi rovat */
.helyek{display:block}
.hatter-hasabok{display:grid;grid-template-columns:repeat(4,1fr);gap:1.2rem;align-items:start}
@media (max-width:1199px){.hatter-hasabok{grid-template-columns:repeat(2,1fr)}}
.hely-fulek.hatter-fulek{display:none}
@media (max-width:640px){.hatter-hasabok{grid-template-columns:1fr}.hely-fulek.hatter-fulek{display:flex}.hatter-hasab:not(.aktiv){display:none}}
.hatter-hasab{background:color-mix(in srgb,var(--r) 7%,var(--lap));border-radius:6px;padding:1.1rem;display:flex;flex-direction:column;gap:.7rem}
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
.hely{background:color-mix(in srgb,var(--r) 6%,var(--lap));border-radius:6px;padding:1.1rem}
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
  .rovat.ujsag .racs{background-image:linear-gradient(var(--vonal),var(--vonal));background-position:50% 0}
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
  .rovat.ujsag .racs{background-image:none}.rovat.ujsag .doboz{padding-left:1rem;padding-right:1rem;--bp:1rem}

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
  if(!el.dataset.rogzitve && !el.closest('.top-szel')){
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
// háttér rovat fülei (csak telefonon látszanak): a választás megmarad
(function(){
  const fulek=[...document.querySelectorAll('.hatter-ful')]; if(!fulek.length) return;
  const valt=(az,ment)=>{ if(!fulek.some(f=>f.dataset.lap===az)) return;
    fulek.forEach(f=>f.setAttribute('aria-selected',f.dataset.lap===az));
    document.querySelectorAll('.hatter-hasab').forEach(d=>d.classList.toggle('aktiv',d.dataset.lap===az));
    if(ment) tarol.set('hp_hatter',az); };
  valt(tarol.get('hp_hatter',''),false);
  fulek.forEach(f=>f.addEventListener('click',()=>valt(f.dataset.lap,true)));
})();
// helyi rovat fülei: a választás megmarad, ?hely=komlo felülírja
(function(){
  const fulek=[...document.querySelectorAll('.hely-ful:not(.hatter-ful)')]; if(!fulek.length) return;
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
    new IntersectionObserver(ev=>{const rejt=!ev[0].isIntersecting; document.body.classList.toggle('fej-elrejtve',rejt); a.tabIndex=rejt?0:-1;
      setTimeout(fer,30); setTimeout(fer,400);})
      .observe(fej);
  }
  // ha a menügombok nem férnek el egy sorban, tömörebb forma (csak asztali szélességen; mobilon görgethető marad)
  function fer(){ nav.classList.remove('szuk'); if(innerWidth>900 && nav.scrollWidth>nav.clientWidth+1) nav.classList.add('szuk'); }
  addEventListener('resize',fer); a.addEventListener('transitionend',fer);
  if(document.fonts){ document.fonts.ready.then(fer); document.fonts.addEventListener('loadingdone',fer); } fer();
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
    var suly = (bt.suly_kis_felbontas && (window.devicePixelRatio || 1) < 1.5) ? bt.suly_kis_felbontas : bt.suly;
    gy.setProperty('--cim-suly', suly); gy.setProperty('--cim-suly-nagy', bt.nagy); gy.setProperty('--cim-ter', bt.ter); gy.setProperty('--cim-nyujt', bt.nyujt || '100%');
    // a listák (friss sáv, További hírek, Egyéb) a címbetűhöz illő, kicsiben is jól olvasható betűt kapnak
    if (bt.lista) { gy.setProperty('--listabetu', '"' + bt.lista + '",' + (bt.lista_tartalek || 'system-ui,sans-serif'));
      gy.setProperty('--lista-nyujt', bt.lista_nyujt || '100%'); gy.setProperty('--lista-suly', bt.lista_suly || 400); }
    var lk = document.createElement('link'); lk.rel = 'stylesheet';
    // + a második fél (Kultúra, Tech, Életmód, Sport, Vegyes) közös, keskeny, karakteres címbetűje
    lk.href = 'https://fonts.googleapis.com/css2?family=' + bt.url + (bt.lista_url ? '&family=' + bt.lista_url : '') + '&family=Saira+Semi+Condensed:wght@700&display=swap';
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

APP_IKONOK = {"ikon-cimlap-512.png": "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", "ikon-cimlap-192.png": "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", "ikon-cimlap-maskable.png": "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"}

SW_JS = r"""// A Kezdőlap – háttérszkript: a címlapot mindig a hálózatról kéri, de elmenti, hogy offline is megnyíljon
const TAR = 'kezdolap-v2';
const ALAP = ['./', 'manifest.webmanifest', 'ikon-cimlap-192.png', 'ikon-cimlap-512.png', 'ikon-cimlap-maskable.png'];
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
            {"src": "ikon-cimlap-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "ikon-cimlap-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "ikon-cimlap-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
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
    ervenyes = [h for h in ervenyes if not re.search(r"\(x\)\s*$", h["cim"], re.I)]
    # a lapok angol nyelvű kiadásainak cikkei (pl. telex.hu/english/…) nem kellenek
    ervenyes = [h for h in ervenyes if not re.search(r"/(english|en)/", urlparse(h["link"]).path)]
    # ugyanannak a lapnak azonos című cikkei közül csak egy maradjon (amelyiknek van képe/leadje)
    egyedi = {}
    for h in sorted(ervenyes, key=lambda h: (bool(h["kep"]), len(h["lead"])), reverse=True):
        egyedi.setdefault((h["forras"], h["cim"].strip().lower()), h)
    ervenyes = list(egyedi.values())
    for h in ervenyes:
        # a mentett hírekre is (pl. egy korábban tévesen frissnek látszó, újra kiadott cikkre)
        h["ido"] = webcim_korlat(h["link"], datetime.fromisoformat(h["ido"])).isoformat()
        h["rovat"] = rovat(h)
        h.pop("rovat_tanult", None)
    tanulo_besorolas(ervenyes)

    allapot_ut.write_text(json.dumps(ervenyes + emlek, ensure_ascii=False), "utf-8")
    orszagos = osszevon(elo_sorozatok([h for h in ervenyes if h["rovat"] not in ("helyi", "idojaras", "hatter")]))
    # a háttéranyagokat nem vonjuk össze: mindegyik önálló „sztori”
    hatter_cs = [osszevon([h])[0] for h in ervenyes if h["rovat"] == "hatter"]
    # a helyi híreket helyenként külön vonjuk össze: pécsi és somogyi cikk akkor se kerüljön egy témába,
    # ha közös a tárgyuk (pl. nyugdíjak), hiszen a fülek helyek szerint választják szét őket
    helyi_cs = osszevon([h for h in ervenyes if h["rovat"] == "helyi"], kulcs=lambda h: FN[h["forras"]].get("hely"))
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
