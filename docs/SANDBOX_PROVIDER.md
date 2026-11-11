# Dostawca Izolowanego Sandboxa Eksperymentów (`SANDBOX_PROVIDER.md`)

## 1. Zasada nadrzędna

**APEX ONE nigdy nie wykonuje niezaufanego kodu wygenerowanego przez agentów bezpośrednio w procesie backendu ani w głównym kontenerze/VM środowiska Arena.**

Dopóki nie zostanie skonfigurowany zewnętrzny dostawca mikro-VM z potwierdzoną izolacją sprzętową/jądra (`APEX_SANDBOX_ISOLATION_ATTESTED=true`), system wymusza tryb chroniony (`GuardedUnverifiedSandboxAdapter`) i odrzuca próby uruchomienia kodu statusem `HTTP 503` (`UNTRUSTED_EXECUTION_BLOCKED`).

## 2. Architektura warstwy `apex_one.sandbox`

Warstwa w `backend/apex_one/sandbox/` składa się z:

1. **`SandboxJob`** (`contracts.py`) — silnie typowany kontrakt zadania:
   - dozwolone środowiska wykonawcze (`python3.11`, `node22`, `r4`),
   - jawny `entrypoint` (bez ewaluacji powłoki `sh -c` na niezaufanych ciągach),
   - blokada przekazywania wrażliwych zmiennych środowiskowych (`APEX_*`, `DATABASE_*`, `POSTGRES*`, `REDIS_*`, `AWS_*`, `GH_*`, `OPENAI_*` itd.).
2. **`ResourceLimits` i `TimeoutPolicy`** — twarde limity CPU (`0.1–8.0` rdzenia), RAM (`64–16384 MB`), dysku, liczby procesów (`max_pids`), strumienia wyjścia, `allow_network=False` domyślnie oraz limit czasu zegarowego (`wall_time_seconds`) i czasu CPU.
3. **`IsolatedIOWorkspace` i `ArtifactValidator`** (`validator.py`):
   - katalog wejściowy montowany w trybie tylko do odczytu (`0400`),
   - odrzucanie dowiązań symbolicznych (`symlink`), ucieczek ścieżek (`../`), plików wykonywalnych i rozszerzeń spoza białej listy,
   - egzekwowanie limitu liczby plików, rozmiaru pojedynczego artefaktu i łącznego rozmiaru wyjścia oraz obliczanie skrótu `SHA-256`.
4. **`ExperimentSandboxAdapter`** (`adapter.py`):
   - `GuardedUnverifiedSandboxAdapter` — aktywny domyślnie (`APEX_SANDBOX_PROVIDER=none`); obsługuje rejestrację, walidację kontraktu i anulowanie zadań, lecz blokuje `execute()`.
   - `ExternalMicroVMSandboxAdapter` — klient HTTPS komunikujący się z zewnętrzną bramką mikro-VM (`external_http`, `e2b`, `modal`), wymagający flagi `isolation_attested: true` zarówno w konfiguracji, jak i w odpowiedzi wykonawczej dostawcy.

## 3. Kontrakt HTTP zewnętrznego dostawcy (`POST /v1/sandbox/execute`)

Zewnętrzny serwis wykonawczy (np. wrapper wokół Firecracker, Kata Containers, gVisor, E2B lub Modal) musi przyjmować zserializowany `SandboxJob` z nagłówkiem `Authorization: Bearer <APEX_SANDBOX_API_KEY>` i zwracać:

```json
{
  "isolation_attested": true,
  "state": "SUCCEEDED",
  "exit_code": 0,
  "stdout_preview": "...",
  "stderr_preview": "",
  "artifacts": [
    {
      "relative_path": "metrics.json",
      "size_bytes": 128,
      "sha256": "<64-znakowy-hex-sha256>",
      "extension": ".json"
    }
  ]
}
```

Brak pola `"isolation_attested": true` w odpowiedzi skutkuje natychmiastowym odrzuceniem wyniku przez `ExternalMicroVMSandboxAdapter`.
