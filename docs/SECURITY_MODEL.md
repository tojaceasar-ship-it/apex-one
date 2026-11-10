# Model Bezpieczeństwa APEX ONE (`SECURITY_MODEL.md`)

## 1. Uwierzytelnianie, Sesje i RBAC
- Hasła haszowane algorytmem **Argon2id** (`argon2-cffi`).
- Sesje przeglądarkowe przekazywane w ciasteczku `HttpOnly` (`SameSite=Lax`) z wymaganym nagłówkiem `X-CSRF-Token` dla żądań modyfikujących lub poprzez nagłówek `Authorization: Bearer`.
- Sześć ról RBAC: `Owner`, `Administrator`, `Researcher`, `Operator`, `Evaluator`, `Viewer`.

## 2. Izolacja Wielodostępna (Multi-Tenant Isolation)
- Backend **nigdy nie ufa** nagłówkowi `X-Organization-Id` przesłanemu przez przeglądarkę bez weryfikacji aktywnego członkostwa użytkownika w danej organizacji (`403 FORBIDDEN_TENANT_OR_ROLE`).
- Na poziomie PostgreSQL wszystkie 47 tabel platformowych `0002` oraz tabele rdzeniowe `0001` wymuszają polityki `ENABLE & FORCE ROW LEVEL SECURITY`.

## 3. Ochrona przed Prompt Injection, SSRF i Samoeskalacją Agentów
- Importowane dokumenty są traktowane jako niezaufane dane dowodowe: skaner `inspect_untrusted_document` wykrywa próby nadpisania instrukcji systemowych i usuwa znaczniki `<script>`.
- Agenci mają trwale zablokowaną możliwość zmiany polityk autoryzacyjnych lub zatwierdzania własnych żądań R2/R3.
- Adaptery HTTP blokują adresy prywatne, loopback i link-local (`169.254.169.254`) przed atakami SSRF.

## 4. Nadzór R0–R3, Wiązanie Parametrów i Niezmienny Audyt
- Zgody `R2`/`R3` podpisują kanoniczny skrót `SHA-256` parametrów oraz wersji wykonania; każda modyfikacja parametrów unieważnia zgodę (`INVALIDATED`).
- Dziennik audytu tworzy łańcuch skrótów `SHA-256` i blokuje operacje `UPDATE` oraz `DELETE` zarówno w warstwie aplikacji (`403 AUDIT_IMMUTABLE`), jak i wyzwalaczem PostgreSQL `trg_audit_events_immutable`.
