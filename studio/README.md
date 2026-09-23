# Switch Studio

**Company Builder & Driver:** firmy, portfolio projektů, oddělení, závislosti, opakovaná práce a trvalá smyčka s limity. [Použití a hranice](docs/COMPANY_DRIVER.md).

Vlastní lokální GUI nad plným FrontierAgent backendem. Běží na `http://127.0.0.1:4317` a nevyžaduje frontendový build, CDN ani cloudový účet.

## Spuštění

Instalace pro **macOS, Linux a Windows přes WSL2**: [INSTALL.md](../INSTALL.md).

V kořenové složce projektu:

```sh
./switch-studio
# nebo konkrétní pracovní složka
./switch-studio --cwd /cesta/k/projektu
# bez automatického otevření prohlížeče
./switch-studio --no-open --port 4317
```

Na macOS lze také dvakrát kliknout na `Switch Studio.command`. Okno terminálu udržuje server v běhu; Ctrl+C ukončí server i jeho aktivní úlohu. Zavření samotné webové záložky běžící úlohu neruší. Nejdřív použij tlačítko **Zastavit**, pokud ji chceš ukončit.

Používá `frontier/.venv`; chybějící prostředí při prvním startu vytvoří `setup-studio`.
Ruční instalace nebo aktualizace: `sh setup-studio` (vyžaduje `uv`). Instaluje základní
runtime a čtečky dokumentů z lockfile. `--all-extras` je určeno pro širší vývojové prostředí.

## Volitelné účty ChatGPT a Claude

V okně **Modely → Účty · volitelné** vyber poskytovatele. Studio nejdřív ověří přihlášení. Pokud chybí, klikni na **Přihlásit v prohlížeči**, potom na **Otevřít přihlášení** a dokonči přihlášení u poskytovatele. Po návratu se automaticky objeví nabídka dostupných modelů. **Přidat vybraný model** vytvoří profil; model pro úlohu se vybírá v pravém panelu. Interní Ollama zůstává výchozí a nepotřebuje žádný cloudový účet.

- **ChatGPT:** oficiální [Codex App Server](https://learn.chatgpt.com/docs/app-server), sdílí přihlášení s lokálním Codex CLI. Na tomto počítači byl ověřen Codex `0.155.1`. Model běží přes vlastní Codex agentní prostředí, v režimu jednoho agenta. Délku úlohy spravuje Codex; tlačítko Zastavit funguje. Studio výslovně nastavuje sandbox `workspace-write` a při vypnutém automatickém schvalování politiku `untrusted`. Požadavky na příkazy a změny souborů se předají do GUI. Nepodporované interakce se odmítnou. Nejde o ChatGPT OAuth token vložený do OpenAI API ani o týmový režim Frontieru.
- **Claude Console:** oficiální [Anthropic CLI OAuth](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication), samostatné účtování API v Console. Není to přihlášení předplatným Claude Pro/Max. Používá původní agentní prostředí Studia, včetně ReAct, Agent Team a kompakce. Podporu obnovování tokenů zajišťuje Anthropic SDK `>=1.0.0`.

Cloudový profil lze tlačítkem **Odebrat ze Studia** odstranit. Přihlášení ostatních aplikací se tím nemění; sdílený Codex účet se neodhlašuje. Úloha s tímto profilem musí být nejdřív zastavená. Přihlášení čekající na dokončení lze zrušit; po pěti minutách vyprší.

Tokeny se neukládají do modelových profilů, úloh ani GUI. Codex spravuje své vlastní přihlašovací údaje. Claude má oddělený profil `switch-studio` pod `.switch-agent/studio/anthropic/`; CLI a SDK spravují jeho přihlašovací soubory a obnovování tokenů. Globální přihlášení Claude Code ani prostředí shellu se nemění. Přihlášení se spouští až akcí uživatele, bez cloudového účtu lze nadále pracovat lokálně.

Volitelné závislosti:

- `codex` musí být na PATH, viz [instalace Codex CLI](https://developers.openai.com/codex/cli).
- `ant` musí být na PATH nebo v `.switch-agent/tools/ant`, viz [oficiální Anthropic CLI](https://github.com/anthropics/anthropic-cli). Na tomto Macu je lokálně nainstalovaná `1.34.0` pro arm64 s ověřeným SHA-256 z oficiálního release. Neinstaluje se nic do globální konfigurace.

Přihlášení je lokální: autorizační odkaz otevři v prohlížeči na stejném počítači, na kterém běží Studio a callback poskytovatele.

## Funkce

- **Ověřená dodávka a obnova:** izolovaná pracovní kopie, skutečné schválené testovací příkazy,
  historie rozhodnutí, obsahové verze, kontrolované sloučení a návrat souborů.
  Volitelně místní služba s HTTP monitoringem a frontou oprav.
  [Architektura a přesný postup](docs/CONTROL_LOOP.md).

- **Produkty:** trvalá karta digitálního produktu, fronta oprav a nových funkcí,
  navazující realizace s původními kritérii, historie převzatých verzí a volitelná
  plánovaná údržba. Automatické navazování má vlastní limit a ve výchozím stavu je
  vypnuté. [Použití a hranice správy produktů](docs/PRODUCT_LIFECYCLE.md).

- **Projektové zápisky v Markdownu:** pokud má vybraný projekt v kořeni `PROJECT.md`, Studio přidá jeho uložený obsah do nového zadání. Stačí krátký rozcestník s odkazy na rozhodnutí, poznatky a zdroje; soubory upravíš v existujícím editoru. [Pravidla a použití](docs/MARKDOWN_NOTES.md).

- **Náhled:** spuštění statického HTML/CSS/JS webu na odděleném loopback portu, klikání přímo ve Studiu, mobilní šířka 390 px a automatické obnovení uložených změn. Tlačítko **Vytvořit startovací web** uloží skutečný `index.html` do nové složky. [Použití a hranice náhledu](docs/WEB_PREVIEW.md).
- **Projekty AI:** experimentální dlouhodobé zadání, trvalý plán a otázky, obnova po restartu a samostatný realizátor/reviewer. [Použití a ověřené hranice](docs/LONG_RUNNING_PROJECTS.md).
- **Šablony → AI Build Company:** klikací přehled 30 rolí, uložení vlastní kopie do projektu a přidání instrukcí k zadání. [Struktura a použití šablony](templates/README.md).
- Výběr existujících projektových složek a strom souborů.
- Editor UTF-8 souborů do 2 MB, číslování řádků, Tab, Cmd/Ctrl+S, vytváření souborů a opětovné načtení z disku.
- Kontrola revize při ukládání: změnil-li soubor mezitím agent nebo jiný editor, server vrátí konflikt a změny nepřepíše.
- Výběr a přidávání modelových profilů, kontrola `/models` pro OpenAI kompatibilní API/Ollamu.
- ReAct a Agent Team přes skutečný plný backend, nastavitelný limit kroků hlavního agenta.
- Živý text, oddělené uvažování, volání nástrojů, schvalovací karty, aktivita, konzole a výstupní soubory.
- Historie úloh vytvořených ve Studiu; obnovení zobrazení po reloadu stránky.
- Přehled hlavního agenta a požadavků na vytvoření pracovníků z reálných událostí.
- Zastavení sledovaného stromu procesů včetně potomků ve vlastních procesních skupinách. Po 5 s následuje nucené ukončení; stav zrušeno se zobrazí až po úklidu.
- Čekání na schválení se řeší přímo v GUI. Automatické schvalování je standardně vypnuté a lze ho zapnout pro konkrétní úlohu.

Přepnutí projektu ani modelu nemění už spuštěnou úlohu. Agent pracuje s uloženými soubory. Otevřený soubor se do zadání přidá svou cestou; jeho neuložené změny je nutné nejdřív uložit. Nové zadání vytváří novou relaci; GUI zatím neobnovuje kontext staré relace pro další rozhovor.

## Uložení a provoz

- Původní konfigurace: `agent.toml`.
- Modelové úpravy z GUI: `.switch-agent/studio/models.json`. Původní TOML se nepřepisuje; přepisy se týkají Studia.
- Projekty a historie: `.switch-agent/studio/`.
- Události, schvalování a log: `.switch-agent/studio/runs/<id>/`.
- Skutečné relace a výstupy backendu: `<projekt>/.apodex/runs/<session-id>/`.
- API klíče se čtou z prostředí nebo `.env` u původního TOML / ve `frontier/`; GUI ukládá jen jméno proměnné.

Server naslouchá pouze na loopbacku. Kontroluje Host, Origin a token pro změnové požadavky. Editor nepovoluje cestu mimo otevřený projekt, symbolické odkazy v kterékoli části cesty ani `.env*` / `.git` / `.switch-agent` bez ohledu na velikost písmen. Čtení a zápis používají otevřené deskriptory složek a `O_NOFOLLOW`, takže výměna složky za symlink mezi kontrolou a otevřením ochranu neobejde. Tyto kontroly webového API nenahrazují izolaci agentových příkazů: **nativní backend běží s oprávněními uživatele** stejně jako terminálová verze.

GUI Studia zatím pouští jednu úlohu současně. Každé nativní spuštění má vlastní stabilní pracovní odkaz pod `.apodex/runtime/native/workspaces/<invocation>/workspace`; samostatný CLI běh jej nepřepíše. Uvnitř týmové úlohy delegování zůstává funkční. Úspěšný návrat procesu sám neznamená hotový úkol: GUI rozlišuje potvrzené dokončení, neúplný výsledek, chybu a zrušení.

## Rozsah této verze

Je to lokální webové IDE s jednoduchým textovým editorem. Nemá zatím LSP/autocomplete, debugger, ruční interaktivní terminál ani více otevřených editorových záložek. Vnořená oddělení 1 → 5 → 25 a modely pro libovolné jednotlivé role zatím nejsou implementované. Projekty AI mají samostatný výběr modelu pro práci a review a automatické vracení oprav. Spolehlivá komplexní dodávka na konkrétním Qwenu a týdenní provoz zůstávají otevřeným ověřením.

## Testy

```sh
frontier/.venv/bin/python -m unittest discover -s studio/tests -v
frontier/.venv/bin/ruff check studio frontier/apodex/task_runner.py
node --check studio/static/app.js
node --test studio/tests/*.cjs
```

Integrační testy spouštějí skutečný Frontier proces proti deterministickému lokálnímu testovacímu API. Ověřují schválení → nástroj → soubor → dokončení, zastavení, historii a HTTP/editační hranice. Nejde o test inteligence ani kvality reálného modelu.

## Opravy auditu z 22. 9. 2026

Všech sedm potvrzených nálezů má opravu a regresní ověření v [auditním reportu](../analysis/audit/AUDIT.md). Pomocná sumarizace používá protokol a konfiguraci zvoleného workflow v obou režimech. Kontrola modelu bez autentizace neposílá žádný klíč z prostředí. Seznam výstupů Codexu prochází všechny stránky událostí až po velikost logu zaznamenanou při zahájení čtení.

Správa nativních procesů pravidelně sleduje potomky a před Stop znovu zachytí jejich identitu. Nejde o OS izolaci úmyslně unikajícího programu; takové omezení by vyžadovalo kontejner nebo jiný systémový sandbox. Pracovní odkazy jsou oddělené, samotné projektové soubory a instalační cache mohou nadále sdílet různé běhy.
