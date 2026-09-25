# JevLoop — integrated dev workflow
# make dev   restarts Laya (:8791) + backend (:8790) + frontend (:5173)
# make stop  kills whatever is listening on those ports

BACKEND_PORT   := 8790
LAYA_PORT      := 8791
FRONTEND_PORTS := 5173-5199   # Vite falls back to 5174+ when 5173 is taken

.DEFAULT_GOAL := help
.PHONY: help dev stop serve build test smoke check

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-8s\033[0m %s\n", $$1, $$2}'

dev: stop ## Restart Laya, backend, and frontend dev servers with the latest code
	@echo "==> laya http://127.0.0.1:$(LAYA_PORT)      (jevloop laya-serve, MLX or CUDA)"
	@echo "==> api  http://127.0.0.1:$(BACKEND_PORT)  (jevloop serve, LAYA_BASE_URL -> $(LAYA_PORT))"
	@echo "==> web  http://localhost:5173             (vite dev, proxies /api -> $(BACKEND_PORT))"
	pnpm dev

stop: ## Stop anything listening on the Laya, backend, and frontend ports
	@pids=$$(lsof -nP -t -iTCP:$(BACKEND_PORT) -iTCP:$(LAYA_PORT) -iTCP:$(FRONTEND_PORTS) -sTCP:LISTEN 2>/dev/null || true); \
	if [ -n "$$pids" ]; then \
		echo "==> stop: killing $$pids"; \
		kill $$pids 2>/dev/null || true; \
		sleep 1; \
		kill -9 $$pids 2>/dev/null || true; \
	fi

serve: stop ## Serve the built frontend from the backend (http://127.0.0.1:$(BACKEND_PORT))
	pnpm serve

build: ## Build the frontend into backend-served dist/
	pnpm build:frontend

test: ## Run backend tests
	pnpm test

smoke: ## Offline kernel + Docker smoke (no model keys needed)
	cd backend && uv run jevloop smoke

bench: ## Paired multi-turn benchmark (needs model keys + Docker)
	cd backend && uv run jevloop bench

check: ## TypeScript check for the frontend
	pnpm --filter jevloop-web check
