# Bezpečnost

Miner / Switch Studio je alfa verze pro jednoho důvěryhodného uživatele. Nemá přihlášení pro více uživatelů ani izolaci oddělení. Nativní nástroje mají oprávnění uživatele operačního systému, pod kterým běží Studio. Kontroly Host/Origin a tokenu požadavku nejsou přihlášením uživatele. Ponechte místní adresu nebo používejte důvěryhodnou soukromou síť; port nevystavujte přímo internetu.

Pro nedůvěryhodnou práci použijte vyhrazený účet nebo samostatně nastavené izolované prostředí. Před připojením poskytovatele zkontrolujte schvalování nástrojů a nakládání s daty. Výstupy modelů, webové stránky i soubory repozitáře mohou obsahovat nedůvěryhodné pokyny.

Nikdy nezveřejňujte `agent.toml`, `.env`, SSH klíče, `ssh-targets.json`, běhové složky ani soukromé výstupy agentů. SSH inventarizaci pouze pro čtení nastavujte jen pro výslovně povolené servery a úkoly. Nevypínejte ověřování klíče SSH serveru.

Zranitelnosti hlaste soukromým formulářem GitHubu **Security → Report a vulnerability**. Nezakládejte veřejné hlášení s podrobnostmi zneužití nebo tajnými údaji. Tato alfa verze nemá zaručenou dobu reakce. Pro chyby pouze v původním projektu může platit také `frontier/SECURITY.md`.
