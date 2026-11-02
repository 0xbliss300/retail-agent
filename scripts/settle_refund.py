"""内部模拟退款结算入口；不作为 Agent 工具暴露。"""

import argparse
import json
from datetime import UTC, datetime

from app.business.service import settle_refund


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="模拟售后审核并完成退款")
    parser.add_argument("--refund-id", required=True, help="待结算的售后退款申请编号")
    args = parser.parse_args(argv)

    result = settle_refund(
        refund_id=args.refund_id,
        now=datetime.now(UTC),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()