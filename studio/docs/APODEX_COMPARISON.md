# Apodex a náš dlouhodobý režim

Ověřeno z veřejného webu 22. 9. 2026. Webová tvrzení nejsou nezávislý provozní test.

Apodex rozlišuje Deep Research, Deep Solve a Deep Discover. Zdůrazňuje uchování stavu, rozhodnutí a omezení napříč týdny práce. [Oficiální web](https://www.apodex.com/)

U Deep Discover popisuje asynchronní orchestraci až 150 agentů, společné průběžně doplňované úložiště zjištění a oddělené kontexty kontrolorů. [Popis architektury](https://www.apodex.com/discover)

FrontierAgent je jejich veřejný agentní framework. Převzetí frameworku nezpřístupňuje automaticky veškeré funkce hostovaného produktu nebo jeho modely. [Oficiální GitHub](https://github.com/ApodexAI)

## Co implementujeme v Mineru

| Princip | Aktuální provedení |
|---|---|
| Trvalý stav projektu | SQLite: zadání, plán, závislosti, otázky, odpovědi a pokusy |
| Dohledatelné důkazy | Reporty na disku, kopie přijatých reportů v databázi, kontrolní součty produktových souborů |
| Oddělené ověření | Nová relace review pro každý úkol a závěrečné ověření produktu; model lze zvolit zvlášť |
| Automatické opravy | Nálezy se vrací realizátorovi; po třech neúspěšných kolech vzniká blokace |
| Obnova po přerušení | Jedinečné pokusy, uložené identity procesů, úklid před restartem a omezené opakování |
| Vstup od člověka | Zvýrazněné otázky a trvale uložené odpovědi, pokračování nezávislé práce |

Náš řadič zatím spouští jednoho pracovníka současně. Nemá 150 paralelních agentů, plný graf rozporů mezi zdroji ani automatické přepisování vlastního řídicího kódu. Neuvádíme funkční nebo výkonnostní shodu s hostovaným Apodexem.
