# Saját címlap

Rovatokba rendezett címlap a kedvelt lapok RSS-csatornáiból.

**Országos lapok:** Telex, 444, 24.hu, HVG, Magyar Hang, Átlátszó, Válasz Online, Euronews
**Helyi lapok:** PécsMa, PécsAktuál, Szabad Pécs (Pécs oszlop), HírBalaton (Balaton oszlop)

- **A lap tetején** az a 9 friss hír áll, amelyikről a legtöbb lap írt. Az ugyanarról szóló cikkeket a szkript egy dobozba vonja össze. A színes pöttyök mutatják, hány és melyik lap írt az adott hírről, alattuk pedig ott vannak a többi lap cikkeinek linkjei.
- **Rovatok:** Belföld, Külföld, Helyi, Gazdaság, Tudomány és tech, Kultúra, Vélemény és elemzés, Sport, Életmód. A 24 óránál frissebb hírek dobozban jelennek meg, a régebbiek a „Korábbi hírek” listába kerülnek, 72 óra után pedig kiesnek.
- **Helyi rovat:** külön oszlop jut Pécsnek és a Balatonnak. Ezek a hírek nem kerülnek a lap tetejére és az országos rovatokba.
- **Források menü** (jobb felül): itt egy-egy lapot ideiglenesen elrejthetsz, és a böngésző ezt megjegyzi. A rovatokat a nevükre kattintva összecsukhatod.
- Az oldal félóránként magától újratöltődik.

## 1. Kipróbálás a saját gépeden

Python 3.9 vagy újabb kell hozzá, semmit nem kell telepíteni.

```
python3 hirportal.py
```

Ez létrehozza az `index.html` fájlt, amit bármelyik böngészőben megnyithatsz. Minden futtatás frissíti az oldalt.

## 2. GitHubon, a saját géped bekapcsolása nélkül (ingyenes)

1. Regisztrálj a github.com oldalon, és hozz létre egy új, **nyilvános (Public)** tárolót (repository), például `hirek` néven.
2. Töltsd fel bele ennek a mappának a tartalmát: az *Add file → Upload files* gombbal húzd be a `hirportal.py` és a `README.md` fájlt.
   A `.github/workflows/frissites.yml` fájlt külön kell létrehozni (a rejtett mappát a böngésző nem mindig tölti fel): *Add file → Create new file*, a fájl neve legyen `.github/workflows/frissites.yml`, a tartalmát pedig másold be.
3. **Actions** fül → bal oldalt a „Hírportál frissítése” → *Run workflow*. Egy-két perc múlva zöld pipa jelenik meg.
4. **Settings → Pages**: Source = *Deploy from a branch*, Branch = `gh-pages`, mappa = `/ (root)` → *Save*.
5. Pár perc múlva az oldal elérhető a `https://<felhasználónév>.github.io/hirek/` címen, gépről és telefonról is. Innentől félóránként frissül.

Jó tudni:
- Nyilvános tárolóval ingyenes. Privát tárolónál az ingyenes fiók nem tud weboldalt közzétenni.
- Az oldalt a keresők nem veszik fel (`noindex`), de aki ismeri a linket, megnézheti.
- Ha egyszer leállna a frissítés (a GitHub a sokáig inaktív tárolókban kikapcsolhatja az időzített futtatást), az Actions fülön egy kattintással újra bekapcsolható.

## Testreszabás

Mindent a `hirportal.py` elején, a BEÁLLÍTÁSOK részben tudsz átállítani (GitHubon a fájl megnyitása után a ceruza ikonnal szerkesztheted):

- `FORRASOK`: lapok hozzáadása vagy törlése. Ehhez csak a lap RSS-címe kell. Ha a lapnál megadod a `"hely": "Pécs"` mezőt, helyi lapként kezeli, és a megadott helyhez tartozó oszlopba teszi. Egy új hely (pl. `"hely": "Szeged"`) automatikusan új oszlopot kap.
- `ROVATOK`: a rovatok neve, sorrendje és színe, valamint az, hogy melyik URL-szakasz, kategória vagy kulcsszó melyik rovatba sorol. A `HELYI_UTAN` adja meg, melyik rovat után jöjjön a Helyi rovat.
- `OLDAL_CIM`, `FO_HIREK_SZAMA`, `FRISS_ORA`, `MEGORZES_ORA`.

**Hogyan dől el a rovat?** Először a cikk webcíme alapján (pl. `telex.hu/belfold/…`), ezután a lap saját kategóriája alapján (a WordPress-alapú lapok, például a Magyar Hang vagy a Válasz Online ezt megadják), végül a cím és a lead kulcsszavai alapján. Ha egyik sem dönt, a lap „alap” rovatába kerül (Euronews → Külföld, Átlátszó → Belföld), ennek hiányában az Egyéb rovatba.

Ha valamelyik lap épp nem érhető el, vagy megváltozott az RSS-címe, az oldal tetején egy figyelmeztetés jelenik meg, a többi lap hírei pedig ugyanúgy megjelennek.
