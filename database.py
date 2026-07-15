# -*- coding: utf-8 -*-
"""
数据库引擎 - 独立模块
"""
import sqlite3
import os

DB_PATH = "sheetmetal_erp.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # ----- 整板库存 -----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS standard_plate_stock (
            length INTEGER NOT NULL,
            width INTEGER NOT NULL,
            thickness REAL NOT NULL,
            quantity INTEGER DEFAULT 0,
            color TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (length, width, thickness, color)
        )
    """)

    # ----- 余料库存 -----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leftover_plates (
            id TEXT PRIMARY KEY,
            length INTEGER NOT NULL,
            width INTEGER NOT NULL,
            thickness REAL NOT NULL,
            color TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            status TEXT DEFAULT 'available',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (length, width, thickness, color)
        )
    """)

    # ----- 套料日志 -----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nesting_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_data TEXT,
            result_data TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ----- 订单主表 -----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT UNIQUE NOT NULL,
            customer_name TEXT,
            customer_phone TEXT,
            notes TEXT,
            status TEXT DEFAULT 'draft',
            nesting_result TEXT,
            nesting_by_material TEXT,
            total_area REAL DEFAULT 0,
            total_material REAL DEFAULT 0,
            total_process REAL DEFAULT 0,
            total_price REAL DEFAULT 0,
            tax_rate REAL DEFAULT 0.13,
            tax_included INTEGER DEFAULT 1,
            transport_method TEXT DEFAULT '自提',
            delivery_address TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ----- 订单零件 -----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            part_id TEXT NOT NULL,
            length INTEGER NOT NULL,
            width INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            material TEXT DEFAULT '',
            unit_price REAL DEFAULT 0,
            total_price REAL DEFAULT 0,
            area REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
        )
    """)

    # ----- 订单工艺 -----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_processes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            process TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT '米',
            unit_price REAL NOT NULL DEFAULT 0,
            total_price REAL NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
        )
    """)

    # ----- 工艺价格表（含 updated_at） -----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS process_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            process TEXT NOT NULL,
            unit TEXT NOT NULL,
            price REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (process, unit)
        )
    """)

    # ----- 订单附件 -----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            filesize INTEGER DEFAULT 0,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
        )
    """)

    # ----- 材料价格表（含 updated_at） -----
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS material_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_name TEXT NOT NULL UNIQUE,
            unit TEXT DEFAULT '㎡',
            price REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ============================================================
    #  列迁移（兼容旧版本）
    # ============================================================
    cursor.execute("PRAGMA table_info(standard_plate_stock)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'thickness' not in columns:
        cursor.execute("ALTER TABLE standard_plate_stock ADD COLUMN thickness REAL DEFAULT 0")
    if 'width' not in columns:
        cursor.execute("ALTER TABLE standard_plate_stock ADD COLUMN width INTEGER DEFAULT 1220")

    cursor.execute("PRAGMA table_info(leftover_plates)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'thickness' not in columns:
        cursor.execute("ALTER TABLE leftover_plates ADD COLUMN thickness REAL DEFAULT 0")
    if 'quantity' not in columns:
        cursor.execute("ALTER TABLE leftover_plates ADD COLUMN quantity INTEGER DEFAULT 1")

    cursor.execute("PRAGMA table_info(orders)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'customer_phone' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN customer_phone TEXT")
    if 'total_area' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN total_area REAL DEFAULT 0")
    if 'total_material' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN total_material REAL DEFAULT 0")
    if 'total_process' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN total_process REAL DEFAULT 0")
    if 'total_price' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN total_price REAL DEFAULT 0")
    if 'tax_rate' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN tax_rate REAL DEFAULT 0.13")
    if 'tax_included' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN tax_included INTEGER DEFAULT 1")
    if 'transport_method' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN transport_method TEXT DEFAULT '自提'")
    if 'delivery_address' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN delivery_address TEXT")
    if 'nesting_by_material' not in columns:
        cursor.execute("ALTER TABLE orders ADD COLUMN nesting_by_material TEXT")

    cursor.execute("PRAGMA table_info(order_items)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'material' not in columns:
        cursor.execute("ALTER TABLE order_items ADD COLUMN material TEXT DEFAULT ''")
    if 'unit_price' not in columns:
        cursor.execute("ALTER TABLE order_items ADD COLUMN unit_price REAL DEFAULT 0")
    if 'total_price' not in columns:
        cursor.execute("ALTER TABLE order_items ADD COLUMN total_price REAL DEFAULT 0")
    if 'area' not in columns:
        cursor.execute("ALTER TABLE order_items ADD COLUMN area REAL DEFAULT 0")

    # ----- 新增：为 process_prices 添加 updated_at -----
    cursor.execute("PRAGMA table_info(process_prices)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'updated_at' not in columns:
        cursor.execute("ALTER TABLE process_prices ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP")
        print("⚠️ 已添加 updated_at 列到 process_prices")

    # ----- 新增：为 material_prices 添加 updated_at -----
    cursor.execute("PRAGMA table_info(material_prices)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'updated_at' not in columns:
        cursor.execute("ALTER TABLE material_prices ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP")
        print("⚠️ 已添加 updated_at 列到 material_prices")

    # ============================================================
    #  示例数据（首次启动）
    # ============================================================

    # 整板
    cursor.execute("SELECT COUNT(*) FROM standard_plate_stock")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT OR IGNORE INTO standard_plate_stock (length, width, thickness, quantity, color) VALUES (?, ?, ?, ?, ?)",
            [
                (2400, 1220, 0.5, 10, "镀锌板银白无指纹"),
                (3000, 1220, 1.0, 5, "不锈钢砂面拉丝板（1.0）"),
                (3500, 1220, 1.5, 3, "不锈钢拉丝哑光"),
                (4000, 1220, 2.0, 2, "镀锌板无指纹"),
                (2438, 1218, 1.0, 10, "不锈钢黑色拉丝哑光1.2"),
            ]
        )

    # 余料
    cursor.execute("SELECT COUNT(*) FROM leftover_plates")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT OR IGNORE INTO leftover_plates (id, length, width, thickness, quantity, color) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("L-001", 1800, 1220, 0.5, 3, "镀锌板银白无指纹"),
                ("L-002", 2200, 1220, 1.0, 2, "不锈钢砂面拉丝板（1.0）"),
                ("L-003", 900, 1220, 0.5, 1, "镀锌板银白无指纹"),
                ("L-004", 3048, 1218, 1.0, 5, "不锈钢黑色拉丝哑光1.2"),
            ]
        )

    # 工艺价格
    cursor.execute("SELECT COUNT(*) FROM process_prices")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT OR IGNORE INTO process_prices (process, unit, price) VALUES (?, ?, ?)",
            [
                ("激光切割", "米", 2.5),
                ("焊接", "米", 8.0),
                ("打磨", "件", 5.0),
                ("刨槽", "米", 12.0),
                ("折弯", "刀", 3.0),
                ("冲压", "件", 1.5),
                ("表面处理", "㎡", 15.0),
                ("组装", "件", 10.0),
            ]
        )

    # 材料价格
    cursor.execute("SELECT COUNT(*) FROM material_prices")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT INTO material_prices (material_name, unit, price) VALUES (?, ?, ?)",
            [
                ("不锈钢", "㎡", 45.0),
                ("镀锌板", "㎡", 25.0),
                ("冷轧板", "㎡", 30.0),
                ("铝板", "㎡", 60.0),
                ("不锈钢黑色拉丝哑光1.2", "㎡", 48.0),
                ("不锈钢玫瑰金拉丝1.0", "㎡", 55.0),
            ]
        )

    conn.commit()
    conn.close()
    print("✅ 数据库初始化完成")

if __name__ == "__main__":
    init_db()
    print(f"📁 数据库文件: {os.path.abspath(DB_PATH)}")