"""
华为云 IoT 蜂鸣器控制 MCP Server
提供控制 IoT 设备蜂鸣器的工具
"""
import os
import json

from fastmcp import FastMCP

# 华为云 SDK
from huaweicloudsdkcore.auth.credentials import BasicCredentials, DerivedCredentials
from huaweicloudsdkcore.region.region import Region as coreRegion
from huaweicloudsdkcore.exceptions import exceptions
from huaweicloudsdkiotda.v5 import *

# 初始化 MCP Server
mcp = FastMCP("HuaweiCloud IoT Beep Controller")

def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _require(name: str) -> str:
    v = _env(name)
    if not v:
        raise RuntimeError(f"missing required env var: {name}")
    return v


def _load_config() -> dict[str, str]:
    # Never hardcode credentials in repo. Configure via env vars.
    # Required:
    # - HUAWEICLOUD_AK
    # - HUAWEICLOUD_SK
    # - HUAWEICLOUD_ENDPOINT
    # - HUAWEICLOUD_REGION_ID
    # - HUAWEICLOUD_DEVICE_ID
    return {
        "ak": _require("HUAWEICLOUD_AK"),
        "sk": _require("HUAWEICLOUD_SK"),
        "endpoint": _require("HUAWEICLOUD_ENDPOINT"),
        "region_id": _require("HUAWEICLOUD_REGION_ID"),
        "device_id": _require("HUAWEICLOUD_DEVICE_ID"),
    }


def create_iotda_client() -> IoTDAClient:
    """创建华为云 IoTDA 客户端"""
    cfg = _load_config()
    credentials = BasicCredentials(
        cfg["ak"],
        cfg["sk"],
    ).with_derived_predicate(
        DerivedCredentials.get_default_derived_predicate()
    )
    
    client = IoTDAClient.new_builder() \
        .with_credentials(credentials) \
        .with_region(coreRegion(
            id=cfg["region_id"],
            endpoint=cfg["endpoint"],
        )) \
        .build()
    
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
        # 参数校验
        state_lower = state.strip().lower()
        if state_lower not in ["on", "off"]:
            return f"错误: 无效的状态 '{state}'，请使用 'on' 或 'off'"
        
        # 确定蜂鸣器状态
        is_on = state_lower == "on"
        beep_status = "ON" if is_on else "OFF"
        
        cfg = _load_config()
        client = create_iotda_client()
        
        # 构造命令请求
        request = CreateCommandRequest()
        request.device_id = cfg["device_id"]
        request.body = DeviceCommandRequest(
            paras=json.dumps({"Beep": beep_status})
        )
        
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
            
    except exceptions.ClientRequestException as e:
        error_msg = (
            f"华为云 API 错误:\n"
            f"  状态码: {e.status_code}\n"
            f"  错误码: {e.error_code}\n"
            f"  错误信息: {e.error_msg}"
        )
        return error_msg
    except RuntimeError as e:
        return f"配置错误: {str(e)}"
    except Exception as e:
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
        client = create_iotda_client()
        
        from huaweicloudsdkiotda.v5.model.show_device_request import ShowDeviceRequest
        
        request = ShowDeviceRequest()
        request.device_id = cfg["device_id"]
        
        response = client.show_device(request)
        response_json = json.loads(str(response))
        
        device_name = response_json.get("device_name", "未知")
        status = response_json.get("status", "未知")
        
        return f"设备名称: {device_name}, 状态: {'在线' if status == 'ONLINE' else '离线'}"
    except RuntimeError as e:
        return f"配置错误: {str(e)}"
    except Exception as e:
        return f"查询失败: {str(e)}"


# 启动 MCP Server
if __name__ == "__main__":
    # 使用 SSE 模式，监听 9009 端口
    print("Starting MCP Server on http://localhost:9009/sse", flush=True)
    mcp.run(transport="sse", port=9009)
