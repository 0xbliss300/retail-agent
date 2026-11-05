"""本轮请求是否属于售后助手的服务范围。

这里只做**高精度**拦截：命中明显的域外请求特征、且消息里没有任何售后领域线索时才拒答。
判定拿不准一律放行，交给 BASE_PROMPT 里的边界说明和模型处理。

理由：误拒一条真实售后请求的代价，远高于多发一次模型调用。
带领域词包裹的越界请求（例如“用 Python 写个查订单的脚本”）不会在这里被拦住，
那属于提示词边界与工具白名单的职责，不是关键词闸能覆盖的。
"""

import re

# 出现任一售后线索即视为域内，直接放行。
DOMAIN_HINTS = (
    "订单", "退款", "退货", "换货", "售后", "物流", "快递", "运单", "轨迹",
    "取消", "发票", "收货", "发货", "签收", "配送", "承运", "到账",
    "申请", "赔付", "补偿", "工单", "客服",
)

# 订单号形态（ORD-1001、REF-20240101 之类）本身也是域内线索。
_ORDER_ID = re.compile(r"[A-Z]{2,}-\d{3,}", re.IGNORECASE)

# 每条规则给出的 reason 会写进 trace，便于统计域外请求占比。
_OFF_TOPIC_RULES = (
    (
        "code",
        re.compile(
            r"写代码|写段代码|写一个代码|写个代码|写程序|写个程序|写脚本|写个脚本"
            r"|实现(一个|个)?(算法|函数|程序|排序|脚本)"
            r"|(用|以)\s*(python|java|javascript|c\+\+|go|sql|shell|bash)"
            r"|代码(示例|实现)|函数实现|算法实现"
            r"|时间复杂度|空间复杂度|排序算法|冒泡排序|快速排序|二分查找"
            r"|递归|斐波那契|leetcode|力扣"
            r"|print\(|def\s+\w+\(|```",
            re.IGNORECASE,
        ),
    ),
    ("translation", re.compile(r"翻译|译成|translate", re.IGNORECASE)),
    ("homework", re.compile(r"解方程|求导|证明题|数学题|计算题|作业|考试题|试卷")),
    (
        "writing",
        re.compile(
            r"写一篇|写首|写一首|写个故事|写故事|写文案|文案|诗歌|小说|作文"
            r"|论文|简历|演讲稿|ppt|周报|写一封|邮件"
        ),
    ),
    (
        "chitchat",
        re.compile(r"讲个笑话|说个笑话|笑话|天气|星座|讲个故事|你是谁|你叫什么"),
    ),
)

SCOPE_REFUSAL = (
    "我只处理订单、物流、退款和取消这类售后问题，"
    "写代码、翻译、作业、写作和闲聊都不在服务范围内。\n"
    "你可以直接问：我有哪些订单、某个订单的物流到哪了、某个订单能不能退款。"
)


def off_topic_reason(message: str) -> str | None:
    """返回域外请求的类别；属于售后范围或无法判定时返回 None。"""
    if not isinstance(message, str) or not message.strip():
        return None

    text = message.strip()
    if any(hint in text for hint in DOMAIN_HINTS) or _ORDER_ID.search(text):
        return None

    for reason, pattern in _OFF_TOPIC_RULES:
        if pattern.search(text):
            return reason
    return None
