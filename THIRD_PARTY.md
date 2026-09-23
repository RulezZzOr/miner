# Uvedení třetích stran

## FrontierAgent

Repozitář obsahuje upravenou kopii projektu [ApodexAI/FrontierAgent](https://github.com/ApodexAI/FrontierAgent) ve složce `frontier/`, založenou na commitu `9e533db6f6c34d16037ee5ec964c479d0eb51cde`.
Původní zdrojový kód, oznámení o autorských právech, licence, testy a dokumentace jsou zachovány. Viz [frontier/LICENSE](frontier/LICENSE) a [poznámky ke změnám](frontier/SWITCH.md). Původní názvy projektu zůstávají kvůli uvedení autorů a kompatibilitě API.

FrontierAgent dodává běhové prostředí agentů, pracovní postupy ReAct a Agent Team, nástroje, terminálové rozhraní a vyhodnocovací rámec. Switch Studio dodává webové vývojové prostředí a řadiče projektů, firem a produktů; zahrnuje úpravy pro poskytovatele modelů a předávání zpráv o práci.

## Závislosti a další součásti

Pevné verze závislostí Pythonu uvádí `frontier/uv.lock`; platí pro ně příslušné licence. Vnořené součásti, materiály a balíky testovacích úloh si zachovávají vlastní oznámení a licence, včetně `frontier/benchmarks/frontierchallenge/LICENSE`.

Váhy modelů ani soukromé testovací datové sady nejsou součástí distribuce. Názvy modelů a poskytovatelů popisují kompatibilitu. Tento projekt není oficiálně spojený s Apodexem ani jím schválený.

## FrontierChallenge — CC BY 4.0

Přibalená součást `frontier/benchmarks/frontierchallenge/` uvádí jako autory Apodex Team a jmenované autory FrontierChallenge zachované v [původním README a citaci](frontier/benchmarks/frontierchallenge/README.md#citation). [Původní zdroj](https://github.com/ApodexAI/FrontierAgent/tree/9e533db6f6c34d16037ee5ec964c479d0eb51cde/benchmarks/frontierchallenge).

Její licence je [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/); úplné znění je zachováno v [jejím LICENSE](frontier/benchmarks/frontierchallenge/LICENSE). Miner nezměnil zdrojový obsah této součásti oproti uvedené původní verzi. Kořenová licence Apache-2.0 nenahrazuje podmínky CC BY 4.0 ani licence dalšího softwaru.
