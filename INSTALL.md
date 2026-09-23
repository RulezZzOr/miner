# Switch Studio — macOS, Linux, and Windows

Version `0.4.0-alpha.2`. A lightweight local web IDE with the same interface and backend.

Packages contain source code; on first installation, `uv` downloads Python 3.12 and pinned
dependencies. They do not include models, personal settings, or history. Internet access is required
for installation and a locally available model server for agent operation.

| System | Run | Launch |
|---|---|---|
| macOS | Directly, Python matching the machine’s architecture | `Switch Studio.command` |
| Linux | Directly, recommended default distribution: Ubuntu 24.04 | `./switch-studio` |
| Windows 11 / Windows 10 with WSL2 | Backend in Linux, GUI in Windows browser | `Switch Studio Windows.cmd` |

The Windows variant requires **WSL2**; this version does not provide a native `.exe` or a signed `.dmg`. The backend uses Unix locks, process signals, and secure file operations. Simply replacing these checks would weaken the editor’s protection.

## macOS and Linux

1. Install [uv following the official guide](https://docs.astral.sh/uv/getting-started/installation/).
2. Extract the appropriate package into your own writable directory. Do not copy `.venv` from another machine.
3. In the extracted directory, run:

   ```sh
   sh setup-studio
   sh switch-studio
   ```

On macOS, you can also use `Setup Studio.command` followed by `Switch Studio.command`.
If your extraction tool did not preserve executability, use the commands above.
The first run can invoke installation automatically if the virtual environment does not yet exist.
Install `uv` beforehand; the launcher does not download or execute a remote installation script.

The interface is at `http://127.0.0.1:4317`. The server runs in an open terminal.
Ctrl+C terminates the server and cleans up active tasks; closing the tab alone does not stop the server.
For headless Linux: `./switch-studio --no-open`. From your local machine, you can use an SSH tunnel: `ssh -L 4317:127.0.0.1:4317 user@server`.
The embedded web preview uses an additional dynamic local port, and a single tunnel to port 4317 does not forward it.

Direct access on a trusted LAN: `./switch-studio --host 192.168.1.50 --port 4318 --no-open`.
Open `http://192.168.1.50:4318`. Use the actual IP address of the server’s interface.
The web preview on the LAN uses the same IP and its own dynamic port.
Studio has no login; clients with access to this port can control agents and files.

## Windows via WSL2

1. If WSL is missing, run `wsl --install` in an administrator PowerShell, restart Windows,
   and complete the creation of the Linux user in Ubuntu. Detailed
   [Microsoft guide](https://learn.microsoft.com/en-us/windows/wsl/install).
   `wsl --list --verbose` must show version `2` for the default distribution.
2. In the **Ubuntu / WSL** terminal, install the Linux version of `uv` using the link above.
   Installing `uv` only on Windows is insufficient.
3. Extract the `windows-wsl2.zip` package and double-click `Switch Studio Windows.cmd`.
   The first start installs the backend. If the browser does not open, use the printed URL.

Alternatively, extract the Linux package into `~/switch-studio` inside WSL and use
the Linux commands above. The Linux filesystem is better suited for larger projects;
Windows file paths should be specified as `/mnt/c/Users/username/project`, not `C:\...`.
WSL uses the default distribution and its user. The launcher does not change the distribution,
does not enable WSL, and does not require disabling PowerShell security policies.

Windows exposes Linux servers to the browser via localhost under standard WSL settings.
See [WSL networking behavior](https://learn.microsoft.com/en-us/windows/wsl/networking).
Ollama running directly on Windows may not be reachable from WSL via `127.0.0.1` in NAT mode;
configure an address reachable **from WSL**, or use a model server on the LAN.
Do not expose the unauthenticated model API to the public.

## First configuration, updates, and data

The installer creates `agent.toml` from the example **only if it is missing**. Existing configurations are not overwritten.
In the GUI, open **Models**, enter the actual URL and model name; the placeholder `REPLACE_WITH_LOCAL_MODEL` is not a functional model. Internal addresses of this development machine are not embedded as default configuration in packages.

Base dependencies and document readers are installed. Benchmarks, containerized services, cloud CLIs, and their accounts are not installed automatically.
For optional OAuth connections, the CLI must be installed in the same environment as the backend (on Windows, inside WSL).
End-to-end login on Windows and Linux still needs verification.

Updates within the same directory: replace source files and run `sh setup-studio`.
Do not delete `agent.toml`, `.switch-agent`, project directories, or their `.apodex` subdirectories.
The installer does not remove previously added optional dependencies. A fresh directory will have new history;
transferring history and secrets is not part of distribution packages.
Background launchd setup remains a macOS-specific feature and is not a cross-OS service.

## Building and verification

```sh
frontier/.venv/bin/python scripts/build_release.py
frontier/.venv/bin/python scripts/smoke_release.py dist/switch-studio-0.4.0-alpha.2-macos.zip --suite
```

Output is in `dist/`: three archives, file manifests, and `SHA256SUMS`.
The smoke test extracts into a temporary directory with spaces, installs a clean environment,
verifies HTTP startup and server shutdown. `--suite` adds integration and regression tests.
The workflow `.github/workflows/studio-platforms.yml` prepares these checks for Ubuntu and macOS;
mere existence of the workflow is not a successful CI run.

Current local verification is recorded in `analysis/platform-validation.json`
in the development copy. Testing on Windows/WSL and other architectures requires the respective machine.
This installation does not itself demonstrate week-long autonomous operation or model quality.

## Read-only SSH inventory for a mission

Studio's generic shell still rejects SSH. To provision the dedicated `ssh_inventory` connector, create `ssh-targets.json` next to the active `agent.toml` on the Studio host. This is an operator configuration, not a model argument. Example:

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
