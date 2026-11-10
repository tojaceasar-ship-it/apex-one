# APEX ONE 1.0 — Specyfikacja Produktowa (`PRODUCT_SPEC.md`)

## 1. Wizja i Cel Systemu

**APEX ONE (Full Research Edition 1.0)** stanowi ujednoliconą platformę inżynieryjno-badawczą umożliwiającą zespołom naukowym, analitycznym i R&D prowadzenie weryfikowalnych badań wieloagentowych opartych na dowodach pierwotnych, powtarzalnych eksperymentach i rygorystycznym nadzorze bezpieczeństwa.

## 2. Kluczowe Zasady Projektowe

1. **Rygor Dowodowy ponad Płynność Językową:** Wnioski raportowe muszą być powiązane z zarejestrowanymi źródłami (`Source` → `Evidence` → `Claim`). Samoocena modelu LLM nigdy nie jest prezentowana jako skalibrowane prawdopodobieństwo statystyczne.
2. **Brak Predefiniowanych Zwycięzców w Benchmarkach:** Porównania architektur (np. Single-Agent vs Multi-Agent) mierzą rzeczywiste kompromisy między wykrywalnością sprzeczności a kosztem tokenowym i opóźnieniem.
3. **Wielowarstwowy Nadzór R0–R3:** Operacje o podwyższonym wpływie wymagają jawnych zatwierdzeń powiązanych ze skrótem SHA-256 parametrów; każda zmiana parametrów unieważnia zgodę.
4. **Separacja Danych Demonstracyjnych i Produkcyjnych:** Przestrzeń `demo` jest jawnie oznaczona i odseparowana filtrem `workspace_scope` od rzeczywistych projektów badawczych (`research`).

## 3. Zakres 18 Modułów i 23 Widoków Workbench

Wszystkie 18 modułów opisanych w dyrektywie architektonicznej (od `APEX CORE` po `APEX PLUGINS & EXTENSIONS`) jest zrealizowanych w kodzie backendu (`backend/apex_one/`) oraz udostępnionych w 19 sekcjach nawigacyjnych i 23 trasach aplikacji `@apex-one/web`.
