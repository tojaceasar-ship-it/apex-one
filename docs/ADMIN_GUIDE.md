# Podręcznik Administratora (`ADMIN_GUIDE.md`)

## 1. Zarządzanie Organizacjami i Rolami RBAC

W widoku **Ustawienia i Administracja (`/settings`)** właściciel (`Owner`) lub `Administrator` może:
- tworzyć nowe izolowane organizacje wielodostępne,
- zapraszać użytkowników z przypisaniem jednej z 6 ról (`Owner`, `Administrator`, `Researcher`, `Operator`, `Evaluator`, `Viewer`),
- wystawiać poświadczenia programistyczne API (`apxkey_...`).

## 2. Centrum Zatwierdzeń R2/R3 i Audyt

- W widoku **Centrum Zatwierdzeń (`/approvals`)** zatwierdzaj lub odrzucaj wstrzymane przepływy o poziomie ryzyka `R2` i `R3`.
- W widoku **Dziennik Audytu (`/audit`)** monitoruj wskaźnik spójności łańcucha skrótów `SHA-256`.

## 3. Wyłącznik Bezpieczeństwa (`Emergency Stop`)

W sytuacji incydentu użyj przycisku **AKTYWUJ EMERGENCY STOP** w `/settings`. Operacja natychmiast anuluje wszystkie aktywne przebiegi agentów w organizacji i blokuje uruchamianie kolejnych zadań (`HTTP 409 GOVERNANCE_BLOCKED`) do czasu odblokowania.
