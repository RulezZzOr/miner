# Security

Miner / Switch Studio is an alpha for a single trusted operator. It has no multi-user login or
per-department isolation. Native tools have the Studio OS user's permissions. Host/Origin and
request-token checks are not user authentication. Keep the default loopback listener or use a
trusted private network; do not publish the port directly to the internet.

Use a dedicated account or independently configured sandbox for untrusted work. Review tool
approval and model data handling before connecting a provider. Model output, web pages and
repository files can contain untrusted instructions.

Never commit `agent.toml`, `.env`, SSH keys, `ssh-targets.json`, runtime directories or private
agent outputs. Configure the read-only SSH inventory connector only for explicitly allowed
hosts and missions. Do not disable SSH host-key validation.

Please report vulnerabilities through GitHub's **Security → Report a vulnerability** private
reporting flow. Do not open a public issue with exploit details or secrets. No response SLA is
promised for this alpha. Upstream-only issues may also be covered by `frontier/SECURITY.md`.
