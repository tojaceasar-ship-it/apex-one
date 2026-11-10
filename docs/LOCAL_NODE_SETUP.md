# Konfiguracja Węzła Lokalnego APEX HYBRID (`LOCAL_NODE_SETUP.md`)

## 1. Przeznaczenie Węzła Lokalnego

Pakiet `local_node/` (`apex-local-node`) to niezależna aplikacja Python instalowana na fizycznej stacji roboczej lub serwerze badawczym użytkownika, łącząca się wychodząco z bramką chmurową APEX ONE.

> **Uwaga dot. środowiska Arena:** W odizolowanym kontenerze Arena serwer chmurowy rzetelnie raportuje `user_machine_connected: false`, ponieważ sandbox chmurowy nie ma dostępu do fizycznego komputera użytkownika.

## 2. Instalacja i Parowanie na Komputerze Użytkownika

```bash
cd local_node
python3 -m venv .venv
.venv/bin/pip install -e .

# Wygeneruj jednorazowy token parowania w Workbenchu -> Węzły Lokalne (Hybrid)
export APEX_CLOUD_GATEWAY_URL="https://twoja-instancja-apex.org"
export APEX_LOCAL_NODE_SHARED_SECRET="<token_parowania>"
export APEX_LOCAL_WORKSPACE_DIR="/sciezka/do/wybranego/katalogu"

.venv/bin/apex-local-node status
```

## 3. Gwarancje Bezpieczeństwa Lokalnego

- Brak zdalnego nieograniczonego terminala (`shell`/`bash`/`cmd` są trwale zablokowane w `permissions.py`).
- Oddzielna kontrola uprawnień dla operacji `read`, `write` oraz typowanych zadań `execute`.
- Dostęp ograniczony wyłącznie do jawnie wskazanych katalogów lokalnych (ochrona przed `..` i dowiązaniami symbolicznymi poza granicę).
- Natychmiastowe unieważnienie urządzenia przyciskiem **Revoke** w Workbenchu.
