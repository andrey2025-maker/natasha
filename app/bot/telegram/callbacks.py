from __future__ import annotations

from dataclasses import dataclass

from app.security.callback_signer import CallbackSigner, InvalidCallbackSignatureError


class CallbackAuthError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class CallbackCodec:
    signer: CallbackSigner

    def encode(self, action: str, user_id: int) -> str:
        return self.signer.sign({"a": action, "u": user_id})

    def decode(self, raw_data: str, user_id: int) -> str:
        try:
            payload = self.signer.verify(raw_data)
        except InvalidCallbackSignatureError as exc:
            raise CallbackAuthError("Invalid callback signature") from exc

        if payload.get("u") != user_id:
            raise CallbackAuthError("Callback is not for this user")
        action = payload.get("a")
        if not isinstance(action, str) or not action:
            raise CallbackAuthError("Invalid callback action")
        return action
