from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any


class InvalidCallbackSignatureError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class CallbackSigner:
    secret: str

    def sign(self, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(self.secret.encode("utf-8"), serialized, hashlib.sha256).hexdigest()
        body = json.dumps({"data": payload, "sig": signature}, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(body).decode("ascii")

    def verify(self, signed_payload: str) -> dict[str, Any]:
        try:
            decoded = base64.urlsafe_b64decode(signed_payload.encode("ascii"))
            envelope = json.loads(decoded.decode("utf-8"))
            payload = envelope["data"]
            signature = envelope["sig"]
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidCallbackSignatureError("Malformed callback payload") from exc

        expected = hmac.new(
            self.secret.encode("utf-8"),
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidCallbackSignatureError("Invalid callback signature")

        return payload
