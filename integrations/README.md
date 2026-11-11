# APEX ONE Integrations

Concrete adapter contracts and configuration schemas for external & local integrations:

- `web-search/` — Configurable web-search provider (`APEX_WEB_SEARCH_PROVIDER`, `APEX_WEB_SEARCH_API_KEY`); degrades cleanly to local document research when unconfigured.
- `ollama/` — Local LLM & embedding provider (`APEX_OLLAMA_URL`, default `http://127.0.0.1:11434`).
- `github/` — GitHub REST repository & citation metadata adapter (`api.github.com`).
- `generic-api/` — SSRF-guarded HTTP REST integration adapter.
- `browser/` — Controlled URL snapshot & attribution extractor.
