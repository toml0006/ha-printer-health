.PHONY: pk-dev-init pk-dev-up pk-dev-down pk-dev-restart pk-dev-logs pk-dev-health pk-dev-discovery pk-dev-rescan

PK_DEV_COMPOSE := printer_keepalive/dev/docker-compose.yml
PK_DEV_DATA := printer_keepalive/dev/data
PK_DEV_OPTIONS := $(PK_DEV_DATA)/options.json
PK_DEV_OPTIONS_EXAMPLE := printer_keepalive/dev/options.example.json

pk-dev-init:
	@mkdir -p $(PK_DEV_DATA)
	@test -f $(PK_DEV_OPTIONS) || cp $(PK_DEV_OPTIONS_EXAMPLE) $(PK_DEV_OPTIONS)

pk-dev-up: pk-dev-init
	docker compose -f $(PK_DEV_COMPOSE) up -d --build

pk-dev-down:
	docker compose -f $(PK_DEV_COMPOSE) down

pk-dev-restart:
	docker compose -f $(PK_DEV_COMPOSE) restart printer_keepalive

pk-dev-logs:
	docker compose -f $(PK_DEV_COMPOSE) logs -f printer_keepalive

pk-dev-health:
	@curl -sSf --retry 10 --retry-connrefused --retry-delay 1 http://127.0.0.1:18099/health | python3 -m json.tool

pk-dev-discovery:
	@curl -sSf --retry 10 --retry-connrefused --retry-delay 1 http://127.0.0.1:18099/discovery | python3 -m json.tool

pk-dev-rescan:
	@curl -sSf --retry 10 --retry-connrefused --retry-delay 1 "http://127.0.0.1:18099/discovery?force=true" | python3 -m json.tool
