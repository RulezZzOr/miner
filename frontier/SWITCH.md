# Odvozená verze Switch: původ a místní změny

Toto je kompletní lokální forka veřejného repozitáře [ApodexAI/FrontierAgent](https://github.com/ApodexAI/FrontierAgent),
na základě commitu `9e533db6f6c34d16037ee5ec964c479d0eb51cde` (upstream HEAD ověřen 2026-09-22).
Veřejný Miner repozitář dodává zdrojový kód bez původní Git databáze. Vývoj původně
používal lokální větev `switch/local`. Publikace Mineru: https://github.com/RulezZzOr/miner.

Původní zdrojový kód, testy, dokumentace, obsah a licence Apache-2.0 [LICENSE](LICENSE) jsou zachovány.
Názvy upstream produktů jsou zachovány pro uvedení autora a kompatibilitu. Nejde o
nezávislou implementaci ani nárok na autorství upstream díla.

Úpravy Switch:

- `apodex/switch_cli.py`: spouštěč volí modely z `agent.toml` nadřazeného projektu, vytváří modelově specifické workflow YAML, zachovává upstream nástroje, chování týmu a UI. Pro Ollama spouštěč výchozě používá jeden probíhající volání modelu a 600sekundové limity pro první token a mezičásti, což lze přepsat pomocí upstream proměnných prostředí. Generované YAML obsahuje zástupné symboly přihlašovacích údajů; ty jsou řešeny pouze v prostředí procesu.
- `apodex/profiles/react.yaml` a `agent_team.yaml`: volitelné cesty k externím profilům workflow; výchozí `tui` zůstává pro běžná upstream spuštění beze změny.
- `frontier_agent/infra/openai_client.py`: explicitní dialekt Ollama pro limity výstupu (`max_tokens`) a historii odvozování (`reasoning`), včetně streamování. Výchozí chování OpenAI zůstává zachováno.
- `workflows/stateful_react_agent/profile.py` a `workflows/agent_team/profile.py`: předává vybraný drátový dialekt všem částem workflow klienta.
- `apodex/cli.py`: předává explicitní `--max-turns` do nativního prostředí workflow, které dříve četlo pouze limit z YAML.
- `tests/test_switch_integration.py`: kontrola tvaru SDK drátu, zachování schopností profilu a trvalosti přihlašovacích údajů.
- `pyproject.toml`: přidává `switch-frontier` vedle nezměněných upstream vstupních bodů CLI.

Nadřazený spouštěč `./switch` používá izolované Python prostředí tohoto checkoutu. Původní minimální Switch Agent zůstává dostupný odděleně; jeho formát stavu není migrován do relací FrontierAgent.

Operační ekvivalence závisí na schopnostech modelu, přihlašovacích údajích služeb, runtime sandboxu a volitelných benchmark datech. Fork zdrojového kódu zachovává veřejnou sadu funkcí včetně upstream omezení dokumentovaných v nadřazené analýze. Soukromé komponenty služeb Apodex, soukromé vyhodnocovací nástroje a váhy modelů nejsou reprodukovány.

## Switch Studio (2026-09-22)

Lokální webové IDE je v sousední složce `../studio/` a používá plný backend přes samostatný proces. Spouštěč `../switch-studio` a `../Switch Studio.command` otevře editor, konfiguraci modelů, úlohy, schvalování a výstupy. Původní terminálové rozhraní zůstává dostupné.

Při integračním ověření byla opravena klasifikace dokončení v `apodex/task_runner.py`: Stateful ReAct používá `no_tool_behavior="stop"`, takže jeho přirozené `no_tool` ukončení smí být dokončené. Agent Team nadále zachovává nedokončený stav pro `no_tool`; limity a explicitně neúplné odpovědi se touto výjimkou nepovyšují na úspěch.

## Veřejná verze zdrojového kódu Mineru (2026-09-23)

Tento snímek zachovává všech 745 souborů sledovaných v upstream základu, plus lokální přídavky.
Původní kompaktní prototyp Switch Agent není součástí tohoto aplikovaného repozitáře.
Studio a jeho spouštěče/skripty jsou v nadřazeném adresáři. Další integrační změny
zahrnují zpracování výstupních souborů, chování nativního pracovního prostoru,
obnovu po zrušení/kontrolních bodech, adaptéry poskytovatelů, chování přímého načítání webu
a strukturované zprávy mise. Studio vlastní řízení firmy/projektu/výrobku a explicitně
omezený SSH inventarizační konektor. Soukromý stav runtime, lokální konfigurace
a protokoly vývojových relací jsou vyloučeny.
