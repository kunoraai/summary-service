#!/usr/bin/env bash
set -euo pipefail

image="summary-service:test"
volume="summary-service-test-${RANDOM}"
container="summary-service-test-${RANDOM}"

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build -t "$image" .
test "$(docker run --rm --entrypoint id "$image" -u)" != "0"
docker volume create "$volume" >/dev/null

env_args=(
  -e SUMMARY_DASHSCOPE_API_KEY=test-key
  -e SUMMARY_API_KEYS=container-client:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  -e SUMMARY_IDEMPOTENCY_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
)

docker run --rm "${env_args[@]}" -v "$volume:/data" "$image" migrate
docker run --rm --entrypoint test -v "$volume:/data" "$image" -f /data/summary.db

docker run -d --name "$container" -p 18080:8080 "${env_args[@]}" -v "$volume:/data" "$image" api >/dev/null
for _ in {1..30}; do
  if curl --fail --silent http://127.0.0.1:18080/healthz >/dev/null; then
    exit 0
  fi
  sleep 1
done

docker logs "$container"
exit 1
