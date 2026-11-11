# Przewodnik Ewaluacji i Benchmarków (`EVALUATION_GUIDE.md`)

## 1. Dziewięć Wymaganych Kategorii Oceny

Każdy przebieg ewaluacyjny ocenia badaną konfigurację w 9 wymiarach:
1. `functional_accuracy` — trafność funkcjonalna i kompletność.
2. `research_accuracy` — trafność badawcza (w tym wychwycenie niuansów i sprzeczności).
3. `evidence_quality` — odsetek twierdzeń powiązanych ze źródłami pierwotnymi.
4. `tool_reliability` — niezawodność wywołań narzędzi.
5. `safety_compliance` — zgodność z pułapem ryzyka i atrybucją.
6. `reproducibility` — deterministyczna powtarzalność artefaktów.
7. `latency` — efektywność czasowa względem złożoności potoku.
8. `cost_efficiency` — efektywność kosztowa i tokenowa.
9. `regression_detection` — brak regresji względem przebiegu bazowego.

## 2. Ręczna Ocena Ekspercka (Human Review)

Użytkownicy z rolą `Evaluator`, `Administrator` lub `Owner` mogą rejestrować oceny eksperckie (`POST /api/v1/evaluations/runs/{id}/human-review`). Pojedyncza ocena ludzka **nigdy nie powoduje automatycznej aktualizacji agenta produkcyjnego** (`production_agent_auto_updated = false`).
