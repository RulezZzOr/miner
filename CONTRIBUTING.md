# Přispívání

Jde o experimentální aplikaci pro vlastní provoz. Před nahlášením chybu zopakujte v dočasném projektu. Uveďte verzi, systém, poskytovatele modelu a chybovou zprávu bez citlivých údajů. Nepřikládejte hesla, soukromé firemní soubory ani celé přepisy agentů.

## Místní kontroly

Nainstalujte aplikaci pomocí `sh setup-studio`. V kořenové složce repozitáře spusťte:

```sh
frontier/.venv/bin/python -m unittest discover -s studio/tests -v
node --test studio/tests/*.cjs
frontier/.venv/bin/python scripts/build_release.py
```

Node.js je potřeba pro testy rozhraní, nikoli pro provoz Studia. Čistou instalaci ověřte takto:

```sh
frontier/.venv/bin/python scripts/smoke_release.py dist/switch-studio-0.4.0-alpha.4-macos.zip --suite
```

Na Linuxu použijte archiv `.tar.gz`. Tyto testy ověřují mechanismy a pracovní postupy s předvídatelným výsledkem. Kvalita skutečného modelu vyžaduje samostatnou ohraničenou zkoušku s uloženými výsledky.

Volitelná šablona GitHub Actions je v `docs/ci/studio-platforms.example.yml`. Není aktivním pracovním postupem. Správce s oprávněním zápisu pracovních postupů ji může zkopírovat do `.github/workflows/studio-platforms.yml`. Původní postupy ve `frontier/.github/` jsou zachovány jako zdrojový obsah a zde se nespouštějí.

Změny původního projektu udržujte úzce zaměřené a zachovejte uvedení autorů. V návrhu změny popište chování a provedené ověření. Neukládejte do Gitu `agent.toml`, `.env`, `.switch-agent`, `.apodex`, konfiguraci SSH cílů, generované datové sady ani přihlašovací údaje.
