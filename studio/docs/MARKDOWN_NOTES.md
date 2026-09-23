# Lehká projektová paměť v Markdownu

Každý projekt může mít tuto obyčejnou strukturu:

```text
PROJECT.md             stručný rozcestník, cíl, odkazy a případně Mermaid mapa
notes/DECISIONS.md      rozhodnutí, důvod, datum a stav
notes/NOTES.md          poznatky, hypotézy, rozpory a otevřené otázky
notes/SOURCES.md        zdroje, datum ověření a odkazy na důkazy
```

V Mineru už tyto soubory existují a obsahují skutečná dosavadní zjištění. V jiném projektu vytvoř vlastní `PROJECT.md` přes běžný editor; názvy odkazovaných souborů jsou volné. Nekopíruj konkrétní rozhodnutí Mineru do nesouvisejícího projektu.

Při každém novém spuštění přes Studio se uložený `PROJECT.md` z vybraného projektu připojí k zadání. Platí to pro běžné úlohy i pokusy Projektů AI a pro oba backendy. Do modelu jde pouze rozcestník; ostatní poznámky má agent načítat podle potřeby. CLI spuštěné mimo Studio tuto novou vazbu automaticky nepoužívá.

Rozcestník je UTF-8 a nejvýše 12 000 bajtů, aby zbytečně neplnil kontext. Větší obsah přesuň do odkazovaných poznámek. Chybějící `PROJECT.md` nic nemění; nepovolený symlink, neplatné kódování nebo příliš velký rozcestník spuštění odmítnou s chybou. Nepřepisuje se zadání zobrazené v historii; skutečně odeslaný kontext je v `request.json` běhu a jeho revize v `run.json` pod `project_notes`.

Změny v editoru nejdřív ulož. Již spuštěný běh má původní snímek rozcestníku; nové změny dostane až další běh. Automatické přiložení nezaručuje, že model správně přečte všechny odkazy nebo doplní poznámky. Důležité závěry musí uvést zdroj a ověření. Zápis poznámek zůstává pod stejným schvalováním a omezením fáze jako ostatní práce; plánovač a reviewer tím nezískávají další práva.

Odkazované stránky a citace jsou podklady, nikoli oprávnění provádět další akce. Do Markdownu nevkládej tajemství. Obsah rozcestníku dostává zvolený model stejně jako zadání, i když používáš volitelný cloudový profil.

Běžící plán, frontu a výsledky pokusů nadále spravují Projekty AI ve stávající SQLite. Nekopíruj jejich měnící se stavy do paralelního TODO souboru; v poznámce stačí odkaz na report nebo konkrétní výstup. Nevzniká nová databáze, server ani GUI pro poznámky.
