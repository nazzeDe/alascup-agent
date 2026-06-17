.PHONY: build build-test up down up-test down-test logs logs-web logs-tool clean \
        test test-unit test-integration test-e2e test-coverage \
        test-unit-web test-unit-tool test-unit-fe \
        test-integration-web test-integration-tool \

COMPOSE_PROD := docker compose
COMPOSE_TEST := docker compose -f docker-compose.test.yml

# === 构建 ===
build:
	$(COMPOSE_PROD) build

build-test:
	$(COMPOSE_TEST) build

# === 运行 ===
up:
	$(COMPOSE_PROD) up -d

down:
	$(COMPOSE_PROD) down

up-test:
	$(COMPOSE_TEST) up -d

down-test:
	$(COMPOSE_TEST) down

# === 日志 ===
logs:
	$(COMPOSE_PROD) logs -f

logs-web:
	$(COMPOSE_PROD) logs -f web-server

logs-tool:
	$(COMPOSE_PROD) logs -f tool-server

# === 测试 ===
test: test-unit test-integration test-e2e

test-unit: test-unit-web test-unit-tool test-unit-fe

test-unit-web:
	cd web-server && uv run pytest tests/ -m unit -v

test-unit-tool:
	cd tool-server && uv run pytest tests/ -m unit -v

test-unit-fe:
	cd frontend/vue-project && bun run test -- --run

test-integration: test-integration-web test-integration-tool

test-integration-web:
	cd web-server && uv run pytest tests/ -m integration -v

test-integration-tool:
	cd tool-server && uv run pytest tests/ -m integration -v

# E2E 测试：默认用 mock API + Vite dev server 运行，无需后端。
#   针对 docker-compose 测试环境：E2E_BASE_URL=http://localhost E2E_SKIP_WEB_SERVER=1 make test-e2e
#   交互式 UI：cd frontend/vue-project && bun run test:e2e:ui
test-e2e:
	cd frontend/vue-project && bun run test:e2e

# E2E Live 测试：需先手动启动 web-server + tool-server（见 plan Phase 1 步骤）。
#   E2E_LIVE_HEADED=1 make test-e2e-live  显示浏览器窗口
test-e2e-live:
	cd frontend/vue-project && bun run test:e2e:live

# === 覆盖率 ===
test-coverage:
	cd web-server && uv run pytest tests/ -m unit --cov=src --cov-report=term-missing
	cd tool-server && uv run pytest tests/ -m unit --cov=src --cov-report=term-missing
	cd frontend/vue-project && bun run test -- --coverage

# === 清理 ===
clean:
	$(COMPOSE_TEST) down -v
	rm -rf logs/

