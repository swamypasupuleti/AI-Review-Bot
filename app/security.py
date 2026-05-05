import hashlib
import hmac


def verify_github_signature(secret: str, payload: bytes, signature_header: str | None) -> bool:

    if not secret:
        # Fail closed: a missing secret means the deployment is misconfigured.
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    sent_digest = signature_header.split("=", 1)[1].strip()
    expected_digest = hmac.new(
        secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(sent_digest, expected_digest)
