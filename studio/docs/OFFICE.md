# 3D kancelář

Otevřete **3D kancelář** v horní liště. Vyberte firmu nebo ponechte **Všechny pracovní prostory**, poté vyberte obsazené pracoviště pro prozkoumání úkolu. Detailní panel odkazuje na stávající živou mapu, běh a schválení, dále na firmu.

Scéna je původní geometrií v CSS 3D bez externích zdrojů, vykreslovače, CDN ani dalších služeb. Čte stávající API Studio. Jde o přehled; nepřidává plánovač ani nezvyšuje kapacitu realizátorů.

- Každé obsazené pracoviště představuje zaznamenaný úkol nebo samostatný běh. Prázdná pracoviště jsou ilustrativní, nejsou aktivními agenty.
- Oddělení zobrazuje až čtyři úkoly podle priority. Seznam zahrnuje všechny úkoly ve vybraném rozsahu firmy nebo pracovního prostoru.
- Pohyb při práci a kontrole vyžaduje nedávnou zaznamenanou aktivitu. Běžící proces bez nových událostí je označen jako **Čeká na aktivitu**. Samostatná ověření mají vlastní stav kontroly.
- Stavy řadiče: blokovaný, pozastavený a zrušený mají přednost před zbytkovými příznaky procesu. Dokončený samostatný běh je označen jako **Běh dokončen**, nikoli jako převzatý produkt.
- Po selhání spojení se animace zastaví, počet aktivních se stává neznámým a zobrazení označí zachovaný snímek jako potenciálně zastaralý.
- Zastavený běh zobrazuje svůj poslední zaznamenaný běh a model, aby nedošlo k záměně selhání kontrolora s profilem realizátora.
- Přiřazení do oddělení je organizační kontext, nikoli hranice oprávnění. Řadič firmy stále sdílí jeden pracovní slot.

Použijte ovládací prvky pro rotaci a přiblížení nebo přetáhněte podlahu. **Resetovat kameru** obnoví výchozí pohled. **Zobrazení seznamem** poskytuje přístupnou alternativu, včetně na menších obrazovkách. Volby sníženého pohybu vypnou animace scény.

Ovládací prvky s otazníkem vysvětlují účel, použití a příklad. Klikněte na ně nebo použijte Enter/Mezerník; Esc ukončí nápovědu a vrátí klávesové zaměření. Otevření nápovědy nepředává formulář ani neschvaluje nástroj.

Rozhraní i vestavěné šablony jsou v češtině. Stávající uživatelské názvy, pokyny, uložené zprávy a historie si zachovávají původní jazyk.

## Soubory a umístění serveru

Studio upravuje soubory na svém hostitelském počítači. Při otevření přes adresu LAN odkazují cesty na tento server. Běžné běhy agentů i dlouhodobé projekty mapují `/workspace` na projekt vybraný v editoru. Protokoly běhu a artefakty v `/outputs` zůstávají uloženy odděleně. Po zapsání nového souboru agentem obnovte soubory a otevřete je pro kontrolu uloženého obsahu.
