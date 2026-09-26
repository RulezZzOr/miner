# Security

Miner / Switch Studio is an alpha for a single trusted operator. All controller APIs require
an owner access key or an authenticated, expiring HttpOnly session. Mutations also require
the existing CSRF token and matching Origin/Host checks. LAN listeners require TLS.
The key is generated in `.switch-agent/studio/access-key` with mode 0600. Runtime state
is private (0700); launchers use umask 077. See [installation](INSTALL.md) for sign-in.

Workers, verification commands and managed services run inside Linux bubblewrap with
private process, IPC and filesystem views and no capabilities. Only the chosen project (or
Studio's managed working copy or deployment release for it), the current attempt and explicitly
selected immutable evidence are mounted. Verification commands run in the project or its managed
working copy and can write only their own status file. The application directory, the
home directory itself, `/` and the controller's state directory cannot be selected as a project.
The rest of the controller's state, home credentials, SSH keys and inherited secret environment
are not available.
SSH inventory uses a typed, per-attempt broker; it accepts configured targets and read-only
sections, never arbitrary commands. Missing sandbox support fails closed.

This is not multi-tenant isolation: the selected project and provider key belong to the worker,
network access remains enabled, and workers share the host kernel. Protect model endpoints
and do not store unrelated secrets in a project. Keep a dedicated service account and private
network; this alpha is not hardened as an internet-facing multi-user service.
Native macOS execution and OAuth worker execution are currently disabled pending compatible
isolation/credential brokers. Use the Linux backend with local or API models. The browser UI
can be used from macOS and Windows; Linux under WSL2 needs working bubblewrap.

Review tool approval and model data handling before connecting a provider. Model output,
web pages and repository files can contain untrusted instructions.

Main is protected against direct pushes, force pushes and deletion, with protection enforced
for administrators. Changes go through pull requests; an external approving reviewer is not
mandatory for this single-owner project. Dependabot alerts, secret scanning and push protection
are enabled. Release verification does not depend on GitHub Actions; tests are run locally and
on the dedicated Linux host, where the sandbox-dependent tests can execute.

Never commit `agent.toml`, `.env`, SSH keys, `ssh-targets.json`, runtime directories or private
agent outputs. Configure the read-only SSH inventory connector only for explicitly allowed
hosts and missions. Do not disable SSH host-key validation.

Please report vulnerabilities through GitHub's **Security → Report a vulnerability** private
reporting flow. Do not open a public issue with exploit details or secrets. No response SLA is
promised for this alpha. Upstream-only issues may also be covered by `frontier/SECURITY.md`.
