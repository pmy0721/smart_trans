"""
LLM + MCP Client (SSE 模式)
通过 SSE 连接到独立的 MCP Server
"""
import asyncio
import os
import json
from typing import List, Dict, Any, Optional, cast
from dotenv import load_dotenv
from openai import AsyncOpenAI
from mcp import ClientSession  # type: ignore
from mcp.client.sse import sse_client  # type: ignore

# 加载环境变量
load_dotenv()

# 初始化 OpenAI 客户端
client = AsyncOpenAI(
    api_key=os.environ.get("SILICONFLOW_API_KEY"),
    base_url=os.environ.get("SILICONFLOW_BASE_URL")
)

# MCP Server SSE 地址
MCP_SSE_URL = "http://localhost:9009/sse"


async def beep_n(
    n: int,
    url: str = MCP_SSE_URL,
    on_time: float = 0.3,
    gap: float = 0.3,
) -> None:
    """Beep N times via MCP `set_beep`.

    Each beep is: on -> sleep(on_time) -> off, then sleep(gap).
    """

    count = max(0, int(n))
    if count <= 0:
        return

    on_time_s = max(0.0, float(on_time))
    gap_s = max(0.0, float(gap))

    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            try:
                for i in range(count):
                    await session.call_tool("set_beep", arguments={"state": "on"})
                    if on_time_s:
                        await asyncio.sleep(on_time_s)
                    await session.call_tool("set_beep", arguments={"state": "off"})
                    if gap_s and i != count - 1:
                        await asyncio.sleep(gap_s)
            finally:
                # Best-effort: ensure beep is off.
                try:
                    await session.call_tool("set_beep", arguments={"state": "off"})
                except Exception:
                    pass


class AlarmAssistant:
    """交通事故报警助手"""
    
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.tools: List[Dict[str, Any]] = []
    
    async def connect_mcp(self):
        """通过 SSE 连接 MCP Server"""
        print(f"🔗 正在连接 MCP Server (SSE): {MCP_SSE_URL}")
        
        async with sse_client(MCP_SSE_URL) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("✅ MCP Server 连接成功")
                
                # 获取工具列表
                mcp_tools = await session.list_tools()
                
                # 转换为 OpenAI function calling 格式
                for tool in mcp_tools.tools:
                    self.tools.append({
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.inputSchema
                        }
                    })
                    print(f"  📦 工具已加载: {tool.name}")
                
                self.session = session
                
                # 保持连接
                while True:
                    await asyncio.sleep(1)
    
    async def handle_alarm(self, message: str) -> str:
        """处理报警请求"""

        if not self.session:
            raise RuntimeError("MCP session not connected")
        
        system_prompt = """你是一个交通事故报警助手。

当用户报告交通事故时，你需要：
1. 理解事故情况
2. 调用 `set_beep` 工具控制警报器进行报警（蜂鸣器只响 1 秒即可）

`set_beep` 工具说明：
- 参数 state: 蜂鸣器状态
  - "on" - 开启警报器
  - "off" - 关闭警报器

请确认报警操作已完成，并向用户反馈结果。"""

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message}
        ]
        
        # 第一次调用 LLM
        response = await client.chat.completions.create(
            model="Pro/deepseek-ai/DeepSeek-V3.2",
            messages=cast(Any, messages),
            tools=cast(Any, self.tools),
        )
        
        assistant_message = response.choices[0].message
        
        # 检查是否需要调用工具
        if assistant_message.tool_calls:
            print(f"🔧 检测到工具调用")
            
            messages.append({
                "role": "assistant",
                "content": assistant_message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    } for tc in assistant_message.tool_calls
                ]
            })
            
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)
                
                print(f"  → 执行: {tool_name}({tool_args})")
                
                try:
                    # One-shot beep: if model turns beep on, auto turn it off after 0.3s.
                    if tool_name == "set_beep" and isinstance(tool_args, dict):
                        state = str(tool_args.get("state", "")).strip().lower()
                        if state == "on":
                            result_on = await self.session.call_tool(tool_name, arguments=tool_args)
                            on_text = result_on.content[0].text if result_on.content else "成功"
                            await asyncio.sleep(0.3)
                            try:
                                result_off = await self.session.call_tool(tool_name, arguments={"state": "off"})
                                off_text = result_off.content[0].text if result_off.content else "成功"
                                tool_result = f"{on_text} (0.3s auto-off) -> {off_text}"
                            except Exception as e_off:
                                tool_result = f"{on_text} (0.3s auto-off failed): {str(e_off)}"
                            print(f"  ✅ 成功: {tool_result}")
                        else:
                            result = await self.session.call_tool(tool_name, arguments=tool_args)
                            tool_result = result.content[0].text if result.content else "成功"
                            print(f"  ✅ 成功: {tool_result}")
                    else:
                        result = await self.session.call_tool(tool_name, arguments=tool_args)
                        tool_result = result.content[0].text if result.content else "成功"
                        print(f"  ✅ 成功: {tool_result}")

                except Exception as e:
                    tool_result = f"失败: {str(e)}"
                    print(f"  ❌ 失败: {tool_result}")
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result
                })
            
            final_response = await client.chat.completions.create(
                model="Pro/deepseek-ai/DeepSeek-V3.2",
                messages=cast(Any, messages),
            )
            
            return final_response.choices[0].message.content or ""
        
        return assistant_message.content or ""


async def main():
    """主函数"""
    assistant = AlarmAssistant()
    
    # 启动 MCP 连接
    mcp_task = asyncio.create_task(assistant.connect_mcp())
    
    # 等待连接
    await asyncio.sleep(3)
    
    if not assistant.session:
        print("❌ MCP Server 连接失败")
        return
    
    print("\n" + "="*60)
    print("🚨 交通事故报警助手已启动")
    print("="*60)
    
    # 测试报警
    test_message = "发现一起交通事故，帮我报警"
    print(f"\n📝 测试: {test_message}")
    
    try:
        response = await assistant.handle_alarm(test_message)
        print(f"\n🤖 助手: {response}")
    except Exception as e:
        print(f"❌ 错误: {str(e)}")
    
    # 保持运行
    print("\n" + "="*60)
    print("按 Ctrl+C 退出")
    print("="*60)
    
    try:
        await mcp_task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 再见!")
