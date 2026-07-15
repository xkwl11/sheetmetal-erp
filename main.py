# -*- coding: utf-8 -*-
"""
钣金ERP服务端 - 主入口
数据库功能已拆分到 database.py
套料逻辑已拆分到 nesting_engine.py
订单逻辑已拆分到 order_engine.py
"""
from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
import sqlite3
import json
import os
import io
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from collections import defaultdict
from PIL import Image

# ---------- 导入数据库引擎 ----------
from database import get_db, init_db

# ---------- 导入订单引擎 ----------
from order_engine import (
    OrderCreate,
    OrderUpdate,
    OrderItem,
    OrderItemUpdate,
    OrderProcess,
    OrderProcessUpdate,
    create_order,
    get_order,
    get_orders,
    update_order,
    delete_order,
    add_order_item,
    update_order_item,
    delete_order_item,
    add_order_process,
    update_order_process,
    delete_order_process,
    nest_order_by_material,
    nest_order,
    confirm_order_with_color,
    confirm_order_by_material,
    save_attachment,
    get_attachments,
    delete_attachment,
    UPLOAD_DIR
)

# ---------- 导入套料引擎 ----------
from nesting_engine import (
    generate_leftover_id,
    PartItem,
    NestingRequest,
    calculate_nesting_engine,
    confirm_nesting_engine
)

# ---------- 导入财务引擎 ----------
from finance_engine import (
    MaterialPrice,
    MaterialPriceUpdate,
    ProcessPrice,
    ProcessPriceUpdate,
    get_material_prices,
    get_material_price,
    create_material_price,
    update_material_price,
    delete_material_price,
    sync_material_prices,
    get_process_prices,
    get_process_price,
    create_process_price,
    update_process_price,
    delete_process_price,
    get_tax_rate,
    set_tax_rate
)

# ---------- 初始化数据库 ----------
init_db()

# ---------- FastAPI 应用 ----------
app = FastAPI(title="钣金ERP系统", version="2.7")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

# 创建必要目录
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))

# ---------- 页面路由 ----------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(quantity) as total FROM leftover_plates WHERE status='available'")
    leftover_total = cursor.fetchone()["total"] or 0
    cursor.execute("SELECT length, width, thickness, quantity, color FROM standard_plate_stock")
    standard = cursor.fetchall()
    total_standard = sum(row["quantity"] for row in standard)
    cursor.execute("SELECT COUNT(*) FROM nesting_logs WHERE date(created_at) = date('now')")
    today_nesting = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM nesting_logs")
    total_nesting = cursor.fetchone()[0]
    cursor.execute("SELECT * FROM leftover_plates WHERE status='available' ORDER BY created_at DESC LIMIT 5")
    recent_leftovers = cursor.fetchall()
    conn.close()
    template = jinja_env.get_template("index.html")
    html = template.render(request=request, active_page="index", stats={
        "leftover_count": leftover_total,
        "total_standard": total_standard,
        "today_nesting": today_nesting,
        "total_nesting": total_nesting,
        "standard_details": standard,
        "recent_leftovers": recent_leftovers
    })
    return HTMLResponse(content=html)

@app.get("/nesting", response_class=HTMLResponse)
async def nesting_page(request: Request):
    template = jinja_env.get_template("nesting.html")
    html = template.render(request=request, active_page="nesting")
    return HTMLResponse(content=html)

@app.get("/inventory", response_class=HTMLResponse)
async def inventory_page(request: Request):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM standard_plate_stock")
    standard = cursor.fetchall()
    cursor.execute("SELECT * FROM leftover_plates")
    leftovers = cursor.fetchall()
    conn.close()
    template = jinja_env.get_template("inventory.html")
    html = template.render(request=request, active_page="inventory", standard=standard, leftovers=leftovers)
    return HTMLResponse(content=html)

@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request):
    template = jinja_env.get_template("orders.html")
    html = template.render(request=request, active_page="orders")
    return HTMLResponse(content=html)

@app.get("/orders/new", response_class=HTMLResponse)
async def order_new_page(request: Request):
    template = jinja_env.get_template("order_form.html")
    html = template.render(request=request, active_page="orders", order=None)
    return HTMLResponse(content=html)

@app.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail_page(request: Request, order_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        order = get_order(order_id, cursor)
    finally:
        conn.close()
    template = jinja_env.get_template("order_detail.html")
    html = template.render(request=request, active_page="orders", order=order)
    return HTMLResponse(content=html)

@app.get("/orders/{order_id}/edit", response_class=HTMLResponse)
async def order_edit_page(request: Request, order_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        order = get_order(order_id, cursor)
    finally:
        conn.close()
    template = jinja_env.get_template("order_form.html")
    html = template.render(request=request, active_page="orders", order=order)
    return HTMLResponse(content=html)

@app.get("/attachments", response_class=HTMLResponse)
async def attachments_page(request: Request):
    """附件管理页面"""
    template = jinja_env.get_template("attachments.html")
    html = template.render(request=request, active_page="attachments")
    return HTMLResponse(content=html)

# ---------- 财务管理页面 ----------
@app.get("/finance/pricing", response_class=HTMLResponse)
async def finance_pricing_page(request: Request):
    """定价管理页面"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        tax_rate = get_tax_rate(cursor)
    finally:
        conn.close()
    template = jinja_env.get_template("finance/pricing.html")
    html = template.render(request=request, active_page="finance", tax_rate=tax_rate)
    return HTMLResponse(content=html)

# ========== 订单管理API ==========
@app.get("/api/orders")
async def api_get_orders(status: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return get_orders(status, conn, cursor)
    finally:
        conn.close()

@app.get("/api/orders/{order_id}")
async def api_get_order(order_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return get_order(order_id, cursor)
    finally:
        conn.close()

@app.post("/api/orders")
async def api_create_order(order_data: OrderCreate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return create_order(order_data, conn, cursor)
    finally:
        conn.close()

@app.put("/api/orders/{order_id}")
async def api_update_order(order_id: int, update_data: OrderUpdate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return update_order(order_id, update_data, conn, cursor)
    finally:
        conn.close()

@app.delete("/api/orders/{order_id}")
async def api_delete_order(order_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return delete_order(order_id, conn, cursor)
    finally:
        conn.close()

# ---------- 订单零件管理 ----------
@app.post("/api/orders/{order_id}/items")
async def api_add_order_item(order_id: int, item: OrderItem):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return add_order_item(order_id, item, conn, cursor)
    finally:
        conn.close()

@app.put("/api/orders/items/{item_id}")
async def api_update_order_item(item_id: int, update_data: OrderItemUpdate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return update_order_item(item_id, update_data, conn, cursor)
    finally:
        conn.close()

@app.delete("/api/orders/items/{item_id}")
async def api_delete_order_item(item_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return delete_order_item(item_id, conn, cursor)
    finally:
        conn.close()

# ---------- 订单工艺管理 ----------
@app.post("/api/orders/{order_id}/processes")
async def api_add_order_process(order_id: int, process: OrderProcess):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return add_order_process(order_id, process, conn, cursor)
    finally:
        conn.close()

@app.put("/api/orders/processes/{proc_id}")
async def api_update_order_process(proc_id: int, update_data: OrderProcessUpdate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return update_order_process(proc_id, update_data, conn, cursor)
    finally:
        conn.close()

@app.delete("/api/orders/processes/{proc_id}")
async def api_delete_order_process(proc_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return delete_order_process(proc_id, conn, cursor)
    finally:
        conn.close()

# ---------- 订单附件API ----------
@app.post("/api/orders/{order_id}/attachments")
async def api_upload_attachment(order_id: int, file: UploadFile = File(...)):
    """上传附件"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM orders WHERE id = ?", (order_id,))
        if not cursor.fetchone():
            raise HTTPException(404, "订单不存在")
        result = save_attachment(order_id, file, cursor, conn)
        return result
    finally:
        conn.close()

@app.post("/api/orders/{order_id}/attachments/batch")
async def api_upload_attachments(order_id: int, files: List[UploadFile] = File(...)):
    """批量上传附件"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM orders WHERE id = ?", (order_id,))
        if not cursor.fetchone():
            raise HTTPException(404, "订单不存在")
        results = []
        for file in files:
            result = save_attachment(order_id, file, cursor, conn)
            results.append(result)
        return {"message": f"成功上传 {len(results)} 个文件", "files": results}
    finally:
        conn.close()

@app.get("/api/orders/{order_id}/attachments")
async def api_get_attachments(order_id: int):
    """获取订单附件列表"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        return get_attachments(order_id, cursor)
    finally:
        conn.close()

@app.delete("/api/orders/attachments/{attachment_id}")
async def api_delete_attachment(attachment_id: int):
    """删除附件"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        return delete_attachment(attachment_id, cursor, conn)
    finally:
        conn.close()

@app.get("/api/orders/attachments/{attachment_id}/download")
async def api_download_attachment(attachment_id: int):
    """下载附件"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT filepath, filename FROM order_attachments WHERE id = ?", (attachment_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "附件不存在")
        if not os.path.exists(row["filepath"]):
            raise HTTPException(404, "文件不存在")
        return FileResponse(row["filepath"], filename=row["filename"])
    finally:
        conn.close()

@app.get("/api/orders/attachments/{attachment_id}/thumbnail")
async def api_get_thumbnail(attachment_id: int):
    """获取图片缩略图"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT filepath, filename FROM order_attachments WHERE id = ?", (attachment_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "附件不存在")
        if not os.path.exists(row["filepath"]):
            raise HTTPException(404, "文件不存在")
        
        ext = row["filename"].split('.')[-1].lower()
        if ext not in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']:
            raise HTTPException(400, "不是图片文件")
        
        img = Image.open(row["filepath"])
        img.thumbnail((200, 200))
        img_byte_arr = io.BytesIO()
        if ext in ['png', 'gif', 'webp']:
            img.save(img_byte_arr, format='PNG')
            media_type = "image/png"
        else:
            img.save(img_byte_arr, format='JPEG', quality=85)
            media_type = "image/jpeg"
        img_byte_arr.seek(0)
        
        return StreamingResponse(img_byte_arr, media_type=media_type)
    finally:
        conn.close()

# ---------- 独立附件管理API ----------
@app.get("/api/attachments")
async def api_get_all_attachments(order_id: Optional[int] = None, keyword: Optional[str] = None):
    """获取所有附件（支持按订单筛选和文件名搜索）"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        sql = """
            SELECT a.id, a.filename, a.filesize, a.uploaded_at,
                   a.order_id, o.order_no, o.customer_name
            FROM order_attachments a
            LEFT JOIN orders o ON a.order_id = o.id
            WHERE 1=1
        """
        params = []
        if order_id:
            sql += " AND a.order_id = ?"
            params.append(order_id)
        if keyword:
            sql += " AND a.filename LIKE ?"
            params.append(f"%{keyword}%")
        sql += " ORDER BY a.uploaded_at DESC"
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

@app.get("/api/attachments/orders")
async def api_get_attachments_orders():
    """获取有附件的订单列表（用于筛选下拉框）"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT DISTINCT o.id, o.order_no, o.customer_name
            FROM orders o
            INNER JOIN order_attachments a ON o.id = a.order_id
            ORDER BY o.order_no DESC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

@app.get("/api/attachments/{attachment_id}/download")
async def api_download_attachment_global(attachment_id: int):
    """下载附件（全局）"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT filepath, filename FROM order_attachments WHERE id = ?", (attachment_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "附件不存在")
        if not os.path.exists(row["filepath"]):
            raise HTTPException(404, "文件不存在")
        return FileResponse(row["filepath"], filename=row["filename"])
    finally:
        conn.close()

@app.delete("/api/attachments/{attachment_id}")
async def api_delete_attachment_global(attachment_id: int):
    """删除附件（全局）"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        return delete_attachment(attachment_id, cursor, conn)
    finally:
        conn.close()

# ---------- 套料集成 ----------
@app.post("/api/orders/{order_id}/nest")
async def api_nest_order(order_id: int):
    """对订单执行套料（自动按材料分组）"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        return nest_order_by_material(order_id, conn, cursor)
    finally:
        conn.close()

@app.post("/api/orders/{order_id}/nest/single")
async def api_nest_order_single(order_id: int, color: str):
    """对订单执行套料（单一材料，兼容旧接口）"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        return nest_order(order_id, color, conn, cursor)
    finally:
        conn.close()

@app.post("/api/orders/{order_id}/confirm")
async def api_confirm_order(order_id: int):
    """确认套料方案（自动识别多材料或单材料）"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        order = get_order(order_id, cursor)
        if not order:
            raise HTTPException(404, "订单不存在")
        if order.get("nesting_by_material"):
            return confirm_order_by_material(order_id, conn, cursor)
        elif order.get("nesting_result"):
            scheme = order["nesting_result"]
            color = scheme.get("color")
            if not color:
                raise HTTPException(400, "无法自动识别材料颜色，请使用旧接口")
            return confirm_order_with_color(order_id, color, conn, cursor)
        else:
            raise HTTPException(400, "订单没有套料方案")
    finally:
        conn.close()

# ---------- 财务API ----------
# ---- 税率管理 ----
@app.get("/api/finance/tax-rate")
async def api_get_tax_rate():
    conn = get_db()
    cursor = conn.cursor()
    try:
        rate = get_tax_rate(cursor)
        return {"tax_rate": rate}
    finally:
        conn.close()

@app.put("/api/finance/tax-rate")
async def api_set_tax_rate(tax_rate: float):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return set_tax_rate(tax_rate, conn, cursor)
    finally:
        conn.close()

# ---- 材料价格管理 ----
@app.get("/api/finance/material-prices")
async def api_get_material_prices():
    conn = get_db()
    cursor = conn.cursor()
    try:
        prices = get_material_prices(cursor)
        if not prices:
            return []
        return prices
    finally:
        conn.close()

@app.post("/api/finance/material-prices")
async def api_create_material_price(item: MaterialPrice):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return create_material_price(item, conn, cursor)
    finally:
        conn.close()

@app.put("/api/finance/material-prices/{material_name}")
async def api_update_material_price(material_name: str, update_data: MaterialPriceUpdate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return update_material_price(material_name, update_data, conn, cursor)
    finally:
        conn.close()

@app.delete("/api/finance/material-prices/{material_name}")
async def api_delete_material_price(material_name: str):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return delete_material_price(material_name, conn, cursor)
    finally:
        conn.close()

@app.post("/api/finance/material-prices/sync")
async def api_sync_material_prices():
    conn = get_db()
    cursor = conn.cursor()
    try:
        return sync_material_prices(conn, cursor)
    finally:
        conn.close()

@app.post("/api/finance/process-prices/sync")
async def api_sync_process_prices():
    """从订单同步工艺到价格表"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        from finance_engine import sync_process_prices
        return sync_process_prices(conn, cursor)
    finally:
        conn.close()

# ---- 工艺价格管理 ----
@app.get("/api/finance/process-prices")
async def api_get_process_prices():
    conn = get_db()
    cursor = conn.cursor()
    try:
        prices = get_process_prices(cursor)
        if not prices:
            return []
        return prices
    finally:
        conn.close()

@app.post("/api/finance/process-prices")
async def api_create_process_price(item: ProcessPrice):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return create_process_price(item, conn, cursor)
    finally:
        conn.close()

@app.put("/api/finance/process-prices/{process}/{unit}")
async def api_update_process_price(process: str, unit: str, update_data: ProcessPriceUpdate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return update_process_price(process, unit, update_data, conn, cursor)
    finally:
        conn.close()

@app.delete("/api/finance/process-prices/{process}/{unit}")
async def api_delete_process_price(process: str, unit: str):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return delete_process_price(process, unit, conn, cursor)
    finally:
        conn.close()

# ---------- 数据模型 ----------
class StandardStockAdd(BaseModel):
    length: int
    width: int
    thickness: float
    quantity: int
    color: str

class StandardStockUpdate(BaseModel):
    length: int
    width: int
    thickness: float
    quantity: int
    color: str

class LeftoverAdd(BaseModel):
    length: int
    width: int
    thickness: float
    quantity: int = 1
    color: str

class LeftoverUpdate(BaseModel):
    length: int
    width: int
    thickness: float
    quantity: int
    color: str

# ---------- 库存API ----------
@app.post("/api/v1/inventory/standard/add")
async def add_standard_stock(item: StandardStockAdd):
    conn = get_db()
    cursor = conn.cursor()
    try:
        color = item.color.strip()
        cursor.execute("SELECT quantity FROM standard_plate_stock WHERE length=? AND width=? AND thickness=? AND color=?",
                       (item.length, item.width, item.thickness, color))
        row = cursor.fetchone()
        if row:
            new_qty = row["quantity"] + item.quantity
            cursor.execute("UPDATE standard_plate_stock SET quantity=? WHERE length=? AND width=? AND thickness=? AND color=?",
                           (new_qty, item.length, item.width, item.thickness, color))
            message = f"整板 {item.length}×{item.width}×{item.thickness}mm 库存增加 {item.quantity} 张，现为 {new_qty} 张"
        else:
            cursor.execute("INSERT INTO standard_plate_stock (length, width, thickness, quantity, color) VALUES (?, ?, ?, ?, ?)",
                           (item.length, item.width, item.thickness, item.quantity, color))
            message = f"整板 {item.length}×{item.width}×{item.thickness}mm × {item.quantity} 张入库成功"
        conn.commit()
        return {"message": message}
    except sqlite3.OperationalError as e:
        if "no such column" in str(e):
            conn2 = get_db()
            cursor2 = conn2.cursor()
            if "thickness" in str(e):
                cursor2.execute("ALTER TABLE standard_plate_stock ADD COLUMN thickness REAL DEFAULT 0")
            if "width" in str(e):
                cursor2.execute("ALTER TABLE standard_plate_stock ADD COLUMN width INTEGER DEFAULT 1220")
            conn2.commit()
            conn2.close()
            return await add_standard_stock(item)
        else:
            raise HTTPException(status_code=500, detail=f"数据库错误: {str(e)}")
    finally:
        conn.close()

@app.post("/api/v1/inventory/standard/update")
async def update_standard_stock(item: StandardStockUpdate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        color = item.color.strip()
        cursor.execute("SELECT quantity FROM standard_plate_stock WHERE length=? AND width=? AND thickness=? AND color=?",
                       (item.length, item.width, item.thickness, color))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE standard_plate_stock SET quantity=? WHERE length=? AND width=? AND thickness=? AND color=?",
                           (item.quantity, item.length, item.width, item.thickness, color))
            message = f"整板 {item.length}×{item.width}×{item.thickness}mm 数量已更新为 {item.quantity}"
        else:
            cursor.execute("INSERT INTO standard_plate_stock (length, width, thickness, quantity, color) VALUES (?, ?, ?, ?, ?)",
                           (item.length, item.width, item.thickness, item.quantity, color))
            message = f"整板 {item.length}×{item.width}×{item.thickness}mm 已新增，数量 {item.quantity}"
        conn.commit()
        return {"message": message}
    finally:
        conn.close()

@app.delete("/api/v1/inventory/standard/delete")
async def delete_standard_stock(length: int, width: int, thickness: float, color: str):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM standard_plate_stock WHERE length=? AND width=? AND thickness=? AND color=?",
                       (length, width, thickness, color.strip()))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="该整板不存在")
        conn.commit()
        return {"message": f"整板 {length}×{width}×{thickness}mm {color} 已删除"}
    finally:
        conn.close()

@app.post("/api/v1/inventory/leftover/add")
async def add_leftover(item: LeftoverAdd):
    conn = get_db()
    cursor = conn.cursor()
    try:
        color = item.color.strip()
        cursor.execute("SELECT id, quantity FROM leftover_plates WHERE length=? AND width=? AND thickness=? AND color=? AND status='available'",
                       (item.length, item.width, item.thickness, color))
        row = cursor.fetchone()
        if row:
            new_qty = row["quantity"] + item.quantity
            cursor.execute("UPDATE leftover_plates SET quantity=? WHERE id=?", (new_qty, row["id"]))
            message = f"余料 {item.length}×{item.width}×{item.thickness}mm 数量增加 {item.quantity}，现为 {new_qty}"
        else:
            new_id = generate_leftover_id(cursor)
            cursor.execute("INSERT INTO leftover_plates (id, length, width, thickness, color, quantity, status) VALUES (?, ?, ?, ?, ?, ?, 'available')",
                           (new_id, item.length, item.width, item.thickness, color, item.quantity))
            message = f"余料 {item.length}×{item.width}×{item.thickness}mm × {item.quantity} 块入库成功"
        conn.commit()
        return {"message": message}
    finally:
        conn.close()

@app.post("/api/v1/inventory/leftover/update")
async def update_leftover(item: LeftoverUpdate):
    conn = get_db()
    cursor = conn.cursor()
    try:
        color = item.color.strip()
        cursor.execute("SELECT id FROM leftover_plates WHERE length=? AND width=? AND thickness=? AND color=? AND status='available'",
                       (item.length, item.width, item.thickness, color))
        row = cursor.fetchone()
        if not row:
            new_id = generate_leftover_id(cursor)
            cursor.execute("INSERT INTO leftover_plates (id, length, width, thickness, color, quantity, status) VALUES (?, ?, ?, ?, ?, ?, 'available')",
                           (new_id, item.length, item.width, item.thickness, color, item.quantity))
            message = f"余料 {item.length}×{item.width}×{item.thickness}mm × {item.quantity} 块新增成功"
        else:
            if item.quantity == 0:
                cursor.execute("DELETE FROM leftover_plates WHERE id=?", (row["id"],))
                message = f"余料 {item.length}×{item.width}×{item.thickness}mm 已删除"
            else:
                cursor.execute("UPDATE leftover_plates SET quantity=? WHERE id=?", (item.quantity, row["id"]))
                message = f"余料 {item.length}×{item.width}×{item.thickness}mm 数量已更新为 {item.quantity}"
        conn.commit()
        return {"message": message}
    finally:
        conn.close()

@app.delete("/api/v1/inventory/leftover/delete")
async def delete_leftover_by_spec(length: int, width: int, thickness: float, color: str):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM leftover_plates WHERE length=? AND width=? AND thickness=? AND color=?",
                       (length, width, thickness, color.strip()))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="该余料不存在")
        conn.commit()
        return {"message": f"余料 {length}×{width}×{thickness}mm 已删除"}
    finally:
        conn.close()

@app.get("/api/v1/inventory/standards/list")
async def list_standards():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT length, width, thickness, quantity, color FROM standard_plate_stock ORDER BY color, length")
        rows = cursor.fetchall()
        return {"data": [dict(row) for row in rows]}
    finally:
        conn.close()

@app.get("/api/v1/inventory/leftovers/list")
async def list_leftovers():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT length, width, thickness, quantity, color FROM leftover_plates WHERE status='available' ORDER BY color, length")
        rows = cursor.fetchall()
        return {"data": [dict(row) for row in rows]}
    finally:
        conn.close()

@app.get("/api/v1/inventory/stock")
async def get_stock_count(color: str, thickness: float = None):
    conn = get_db()
    cursor = conn.cursor()
    try:
        if thickness is not None:
            cursor.execute("SELECT SUM(quantity) as total FROM standard_plate_stock WHERE color=? AND thickness=?", (color.strip(), thickness))
        else:
            cursor.execute("SELECT SUM(quantity) as total FROM standard_plate_stock WHERE color=?", (color.strip(),))
        standard_row = cursor.fetchone()
        standard_count = standard_row["total"] if standard_row["total"] else 0
        if thickness is not None:
            cursor.execute("SELECT SUM(quantity) as total FROM leftover_plates WHERE color=? AND thickness=? AND status='available'", (color.strip(), thickness))
        else:
            cursor.execute("SELECT SUM(quantity) as total FROM leftover_plates WHERE color=? AND status='available'", (color.strip(),))
        leftover_row = cursor.fetchone()
        leftover_count = leftover_row["total"] if leftover_row["total"] else 0
        return {"standard_count": standard_count, "leftover_count": leftover_count}
    finally:
        conn.close()

@app.get("/api/v1/materials/search")
async def search_materials(keyword: str = ""):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT color, thickness, width, length, SUM(quantity) as total_qty
        FROM standard_plate_stock 
        WHERE quantity>0
        GROUP BY color, thickness, width, length
    """)
    standard_items = cursor.fetchall()
    
    cursor.execute("""
        SELECT color, thickness, width, length, SUM(quantity) as total_qty
        FROM leftover_plates 
        WHERE status='available' AND quantity>0
        GROUP BY color, thickness, width, length
    """)
    leftover_items = cursor.fetchall()
    
    material_map = {}
    
    for row in standard_items:
        color = row["color"].strip()
        thickness = row["thickness"]
        width = row["width"]
        length = row["length"]
        key = (color, thickness, width)
        if key not in material_map:
            material_map[key] = {
                "color": color,
                "thickness": thickness,
                "width": width,
                "lengths": set(),
                "standard_qty": 0,
                "leftover_qty": 0
            }
        material_map[key]["lengths"].add(length)
        material_map[key]["standard_qty"] += row["total_qty"]
    
    for row in leftover_items:
        color = row["color"].strip()
        thickness = row["thickness"]
        width = row["width"]
        length = row["length"]
        key = (color, thickness, width)
        if key not in material_map:
            material_map[key] = {
                "color": color,
                "thickness": thickness,
                "width": width,
                "lengths": set(),
                "standard_qty": 0,
                "leftover_qty": 0
            }
        material_map[key]["lengths"].add(length)
        material_map[key]["leftover_qty"] += row["total_qty"]
    
    result_data = []
    for (color, thickness, width), info in material_map.items():
        lengths = sorted(info["lengths"]) if info["lengths"] else []
        if len(lengths) == 1:
            length_display = f"{lengths[0]}"
        else:
            length_display = f"{min(lengths)}-{max(lengths)}"
        
        source_parts = []
        if info["standard_qty"] > 0:
            source_parts.append(f"整板{info['standard_qty']}张")
        if info["leftover_qty"] > 0:
            source_parts.append(f"余料{info['leftover_qty']}块")
        source_str = " | ".join(source_parts) if source_parts else "无库存"
        source_short = "整板+余料" if info["standard_qty"] > 0 and info["leftover_qty"] > 0 else ("整板" if info["standard_qty"] > 0 else "余料")
        
        display = f"{color} {length_display}×{width}×{thickness} [{source_short}]"
        
        result_data.append({
            "id": color,
            "name": display,
            "display": display,
            "color": color,
            "thickness": thickness,
            "width": width,
            "lengths": list(lengths),
            "standard_qty": info["standard_qty"],
            "leftover_qty": info["leftover_qty"],
            "source": source_str,
            "source_short": source_short
        })
    
    if keyword:
        keyword_lower = keyword.lower()
        result_data = [item for item in result_data if keyword_lower in item["name"].lower()]
    
    result_data.sort(key=lambda x: x["name"])
    conn.close()
    return {"data": result_data}

# ---------- 套料路由（调用引擎） ----------
@app.post("/api/v1/nesting/calculate")
async def calculate_nesting(request: NestingRequest):
    conn = get_db()
    cursor = conn.cursor()
    try:
        return calculate_nesting_engine(request, conn, cursor)
    finally:
        conn.close()

@app.post("/api/v1/nesting/confirm")
async def confirm_nesting(scheme: dict):
    conn = get_db()
    cursor = conn.cursor()
    try:
        result = confirm_nesting_engine(scheme, conn, cursor)
        conn.commit()
        return result
    finally:
        conn.close()

# ---------- 启动 ----------
if __name__ == "__main__":
    print("🚀 正在启动钣金ERP服务端 (完整版)...")
    print("🌐 管理界面: http://127.0.0.1:8000/")
    print("📄 API文档: http://127.0.0.1:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)