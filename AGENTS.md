## 本地开发

```
# web-server (11450)
cd web-server && ALASCUP_DEBUG=DEBUG ALASCUP_DATABASE_URL=postgresql://nazze:1115@localhost:5432/alascup_agent ALASCUP_SERVERS_CONFIG=config/servers.dev.json uv run uvicorn src.main:app --port 11450
# tool-server (11451)
cd tool-server && uv run python -m src.main
# frontend (5173)
cd frontend/vue-project && bun run dev
```

日志持久化：`>> logs/services/{web-server,tool-server,frontend}.log 2>&1 &`
读取：`tail -f logs/services/*.log` / `grep -i error logs/services/*.log`

## Agent skills

- Issues: GitHub `nazzeDe/alascup-agent` → `docs/agents/issue-tracker.md`
- Labels: 五角色默认名 → `docs/agents/triage-labels.md`
- Domain: CONTEXT.md + `docs/adr/` → `docs/agents/domain.md`
