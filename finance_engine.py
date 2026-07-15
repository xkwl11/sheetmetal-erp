# -*- coding: utf-8 -*-
"""
财务管理引擎 - 独立模块
"""
from fastapi import HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import json
import sqlite3

# ---------- 数据模型 ----------
class MaterialPrice(BaseModel):
    material_name: str
    unit: str = "㎡"
    price: float = 0

class MaterialPriceUpdate(BaseModel):
    unit: Optional[str] = None
    price: Optional[float] = None

class ProcessPrice(BaseModel):
    process: str
    unit: str
    price: float = 0

class ProcessPriceUpdate(BaseModel):
    unit: Optional[str] = None
    price: Optional[float] = None

# ============================================================
#  材料价格管理
# ============================================================

def get_material_prices(cursor):
    cursor.execute("""
        SELECT id, material_name, unit, price, updated_at
        FROM material_prices
        ORDER BY material_name
    """)
    return [dict(row) for row in cursor.fetchall()]

def get_material_price(material_name: str, cursor):
    cursor.execute(
        "SELECT id, material_name, unit, price, updated_at FROM material_prices WHERE material_name = ?",
        (material_name,)
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, f"材料 '{material_name}' 未找到")
    return dict(row)

def create_material_price(item: MaterialPrice, conn, cursor):
    cursor.execute(
        "INSERT INTO material_prices (material_name, unit, price) VALUES (?, ?, ?)",
        (item.material_name.strip(), item.unit, item.price)
    )
    conn.commit()
    return get_material_price(item.material_name, cursor)

def update_material_price(material_name: str, update_data: MaterialPriceUpdate, conn, cursor):
    fields = []
    values = []
    if update_data.unit is not None:
        fields.append("unit = ?")
        values.append(update_data.unit)
    if update_data.price is not None:
        fields.append("price = ?")
        values.append(update_data.price)
    if not fields:
        return get_material_price(material_name, cursor)
    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(material_name)
    cursor.execute(
        f"UPDATE material_prices SET {', '.join(fields)} WHERE material_name = ?",
        values
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, f"材料 '{material_name}' 未找到")
    conn.commit()
    return get_material_price(material_name, cursor)

def delete_material_price(material_name: str, conn, cursor):
    cursor.execute("DELETE FROM material_prices WHERE material_name = ?", (material_name,))
    if cursor.rowcount == 0:
        raise HTTPException(404, f"材料 '{material_name}' 未找到")
    conn.commit()
    return {"message": f"材料 '{material_name}' 已删除"}

def sync_material_prices(conn, cursor):
    """从库存同步材料到价格表"""
    cursor.execute("""
        SELECT DISTINCT color FROM standard_plate_stock WHERE quantity > 0
        UNION
        SELECT DISTINCT color FROM leftover_plates WHERE status='available' AND quantity > 0
    """)
    stock_materials = [row["color"].strip() for row in cursor.fetchall() if row["color"].strip()]
    cursor.execute("SELECT material_name FROM material_prices")
    existing = set(row["material_name"] for row in cursor.fetchall())
    added = 0
    for material in stock_materials:
        if material not in existing:
            cursor.execute(
                "INSERT INTO material_prices (material_name, unit, price) VALUES (?, ?, ?)",
                (material, "㎡", 0.0)
            )
            added += 1
    conn.commit()
    return {"added": added, "existing": len(existing)}

# ============================================================
#  工艺价格管理
# ============================================================

def get_process_prices(cursor):
    cursor.execute("""
        SELECT id, process, unit, price, updated_at
        FROM process_prices
        ORDER BY process, unit
    """)
    return [dict(row) for row in cursor.fetchall()]

def get_process_price(process: str, unit: str, cursor):
    cursor.execute(
        "SELECT id, process, unit, price, updated_at FROM process_prices WHERE process = ? AND unit = ?",
        (process, unit)
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, f"工艺 '{process}' ({unit}) 未找到")
    return dict(row)

def create_process_price(item: ProcessPrice, conn, cursor):
    cursor.execute(
        "INSERT INTO process_prices (process, unit, price) VALUES (?, ?, ?)",
        (item.process.strip(), item.unit, item.price)
    )
    conn.commit()
    return get_process_price(item.process, item.unit, cursor)

def update_process_price(process: str, unit: str, update_data: ProcessPriceUpdate, conn, cursor):
    fields = []
    values = []
    if update_data.unit is not None:
        fields.append("unit = ?")
        values.append(update_data.unit)
    if update_data.price is not None:
        fields.append("price = ?")
        values.append(update_data.price)
    if not fields:
        return get_process_price(process, unit, cursor)
    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(process)
    values.append(unit)
    cursor.execute(
        f"UPDATE process_prices SET {', '.join(fields)} WHERE process = ? AND unit = ?",
        values
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, f"工艺 '{process}' ({unit}) 未找到")
    conn.commit()
    return get_process_price(process, unit, cursor)

def delete_process_price(process: str, unit: str, conn, cursor):
    cursor.execute("DELETE FROM process_prices WHERE process = ? AND unit = ?", (process, unit))
    if cursor.rowcount == 0:
        raise HTTPException(404, f"工艺 '{process}' ({unit}) 未找到")
    conn.commit()
    return {"message": f"工艺 '{process}' ({unit}) 已删除"}

def sync_process_prices(conn, cursor):
    """从订单同步工艺到价格表"""
    cursor.execute("""
        SELECT DISTINCT process, unit FROM order_processes
        WHERE process IS NOT NULL AND process != ''
    """)
    order_processes = [{"process": row["process"], "unit": row["unit"]} for row in cursor.fetchall()]
    cursor.execute("SELECT process, unit FROM process_prices")
    existing = set((row["process"], row["unit"]) for row in cursor.fetchall())
    added = 0
    for item in order_processes:
        if (item["process"], item["unit"]) not in existing:
            cursor.execute(
                "INSERT INTO process_prices (process, unit, price) VALUES (?, ?, ?)",
                (item["process"], item["unit"], 0.0)
            )
            added += 1
    conn.commit()
    return {"added": added, "existing": len(existing)}

# ============================================================
#  税率管理
# ============================================================

def get_tax_rate(cursor):
    cursor.execute("SELECT tax_rate FROM orders LIMIT 1")
    row = cursor.fetchone()
    if row:
        return row["tax_rate"]
    return 0.13

def set_tax_rate(tax_rate: float, conn, cursor):
    cursor.execute(
        "UPDATE orders SET tax_rate = ? WHERE status = 'draft'",
        (tax_rate,)
    )
    conn.commit()
    return {"message": f"税率已更新为 {tax_rate*100:.1f}%"}