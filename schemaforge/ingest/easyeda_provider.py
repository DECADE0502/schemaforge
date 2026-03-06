"""EasyEDA / JLCPCB 在线器件搜索

通过 JLCPCB 搜索 API 查找器件（含价格/库存/LCSC 编号），
再通过 EasyEDA 产品 API 获取完整符号数据（引脚定义、原理图形状）。

两个 API 均无需认证，仅需标准 User-Agent。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from schemaforge.agent.tool_registry import ToolResult
from schemaforge.common.errors import ErrorCode, ToolError


# ============================================================
# API 端点
# ============================================================

JLCPCB_SEARCH_URL = (
    "https://jlcpcb.com/api/overseas-pcb-order/v1"
    "/shoppingCart/smtGood/selectSmtComponentList/v2"
)
EASYEDA_PRODUCT_URL = (
    "https://easyeda.com/api/products/{lcsc_id}/components?version=6.4.19.5"
)

_USER_AGENT = "Mozilla/5.0 SchemaForge/1.0"


# ============================================================
# 电气/引脚类型映射
# ============================================================

ELECTRIC_TYPE_MAP: dict[int, str] = {
    0: "passive",
    1: "input",
    2: "output",
    3: "bidirectional",
    4: "passive",
    5: "power_in",
}

PIN_TYPE_MAP: dict[int, str] = {
    0: "passive",
    1: "input",
    2: "output",
    3: "bidirectional",
    4: "passive",
    5: "power_in",
    6: "open_collector",
    7: "open_emitter",
    8: "nc",
}


# ============================================================
# 数据模型
# ============================================================


@dataclass
class EasyEDAPinInfo:
    """EasyEDA 引脚信息"""

    number: str = ""
    name: str = ""
    pin_type: str = ""  # input, output, power_in, passive, etc.
    electric_type: str = ""  # 电气类型 (来自 P~ index 2)
    description: str = ""


@dataclass
class EasyEDAHit:
    """EasyEDA / JLCPCB 搜索命中结果"""

    title: str = ""
    uuid: str = ""  # EasyEDA 内部 ID (搜索阶段可为空)
    description: str = ""
    package: str = ""
    manufacturer: str = ""
    lcsc_part: str = ""  # LCSC 编号 (e.g. "C82899")
    datasheet_url: str = ""
    pin_count: int = 0
    stock: int = 0
    price_range: str = ""  # e.g. "¥1.20~¥3.50"
    category_name: str = ""  # 器件类别
    library_type: str = ""  # "base" 或 "expand"


@dataclass
class EasyEDASymbolResult:
    """EasyEDA 器件符号详情"""

    uuid: str = ""
    title: str = ""
    package: str = ""
    pins: list[EasyEDAPinInfo] = field(default_factory=list)
    symbol_data: dict[str, Any] = field(default_factory=dict)  # 原始符号数据
    footprint_data: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, str] = field(default_factory=dict)


# ============================================================
# 引脚解析
# ============================================================


def parse_easyeda_pins(shapes: list[str]) -> list[EasyEDAPinInfo]:
    """从 EasyEDA shape 列表中提取引脚信息。

    EasyEDA P~ 格式（以 ``~`` 分隔）::

        P~show~electric~pinDotX~pinDotY~pinX~pinY~pinNumber~rotation
        ~ID~locked~pinName~font_size~pinNameDotX~pinNameDotY~gId~clock
        ~pinType~pinDotBX~pinDotBY~nameVisible~numberVisible~spicePin~...

    关键字段索引:
        1: show (0/1)
        2: electric type int
        7: pinNumber
        11: pinName
        17: pinType int

    Returns:
        解析出的引脚列表
    """
    pins: list[EasyEDAPinInfo] = []

    for shape in shapes:
        if not isinstance(shape, str) or not shape.startswith("P~"):
            continue

        parts = shape.split("~")
        # 至少需要 12 个字段才能提取 pinNumber 和 pinName
        if len(parts) < 12:
            continue

        pin_number = parts[7] if len(parts) > 7 else ""
        pin_name = parts[11] if len(parts) > 11 else ""

        # 解析电气类型
        electric_int = _safe_int(parts[2]) if len(parts) > 2 else 0
        electric_type = ELECTRIC_TYPE_MAP.get(electric_int, "passive")

        # 解析引脚类型
        pin_type_int = _safe_int(parts[17]) if len(parts) > 17 else 0
        pin_type = PIN_TYPE_MAP.get(pin_type_int, "passive")

        pins.append(EasyEDAPinInfo(
            number=pin_number,
            name=pin_name,
            pin_type=pin_type,
            electric_type=electric_type,
        ))

    return pins


def _safe_int(value: str) -> int:
    """安全解析整数，失败返回 0。"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


# ============================================================
# JLCPCB 搜索
# ============================================================


def _format_price_range(prices: list[dict[str, Any]]) -> str:
    """从 JLCPCB componentPrices 列表提取价格范围字符串。"""
    if not prices:
        return ""
    all_prices: list[float] = []
    for p in prices:
        val = p.get("productPrice")
        if val is not None:
            try:
                all_prices.append(float(val))
            except (ValueError, TypeError):
                pass
    if not all_prices:
        return ""
    lo, hi = min(all_prices), max(all_prices)
    if lo == hi:
        return f"¥{lo:.2f}"
    return f"¥{lo:.2f}~¥{hi:.2f}"


def search_jlcpcb(keyword: str, limit: int = 10) -> ToolResult:
    """通过 JLCPCB API 搜索器件。

    Args:
        keyword: 搜索关键词 (e.g. "ESP32", "STM32F103")
        limit: 最大返回数

    Returns:
        ToolResult, data 为 list[EasyEDAHit]
    """
    if not keyword or not keyword.strip():
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.INVALID_FORMAT,
            message="搜索关键词不能为空",
        ))

    body = json.dumps({
        "currentPage": 1,
        "pageSize": limit,
        "keyword": keyword.strip(),
        "searchType": 2,
    }).encode("utf-8")

    req = urllib.request.Request(
        JLCPCB_SEARCH_URL,
        data=body,
        method="POST",
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except urllib.error.URLError as exc:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_TIMEOUT,
            message=f"JLCPCB 请求失败: {exc}",
            retriable=True,
        ))
    except json.JSONDecodeError:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_NOT_FOUND,
            message="JLCPCB 返回了非 JSON 响应",
            retriable=True,
        ))
    except Exception as exc:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_TIMEOUT,
            message=f"JLCPCB 请求异常: {exc}",
            retriable=True,
        ))

    # 解析 JLCPCB 响应
    hits = _parse_jlcpcb_response(data, limit)

    if not hits:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_NOT_FOUND,
            message=f"JLCPCB 中未找到器件: {keyword}",
            retriable=False,
        ))

    return ToolResult(success=True, data=hits)


def _parse_jlcpcb_response(
    data: dict[str, Any], limit: int = 10,
) -> list[EasyEDAHit]:
    """解析 JLCPCB 搜索 API 响应为 EasyEDAHit 列表。"""
    hits: list[EasyEDAHit] = []

    # 导航: data.componentPageInfo.list
    resp_data = data.get("data", {})
    if not isinstance(resp_data, dict):
        return hits

    page_info = resp_data.get("componentPageInfo", {})
    if not isinstance(page_info, dict):
        return hits

    items = page_info.get("list", [])
    if not isinstance(items, list):
        return hits

    for item in items[:limit]:
        if not isinstance(item, dict):
            continue

        prices = item.get("componentPrices", [])
        if not isinstance(prices, list):
            prices = []

        stock_raw = item.get("stockCount", 0)
        try:
            stock_val = int(stock_raw)
        except (ValueError, TypeError):
            stock_val = 0

        hit = EasyEDAHit(
            title=str(item.get("componentModelEn", "")),
            description=str(item.get("describe", "")),
            package=str(item.get("componentSpecificationEn", "")),
            manufacturer=str(item.get("componentBrandEn", "")),
            lcsc_part=str(item.get("componentCode", "")),
            datasheet_url=str(item.get("dataManualUrl", "") or ""),
            stock=stock_val,
            price_range=_format_price_range(prices),
            category_name=str(item.get("componentTypeEn", "")),
            library_type=str(item.get("componentLibraryType", "")),
        )
        hits.append(hit)

    return hits


# ============================================================
# 向后兼容入口
# ============================================================


def search_easyeda(part_number: str, limit: int = 10) -> ToolResult:
    """搜索器件 — 向后兼容接口。

    内部委托给 ``search_jlcpcb``。

    Args:
        part_number: 器件型号（如 "TPS54202"）
        limit: 最大返回数

    Returns:
        ToolResult, data 为 list[EasyEDAHit]
    """
    return search_jlcpcb(part_number, limit=limit)


# ============================================================
# EasyEDA 符号详情
# ============================================================


def fetch_easyeda_symbol(lcsc_part: str) -> ToolResult:
    """通过 EasyEDA 产品 API 获取器件符号详情。

    Args:
        lcsc_part: LCSC 编号 (e.g. "C82899")

    Returns:
        ToolResult, data 为 EasyEDASymbolResult
    """
    if not lcsc_part or not lcsc_part.strip():
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.INVALID_FORMAT,
            message="LCSC 编号不能为空",
        ))

    url = EASYEDA_PRODUCT_URL.format(lcsc_id=lcsc_part.strip())
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except urllib.error.URLError as exc:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_TIMEOUT,
            message=f"EasyEDA 产品 API 请求失败: {exc}",
            retriable=True,
        ))
    except json.JSONDecodeError:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_NOT_FOUND,
            message="EasyEDA 返回了非 JSON 响应",
            retriable=True,
        ))
    except Exception as exc:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_TIMEOUT,
            message=f"EasyEDA 请求异常: {exc}",
            retriable=True,
        ))

    # 解析 EasyEDA 产品响应
    result = _parse_easyeda_product(data, lcsc_part)

    if result is None:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_NOT_FOUND,
            message=f"EasyEDA 中未找到器件符号: {lcsc_part}",
        ))

    return ToolResult(success=True, data=result)


def _parse_easyeda_product(
    data: dict[str, Any], lcsc_part: str,
) -> EasyEDASymbolResult | None:
    """解析 EasyEDA 产品 API 响应为 EasyEDASymbolResult。"""
    results = data.get("result")
    if not results:
        return None

    # result 可以是列表或单个对象
    if isinstance(results, list):
        if not results:
            return None
        comp = results[0]
    elif isinstance(results, dict):
        comp = results
    else:
        return None

    if not isinstance(comp, dict):
        return None

    uuid = str(comp.get("uuid", ""))
    title = str(comp.get("title", ""))

    # 解析 dataStr（可能是字符串或 dict）
    data_str = comp.get("dataStr", {})
    if isinstance(data_str, str):
        try:
            data_str = json.loads(data_str)
        except json.JSONDecodeError:
            data_str = {}

    if not isinstance(data_str, dict):
        data_str = {}

    # 从 head.c_para 提取元数据
    head = data_str.get("head", {})
    if not isinstance(head, dict):
        head = {}
    c_para = head.get("c_para", {})
    if not isinstance(c_para, dict):
        c_para = {}

    package = str(c_para.get("package", ""))
    part_name = str(c_para.get("name", title))

    # 解析引脚
    shapes = data_str.get("shape", [])
    if not isinstance(shapes, list):
        shapes = []

    pins = parse_easyeda_pins(shapes)

    # 构建属性字典
    attributes: dict[str, str] = {}
    for k, v in c_para.items():
        attributes[str(k)] = str(v)
    attributes["lcsc_part"] = lcsc_part

    return EasyEDASymbolResult(
        uuid=uuid,
        title=part_name or title,
        package=package,
        pins=pins,
        symbol_data=data_str,
        attributes=attributes,
    )
