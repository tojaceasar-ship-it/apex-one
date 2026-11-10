# Przewodnik Badawczy i Inżynieria Dowodowa (`RESEARCH_GUIDE.md`)

## 1. Maszyna Stanów Projektu Badawczego (13 Stanów)

Każdy projekt badawczy przechodzi przez walidowane na backendzie stany:
`CREATED` → `PLANNING` → `RESEARCHING` → `ANALYZING` → `HYPOTHESIS_FORMATION` → `EXPERIMENT_DESIGN` → `AWAITING_APPROVAL` → `EXECUTING` → `VERIFYING` → `REPORTING` → `COMPLETED` (oraz stany wyjątkowe `FAILED` i `CANCELLED`).

## 2. Rejestracja Źródeł i Praca Lokalna Bez Płatnych Kluczy

Moduł `APEX SOURCE REGISTRY` obsługuje dokumenty `PDF`, `DOCX`, `MD`, `TXT` oraz zasoby `URL`. Każde źródło otrzymuje sumę kontrolną `SHA-256`, migawkę wersji oraz przechodzi kontrolę bezpieczeństwa pod kątem złośliwych instrukcji (`Prompt Injection`). Lokalny silnik dokumentów działa w 100% autonomicznie nawet wtedy, gdy zewnętrzny dostawca wyszukiwania Web nie jest skonfigurowany.

## 3. Graf Dowodowy i Rzetelność Niepewności

Każde twierdzenie (`ResearchClaim`) posiada jeden z pięciu statusów: `supported`, `partially_supported`, `contradicted`, `unresolved`, `rejected`. Pokrycie dowodowe jest obliczane wyłącznie na podstawie zweryfikowanych powiązań ze źródłami pierwotnymi — samoocena pewności modelu językowego nigdy nie jest traktowana jako skalibrowane prawdopodobieństwo.
