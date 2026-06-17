from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, Message

from app.bot.telegram.callbacks import CallbackAuthError, CallbackCodec
from app.bot.telegram.keyboards.main_menu import my_orders_filters_keyboard, my_orders_pagination_keyboard
from app.bot.telegram.keyboards.profile import platforms_keyboard, profile_menu_keyboard
from app.bot.texts import messages as msg
from app.core.container import AppContainer
from app.domain.enums import DialogState, Platform
from app.domain.models import OutboundMessage
from app.services.flows.profile_flow import FlowResponse


PROFILE_BUTTONS = {"Профиль", "👤 Профиль", "Заполнить профиль"}
CONFIRM_BUTTONS = {"Да", "Имя", "Тел.", "Город"}
SYNC_BUTTONS = {"Есть профиль ВК"}
PROFILE_CALLBACK_ACTIONS = {
    "code_confirm",
    "code_fix",
    "has_code_yes",
    "has_code_no",
    "passport_yes",
    "passport_no",
    "edit_name",
    "edit_phone",
    "edit_city",
    "confirm_yes",
    "profile:start_fill",
    "profile:start_sync",
    "profile:buyout_start",
    "profile:buyout_orders",
    "profile:buyout_filters",
}


def build_profile_router(container: AppContainer) -> Router:
    router = Router()
    platform = Platform.TELEGRAM
    callback_codec = CallbackCodec(container.callback_signer)

    async def _apply_response(message: Message, response: FlowResponse) -> None:
        await _dispatch_outbound(message, response)
        kwargs = {"parse_mode": "HTML"}
        if response.reply_markup is not None:
            kwargs["reply_markup"] = response.reply_markup
        await message.answer(response.text, **kwargs)

    async def _dispatch_outbound(message: Message, response: FlowResponse) -> None:
        for outgoing in response.outbound_messages:
            platform_name = str(outgoing["platform"])
            target_platform = Platform(platform_name)
            target_user_id = int(outgoing["platform_user_id"])
            payload = dict(outgoing["payload"])
            if target_platform == Platform.TELEGRAM:
                try:
                    await message.bot.send_message(
                        chat_id=target_user_id,
                        text=msg.sync_code_for_other_platform(
                            code=str(payload.get("code", "")),
                            profile_code=str(payload.get("profile_code", "")),
                            from_platform=str(payload.get("from_platform", "")),
                        ),
                        parse_mode="HTML",
                    )
                except TelegramForbiddenError:
                    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, target_user_id)
                    if profile and not profile.blocked_bot:
                        profile.blocked_bot = True
                        await container.profile_repo.save(profile)
                continue

            await container.outbound_repo.enqueue(
                OutboundMessage(
                    id=0,
                    platform=target_platform,
                    platform_user_id=target_user_id,
                    message_type=str(outgoing["message_type"]),
                    payload=payload,
                )
            )

    @router.message(F.text.in_({"Профиль", "👤 Профиль"}))
    async def profile_menu(message: Message) -> None:
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        response = await container.profile_flow.show_profile_menu(session, other_platform_label="ВК")
        response.reply_markup = profile_menu_keyboard("ВК", message.from_user.id, callback_codec)
        await _apply_response(message, response)

    @router.message(F.text == "Заполнить профиль")
    async def start_fill(message: Message) -> None:
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        response = await container.profile_flow.start_fill(session)
        await _apply_response(message, response)

    @router.message(F.text.in_(CONFIRM_BUTTONS))
    async def confirm_buttons(message: Message) -> None:
        if not message.from_user or not message.text:
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        if session.state != DialogState.PROFILE_CONFIRM:
            return

        action_map = {
            "Да": "confirm_yes",
            "Имя": "edit_name",
            "Тел.": "edit_phone",
            "Город": "edit_city",
        }
        response = await container.profile_flow.handle_callback(
            session,
            action_map[message.text],
            callback_codec,
        )
        await _apply_response(message, response)

    @router.message(F.text == "Есть профиль ВК")
    async def start_sync(message: Message) -> None:
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        response = await container.profile_flow.start_sync_with_other_platform(session)
        await _apply_response(message, response)

    @router.callback_query()
    async def profile_callbacks(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data or not callback.message:
            return
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            return
        if action not in PROFILE_CALLBACK_ACTIONS:
            return
        session = await container.profile_flow.get_or_create_session(platform, callback.from_user.id)
        if action == "profile:start_fill":
            response = await container.profile_flow.start_fill(session)
            await callback.answer()
            await _apply_response(callback.message, response)
            return
        if action == "profile:start_sync":
            response = await container.profile_flow.start_sync_with_other_platform(session)
            await callback.answer()
            await _apply_response(callback.message, response)
            return
        if action == "profile:buyout_start":
            response = await container.buyout_flow.start(session)
            await callback.answer()
            await _apply_response(callback.message, response)
            return
        if action == "profile:buyout_orders":
            response = await container.buyout_flow.render_orders(session, page=1)
            if response.state_data:
                response.reply_markup = my_orders_pagination_keyboard(
                    user_id=callback.from_user.id,
                    current_page=int(response.state_data.get("page", 1)),
                    total_pages=int(response.state_data.get("total_pages", 1)),
                    codec=callback_codec,
                )
            await callback.answer()
            await _apply_response(callback.message, response)
            return
        if action == "profile:buyout_filters":
            filters = container.buyout_flow.filter_states(session)
            await callback.answer()
            await callback.message.answer(
                container.buyout_flow.filters_hint_text(session),
                parse_mode="HTML",
                reply_markup=my_orders_filters_keyboard(
                    user_id=callback.from_user.id,
                    filters=filters,
                    codec=callback_codec,
                ),
            )
            return
        response = await container.profile_flow.handle_callback(session, action, callback_codec)
        if action in {"passport_yes", "passport_no"}:
            response.reply_markup = platforms_keyboard(callback.from_user.id, callback_codec)
        await callback.answer()
        await _apply_response(callback.message, response)

    @router.message()
    async def profile_text_flow(message: Message) -> None:
        if not message.from_user or not message.text:
            return
        if message.text in PROFILE_BUTTONS or message.text in CONFIRM_BUTTONS or message.text in SYNC_BUTTONS:
            return
        if message.text.startswith("/"):
            return

        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        if session.state == DialogState.IDLE:
            return

        user_key = f"tg:{message.from_user.id}"
        if not container.rate_limiter.allow_request(user_key, message.text):
            return
        if not container.rate_limiter.validate_user_payload_size(len(message.text)):
            return

        response = await container.profile_flow.handle_text(session, message.text, callback_codec=callback_codec)
        await _apply_response(message, response)

    return router
