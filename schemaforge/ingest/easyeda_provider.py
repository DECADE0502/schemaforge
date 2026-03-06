"""EasyEDA 在线器件搜索

通过 EasyEDA 未公开 API 搜索器件信息、获取引脚定义和原理图符号。
参考 JLC2KiCad_lib 的解析逻辑。

注意: EasyEDA API 未公开，可能随时变动。做好降级处理。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from schemaforge.agent.tool_registry import ToolResult
from schemaforge.common.errors import ErrorCode, ToolError


# EasyEDA API 端点
EASYEDA_SEARCH_URL = "https://easyeda.com/api/products/{keyword}/search"
EASYEDA_COMPONENT_URL = "https://easyeda.com/api/components/{uuid}"


@dataclass
class EasyEDAPinInfo:
    """EasyEDA 引脚信息"""

    number: str = ""
    name: str = ""
    pin_type: str = ""  # input, output, power, passive, etc.
    description: str = ""


@dataclass
class EasyEDAHit:
    """EasyEDA 搜索命中结果"""

    title: str = ""
    uuid: str = ""  # EasyEDA 内部 ID
    description: str = ""
    package: str = ""
    manufacturer: str = ""
    lcsc_part: str = ""  # LCSC 编号
    datasheet_url: str = ""
    pin_count: int = 0


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


def search_easyeda(
    part_number: str,
    limit: int = 10,
) -> ToolResult:
    """搜索 EasyEDA 器件库

    Args:
        part_number: 器件型号（如 "TPS54202"）
        limit: 最大返回数

    Returns:
        ToolResult，data 为 list[EasyEDAHit]
    """
    if not part_number or not part_number.strip():
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.INVALID_FORMAT,
            message="搜索关键词不能为空",
        ))

    try:
        import urllib.request
        import urllib.error

        url = EASYEDA_SEARCH_URL.format(keyword=part_number.strip())
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 SchemaForge/1.0",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)

    except urllib.error.URLError as exc:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_TIMEOUT,
            message=f"EasyEDA 请求失败: {exc}",
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

    # 解析搜索结果
    hits: list[EasyEDAHit] = []
    products = data.get("result", data.get("products", []))
    if isinstance(products, dict):
        products = products.get("lists", [])

    for item in products[:limit]:
        if not isinstance(item, dict):
            continue
        hit = EasyEDAHit(
            title=item.get("title", ""),
            uuid=item.get("uuid", item.get("component_uuid", "")),
            description=item.get("description", ""),
            package=item.get("package", ""),
            manufacturer=item.get("manufacturer", ""),
            lcsc_part=item.get("lcsc", item.get("szlcsc", "")),
            datasheet_url=item.get("datasheet", ""),
        )
        hits.append(hit)

    if not hits:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_NOT_FOUND,
            message=f"EasyEDA 中未找到器件: {part_number}",
            retriable=False,
        ))

    return ToolResult(success=True, data=hits)


def fetch_easyeda_symbol(
    easyeda_id: str,
) -> ToolResult:
    """获取 EasyEDA 器件符号详情

    Args:
        easyeda_id: EasyEDA 器件 UUID

    Returns:
        ToolResult，data 为 EasyEDASymbolResult
    """
    if not easyeda_id:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.INVALID_FORMAT,
            message="EasyEDA UUID 不能为空",
        ))

    try:
        import urllib.request
        import urllib.error

        url = EASYEDA_COMPONENT_URL.format(uuid=easyeda_id)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 SchemaForge/1.0",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)

    except Exception as exc:
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_TIMEOUT,
            message=f"EasyEDA 器件详情获取失败: {exc}",
            retriable=True,
        ))

    # 解析器件数据
    comp_data = data.get("result", data)
    if not isinstance(comp_data, dict):
        return ToolResult(success=False, error=ToolError(
            code=ErrorCode.EASYEDA_NOT_FOUND,
            message="EasyEDA 返回了无效的器件数据",
        ))

    result = EasyEDASymbolResult(
        uuid=easyeda_id,
        title=comp_data.get("title", ""),
        package=comp_data.get("package", ""),
    )

    # 解析引脚信息
    pin_data = comp_data.get("dataStr", {})
    if isinstance(pin_data, str):
        try:
            pin_data = json.loads(pin_data)
        except json.JSONDecodeError:
            pin_data = {}

    # EasyEDA 的引脚在 dataStr.shape 中
    shapes = pin_data.get("shape", [])
    if isinstance(shapes, list):
        for shape in shapes:
            if isinstance(shape, str) and shape.startswith("P~"):
                # EasyEDA 引脚格式: P~pinNumber~x~y~rotation~...~pinName~...
                parts = shape.split("~")
                if len(parts) >= 7:
                    pin = EasyEDAPinInfo(
                        number=parts[1] if len(parts) > 1 else "",
                        name=parts[6] if len(parts) > 6 else "",
                    )
                    result.pins.append(pin)

    result.symbol_data = pin_data
    result.attributes = {
        k: str(v) for k, v in comp_data.items()
        if isinstance(v, (str, int, float)) and k not in ("dataStr",)
    }

    return ToolResult(success=True, data=result)
