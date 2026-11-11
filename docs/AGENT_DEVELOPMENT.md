# Rozwój Agentów i Specyfikacja `AgentDNA` (`AGENT_DEVELOPMENT.md`)

## 1. Struktura `AgentDNA`

Każdy agent w APEX ONE jest opisany wersjonowanym dokumentem `AgentDNA` (przykłady referencyjne w katalogu `agents/*/agent_dna.json`):

- `instructions` — instrukcje systemowe oraz wymogi atrybucji dowodowej.
- `model_policy` — preferowana klasa modelu oraz łańcuch fallback.
- `capabilities` — deklarowane kompetencje badawcze.
- `allowed_tools` — jawna biała lista dozwolonych narzędzi (zasada najmniejszych uprawnień).
- `input_schema` / `output_schema` — kontrakty wejścia/wyjścia JSON Schema.
- `memory_policy` — dozwolone typy odczytu i zapisu w Pamięci Współdzielonej.
- `execution_policy` — pułap ryzyka (`R0`–`R3`) oraz limit ponowień.
- `budget` — maksymalna liczba tokenów i koszt USD na przebieg.
- `max_steps` / `timeout` / `evaluation_profile`.

## 2. Niezmienne Wersjonowanie

Edycja agenta (`PUT /api/v1/agents/{id}`) **nigdy nie nadpisuje** poprzedniej wersji. System tworzy wersję `v+1`, zachowując pełną historię oraz umożliwiając porównanie różnic (`GET /api/v1/agents/{id}/compare`) i powiązanie historycznych przebiegów eksperymentalnych z dokładnym numerem wersji agenta.

## 3. Zespół 8 Agentów Specjalistycznych

1. `supervisor` — dekompozycja celu i nadzór DAG.
2. `research-planner` — tworzenie Research Brief i pytań.
3. `source-scout` — rejestracja źródeł i deduplikacja SHA-256.
4. `evidence-analyst` — ekstrakcja twierdzeń i sprzeczności.
5. `data-analyst` — statystyki korpusu i wskaźników.
6. `experiment-engineer` — projekt powtarzalnego eksperymentu.
7. `critic-replicator` — audyt metodologiczny i kontrola replikacji.
8. `research-writer` — synteza raportu z powiązaniami źródłowymi.
