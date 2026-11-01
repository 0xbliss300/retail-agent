"""初始化数据库结构，并加载本地模拟数据。"""

from app.storage.database import init_db
from app.storage.seed import seed_logistics, seed_orders, seed_refunds

if __name__ == "__main__":
    init_db()
    seed_orders()
    seed_refunds()
    seed_logistics()
    print("模拟订单、退款和物流记录加载完成。")
