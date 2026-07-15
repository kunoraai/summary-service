from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue_task_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, sha256_hex(token)


class ApiKeyVerifier:
    def __init__(self, key_hashes: dict[str, str]) -> None:
        self._key_hashes = key_hashes

    def authenticate(self, candidate: str) -> str | None:
        candidate_hash = sha256_hex(candidate)
        matched_client: str | None = None
        for client_id, expected_hash in self._key_hashes.items():
            if hmac.compare_digest(candidate_hash, expected_hash):
                matched_client = client_id
        return matched_client

    def issue_idempotent_token(
        self,
        client_id: str,
        idempotency_key: str,
        secret: str,
    ) -> tuple[str, str]:
        digest = hmac.new(
            secret.encode(),
            f"{client_id}\0{idempotency_key}".encode(),
            hashlib.sha256,
        ).digest()
        token = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return token, sha256_hex(token)
