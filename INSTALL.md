# Switch Studio — macOS, Linux a Windows

Verze `0.4.0-alpha.1`. Lehké lokální webové IDE se stejným rozhraním a backendem.
Balíčky obsahují zdrojový kód; při první instalaci `uv` stáhne Python 3.12 a připnuté
závislosti. Neobsahují modely, osobní nastavení ani historii. Vyžadují internet při
instalaci a vlastní dostupný modelový server pro práci agentů.

| Systém | Běh | Spuštění |
|---|---|---|
| macOS | Přímo, Python podle architektury stroje | `Switch Studio.command` |
| Linux | Přímo, doporučená výchozí distribuce Ubuntu 24.04 | `./switch-studio` |
| Windows 11 / Windows 10 s WSL2 | Backend v Linuxu, GUI v prohlížeči Windows | `Switch Studio Windows.cmd` |

Windows varianta vyžaduje **WSL2**; čistě nativní `.exe` ani podepsané `.dmg` tato
verze neposkytuje. Backend používá unixové zámky, procesní signály a bezpečné operace
nad soubory. Pouhé nahrazení těchto kontrol by ochranu editoru oslabilo.

## macOS a Linux

1. Nainstaluj [uv podle oficiálního návodu](https://docs.astral.sh/uv/getting-started/installation/).
2. Rozbal příslušný balíček do vlastní zapisovatelné složky. Nekopíruj `.venv` z jiného počítače.
3. V rozbalené složce spusť:

   ```sh
   sh setup-studio
   sh switch-studio
   ```

Na Macu lze použít také `Setup Studio.command` a poté `Switch Studio.command`.
Pokud rozbalovací program nezachoval spustitelnost, použij příkazy výše.
První spuštění umí zavolat instalaci samo, pokud virtuální prostředí ještě neexistuje.
`uv` si instaluj předem; spouštěč nestahuje ani nespouští vzdálený instalační skript.

Rozhraní je na `http://127.0.0.1:4317`. Server běží v otevřeném terminálu.
Ctrl+C ukončí server a uklidí aktivní úlohy; zavření záložky samotný běh neukončuje.
Pro headless Linux: `./switch-studio --no-open`. Ze svého počítače můžeš použít
SSH tunel `ssh -L 4317:127.0.0.1:4317 uzivatel@server`.
Vložený náhled webu má další dynamický lokální port a jediný tunel na 4317 jej nepřenáší.

Přímý přístup v důvěryhodné LAN: `./switch-studio --host 192.168.1.50 --port 4318 --no-open`.
Otevři `http://192.168.1.50:4318`. Použij skutečnou IP rozhraní serveru.
Náhled webu v LAN používá stejnou IP a vlastní dynamický port.
Studio nemá přihlašování; klienti s přístupem k tomuto portu mohou ovládat agenty a soubory.

## Windows přes WSL2

1. Pokud WSL chybí, v administrátorském PowerShellu spusť `wsl --install`, restartuj
   Windows a dokonči vytvoření linuxového uživatele v Ubuntu. Podrobný
   [návod Microsoftu](https://learn.microsoft.com/en-us/windows/wsl/install).
   `wsl --list --verbose` musí u výchozí distribuce ukazovat verzi `2`.
2. V terminálu **Ubuntu / WSL** nainstaluj linuxové `uv` podle odkazu výše.
   Instalace `uv` pouze ve Windows nestačí.
3. Rozbal balíček `windows-wsl2.zip` a dvakrát klikni na `Switch Studio Windows.cmd`.
   První start nainstaluje backend. Pokud se prohlížeč neotevře, použij vypsané URL.

Alternativně rozbal linuxový balíček do `~/switch-studio` uvnitř WSL a použij
linuxové příkazy výše. Linuxový souborový systém je vhodnější pro větší projekty;
cesty k souborům Windows zadávej jako `/mnt/c/Users/jmeno/projekt`, nikoli `C:\...`.
Do WSL se používá výchozí distribuce a její uživatel. Spouštěč nemění distribuci,
nezapíná WSL a nevyžaduje vypnutí bezpečnostních zásad PowerShellu.

Windows zpřístupňuje linuxové servery prohlížeči přes localhost při běžném nastavení
WSL. Viz [síťové chování WSL](https://learn.microsoft.com/en-us/windows/wsl/networking).
Ollama běžící přímo ve Windows nemusí být z WSL dostupná přes `127.0.0.1` v režimu NAT;
nastav adresu dosažitelnou **z WSL**, případně použij modelový server v LAN.
Nepovoluj veřejný přístup k neautentizovanému modelovému API.

## První konfigurace, aktualizace a data

Instalátor vytvoří `agent.toml` z příkladu **jen pokud chybí**. Existující konfiguraci
nepřepisuje. V GUI otevři **Modely**, zadej skutečné URL a název modelu; placeholder
`REPLACE_WITH_LOCAL_MODEL` není funkční model. Interní adresy tohoto vývojového stroje
se do balíčků nepřenášejí jako výchozí konfigurace.

Instalují se základní závislosti a čtečky dokumentů. Benchmarky, kontejnerové služby,
cloudové CLI a jejich účty se automaticky neinstalují. Pro volitelné OAuth připojení
musí být CLI nainstalované ve stejném prostředí jako backend (u Windows uvnitř WSL).
End-to-end přihlášení na Windows a Linuxu je nutné ještě ověřit.

Aktualizace ve stejné složce: nahraď zdrojové soubory a spusť `sh setup-studio`.
Neodstraňuj `agent.toml`, `.switch-agent`, projektové složky ani jejich `.apodex`.
Instalátor neodstraňuje už přidané volitelné závislosti. Nová čistá složka bude mít
novou historii; přenos historie a tajných údajů není součástí distribučních balíčků.
Pozadí přes launchd zůstává samostatnou macOS funkcí, není to služba pro všechny OS.

## Sestavení a ověření

```sh
frontier/.venv/bin/python scripts/build_release.py
frontier/.venv/bin/python scripts/smoke_release.py dist/switch-studio-0.4.0-alpha.1-macos.zip --suite
```

Výstup je v `dist/`: tři archivy, manifesty souborů a `SHA256SUMS`.
Smoke test rozbaluje do dočasné složky s mezerami, instaluje čisté prostředí,
ověřuje HTTP start a ukončení serveru. `--suite` přidá integrační a regresní testy.
Workflow `.github/workflows/studio-platforms.yml` připravuje tyto kontroly pro
Ubuntu a macOS; pouhá existence workflow není úspěšný běh CI.

Aktuální místní ověření je zaznamenané v `analysis/platform-validation.json`
ve vývojové kopii. Test ve Windows/WSL a na jiných architekturách vyžaduje příslušný
stroj. Tato instalace sama neprokazuje týdenní autonomní provoz ani kvalitu modelu.

## Read-only SSH inventory for a mission

Studio's generic shell still rejects SSH. To provision the dedicated `ssh_inventory`
connector, create `ssh-targets.json` next to the active `agent.toml` on the Studio
host. This is an operator configuration, not a model argument. Example:

```json
{"targets":[{"id":"example-server","host":"192.0.2.10","port":22,"user":"operator","identity_file":"/home/operator/.ssh/id_ed25519","missions":["YOUR_MISSION_ID"]}]}
```

The selected mission's next **build** worker receives this target. Planning and
review workers do not receive SSH access; reviewers inspect saved JSON evidence.
Only an IP address, explicit port/user/key and listed mission are accepted. The
existing key and verified `known_hosts` entry must already work as the Studio OS
user. Never place private key contents in the config or a prompt.

The tool exposes four fixed sections: `system`, `services`, `projects`, and
`integrations`. It does not accept commands or arbitrary remote file paths. It
uses noninteractive key authentication, strict host-key checking, no forwarding,
a 40-second deadline and bounded output. The remote host requires Python 3.
Project discovery searches the remote user's home, `/var/www`, `/opt` and `/srv`
to depth 4; dependency directories and SSH keys are excluded. Integration
inventory returns dependency and configuration **names**, not secret values.
Presence of configuration is not proof that the integration works. Permission
errors and scan limits remain explicit in the evidence.

Evidence is saved in `company/ssh-evidence/` inside the mission workspace with UTC
timestamps. This grants only the connector's fixed inventory operations; it does
not enable deployments, remote edits, restarts or unrestricted shell access.
After changing this config, pause/resume the mission's Driver to start a worker
with the new configuration. Studio and native tools still run as their OS user;
this connector is not an OS sandbox for every other tool.
