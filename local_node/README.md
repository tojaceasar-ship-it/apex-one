# APEX ONE — Hybrid Local Node (`apex-local-node`)

Standalone Python application allowing a researcher's physical workstation or lab machine to connect outbound over TLS/WebSocket to the **APEX ONE Cloud Gateway** without changing Cloud architecture.

> **Important (Arena Sandbox Notice):** Inside Arena's isolated sandbox environment, `apex-local-node` is **not** connected to the user's physical computer (`user_machine_connected = false`). Only unit tests and loopback Cloud↔Node protocol verification tests are executed inside Arena.

## Standalone Local Installation (User Machine)

```bash
cd local_node
python3 -m venv .venv
.venv/bin/pip install .

export APEX_NODE_CLOUD_URL="https://your-apex-cloud.example.com"
export APEX_NODE_TENANT_ID="00000000-0000-0000-0000-000000000001"
export APEX_NODE_SHARED_SECRET="your-32-char-shared-enrollment-secret"
.venv/bin/apex-local-node --inspect-capabilities
.venv/bin/apex-local-node --once
```
