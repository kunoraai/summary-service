from __future__ import annotations

from summary_service.security import ApiKeyVerifier, issue_task_token, sha256_hex


def test_api_key_matches_hash_and_returns_client_identity() -> None:
    verifier = ApiKeyVerifier({"client-a": sha256_hex("secret")})

    assert verifier.authenticate("secret") == "client-a"
    assert verifier.authenticate("wrong") is None


def test_task_token_is_random_and_only_hash_is_persisted() -> None:
    first_token, first_digest = issue_task_token()
    second_token, _ = issue_task_token()

    assert len(first_token) >= 43
    assert first_digest == sha256_hex(first_token)
    assert first_token != first_digest
    assert first_token != second_token


def test_idempotent_token_is_stable_and_client_scoped() -> None:
    verifier = ApiKeyVerifier({"client-a": sha256_hex("secret")})

    first, first_digest = verifier.issue_idempotent_token("client-a", "request-1", "x" * 32)
    repeated, repeated_digest = verifier.issue_idempotent_token("client-a", "request-1", "x" * 32)
    other_client, _ = verifier.issue_idempotent_token("client-b", "request-1", "x" * 32)

    assert (first, first_digest) == (repeated, repeated_digest)
    assert first != other_client
