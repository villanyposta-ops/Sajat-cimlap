# A Kezdőlap

Személyes hírcímlap, amely a kedvelt lapok RSS-csatornáiból rovatokba rendezve, újságszerű elrendezésben mutatja a friss híreket. Az oldal a GitHubon készül és ott is fut, a saját gépet nem kell hozzá bekapcsolva tartani.

Élő oldal: <https://villanyposta-ops.github.io/Sajat-cimlap/>

## Források

- **Országos lapok:** Telex, 444, 24.hu, HVG, Magyar Hang, Népszabadság, Infostart, Euronews
- **Háttér és elemzés:** Átlátszó, BBC News Magyarul, Magyar Hang (publicisztika), Válasz Online
- **Helyi lapok:**
  - Pécs: PécsMa, PécsAktuál, Szabad Pécs
  - Balaton: HírBalaton
  - Komló: 7300.hu, Komlói Újság
  - Somogy: KaposPont, Kapos-T, Atádhír
- **Időjárás:** Időkép és HungaroMet hírek, a HungaroMet automata állomásainak friss mérései és kompozit radarképei (nyílt adat, odp.met.hu), valamint az Open-Meteo előrejelzése

A lapok angol nyelvű kiadásainak cikkei (pl. `telex.hu/english/…`) kimaradnak.

Ha egy lap csatornája nem érhető el, a Telexnél és a BBC-nél a program a Google Hírek keresőjéből pótolja a cikkeket. Ha más lap esik ki, az oldal tetején figyelmeztetés jelenik meg, a többi lap hírei ugyanúgy megjelennek.

## Az oldal felépítése

**Felső rész (Top).** A legfontosabb, a legtöbb lap által megírt friss hírek:
- nagy nyitóhír képpel; asztali nézetben a fejlécszín halvány árnyalatán, mobilon a képre írt címmel;
- alatta négy képes doboz;
- jobb oldalt a lapszéli hasáb hat további hírrel.

Az összevont hírek alatt színes pöttyök mutatják, hány és melyik lap írt róluk, az „Erről írt” sorban és a lapozóval pedig a többi lap cikkei is elérhetők.

**Rovatok:** Belföld, Külföld, Helyi, Gazdaság, Háttér, Kultúra, Tudomány és tech, Életmód, Sport, Időjárás, Vegyes.
- Minden rovat egy kiemelt hírrel nyit. A Kultúra, a Tech, az Életmód és a Sport kiemelt híre képre írt címmel, a rovat színű panelen jelenik meg.
- A Belföld, a Külföld, a Gazdaság, a Kultúra, a Tech és az Életmód újságszerű, hasábos rácsban áll.
- Az egyetlen lap által megírt hírek a sorok végén kis kártyákon jelennek meg.
- Ha egy hírről legalább két lap írt, a doboz tetején rovatszínű, jobbra kifutó vonal jelzi.
- A dobozokból kiszoruló friss hírek a rovat alján, egy négyhasábos rövidhír-sorban látszanak (a Belföldön és a Külföldön 6, máshol 3 órán belül; a Sportban a zöld háttéren fehér lapon), négyesével teli sorokra kiegészítve. A régebbiek a „Korábbi hírek” lenyíló listába kerülnek. 72 óra után a hírek kiesnek.
- A **Háttér** rovat négy hasábban mutatja az Átlátszó, a BBC, a Magyar Hang és a Válasz Online elemzéseit.
- A **Helyi** rovatban minden hely (Pécs, Balaton, Komló, Somogy) külön oszlopot kap. Ezek a hírek nem kerülnek a felső részbe és az országos rovatokba.
- Az **Időjárás** rovatban:
  - a néző saját helyének mért adatai és előrejelzése, ha a böngésző engedi a helymeghatározást;
  - 21 állomás friss mérése három hasábban (hőmérséklet, égkép, szél, pára, az utolsó óra csapadéka); az 500 méter fölötti hegyi állomásoknál a magasság is; a Nagy-Hideg-hegy helyén, ha nem jelent, a Dobogókő; telefonon lenyíló listában;
  - csapadékradar: a HungaroMet ötpercenként frissülő kompozit képéből (CMax) Magyarország kivágása; a böngésző maga kéri le a legfrissebbet, az elmúlt óra képei lejátszhatók;
  - az Időkép és a HungaroMet hírei.
- A **Vegyes** rovat az oldal legvégén a máshová nem illő hírek legfrissebb 15 darabját mutatja tömör, háromhasábos listában (telefonon az első hatot).

**Összevonás.** Az ugyanarról szóló cikkeket a program egy dobozba vonja (betű-négyesek alapján mért szövegrokonsággal). A dobozban a legfrissebb, képpel és bevezetővel rendelkező cikk áll elöl. A gyorsan fejlődő történetek, amelyekről az elmúlt órákban több lap is újat írt, előrébb kerülnek.

**Élő tudósítás.** Ha egy lap rövid időn belül legalább három cikket ad ki pontosan ugyanazzal a címkekészlettel (a 24.hu élő tudósításainak részei ilyenek), a program egyetlen dobozzá vonja őket: elöl a legfrissebb rész, a lapnév előtt „Élő · N rész” jelzés, alul lenyitható listában a korábbi részek és a gyűjtőcikk linkje.

**Megjelenés.**
- Három fejlécstílus: Hírlap, Nemzeti és Népszava. Minden megnyitáskor másikat kapsz, mindegyik saját betűtípus-családdal.
- A fejléc színe napszakonként változik: reggel, délelőtt, délután, este és éjjel más.
- Világos és sötét mód: a készülék beállítását követi, vagy kézzel váltható a fejlécben.
- A rovatmenü gombjai a stílus címbetűjével íródnak; görgetéskor a menü az oldal tetejére tapad, a logóval együtt.
- Az oldal telefonra alkalmazásként is telepíthető.

**Kezelés.**
- **Források menü** (jobb felül): egy-egy lap ideiglenesen elrejthető, és a böngésző ezt megjegyzi.
- A rovatok a nevükre kattintva összecsukhatók.
- Az oldal ötpercenként ellenőrzi, van-e újabb változat. Ha az oldal tetején vagy, magától frissül, egyébként egy gomb jelzi az új híreket.

## Hogyan dől el a rovat?

1. **Külön szabályok** néhány lap sajátosságaira. Például:
   - a HVG lapszemléje a Külföldbe kerül;
   - a podcastok és a Népszabadság Mozaik rovata a Vegyesbe; a Mozaikban megjelenő bűnügyi és bírósági hírek a kulcsszavaik alapján a Belföldbe vagy a Külföldbe;
   - a lottóról és a szerencsejátékokról szóló hírek a Vegyesbe, kivéve, ha a lap a Gazdaságba tette őket;
   - a Magyar Hang publicisztikái a Háttérbe.
2. **A cikk webcíme** (pl. `telex.hu/belfold/…`). A dátummal kezdődő címeknél, mint a Népszabadságé, a dátum utáni rész számít.
3. **A lap saját kategóriája vagy címkéi**, ahol a csatorna megadja őket.
4. **A cím és a bevezető kulcsszavai.**
5. **Tanuló besorolás:** a gyenge jelű cikk a szóhasználatban hozzá leghasonlóbb, biztosan besorolt cikkek rovatát kapja, ha azok elég egyértelműen egy rovat felé húznak.
6. Ha egyik sem dönt, a lap alaprovatába kerül (Euronews, BBC → Külföld), ennek hiányában a Vegyesbe.

Ha több lap cikkéből álló hírnél eltér a besorolás, a cikkek a besorolásuk erőssége szerint szavaznak.

## Működés

A `hirportal.py` egyetlen Python-fájl, külső csomag nélkül fut, Python 3.9 vagy újabb kell hozzá. Minden futáskor:
1. letölti a csatornákat;
2. hozzáfésüli a korábbi hírekhez a `hirek_allapot.json` állapotfájlból;
3. besorolja és összevonja a cikkeket;
4. elkészíti az `index.html`-t.

### Kipróbálás a saját gépen

```
python3 hirportal.py
```

Ez létrehozza az `index.html` és a `hirek_allapot.json` fájlt. Az előbbi bármelyik böngészőben megnyitható. Minden futtatás frissíti az oldalt.

### Futtatás a GitHubon

- A `.github/workflows/frissites.yml` munkafolyamat futtatja a programot, és az eredményt a `gh-pages` ágra teszi. A `gh-pages` ág mindig egyetlen mentésből áll, így a tároló nem hízik. Az állapotfájl is ott tárolódik, a következő futás onnan tölti be.
- **A rendes frissítést negyedóránként a cron-job.org indítja**, a GitHub API-n keresztül (`workflow_dispatch`). A GitHub saját időzítője ugyanis gyakran késik vagy kimarad. Tartaléknak a munkafolyamatban háromóránkénti saját időzítés is van.
- A cron-job.org-nál a kérés `Authorization` fejlécébe egy GitHub-hozzáférési kulcs kerül. Ezt csak ott szabad megadni, máshol, például ebben a tárolóban soha.
- Az oldalt a **Settings → Pages** alatt a `gh-pages` ágról tesszük közzé.

Új tároló beállítása:
1. Hozz létre egy nyilvános tárolót, és töltsd fel bele a `hirportal.py`, a `README.md` és a `fejlec.svg` fájlt.
2. A `.github/workflows/frissites.yml` fájlt hozd létre külön: *Add file → Create new file*, a rejtett mappát a böngésző nem mindig tölti fel.
3. **Actions** fül → „Hírportál frissítése” → *Run workflow*.
4. **Settings → Pages:** Source = *Deploy from a branch*, Branch = `gh-pages`, `/ (root)` → *Save*.

Jó tudni:
- Nyilvános tárolóval ingyenes. Privát tárolónál az ingyenes fiók nem tud weboldalt közzétenni.
- Az oldalt a keresők nem veszik fel (`noindex`), de aki ismeri a linket, megnézheti.

## Testreszabás

Minden beállítás a `hirportal.py` elején található, magyarázó megjegyzésekkel:
- `FORRASOK`: a lapok RSS-címe, színe, rövid neve. A `"hely": "Pécs"` mezővel helyi lapként kezeli, új hely esetén új oszlopot nyit. A `"hatter": True` mezővel a Háttér rovatba kerül, a `"tartalek"` mezőben tartalék (Google Hírek) csatorna adható meg.
- `ROVATOK`: a rovatok neve, sorrendje, színe, valamint a besorolás webcím-részletei, kategóriái és kulcsszavai. A `HELYI_UTAN` és a `HATTER_UTAN` adja meg a Helyi és a Háttér rovat helyét.
- `FEJLEC_STILUS`: `"veletlen"`, vagy rögzítve `"hirlap"`, `"nemzeti"`, `"nepszava"`. A `NAPSZAK_SZINEK` a fejléc napszakonkénti színeit adja meg.
- Időzítések: `FRISS_ORA` (dobozban maradás), `MEGORZES_ORA` (meddig marad meg egy hír), `FRISS_SAV_ORA`, `FRISS_SAV_SZELES_ORA` (rövidhír-sor), `KIEMELT_ABLAK`, `FEJLEMENY_ORA`.
- Összevonás és élő tudósítás: `OSSZEVON_…` küszöbök, `ELO_MIN_RESZ`, `ELO_RES_PERC`, `ELO_CIMKE_MIN`.
- Időjárás: `IDOJARAS_HELYEK`, `IDOJARAS_SAJAT_HELY`; a mért adatok állomásai: `MERES_ALLOMASOK` (állomásszámmal vagy névvel, tartalék állomással is), `MERES_HEGYI_M`; a radar: `RADAR_TIPUS`, `RADAR_KEPEK`.
- Vegyes rovat: `VEGYES_MAX`.
