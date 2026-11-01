"""初始化数据库结构，并加载本地模拟数据。"""

from app.storage.database import init_db
from app.storage.seed import seed_cancellation_orders, seed_logistics, seed_orders, seed_refunds

if __name__ == "__main__":
    init_db()
    seed_orders()
    seed_refunds()
    seed_logistics()
    seed_cancellation_orders()
    print("模拟订单、退款、物流和取消演示订单加载完成。")
