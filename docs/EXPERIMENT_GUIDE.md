# Przewodnik Eksperymentów i Odtwarzalności (`EXPERIMENT_GUIDE.md`)

## 1. Komponenty Eksperymentu

Każdy eksperyment w `APEX EXPERIMENT ENGINE` przypina:
- identyfikator i numer wersji zbioru danych (`dataset_id`, `dataset_version`) wraz z sumą kontrolną `SHA-256`,
- wersję agenta lub zespołu wieloagentowego,
- identyfikator modelu oraz parametry wykonania,
- reguły walidacji artefaktów wyjściowych (`results/experiment_results.json`).

## 2. Izolacja Wykonywania Kodu (`ExperimentSandboxAdapter`)

Zgodnie z polityką bezpieczeństwa platforma **nigdy nie wykonuje dowolnego niezaufanego kodu bezpośrednio w procesie API**. Wywołania `/api/v1/sandbox/jobs/execute` bez poświadczonego zewnętrznego dostawcy izolacji microVM/kontenerowej są jawnie blokowane kodem `503 UNTRUSTED_EXECUTION_BLOCKED`. Deklaratywne protokoły benchmarkowe i statystyczne działają bezpiecznie w granicach kontrolowanego silnika analitycznego.

## 3. Silnik Reprodukcji (`Reproduction Engine`)

Wywołanie `POST /api/v1/experiments/runs/{id}/reproduce` odtwarza przebieg na identycznej migawce specyfikacji i porównuje metryki wyjściowe (`reproduction_verified: true`).
