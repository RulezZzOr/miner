# Kontrola instalace Studia

Otevřete URL Studio na hostiteli nebo v síti LAN. Cesty v **Otevřít projekt** odkazují na počítač, kde běží Studio. Začněte v nové složce projektu, aby bylo snadné kontrolovat výstupy testů.

1. **Trvalé ukládání v editoru:** použijte **Nový soubor**, zadejte `notes.md`, napište krátkou poznámku a uložte. Znovu načtěte stránku, znovu otevřete soubor a zkontrolujte jeho obsah.
2. **Skutečné spuštění modelu:** vyberte dostupný model a požádejte ho o vytvoření malého zdrojového souboru a spuštění testu. Zkontrolujte jak uložený soubor v **Souborech**, tak skutečný výstup testu v **Aktivitě** nebo **Konzoli**. Samotné tvrzení modelu není důkazem existence souboru ani úspěšného testu.
3. **Schválení:** pro první běh ponechte **Povolit automatické akce** vypnuté. Zápis souboru se objeví v **Schváleních a odpovědích** a v horní liště. Zkontrolujte akci, poté zvolte **Ano**, **Ne** nebo odešlete zpětnou vazbu. Zpětná vazba neschvaluje akci.
4. **Kancelář a živá mapa:** otevřete **3D kancelář** během aktivního běhu. Vyberte jeho pracoviště, zkontrolujte model a poslední aktivitu, poté otevřete běh nebo živou mapu. Pozastavený nebo blokovaný úkol by se neměl zobrazovat jako aktivně prováděný.
5. **Dlouhodobý projekt:** v **AI projektech** zadejte konkrétní akceptační kritéria a spustitelné kontroly. Zkontrolujte plán, sestavení, výsledky kontroly a ověření. Přijměte práci až po kontrole výsledku. Běžný dokončený běh se liší od převzetí produktu.
6. **Řadič firmy:** vytvořte nebo vyberte firmu, připojte zamýšlené projekty a zkontrolujte její limity. Začněte s jedním omezeným úkolem před přidáním opakování. Potvrďte, jak jsou nakonfigurovány schvalování nástrojů a automatické přijetí.

Pracovní prostor zůstává na hostiteli Studio i po zavření prohlížeče. Pro pokračování práce musí zůstat spuštěný hostitel i služba Studio. Zavření prohlížeče nezastaví úlohu na serveru. Použijte **Zastavit** nebo **Pozastavit řadič**, chcete-li spuštění zastavit.

Úkol, který dosáhne časového limitu nebo limitu kroků či běhů, je nedokončený. Před opakováním zkontrolujte zaznamenaný důvod a poslední skutečnou aktivitu. Dostupnost modelu, rychlost generování a kvalita kontroly závisí na vybraném serveru; samotná odpověď HTTP na stav zdraví tyto parametry neověřuje.

Jedná se o alfa verzi. Windows používají WSL2. Volitelné přihlášení poskytovatele vyžaduje účet vlastníka; není ověřeno lokálním testem modelu. Oddělení firmy popisují organizační rozdělení práce a aktuální řadič sdílí jeden slot realizátora.
