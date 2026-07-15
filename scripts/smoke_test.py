from __future__ import annotations

import argparse
import os
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-env", default="SUMMARY_SERVICE_API_KEY")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--local-address")
    args = parser.parse_args()
    api_key = os.environ[args.api_key_env]
    headers = {"X-API-Key": api_key}

    transport = (
        httpx.HTTPTransport(local_address=args.local_address) if args.local_address else None
    )
    with httpx.Client(base_url=args.base_url, timeout=30, transport=transport) as client:
        assert client.post("/v1/summaries", json={"text": "test"}).status_code == 401
        oversized = client.post("/v1/summaries", json={"text": "界" * 87_382}, headers=headers)
        assert oversized.status_code == 413, oversized.text
        created = client.post(
            "/v1/summaries",
            json={"text": "微服务通过标准接口协作，异步任务可以削峰并提高系统稳定性。"},
            headers={**headers, "Idempotency-Key": f"smoke-{int(time.time())}"},
        )
        assert created.status_code == 202, created.text
        token = created.json()["token"]
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            result = client.get(f"/v1/summaries/{token}", headers=headers)
            assert result.status_code == 200, result.text
            payload = result.json()
            if payload["status"] == "succeeded":
                assert 0 < len(payload["summary"]) <= 400
                print(payload)
                return
            if payload["status"] == "failed":
                raise RuntimeError(payload)
            time.sleep(2)
        raise TimeoutError("summary task did not finish")


if __name__ == "__main__":
    main()
