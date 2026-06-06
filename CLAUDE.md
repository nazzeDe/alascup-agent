本地环境调试运行：

web-server

```
cd web-server && ALASCUP_DEBUG=DEBUG ALASCUP_DATABASE_URL=postgresql://nazze:1115@localhost:5432/alascup_agent ALASCUP_SERVERS_CONFIG=config/servers.dev.json uv run uvicorn src.main:app --port 11450
```

tool-server
```
cd tool-server && uv run python -m src.main
```

frontend
```
cd frontend/vue-project && bun run dev
```

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`nazzeDe/alascup-agent`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary — all five canonical roles use their default names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.
