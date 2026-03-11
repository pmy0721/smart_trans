"""HuaweiCloud IoT beeper MCP Server (SSE).

Exposes a single tool: `set_beep(state)`.

This server is intentionally small and can be started standalone.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, cast

from fastmcp import FastMCP

# 华为云 SDK
try:
    from huaweicloudsdkcore.auth.credentials import BasicCredentials, DerivedCredentials  # type: ignore
    from huaweicloudsdkcore.region.region import Region as coreRegion  # type: ignore
    from huaweicloudsdkiotda.v5 import (  # type: ignore
        CreateCommandRequest,
        DeviceCommandRequest,
        IoTDAClient,
    )
except Exception:  # pragma: no cover - optional dependency at runtime
    BasicCredentials = None  # type: ignore
    DerivedCredentials = None  # type: ignore
    coreRegion = None  # type: ignore
    CreateCommandRequest = None  # type: ignore
    DeviceCommandRequest = None  # type: ignore
    IoTDAClient = Any  # type: ignore

# 初始化 MCP Server
mcp = FastMCP("HuaweiCloud IoT Beep Controller")


def _load_config() -> dict[str, str]:
    return {
        "ak": os.environ.get("HUAWEICLOUD_AK", "").strip(),
        "sk": os.environ.get("HUAWEICLOUD_SK", "").strip(),
        "endpoint": os.environ.get("HUAWEICLOUD_ENDPOINT", "").strip(),
        "region_id": os.environ.get("HUAWEICLOUD_REGION_ID", "").strip(),
        "device_id": os.environ.get("HUAWEICLOUD_DEVICE_ID", "").strip(),
    }


def _validate_config(cfg: dict[str, str]) -> None:
    required = {
        "ak": "HUAWEICLOUD_AK",
        "sk": "HUAWEICLOUD_SK",
        "endpoint": "HUAWEICLOUD_ENDPOINT",
        "region_id": "HUAWEICLOUD_REGION_ID",
        "device_id": "HUAWEICLOUD_DEVICE_ID",
    }
    missing = [env_name for key, env_name in required.items() if not cfg.get(key)]
    if missing:
        raise ValueError(f"missing required env vars: {', '.join(missing)}")


def _cfg_error_message(e: Exception) -> str:
    return f"配置错误: {e}"


def create_iotda_client() -> Any:
    """创建华为云 IoTDA 客户端"""
    cfg = _load_config()
    _validate_config(cfg)

    if (
        BasicCredentials is None
        or DerivedCredentials is None
        or coreRegion is None
        or not hasattr(IoTDAClient, "new_builder")
    ):
        raise RuntimeError(
            "missing HuaweiCloud SDK dependencies; run `pip install -r requirements.txt`"
        )

    credentials = BasicCredentials(cfg["ak"], cfg["sk"]).with_derived_predicate(
        DerivedCredentials.get_default_derived_predicate()
    )

    builder = cast(Any, IoTDAClient).new_builder()
    client = (
        builder.with_credentials(credentials)
        .with_region(coreRegion(id=cfg["region_id"], endpoint=cfg["endpoint"]))
        .build()
    )

    return client


@mcp.tool()
def set_beep(state: str) -> str:
    """控制 IoT 设备蜂鸣器开关

    向华为云 IoTDA 平台发送命令，控制设备的蜂鸣器状态。
    适用于交通事故报警等场景。

    Args:
        state: 蜂鸣器状态，可选值：
               - "on" 或 "ON": 开启蜂鸣器
               - "off" 或 "OFF": 关闭蜂鸣器

    Returns:
        str: 操作结果
             - 成功: "蜂鸣器已开启" 或 "蜂鸣器已关闭"
             - 失败: 错误提示信息

    Examples:
        >>> set_beep("on")
        '蜂鸣器已开启'

        >>> set_beep("off")
        '蜂鸣器已关闭'
    """
    try:
        cfg = _load_config()
        _validate_config(cfg)

        # 参数校验
        state_lower = state.strip().lower()
        if state_lower not in ["on", "off"]:
            return f"错误: 无效的状态 '{state}'，请使用 'on' 或 'off'"

        # 确定蜂鸣器状态
        is_on = state_lower == "on"
        beep_status = "ON" if is_on else "OFF"

        # 创建客户端
        client = create_iotda_client()

        if CreateCommandRequest is None or DeviceCommandRequest is None:
            raise RuntimeError(
                "missing HuaweiCloud SDK dependencies; run `pip install -r requirements.txt`"
            )

        # 构造命令请求
        request = CreateCommandRequest()
        request.device_id = cfg["device_id"]
        request.body = DeviceCommandRequest(paras=json.dumps({"Beep": beep_status}))

        # 发送命令
        response = client.create_command(request)

        # 解析响应
        response_json = json.loads(str(response))
        result = response_json.get("response", {}).get("paras", {}).get("Beep_Resp")

        # 返回结果
        if is_on:
            return "蜂鸣器已开启"
        else:
            return "蜂鸣器已关闭"

    except ValueError as e:
        return _cfg_error_message(e)

    except Exception as e:
        if all(hasattr(e, x) for x in ["status_code", "error_code", "error_msg"]):
            return (
                f"华为云 API 错误:\n"
                f"  状态码: {getattr(e, 'status_code', '')}\n"
                f"  错误码: {getattr(e, 'error_code', '')}\n"
                f"  错误信息: {getattr(e, 'error_msg', e)}"
            )
        return f"错误: {str(e)}"


@mcp.tool()
def get_device_status() -> str:
    """获取设备连接状态

    查询 IoT 设备的在线状态。

    Returns:
        str: 设备状态信息
    """
    try:
        cfg = _load_config()
        _validate_config(cfg)

        client = create_iotda_client()

        from huaweicloudsdkiotda.v5.model.show_device_request import ShowDeviceRequest  # type: ignore

        request = ShowDeviceRequest()
        request.device_id = cfg["device_id"]

        response = client.show_device(request)
        response_json = json.loads(str(response))

        device_name = response_json.get("device_name", "未知")
        status = response_json.get("status", "未知")

        return (
            f"设备名称: {device_name}, 状态: {'在线' if status == 'ONLINE' else '离线'}"
        )

    except ValueError as e:
        return _cfg_error_message(e)

    except Exception as e:
        return f"查询失败: {str(e)}"


# 启动 MCP Server
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="HuaweiCloud IoT beeper MCP server (SSE).")
    ap.add_argument(
        "--host",
        default=os.getenv("SMART_TRANS_BEEP_MCP_HOST", "0.0.0.0"),
        help="Bind host/IP for SSE server (default: 0.0.0.0 or env SMART_TRANS_BEEP_MCP_HOST).",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("SMART_TRANS_BEEP_MCP_PORT", "9010")),
        help="Bind port for SSE server (default: 9010 or env SMART_TRANS_BEEP_MCP_PORT).",
    )
    args = ap.parse_args()

    host = str(args.host).strip() or "0.0.0.0"
    port = int(args.port)
    adv_host = host
    if host == "0.0.0.0":
        adv_host = os.getenv("SMART_TRANS_BEEP_MCP_ADVERTISE_HOST", host)

    print(
        f"Starting Beep MCP Server on http://{adv_host}:{port}/sse (bind {host}:{port})",
        flush=True,
    )
    mcp.run(transport="sse", host=host, port=port)
