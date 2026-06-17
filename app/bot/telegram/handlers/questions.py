from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackAuthError, CallbackCodec
from app.core.container import AppContainer
from app.services.admin_tools_service import (
    ProhibitedGoodsStore,
    StaticContentStore,
    send_stored_media_to_telegram,
)


def build_questions_router(container: AppContainer) -> Router:
    router = Router()
    callback_codec = CallbackCodec(container.callback_signer)
    prohibited_store = ProhibitedGoodsStore(container.settings.database.dsn)
    delivery_store = StaticContentStore(
        database_dsn=container.settings.database.dsn,
        key="delivery_info",
        default_text="Раздел о доставке пока не заполнен.",
    )
    contacts_store = StaticContentStore(
        database_dsn=container.settings.database.dsn,
        key="contacts_info",
        default_text="Раздел контактов пока не заполнен.",
    )

    @router.message(F.text.in_({"Запрещенные товары", "🚫 Запрещенные товары"}))
    async def prohibited_goods(message: Message) -> None:
        text = await prohibited_store.get_text()
        media_items = await prohibited_store.get_media_items()
        await message.answer(text, parse_mode="HTML")
        for media in media_items:
            await send_stored_media_to_telegram(message.bot, message.chat.id, media)

    @router.message(F.text.in_({"Как работает доставка", "🚚 Как работает доставка"}))
    async def delivery_info(message: Message) -> None:
        await _send_static_content(message, delivery_store)

    @router.message(F.text.in_({"Наши контакты", "☎️ Наши контакты"}))
    async def contacts_info(message: Message) -> None:
        await _send_static_content(message, contacts_store)

    @router.message(F.text.in_({"Вопросы", "❓ Вопросы"}))
    async def faq_root(message: Message) -> None:
        if not message.from_user:
            return
        await _send_section(
            message=message,
            user_id=message.from_user.id,
            container=container,
            codec=callback_codec,
            section_id=None,
            edit=False,
        )

    @router.callback_query()
    async def faq_callbacks(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data or not callback.message:
            return
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            return
        if not action.startswith("faq:"):
            return

        raw_section = action.split(":", maxsplit=1)[1]
        if raw_section == "root":
            section_id = None
        else:
            try:
                section_id = int(raw_section)
            except ValueError:
                await callback.answer("Неверный раздел", show_alert=True)
                return

        await callback.answer()
        await _send_section(
            message=callback.message,
            user_id=callback.from_user.id,
            container=container,
            codec=callback_codec,
            section_id=section_id,
            edit=True,
        )

    return router


async def _send_section(
    message: Message,
    user_id: int,
    container: AppContainer,
    codec: CallbackCodec,
    section_id: int | None,
    edit: bool,
) -> None:
    current = await container.faq_service.get_section(section_id) if section_id is not None else None
    children = await container.faq_service.list_children(section_id)
    path_text = await container.faq_service.breadcrumbs(section_id)

    body_lines = [f"<b>{path_text}</b>"]
    if current and current.content_text:
        body_lines.append(current.content_text)
    if children:
        body_lines.append("")
        body_lines.append("Выберите раздел:")
    elif not current or not current.content_text:
        body_lines.append("Раздел пока пуст.")

    parent_id = current.parent_id if current else None
    keyboard = _faq_keyboard(
        user_id=user_id,
        codec=codec,
        section_id=section_id,
        parent_id=parent_id,
        children=children,
    )
    text = "\n".join(body_lines)

    if edit:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            raise
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


def _faq_keyboard(
    user_id: int,
    codec: CallbackCodec,
    section_id: int | None,
    parent_id: int | None,
    children,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for child in children:
        rows.append(
            [
                InlineKeyboardButton(
                    text=child.title,
                    callback_data=codec.encode(f"faq:{child.id}", user_id),
                )
            ]
        )
    if section_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=codec.encode(f"faq:{parent_id if parent_id is not None else 'root'}", user_id),
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🏠 К разделам",
                    callback_data=codec.encode("faq:root", user_id),
                )
            ]
        )
    if not rows:
        rows = [[InlineKeyboardButton(text="🏠 К разделам", callback_data=codec.encode("faq:root", user_id))]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_static_content(message: Message, store: StaticContentStore) -> None:
    text = await store.get_text()
    media_items = await store.get_media_items()
    await message.answer(text, parse_mode="HTML")
    for media in media_items:
        await send_stored_media_to_telegram(message.bot, message.chat.id, media)
