"""从当前候选页解析用户明确给出的取消目标。"""

import re
from decimal import Decimal

CANCEL_SELECTION = "cancel_selection"
_ORDINALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "两": 2}
_SELECT_PREFIX = re.compile(r"^(?:那就|就)?\s*(?:请选择|我选|选定|选|我要取消|帮我取消|取消)?\s*")
_ORDINAL = re.compile(r"第\s*(\d{1,2}|[一二三四五两])\s*(?:笔|个)(?:订单)?")
_PRICE = re.compile(r"(?:[¥￥]\s*\d|\d\s*(?:元|块))")
_ORDER_NUMBER = re.compile(r"ORD-[A-Z0-9-]+", re.IGNORECASE)
_CANCEL_ELIGIBILITY = re.compile(r"(?:可不可以|是否可以|能不能|能否|能|可以)取消")
_PENDING_REFERENCE = re.compile(r"(?:(?:正在)?等(?:待)?(?:取消)?确认|待确认)(?:的?(?:那笔|这笔|订单))?")
_QUERIED_REFERENCE = re.compile(r"(?:刚查(?:过|了)?(?:物流)?|最近查(?:过)?)(?:的?(?:那笔|这笔|订单))?")
_REFERENCE_PREFIX = re.compile(r"^(?:我指的是|我说的是|我指|我选|选|是)\s*")
_YUAN = r"\d{1,12}(?:\.\d{1,2})?"
_CANCEL_BY_AMOUNT = re.compile(
    r"(?:请|麻烦)?\s*(?:帮我|我要|我想)?\s*取消\s*(?:那笔|这笔|一笔)?\s*"
    rf"(?:[¥￥]\s*({_YUAN})|({_YUAN})\s*(?:元|块))\s*的?\s*订单"
)


def cancel_amount_cents(message: str) -> int | None:
    """明确符合简短取消句式时，直接按实付金额查找。其他表达由模型解读。"""
    matched = _CANCEL_BY_AMOUNT.fullmatch(message.strip().rstrip("。！!.").strip())
    if matched is None:
        return None
    return int(Decimal(matched[1] or matched[2]) * 100)


def requires_cancel_lookup(message: str) -> bool:
    """有金额而无订单号的取消请求必须经过金额查找。"""
    return "取消" in message and bool(_PRICE.search(message)) and not _ORDER_NUMBER.search(message)


def selected_order(message: str, orders: list[dict]) -> str | None:
    choice = _SELECT_PREFIX.sub("", message.strip().rstrip("。！!.").strip(), count=1)
    for order in orders:
        if choice.casefold() == order["order_id"].casefold():
            return order["order_id"]

    ordinal = _ORDINAL.fullmatch(choice)
    if ordinal is None:
        return None
    number = ordinal[1]
    index = _ORDINALS[number] if number in _ORDINALS else int(number)
    return orders[index - 1]["order_id"] if 1 <= index <= len(orders) else None


def asks_cancel_eligibility(message: str) -> bool:
    return _CANCEL_ELIGIBILITY.search(message) is not None


def order_reference(message: str, *, choice_only: bool = False) -> str | None:
    """从明确的短答或资格问题提取订单号，或识别两个候选的角色。"""
    text = message.strip().rstrip("。！？!?.").strip()
    if choice_only:
        text = _REFERENCE_PREFIX.sub("", text, count=1)
        if text.casefold() in {"a", "第一笔"}:
            return "pending"
        if text.casefold() in {"b", "第二笔"}:
            return "queried"
        if _ORDER_NUMBER.fullmatch(text):
            return text.upper()
        pending = _PENDING_REFERENCE.fullmatch(text)
        queried = _QUERIED_REFERENCE.fullmatch(text)
    else:
        ids = {order_id.upper() for order_id in _ORDER_NUMBER.findall(text)}
        if ids:
            return next(iter(ids)) if len(ids) == 1 else None
        pending = _PENDING_REFERENCE.search(text)
        queried = _QUERIED_REFERENCE.search(text)
    if pending and not queried:
        return "pending"
    if queried and not pending:
        return "queried"
    return None
