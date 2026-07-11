#!/usr/bin/env bash
set -euo pipefail

docker compose up --build -d
trap 'docker compose down -v' EXIT

echo "Ожидание healthy db..."
for i in $(seq 1 30); do
  status=$(docker compose ps db --format json | grep -o '"Health":"[a-z]*"' || true)
  [[ "$status" == *healthy* ]] && break
  sleep 2
done

echo "Проверка, что бот стартовал и восстановил jobs..."
sleep 5
docker compose logs bot | grep -q "Scheduler started (jobs recovered)" \
  && echo "OK: scheduler recovered" || (echo "FAIL: scheduler did not start" && exit 1)

docker compose exec -T db psql -U "${POSTGRES_USER:-wb_bot}" -d "${POSTGRES_DB:-wb_task_bot}" \
  -c "SELECT count(*) FROM system_settings;" | grep -qE '[1-9][0-9]*' \
  && echo "OK: settings seeded" || (echo "FAIL: settings not seeded" && exit 1)

echo "Docker Compose smoke test: PASSED"
