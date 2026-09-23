# Náhled webu

V horní liště Studia klikni na **Náhled**. Zadej cestu k uloženému HTML souboru v aktuálním projektu a klikni na **Spustit náhled**. Pro nový web zvol název nové složky a **Vytvořit startovací web**: uloží se skutečný `index.html` a otevře se jeho náhled. Existující složka se nepřepisuje.

- **Upravit soubor** otevře vstupní HTML v editoru. Změny ulož přes Cmd/Ctrl+S a vrať se do náhledu.
- Náhled každé dvě sekundy kontroluje načtené soubory. Po změně HTML, CSS, JS nebo jiného načteného podkladu obnoví stránku. Sleduje i chybějící podklady, které teprve vytvoříš.
- **Obnovovat po změně** lze vypnout, aby obnovení nemařilo rozpracovaný formulář. **Obnovit** načte stránku ručně.
- **Mobil · 390 px** mění šířku vykreslení. Nejde o emulaci zařízení nebo mobilního prohlížeče.
- **Otevřít ↗** otevře web samostatně. Automatické obnovování se týká vloženého náhledu ve Studiu, ne této nové záložky.
- Zavření dialogu nezastaví server. **Zastavit** zavře port; ukončení Studia jej také zavře. Po restartu Studia náhled spusť znovu.
- Současně běží jeden náhled. Jiný projekt ani záložka ho nemohou při Start tiše nahradit; nejdřív je nutné použít Stop.

## Co lze zobrazit

Samostatné HTML/CSS/JS weby a hotové statické buildy s lokálními podklady, například `dist/index.html`. Složka obsahující vstupní HTML je kořenem webu; absolutní odkazy `/app.js` míří do této složky. Použij složku určenou pro veřejné podklady. Statický server nabízí běžné webové typy souborů včetně JSON, obrázků a fontů, nejvýše 20 MB na soubor; nepřekládá TypeScript/JSX, nespouští npm, Python, PHP ani aplikační backend. Nemá SPA fallback pro libovolné URL.

Externí CDN, vzdálená API, vnořené stránky a service workers jsou zablokované. Cílem je lokální ověření vlastních statických souborů. Databáze, přihlášení, platby ani hosting se vytvořením šablony nepřidávají. Ukázkový seznam nápadů existuje pouze v paměti stránky.

## Oddělení od Studia

Server používá jiný loopback port než Studio a samostatný krátkodobý token. Token se při otevření převede na HttpOnly cookie; přístup bez něj je odmítnutý. Vložený web má izolované prostředí a vlastní Content Security Policy. Nemá přístup k DOM ani API Studia. Skryté soubory, vybrané konfigurační soubory, backendové typy, cesty nad kořen a symbolické odkazy nejsou dostupné. Otevírání adresářů využívá stejnou ochranu proti výměně za symlink jako editor. Nejde o izolované prostředí pro spouštění libovolných serverových programů; ty tato funkce vůbec nespouští.

## Ověření

`studio/tests/test_preview.py` ověřuje skutečné HTTP požadavky, oddělený port, tokeny, absolutní cesty k podkladům, změny souborů, Stop, souběh záložek a ochranu cest. `studio/tests/preview-ui.cjs` ověřuje obnovování a zpožděné odpovědi při změně projektu nebo stavu náhledu.

V Chrome byl ověřen také postup vytvoření webu přes GUI, kliknutí v aplikaci, změna HTML v editoru, uložení a zobrazení změny v náhledu.
