from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.domain.enums import OrderStatus

from app.bot.telegram.callbacks import CallbackCodec


def main_menu_keyboard(include_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Профиль"), KeyboardButton(text="Как работает доставка")],
        [KeyboardButton(text="Запрещенные товары"), KeyboardButton(text="Вопросы")],
        [KeyboardButton(text="Наши контакты")],
    ]
    if include_admin:
        rows.append([KeyboardButton(text="Админ")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def profile_menu_keyboard(other_platform_label: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Заполнить профиль"), KeyboardButton(text="Вопросы")],
            [KeyboardButton(text=f"Есть профиль {other_platform_label}")],
        ],
        resize_keyboard=True,
    )


def profile_confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Да")],
            [KeyboardButton(text="Имя"), KeyboardButton(text="Тел."), KeyboardButton(text="Город")],
        ],
        resize_keyboard=True,
    )


def yes_no_keyboard(yes_action: str, no_action: str, user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=codec.encode(yes_action, user_id)),
                InlineKeyboardButton(text="Нет", callback_data=codec.encode(no_action, user_id)),
            ]
        ]
    )


def platforms_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Заказ выкупа"), KeyboardButton(text="Мои заказы")],
            [KeyboardButton(text="Фильтры заказов")],
            [KeyboardButton(text="Вопросы")],
        ],
        resize_keyboard=True,
    )


def my_orders_pagination_keyboard(
    user_id: int,
    current_page: int,
    total_pages: int,
    codec: CallbackCodec,
) -> InlineKeyboardMarkup | None:
    if total_pages <= 1:
        return None
    buttons: list[InlineKeyboardButton] = []
    if current_page > 1:
        buttons.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=codec.encode(f"my_orders:{current_page - 1}", user_id),
            )
        )
    if current_page < total_pages:
        buttons.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=codec.encode(f"my_orders:{current_page + 1}", user_id),
            )
        )
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def my_orders_filters_keyboard(
    user_id: int,
    filters: dict[OrderStatus, bool],
    codec: CallbackCodec,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for status in OrderStatus:
        is_enabled = filters.get(status, True)
        emoji = "🟢" if is_enabled else "🔴"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{emoji} {_status_short_title(status)}",
                    callback_data=codec.encode(f"orders_filter:{status.value}", user_id),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Сбросить фильтры",
                callback_data=codec.encode("orders_filter:reset", user_id),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _status_short_title(status: OrderStatus) -> str:
    titles = {
        OrderStatus.PENDING: "Ожидание",
        OrderStatus.PRICE_READY: "Цена",
        OrderStatus.WAITING_PAYMENT: "Оплата",
        OrderStatus.PAID_CHECK: "Проверка",
        OrderStatus.PAID: "Оплачен",
        OrderStatus.IN_TRANSIT: "В пути",
        OrderStatus.PICKUP_POINT: "ПВЗ",
        OrderStatus.ISSUED: "Выдан",
        OrderStatus.CANCELLED: "Отменен",
    }
    return titles.get(status, status.value)


def admin_menu_keyboard(is_main_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Профили"), KeyboardButton(text="Блокировки")],
        [KeyboardButton(text="Заказы"), KeyboardButton(text="Статистика")],
        [KeyboardButton(text="Вопросы"), KeyboardButton(text="Запрещенка")],
        [KeyboardButton(text="Контент"), KeyboardButton(text="Рассылка")],
        [KeyboardButton(text="Утилиты")],
        [KeyboardButton(text="Список админов")],
    ]
    if is_main_admin:
        rows.append([KeyboardButton(text="Добавить админа"), KeyboardButton(text="Удалить админа")])
    rows.append([KeyboardButton(text="Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
