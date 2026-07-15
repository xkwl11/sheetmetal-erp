# -*- coding: utf-8 -*-
"""
套料引擎 - 生产环境最终版
核心：混宽并排 + 短板优先 + 余料优先 + 层间空白余料 + 事务安全
"""

from fastapi import HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from collections import defaultdict, Counter
import json
from datetime import datetime
import sqlite3

# ---------- 常量 ----------
KERF = 0  # 无刀缝
MIN_REMAIN = 20  # 最小可用余料 mm

# ---------- 数据模型 ----------
class PartItem(BaseModel):
    part_id: str
    length: int
    width: int

class NestingRequest(BaseModel):
    parts: List[PartItem]
    color: str

# ---------- 工具函数 ----------
def now_iso():
    return datetime.now().isoformat()

def generate_leftover_id(cursor):
    """生成余料编号 L-XXX（用于库存API）"""
    cursor.execute("SELECT id FROM leftover_plates WHERE id LIKE 'L-%' ORDER BY id DESC LIMIT 1")
    last = cursor.fetchone()
    if last:
        num = int(last["id"].split("-")[1]) + 1
    else:
        num = 1
    return f"L-{num:03d}"

# ---------- 层结构 ----------
def build_layers(parts, max_width):
    """
    构建层：同一层内零件沿宽度方向并排，允许不同宽度混合
    支持零件整体旋转（同宽度组一起旋转，而非单个）
    """
    # 1. 按宽度分组
    width_groups = defaultdict(list)
    for p in parts:
        width_groups[p.width].append(p)
    
    # 2. 对每个宽度组，按长度降序
    for w in width_groups:
        width_groups[w].sort(key=lambda x: x.length, reverse=True)
    
    # 3. 生成所有可能的宽度组并排组合
    layers = []
    remaining_parts = parts.copy()
    
    while remaining_parts:
        current_width = 0
        layer_items = []
        
        # 尝试从最宽的零件开始组合
        i = 0
        while i < len(remaining_parts):
            part = remaining_parts[i]
            # 尝试原始方向
            if current_width + part.width + (KERF if layer_items else 0) <= max_width:
                layer_items.append({
                    "part_id": part.part_id,
                    "length": part.length,
                    "width": part.width,
                    "rotated": False
                })
                current_width += part.width + (KERF if layer_items else 0)
                remaining_parts.pop(i)
                continue
            # 尝试旋转方向（如果长宽不同）
            if part.length != part.width:
                if current_width + part.length + (KERF if layer_items else 0) <= max_width:
                    layer_items.append({
                        "part_id": part.part_id,
                        "length": part.width,
                        "width": part.length,
                        "rotated": True
                    })
                    current_width += part.length + (KERF if layer_items else 0)
                    remaining_parts.pop(i)
                    continue
            i += 1
        
        if not layer_items:
            # 剩余零件都无法放入，报错
            raise HTTPException(
                400,
                f"无法排料：零件 {remaining_parts[0].part_id} ({remaining_parts[0].width}mm) 无法放入板宽 {max_width}mm"
            )
        
        layers.append({
            "width": current_width,
            "length": max(item["length"] for item in layer_items),
            "items": layer_items
        })
    
    # 按层长度降序，利于装箱
    layers.sort(key=lambda x: x["length"], reverse=True)
    return layers


# ---------- 装箱 ----------
def pack_bins(layers, leftover_plates, stock_counter, max_width):
    """装箱：优先余料 -> 已有板 -> 新板（短板优先）"""
    bins = []
    
    for layer in layers:
        layer_len = layer["length"]
        layer_width = layer["width"]
        placed = False
        
        # 1️⃣ 余料优先（支持旋转）
        for left in leftover_plates:
            if left["used"]:
                continue
            
            # 不旋转
            if left["width"] >= layer_width and left["length"] >= layer_len:
                left["used"] = True
                bins.append({
                    "type": "leftover",
                    "id": left["id"],
                    "length": left["length"],
                    "width": left["width"],
                    "used_length": layer_len,
                    "layers": [layer],
                    "rotated": False
                })
                placed = True
                break
            
            # 旋转
            if left["length"] >= layer_width and left["width"] >= layer_len:
                left["used"] = True
                bins.append({
                    "type": "leftover",
                    "id": left["id"],
                    "length": left["width"],
                    "width": left["length"],
                    "used_length": layer_len,
                    "layers": [layer],
                    "rotated": True
                })
                placed = True
                break
        
        if placed:
            continue
        
        # 2️⃣ 放入已有整板（最佳适配：剩余空间最小）
        best_bin = None
        best_remain = float('inf')
        for bin in bins:
            if bin["type"] != "standard":
                continue
            
            used_width = sum(l["width"] for l in bin["layers"])
            if used_width + layer_width > max_width:
                continue
            
            new_max_len = max(bin["used_length"], layer_len)
            if new_max_len <= bin["length"]:
                remain = bin["length"] - new_max_len
                if remain < best_remain:
                    best_bin = bin
                    best_remain = remain
        
        if best_bin is not None:
            best_bin["layers"].append(layer)
            best_bin["used_length"] = max(best_bin["used_length"], layer_len)
            placed = True
        
        if placed:
            continue
        
        # 3️⃣ 新开整板（短板优先）
        suitable = [l for l in stock_counter if l >= layer_len]
        if not suitable:
            raise HTTPException(400, f"板材长度不足，需要至少 {layer_len}mm")
        
        chosen = min(suitable)
        stock_counter[chosen] -= 1
        if stock_counter[chosen] == 0:
            del stock_counter[chosen]
        
        bins.append({
            "type": "standard",
            "length": chosen,
            "width": max_width,
            "used_length": layer_len,
            "layers": [layer]
        })
    
    return bins


# ---------- 主套料 ----------
def calculate_nesting_engine(request: NestingRequest, conn, cursor):
    color = request.color.strip()
    
    cursor.execute(
        "SELECT width, thickness FROM standard_plate_stock WHERE color=? LIMIT 1",
        (color,)
    )
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "SELECT width, thickness FROM leftover_plates WHERE color=? LIMIT 1",
            (color,)
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(400, f"材料 '{color}' 未找到")
    
    MAX_WIDTH = row["width"]
    THICKNESS = row["thickness"]
    
    # ---- 1. 建层 ----
    layers = build_layers(request.parts, MAX_WIDTH)
    
    # ---- 2. 库存 ----
    cursor.execute(
        """
        SELECT id, length, width
        FROM leftover_plates
        WHERE color=? AND status='available'
        ORDER BY length ASC
        """,
        (color,)
    )
    leftover_plates = [
        {
            "id": r["id"],
            "length": r["length"],
            "width": r["width"],
            "used": False
        }
        for r in cursor.fetchall()
    ]
    
    cursor.execute(
        """
        SELECT length, quantity
        FROM standard_plate_stock
        WHERE color=? AND quantity>0
        ORDER BY length ASC
        """,
        (color,)
    )
    stock_counter = Counter()
    for r in cursor.fetchall():
        stock_counter[r["length"]] = r["quantity"]
    
    if not stock_counter and not leftover_plates:
        raise HTTPException(400, "无可用板材或余料")
    
    # ---- 3. 装箱 ----
    bins = pack_bins(layers, leftover_plates, stock_counter, MAX_WIDTH)
    
    # ---- 4. 组装返回 ----
    scheme = {
        "color": color,
        "bins": [],
        "summary": {
            "total_standard": 0,
            "total_leftover": 0,
            "new_leftover_generated": []
        }
    }
    
    for bin in bins:
        # layout 坐标
        layout = []
        y = 0
        for layer in bin["layers"]:
            x = 0
            for item in layer["items"]:
                layout.append({
                    "part_id": item["part_id"],
                    "x": x,
                    "y": y,
                    "w": item["width"],
                    "h": item["length"],
                    "rotated": item["rotated"]
                })
                x += item["width"] + KERF
            y += layer["width"]
        
        bin_info = {
            "type": bin["type"],
            "length": bin["length"],
            "width": bin["width"],
            "used_length": bin["used_length"],
            "layout": layout,
            "layers": bin["layers"],
            "new_leftovers": []
        }
        
        # ----- 余料计算 -----
        total_width = sum(l["width"] for l in bin["layers"])
        max_len = max(l["length"] for l in bin["layers"]) if bin["layers"] else 0
        
        # (a) 长度余料
        remain_len = bin["length"] - max_len
        if remain_len >= MIN_REMAIN:
            bin_info["new_leftovers"].append({
                "length": remain_len,
                "width": total_width,
                "type": "长度余料"
            })
        elif remain_len > 0:
            bin_info["new_leftovers"].append({
                "length": remain_len,
                "width": total_width,
                "type": "长度废料"
            })
        
        # (b) 宽度余料
        remain_width = bin["width"] - total_width
        if remain_width >= MIN_REMAIN and max_len >= MIN_REMAIN:
            bin_info["new_leftovers"].append({
                "length": max_len,
                "width": remain_width,
                "type": "宽度余料"
            })
        elif remain_width > 0 and max_len > 0:
            bin_info["new_leftovers"].append({
                "length": max_len,
                "width": remain_width,
                "type": "宽度废料"
            })
        
        # (c) 层间空白余料（不同层之间的长度差）
        if len(bin["layers"]) > 1:
            sorted_layers = sorted(bin["layers"], key=lambda x: x["length"], reverse=True)
            longest_len = sorted_layers[0]["length"]
            for layer in sorted_layers[1:]:
                gap = longest_len - layer["length"]
                if gap >= MIN_REMAIN:
                    bin_info["new_leftovers"].append({
                        "length": gap,
                        "width": layer["width"],
                        "type": "层间空白余料",
                        "desc": f"{sorted_layers[0]['items'][0]['part_id']}与{layer['items'][0]['part_id']}层长度差"
                    })
        
        if bin["type"] == "leftover":
            scheme["summary"]["total_leftover"] += 1
            bin_info["leftover_id"] = bin["id"]
        else:
            scheme["summary"]["total_standard"] += 1
        
        scheme["summary"]["new_leftover_generated"].extend(bin_info["new_leftovers"])
        scheme["bins"].append(bin_info)
    
    return scheme


# ---------- 确认套料 ----------
def confirm_nesting_engine(scheme: dict, conn, cursor):
    try:
        color = scheme["color"]
        
        cursor.execute(
            "SELECT thickness FROM standard_plate_stock WHERE color=? LIMIT 1",
            (color,)
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "SELECT thickness FROM leftover_plates WHERE color=? LIMIT 1",
                (color,)
            )
            row = cursor.fetchone()
        THICKNESS = row["thickness"]
        
        new_leftover_ids = []
        
        for bin in scheme["bins"]:
            if bin["type"] == "leftover":
                cursor.execute(
                    "DELETE FROM leftover_plates WHERE id=?",
                    (bin["leftover_id"],)
                )
            else:
                cursor.execute(
                    """
                    UPDATE standard_plate_stock
                    SET quantity = quantity - 1
                    WHERE color=? AND length=? AND quantity > 0
                    """,
                    (color, bin["length"])
                )
                if cursor.rowcount == 0:
                    raise HTTPException(400, "库存不足，请重新套料")
            
            # 生成新余料（仅有效余料）
            for left in bin.get("new_leftovers", []):
                if "废料" in left["type"]:
                    continue
                new_id = generate_leftover_id(cursor)
                cursor.execute(
                    """
                    INSERT INTO leftover_plates
                    (id, length, width, thickness, color, quantity, status)
                    VALUES (?, ?, ?, ?, ?, 1, 'available')
                    """,
                    (new_id, left["length"], left["width"], THICKNESS, color)
                )
                new_leftover_ids.append(new_id)
        
        cursor.execute(
            "INSERT INTO nesting_logs (request_data, result_data) VALUES (?, ?)",
            (json.dumps(scheme), json.dumps({
                "new_leftovers": new_leftover_ids,
                "timestamp": now_iso()
            }))
        )
        
        conn.commit()
        return {"message": "套料已确认", "new_leftover_ids": new_leftover_ids}
    
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"确认套料失败: {str(e)}")