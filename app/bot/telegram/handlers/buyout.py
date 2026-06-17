from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackAuthError, CallbackCodec
from app.bot.telegram.keyboards.profile import my_orders_filters_keyboard, my_orders_pagination_keyboard
from app.core.container import AppContainer
from app.domain.enums import DialogState, OrderStatus, Platform
from app.domain.models import OutboundMessage
from app.services.admin_tools_service import GroupTopicsStore, NotificationSettingsStore, PaymentReviewTargetStore
from app.services.flows.buyout_flow import BuyoutFlowResponse


def build_buyout_router(container: AppContainer) -> Router:
    router = Router()
    platform = Platform.TELEGRAM
    callback_codec = CallbackCodec(container.callback_signer)
    payment_target_store = PaymentReviewTargetStore(container.settings.database.dsn)
    notification_settings_store = NotificationSettingsStore(container.settings.database.dsn)
    group_topics_store = GroupTopicsStore(container.settings.database.dsn)

    async def _reply(message: Message, response: BuyoutFlowResponse) -> None:
        kwargs = {"parse_mode": "HTML"}
        if response.reply_markup is not None:
            kwargs["reply_markup"] = response.reply_markup
        await message.answer(response.text, **kwargs)

    @router.message(F.text.in_({"Заказ выкупа", "🛍 Заказ выкупа"}))
    async def start_buyout(message: Message) -> None:
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        response = await container.buyout_flow.start(session)
        await _reply(message, response)

    @router.message(F.text.in_({"Мои заказы", "📦 Мои заказы"}))
    async def show_my_orders(message: Message) -> None:
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        response = await container.buyout_flow.render_orders(session, page=1)
        if response.state_data:
            response.reply_markup = my_orders_pagination_keyboard(
                user_id=message.from_user.id,
                current_page=int(response.state_data.get("page", 1)),
                total_pages=int(response.state_data.get("total_pages", 1)),
                codec=callback_codec,
            )
        await _reply(message, response)

    @router.message(F.text.in_({"Фильтры заказов", "🎛 Фильтры заказов"}))
    async def show_filters(message: Message) -> None:
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        filters = container.buyout_flow.filter_states(session)
        await message.answer(
            container.buyout_flow.filters_hint_text(session),
            parse_mode="HTML",
            reply_markup=my_orders_filters_keyboard(
                user_id=message.from_user.id,
                filters=filters,
                codec=callback_codec,
            ),
        )

    @router.callback_query()
    async def my_orders_pagination(callback: CallbackQuery) -> None:
        if not callback.data or not callback.from_user or not callback.message:
            return
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            return
        if action.startswith("payreview:"):
            if not await container.admin_service.is_admin(callback.from_user.id):
                await callback.answer("Только для админов", show_alert=True)
                return
            parts = action.split(":", maxsplit=2)
            if len(parts) != 3:
                await callback.answer("Некорректная команда", show_alert=True)
                return
            review_action = parts[1]
            order_number = parts[2]
            order = await container.order_admin_service.get_order(order_number)
            if not order:
                await callback.answer("Заказ не найден", show_alert=True)
                return
            if review_action == "approve":
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.PAID,
                    changed_by_user_id=callback.from_user.id,
                    note="payment approved by admin",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                await _notify_user_status_changed(
                    callback,
                    container,
                    order=updated,
                    status=OrderStatus.PAID,
                    note="Оплата подтверждена администратором.",
                )
                await callback.answer("Оплата подтверждена")
                await callback.message.edit_text(
                    callback.message.text + "\n\n✅ Подтверждено",
                    parse_mode="HTML",
                    reply_markup=None,
                )
                await _notify_payment_group_event(
                    callback,
                    container,
                    payment_target_store=payment_target_store,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text=f"✅ Подтверждено админом {callback.from_user.id} ({_omsk_now_text()})",
                )
                return
            if review_action == "reject":
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.CANCELLED,
                    changed_by_user_id=callback.from_user.id,
                    note="payment rejected by admin",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                await _notify_user_status_changed(
                    callback,
                    container,
                    order=updated,
                    status=OrderStatus.CANCELLED,
                    note="Оплата отклонена администратором.",
                )
                await callback.answer("Оплата отклонена")
                await callback.message.edit_text(
                    callback.message.text + "\n\n❌ Отклонено",
                    parse_mode="HTML",
                    reply_markup=None,
                )
                await _notify_payment_group_event(
                    callback,
                    container,
                    payment_target_store=payment_target_store,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text=f"❌ Отклонено админом {callback.from_user.id} ({_omsk_now_text()})",
                )
                return
            await callback.answer("Неизвестное действие", show_alert=True)
            return

        if action.startswith("orderpay:"):
            parts = action.split(":", maxsplit=2)
            if len(parts) != 3:
                await callback.answer("Некорректная команда", show_alert=True)
                return
            pay_action = parts[1]
            order_number = parts[2]
            profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, callback.from_user.id)
            if not profile:
                await callback.answer("Профиль не найден", show_alert=True)
                return
            order = await container.order_admin_service.get_order(order_number)
            if not order or order.user_profile_id != profile.id:
                await callback.answer("Заказ не найден", show_alert=True)
                return
            if pay_action == "paid":
                new_status = OrderStatus.PAID_CHECK
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=new_status,
                    changed_by_user_id=callback.from_user.id,
                    note="user marked paid",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                await callback.answer("Платеж отправлен на проверку")
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.answer(
                    f"Заявка <b>{order_number}</b> помечена как «Проверка оплаты».",
                    parse_mode="HTML",
                )
                await _notify_admin_payment_event(
                    callback,
                    container,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text="Клиент нажал «Оплачено»",
                )
                await _notify_payment_group_event(
                    callback,
                    container,
                    payment_target_store=payment_target_store,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text=f"Клиент {callback.from_user.id} нажал «Оплачено» ({_omsk_now_text()})",
                )
                await _send_payment_review_to_admins(
                    callback,
                    container,
                    codec=callback_codec,
                    payment_target_store=payment_target_store,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                )
                return
            if pay_action == "cancel":
                new_status = OrderStatus.CANCELLED
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=new_status,
                    changed_by_user_id=callback.from_user.id,
                    note="user cancelled payment",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                await callback.answer("Заказ отменен")
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.answer(
                    f"Заявка <b>{order_number}</b> отменена.",
                    parse_mode="HTML",
                )
                await _notify_admin_payment_event(
                    callback,
                    container,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text="Клиент нажал «Отмена оплаты»",
                )
                await _notify_payment_group_event(
                    callback,
                    container,
                    payment_target_store=payment_target_store,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text=f"Клиент {callback.from_user.id} нажал «Отмена оплаты» ({_omsk_now_text()})",
                )
                return
            await callback.answer("Неизвестное действие", show_alert=True)
            return
        if action.startswith("orders_filter:"):
            session = await container.profile_flow.get_or_create_session(platform, callback.from_user.id)
            raw = action.split(":", maxsplit=1)[1]
            if raw == "reset":
                await container.buyout_flow.reset_status_filters(session)
            else:
                try:
                    status = OrderStatus(raw)
                except ValueError:
                    await callback.answer("Неизвестный фильтр", show_alert=True)
                    return
                await container.buyout_flow.toggle_status_filter(session, status)
            filters = container.buyout_flow.filter_states(session)
            await callback.answer("Фильтр обновлен")
            await callback.message.edit_text(
                container.buyout_flow.filters_hint_text(session),
                parse_mode="HTML",
                reply_markup=my_orders_filters_keyboard(
                    user_id=callback.from_user.id,
                    filters=filters,
                    codec=callback_codec,
                ),
            )
            return

        if not action.startswith("my_orders:"):
            return
        try:
            page = int(action.split(":", maxsplit=1)[1])
        except ValueError:
            await callback.answer()
            return
        session = await container.profile_flow.get_or_create_session(platform, callback.from_user.id)
        response = await container.buyout_flow.render_orders(session, page=page)
        if response.state_data:
            response.reply_markup = my_orders_pagination_keyboard(
                user_id=callback.from_user.id,
                current_page=int(response.state_data.get("page", 1)),
                total_pages=int(response.state_data.get("total_pages", 1)),
                codec=callback_codec,
            )
        await callback.answer()
        await callback.message.edit_text(response.text, parse_mode="HTML", reply_markup=response.reply_markup)

    @router.message(F.photo | F.video | F.animation | F.document)
    async def handle_buyout_media(message: Message) -> None:
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        if session.state != DialogState.BUYOUT_WAIT_MEDIA:
            return
        media_type = ""
        file_id = ""
        if message.photo:
            media_type = "photo"
            file_id = message.photo[-1].file_id
        elif message.video:
            media_type = "video"
            file_id = message.video.file_id
        elif message.animation:
            media_type = "animation"
            file_id = message.animation.file_id
        elif message.document:
            media_type = "document"
            file_id = message.document.file_id
        archive_chat_id, archive_topic_id, archive_message_id = await _archive_buyout_media_in_group(
            message=message,
            group_topics_store=group_topics_store,
        )
        media_group_id = message.media_group_id
        response = await container.buyout_flow.handle_media(
            session,
            media_group_id,
            storage_chat_id=archive_chat_id,
            storage_topic_id=archive_topic_id,
            storage_message_id=archive_message_id,
            media_type=media_type or "unknown",
            tg_file_id=file_id or None,
        )
        await _reply(message, response)

    @router.message()
    async def buyout_text_flow(message: Message) -> None:
        if not message.from_user or not message.text:
            return
        if message.text.startswith("/"):
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        if session.state not in {
            DialogState.BUYOUT_WAIT_LINK,
            DialogState.BUYOUT_WAIT_DETAILS,
            DialogState.BUYOUT_ADD_MORE,
        }:
            return
        user_key = f"tg:{message.from_user.id}"
        if not container.rate_limiter.allow_request(user_key, message.text):
            return
        response = await container.buyout_flow.handle_text(session, message.text)
        await _reply(message, response)

    return router


async def _notify_admin_payment_event(
    callback: CallbackQuery,
    container: AppContainer,
    notification_settings_store: NotificationSettingsStore,
    order_number: str,
    event_text: str,
) -> None:
    if not callback.from_user:
        return
    try:
        silent = await notification_settings_store.should_disable_notification("button")
        await callback.bot.send_message(
            chat_id=container.settings.telegram.main_admin_id,
            text=(
                "Событие оплаты:\n"
                f"Заказ: <b>{order_number}</b>\n"
                f"Пользователь TG ID: <code>{callback.from_user.id}</code>\n"
                f"Действие: {event_text}"
            ),
            parse_mode="HTML",
            disable_notification=silent,
        )
    except Exception:
        return


async def _send_payment_review_to_admins(
    callback: CallbackQuery,
    container: AppContainer,
    codec: CallbackCodec,
    payment_target_store: PaymentReviewTargetStore,
    group_topics_store: GroupTopicsStore,
    notification_settings_store: NotificationSettingsStore,
    order_number: str,
) -> None:
    if not callback.from_user:
        return
    admin_ids = await container.admin_service.list_admins()
    text = (
        "Проверка оплаты:\n"
        f"Заказ: <b>{order_number}</b>\n"
        f"Клиент TG ID: <code>{callback.from_user.id}</code>\n"
        "Выберите действие:"
    )
    for admin_id in admin_ids:
        if not admin_id:
            continue
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить",
                        callback_data=codec.encode(f"payreview:approve:{order_number}", admin_id),
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=codec.encode(f"payreview:reject:{order_number}", admin_id),
                    ),
                ]
            ]
        )
        try:
            silent = await notification_settings_store.should_disable_notification("button")
            await callback.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_notification=silent,
            )
        except Exception:
            continue
    target_chat_id, target_topic_id = await payment_target_store.get_target()
    if not target_chat_id:
        target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("payment")
    if target_chat_id:
        try:
            silent = await notification_settings_store.should_disable_notification("button")
            await callback.bot.send_message(
                chat_id=target_chat_id,
                text=text + "\n\nРешение по кнопкам доступно в личных сообщениях админов.",
                parse_mode="HTML",
                disable_notification=silent,
                message_thread_id=target_topic_id,
            )
        except Exception:
            pass


async def _notify_payment_group_event(
    callback: CallbackQuery,
    container: AppContainer,
    payment_target_store: PaymentReviewTargetStore,
    group_topics_store: GroupTopicsStore,
    notification_settings_store: NotificationSettingsStore,
    order_number: str,
    event_text: str,
) -> None:
    target_chat_id, target_topic_id = await payment_target_store.get_target()
    if not target_chat_id:
        target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("payment")
    if not target_chat_id:
        return
    try:
        silent = await notification_settings_store.should_disable_notification("button")
        await callback.bot.send_message(
            chat_id=target_chat_id,
            text=(
                "Событие оплаты:\n"
                f"Заказ: <b>{order_number}</b>\n"
                f"{event_text}"
            ),
            parse_mode="HTML",
            disable_notification=silent,
            message_thread_id=target_topic_id,
        )
    except Exception:
        return


async def _notify_user_status_changed(
    callback: CallbackQuery,
    container: AppContainer,
    order,
    status: OrderStatus,
    note: str,
) -> None:
    profile = await container.profile_repo.get_by_id(order.user_profile_id)
    if not profile:
        return
    text = (
        f"Обновление по заказу <b>№{order.order_number}</b>.\n"
        f"Новый статус: <b>{_status_title(status)}</b>\n"
        f"{note}"
    )
    if profile.telegram_user_id:
        try:
            await callback.bot.send_message(
                chat_id=profile.telegram_user_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as exc:
            await _mark_blocked_bot_if_needed(container, profile, exc)
    if profile.vk_user_id:
        await container.outbound_repo.enqueue(
            OutboundMessage(
                id=0,
                platform=Platform.VK,
                platform_user_id=int(profile.vk_user_id),
                message_type="plain_text",
                payload={"text": text},
            )
        )


def _status_title(status: OrderStatus) -> str:
    names = {
        OrderStatus.PENDING: "Ожидание",
        OrderStatus.PRICE_READY: "Цена готова",
        OrderStatus.WAITING_PAYMENT: "Ожидает оплату",
        OrderStatus.PAID_CHECK: "Проверка оплаты",
        OrderStatus.PAID: "Оплачен",
        OrderStatus.IN_TRANSIT: "В пути",
        OrderStatus.PICKUP_POINT: "В пункте выдачи",
        OrderStatus.ISSUED: "Выдан",
        OrderStatus.CANCELLED: "Отменен",
    }
    return names.get(status, status.value)


def _omsk_now_text() -> str:
    return datetime.now(ZoneInfo("Asia/Omsk")).strftime("%d.%m.%Y %H:%M")


async def _mark_blocked_bot_if_needed(container: AppContainer, profile, error: Exception) -> None:
    if isinstance(error, TelegramForbiddenError):
        if not profile.blocked_bot:
            profile.blocked_bot = True
            await container.profile_repo.save(profile)


async def _archive_buyout_media_in_group(
    message: Message,
    group_topics_store: GroupTopicsStore,
) -> tuple[int | None, int | None, int | None]:
    target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("logs")
    if not target_chat_id:
        return None, None, None
    try:
        copied = await message.bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=target_topic_id,
        )
    except Exception:
        return None, None, None
    return int(target_chat_id), int(target_topic_id) if target_topic_id else None, int(copied.message_id)
