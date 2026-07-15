# -*- coding: utf-8 -*-
"""
订单管理引擎 - 独立模块
包含：订单CRUD、零件管理、工艺管理、套料集成、附件管理
"""

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import sqlite3
import os
import shutil
from collections import defaultdict

# ---------- 配置 ----------
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------- 数据模型 ----------
class OrderItem(BaseModel):
    part_id: str
    length: int
    width: int
    quantity: int = 1
    material: str = ""
    area: Optional[float] = 0
    unit_price: Optional[float] = 0
    total_price: Optional[float] = 0

class OrderProcess(BaseModel):
    process: str
    quantity: float
    unit: str
    unit_price: float
    total_price: float

class OrderCreate(BaseModel):
    customer_name: Optional[str] = ""
    customer_phone: Optional[str] = ""
    notes: Optional[str] = ""
    tax_rate: float = 0.13
    tax_included: bool = True
    transport_method: str = "自提"
    delivery_address: Optional[str] = ""
    items: List[OrderItem] = []
    processes: List[OrderProcess] = []

class OrderUpdate(BaseModel):
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    notes: Optional[str] = None
    tax_rate: Optional[float] = None
    tax_included: Optional[bool] = None
    transport_method: Optional[str] = None
    delivery_address: Optional[str] = None

class OrderItemUpdate(BaseModel):
    part_id: Optional[str] = None
    length: Optional[int] = None
    width: Optional[int] = None
    quantity: Optional[int] = None
    material: Optional[str] = None
    unit_price: Optional[float] = None

class OrderProcessUpdate(BaseModel):
    process: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    total_price: Optional[float] = None

# ---------- 工具函数 ----------
def get_full_order_no(cursor, date_str):
    cursor.execute(
        "SELECT order_no FROM orders WHERE order_no LIKE ? ORDER BY order_no DESC LIMIT 1",
        (f"{date_str}-%",)
    )
    last = cursor.fetchone()
    if last:
        num = int(last["order_no"].split("-")[1]) + 1
    else:
        num = 1
    return f"{date_str}-{num:03d}"

def calc_area(length, width, quantity):
    return round((length * width * quantity) / 1000000, 4)

# ---------- 订单CRUD ----------
def create_order(order_data: OrderCreate, conn, cursor):
    date_str = datetime.now().strftime("%Y%m%d")
    order_no = get_full_order_no(cursor, date_str)

    cursor.execute(
        """
        INSERT INTO orders (
            order_no, customer_name, customer_phone, notes,
            tax_rate, tax_included, transport_method, delivery_address,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            order_no,
            order_data.customer_name or "",
            order_data.customer_phone or "",
            order_data.notes or "",
            order_data.tax_rate,
            1 if order_data.tax_included else 0,
            order_data.transport_method or "自提",
            order_data.delivery_address or ""
        )
    )
    order_id = cursor.lastrowid

    total_material = 0
    total_process = 0
    total_area = 0

    # 插入零件
    for item in order_data.items:
        area = calc_area(item.length, item.width, item.quantity)
        total_area += area
        total_material += item.total_price or 0
        cursor.execute(
            """
            INSERT INTO order_items (
                order_id, part_id, length, width, quantity, material,
                unit_price, total_price, area
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id, item.part_id, item.length, item.width,
                item.quantity, item.material or "",
                item.unit_price or 0,
                item.total_price or 0, area
            )
        )

    # 插入工艺
    for proc in order_data.processes:
        total_process += proc.total_price or 0
        cursor.execute(
            """
            INSERT INTO order_processes (
                order_id, process, quantity, unit, unit_price, total_price
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order_id, proc.process, proc.quantity,
                proc.unit, proc.unit_price, proc.total_price
            )
        )

    # 更新订单汇总
    cursor.execute(
        """
        UPDATE orders SET
            total_area = ?,
            total_material = ?,
            total_process = ?,
            total_price = ?
        WHERE id = ?
        """,
        (total_area, total_material, total_process, total_material + total_process, order_id)
    )

    conn.commit()
    return get_order(order_id, cursor)

def get_order(order_id: int, cursor):
    cursor.execute(
        """
        SELECT id, order_no, customer_name, customer_phone, notes,
               status, nesting_result, nesting_by_material,
               total_area, total_material, total_process, total_price,
               tax_rate, tax_included,
               transport_method, delivery_address,
               created_at, updated_at
        FROM orders WHERE id = ?
        """,
        (order_id,)
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "订单不存在")

    cursor.execute(
        """
        SELECT id, part_id, length, width, quantity, material,
               unit_price, total_price, area
        FROM order_items WHERE order_id = ?
        """,
        (order_id,)
    )
    parts = [dict(r) for r in cursor.fetchall()]

    cursor.execute(
        """
        SELECT id, process, quantity, unit, unit_price, total_price
        FROM order_processes WHERE order_id = ?
        """,
        (order_id,)
    )
    processes = [dict(r) for r in cursor.fetchall()]

    result = dict(row)
    result["parts"] = parts
    result["processes"] = processes
    result["tax_included"] = bool(result["tax_included"])
    
    if result["nesting_result"]:
        result["nesting_result"] = json.loads(result["nesting_result"])
    else:
        result["nesting_result"] = None
    
    if result["nesting_by_material"]:
        result["nesting_by_material"] = json.loads(result["nesting_by_material"])
    else:
        result["nesting_by_material"] = None
        
    return result

def get_orders(status: Optional[str] = None, conn=None, cursor=None):
    if status:
        cursor.execute(
            """
            SELECT id, order_no, customer_name, status,
                   total_area, total_price, created_at,
                   (SELECT COUNT(*) FROM order_items WHERE order_id = orders.id) as item_count
            FROM orders WHERE status = ? ORDER BY created_at DESC
            """,
            (status,)
        )
    else:
        cursor.execute(
            """
            SELECT id, order_no, customer_name, status,
                   total_area, total_price, created_at,
                   (SELECT COUNT(*) FROM order_items WHERE order_id = orders.id) as item_count
            FROM orders ORDER BY created_at DESC
            """
        )
    return [dict(r) for r in cursor.fetchall()]

def update_order(order_id: int, update_data: OrderUpdate, conn, cursor):
    fields = []
    values = []
    if update_data.customer_name is not None:
        fields.append("customer_name = ?"); values.append(update_data.customer_name)
    if update_data.customer_phone is not None:
        fields.append("customer_phone = ?"); values.append(update_data.customer_phone)
    if update_data.notes is not None:
        fields.append("notes = ?"); values.append(update_data.notes)
    if update_data.tax_rate is not None:
        fields.append("tax_rate = ?"); values.append(update_data.tax_rate)
    if update_data.tax_included is not None:
        fields.append("tax_included = ?"); values.append(1 if update_data.tax_included else 0)
    if update_data.transport_method is not None:
        fields.append("transport_method = ?"); values.append(update_data.transport_method)
    if update_data.delivery_address is not None:
        fields.append("delivery_address = ?"); values.append(update_data.delivery_address)

    if not fields:
        return get_order(order_id, cursor)

    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(order_id)
    cursor.execute(
        f"UPDATE orders SET {', '.join(fields)} WHERE id = ?",
        values
    )
    conn.commit()
    return get_order(order_id, cursor)

def delete_order(order_id: int, conn, cursor):
    cursor.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    if cursor.rowcount == 0:
        raise HTTPException(404, "订单不存在")
    conn.commit()
    return {"message": "订单已删除"}

# ---------- 零件管理 ----------
def add_order_item(order_id: int, item: OrderItem, conn, cursor):
    cursor.execute("SELECT id FROM orders WHERE id = ?", (order_id,))
    if not cursor.fetchone():
        raise HTTPException(404, "订单不存在")

    cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
    status = cursor.fetchone()["status"]
    if status in ("nested", "confirmed"):
        raise HTTPException(400, f"订单已{status}，不能修改零件")

    area = calc_area(item.length, item.width, item.quantity)
    total_price = item.unit_price * area if item.unit_price else 0

    cursor.execute(
        """
        INSERT INTO order_items (
            order_id, part_id, length, width, quantity, material,
            unit_price, total_price, area
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (order_id, item.part_id, item.length, item.width,
         item.quantity, item.material or "", item.unit_price or 0,
         total_price, area)
    )
    conn.commit()
    return get_order(order_id, cursor)

def update_order_item(item_id: int, update_data: OrderItemUpdate, conn, cursor):
    cursor.execute("SELECT order_id FROM order_items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "零件不存在")
    order_id = row["order_id"]

    cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
    status = cursor.fetchone()["status"]
    if status in ("nested", "confirmed"):
        raise HTTPException(400, f"订单已{status}，不能修改零件")

    fields = []
    values = []
    if update_data.part_id is not None:
        fields.append("part_id = ?"); values.append(update_data.part_id)
    if update_data.length is not None:
        fields.append("length = ?"); values.append(update_data.length)
    if update_data.width is not None:
        fields.append("width = ?"); values.append(update_data.width)
    if update_data.quantity is not None:
        fields.append("quantity = ?"); values.append(update_data.quantity)
    if update_data.material is not None:
        fields.append("material = ?"); values.append(update_data.material)
    if update_data.unit_price is not None:
        fields.append("unit_price = ?"); values.append(update_data.unit_price)

    if not fields:
        raise HTTPException(400, "没有要更新的字段")

    values.append(item_id)
    cursor.execute(
        f"UPDATE order_items SET {', '.join(fields)} WHERE id = ?",
        values
    )

    # 重新计算面积和总价
    cursor.execute(
        """
        UPDATE order_items SET
            area = (length * width * quantity) / 1000000.0,
            total_price = unit_price * ((length * width * quantity) / 1000000.0)
        WHERE id = ?
        """,
        (item_id,)
    )

    # 更新订单汇总
    cursor.execute(
        """
        UPDATE orders SET
            total_area = (SELECT SUM(area) FROM order_items WHERE order_id = ?),
            total_material = (SELECT SUM(total_price) FROM order_items WHERE order_id = ?),
            total_price = total_material + total_process
        WHERE id = ?
        """,
        (order_id, order_id, order_id)
    )

    conn.commit()
    return get_order(order_id, cursor)

def delete_order_item(item_id: int, conn, cursor):
    cursor.execute("SELECT order_id FROM order_items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "零件不存在")
    order_id = row["order_id"]

    cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
    status = cursor.fetchone()["status"]
    if status in ("nested", "confirmed"):
        raise HTTPException(400, f"订单已{status}，不能删除零件")

    cursor.execute("DELETE FROM order_items WHERE id = ?", (item_id,))
    conn.commit()
    return get_order(order_id, cursor)

# ---------- 工艺管理 ----------
def add_order_process(order_id: int, process: OrderProcess, conn, cursor):
    cursor.execute("SELECT id FROM orders WHERE id = ?", (order_id,))
    if not cursor.fetchone():
        raise HTTPException(404, "订单不存在")

    cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
    status = cursor.fetchone()["status"]
    if status in ("nested", "confirmed"):
        raise HTTPException(400, f"订单已{status}，不能修改工艺")

    cursor.execute(
        """
        INSERT INTO order_processes (
            order_id, process, quantity, unit, unit_price, total_price
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (order_id, process.process, process.quantity,
         process.unit, process.unit_price, process.total_price)
    )
    conn.commit()
    return get_order(order_id, cursor)

def update_order_process(proc_id: int, update_data: OrderProcessUpdate, conn, cursor):
    cursor.execute("SELECT order_id FROM order_processes WHERE id = ?", (proc_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "工艺不存在")
    order_id = row["order_id"]

    cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
    status = cursor.fetchone()["status"]
    if status in ("nested", "confirmed"):
        raise HTTPException(400, f"订单已{status}，不能修改工艺")

    fields = []
    values = []
    if update_data.process is not None:
        fields.append("process = ?"); values.append(update_data.process)
    if update_data.quantity is not None:
        fields.append("quantity = ?"); values.append(update_data.quantity)
    if update_data.unit is not None:
        fields.append("unit = ?"); values.append(update_data.unit)
    if update_data.unit_price is not None:
        fields.append("unit_price = ?"); values.append(update_data.unit_price)
    if update_data.total_price is not None:
        fields.append("total_price = ?"); values.append(update_data.total_price)

    if not fields:
        raise HTTPException(400, "没有要更新的字段")

    values.append(proc_id)
    cursor.execute(
        f"UPDATE order_processes SET {', '.join(fields)} WHERE id = ?",
        values
    )

    # 更新订单汇总
    cursor.execute(
        """
        UPDATE orders SET
            total_process = (SELECT SUM(total_price) FROM order_processes WHERE order_id = ?),
            total_price = total_material + total_process
        WHERE id = ?
        """,
        (order_id, order_id)
    )

    conn.commit()
    return get_order(order_id, cursor)

def delete_order_process(proc_id: int, conn, cursor):
    cursor.execute("SELECT order_id FROM order_processes WHERE id = ?", (proc_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "工艺不存在")
    order_id = row["order_id"]

    cursor.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
    status = cursor.fetchone()["status"]
    if status in ("nested", "confirmed"):
        raise HTTPException(400, f"订单已{status}，不能删除工艺")

    cursor.execute("DELETE FROM order_processes WHERE id = ?", (proc_id,))
    conn.commit()
    return get_order(order_id, cursor)

# ---------- 附件管理 ----------
def save_attachment(order_id: int, file: UploadFile, cursor, conn):
    """保存单个附件"""
    order_dir = os.path.join(UPLOAD_DIR, str(order_id))
    os.makedirs(order_dir, exist_ok=True)

    safe_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(order_dir, safe_filename)

    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    filesize = os.path.getsize(filepath)

    cursor.execute(
        """
        INSERT INTO order_attachments (order_id, filename, filepath, filesize)
        VALUES (?, ?, ?, ?)
        """,
        (order_id, file.filename, filepath, filesize)
    )
    conn.commit()
    return {"id": cursor.lastrowid, "filename": file.filename, "filesize": filesize}

def get_attachments(order_id: int, cursor):
    cursor.execute(
        """
        SELECT id, filename, filepath, filesize, uploaded_at
        FROM order_attachments WHERE order_id = ?
        ORDER BY uploaded_at DESC
        """,
        (order_id,)
    )
    return [dict(row) for row in cursor.fetchall()]

def delete_attachment(attachment_id: int, cursor, conn):
    cursor.execute("SELECT filepath FROM order_attachments WHERE id = ?", (attachment_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "附件不存在")
    filepath = row["filepath"]

    if os.path.exists(filepath):
        os.remove(filepath)

    cursor.execute("DELETE FROM order_attachments WHERE id = ?", (attachment_id,))
    conn.commit()
    return {"message": "附件已删除"}

# ---------- 套料集成（按材料分组） ----------
def nest_order_by_material(order_id: int, conn, cursor):
    """
    按材料分组套料
    返回每个材料的套料方案
    """
    order = get_order(order_id, cursor)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order["status"] == "confirmed":
        raise HTTPException(400, "订单已确认，不能重新套料")
    if not order["parts"]:
        raise HTTPException(400, "订单没有零件")

    from nesting_engine import calculate_nesting_engine, PartItem, NestingRequest

    # ---- 1. 按材料分组 ----
    material_groups = defaultdict(list)
    for item in order["parts"]:
        material = item.get("material") or "默认材料"
        for i in range(item["quantity"]):
            material_groups[material].append(
                PartItem(
                    part_id=f"{item['part_id']}-{i+1}" if item["quantity"] > 1 else item["part_id"],
                    length=item["length"],
                    width=item["width"]
                )
            )

    # ---- 2. 对每种材料执行套料 ----
    results = {}
    summary = {
        "total_plates": 0,
        "total_standard": 0,
        "total_leftover": 0,
        "materials": []
    }

    for material, parts in material_groups.items():
        if not parts:
            continue
        
        req = NestingRequest(parts=parts, color=material)
        scheme = calculate_nesting_engine(req, conn, cursor)
        
        results[material] = scheme
        summary["total_plates"] += scheme.get("total_plates", 0)
        summary["total_standard"] += scheme.get("summary", {}).get("total_standard", 0)
        summary["total_leftover"] += scheme.get("summary", {}).get("total_leftover", 0)
        summary["materials"].append({
            "material": material,
            "parts_count": len(parts),
            "total_plates": scheme.get("total_plates", 0),
            "scheme": scheme
        })

    # ---- 3. 保存套料结果到订单 ----
    cursor.execute(
        """
        UPDATE orders
        SET nesting_by_material = ?, status = 'nested', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (json.dumps({"results": results, "summary": summary}), order_id)
    )
    conn.commit()
    
    # ---- 4. 返回 ----
    return {
        "order": get_order(order_id, cursor),
        "nesting_by_material": {
            "results": results,
            "summary": summary
        }
    }

def nest_order(order_id: int, material_color: str, conn, cursor):
    """对订单执行套料（单一材料，兼容旧接口）"""
    order = get_order(order_id, cursor)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order["status"] == "confirmed":
        raise HTTPException(400, "订单已确认，不能重新套料")
    if not order["parts"]:
        raise HTTPException(400, "订单没有零件")

    from nesting_engine import calculate_nesting_engine, PartItem, NestingRequest

    expanded_parts = []
    for item in order["parts"]:
        for i in range(item["quantity"]):
            expanded_parts.append(
                PartItem(
                    part_id=f"{item['part_id']}-{i+1}" if item["quantity"] > 1 else item["part_id"],
                    length=item["length"],
                    width=item["width"]
                )
            )

    req = NestingRequest(parts=expanded_parts, color=material_color)
    scheme = calculate_nesting_engine(req, conn, cursor)

    cursor.execute(
        """
        UPDATE orders
        SET nesting_result = ?, status = 'nested', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (json.dumps(scheme), order_id)
    )
    conn.commit()
    return get_order(order_id, cursor)

def confirm_order_with_color(order_id: int, material_color: str, conn, cursor):
    order = get_order(order_id, cursor)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order["status"] != "nested":
        raise HTTPException(400, f"订单状态为 {order['status']}，不能确认")
    if not order["nesting_result"]:
        raise HTTPException(400, "订单没有套料方案")

    from nesting_engine import confirm_nesting_engine

    scheme = order["nesting_result"]
    scheme["color"] = material_color

    result = confirm_nesting_engine(scheme, conn, cursor)

    cursor.execute(
        """
        UPDATE orders
        SET status = 'confirmed', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (order_id,)
    )
    conn.commit()

    return {
        "message": "套料已确认，库存已更新",
        "order": get_order(order_id, cursor),
        "inventory": result
    }

def confirm_order_by_material(order_id: int, conn, cursor):
    """
    确认多材料套料方案，扣减所有材料的库存
    """
    order = get_order(order_id, cursor)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order["status"] != "nested":
        raise HTTPException(400, f"订单状态为 {order['status']}，不能确认")
    if not order["nesting_by_material"]:
        raise HTTPException(400, "订单没有多材料套料方案")

    from nesting_engine import confirm_nesting_engine

    nesting_data = order["nesting_by_material"]
    results = nesting_data.get("results", {})
    
    all_new_leftovers = []
    consumed_standards = []
    consumed_leftovers = []

    for material, scheme in results.items():
        scheme["color"] = material
        result = confirm_nesting_engine(scheme, conn, cursor)
        consumed_standards.extend(result.get("consumed_standards", []))
        consumed_leftovers.extend(result.get("consumed_leftovers", []))
        all_new_leftovers.extend(result.get("new_leftover_ids", []))

    cursor.execute(
        """
        UPDATE orders
        SET status = 'confirmed', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (order_id,)
    )
    conn.commit()

    return {
        "message": "多材料套料已全部确认，库存已更新",
        "order": get_order(order_id, cursor),
        "consumed_standards": consumed_standards,
        "consumed_leftovers": consumed_leftovers,
        "new_leftover_ids": all_new_leftovers
    }