"""MCP server for Roborock vacuums, built on python-roborock."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from roborock.data import UserData
from roborock.devices.device_manager import DeviceManager, UserParams, create_device_manager
from roborock.devices.file_cache import FileCache

# Written by `roborock login --email <email>` (python-roborock CLI).
CREDENTIALS_FILE = Path("~/.roborock").expanduser()
# Home data, device IPs and local keys, so restarts do not hit the cloud again.
CACHE_FILE = Path("~/.roborock-mcp.cache").expanduser()

ManagerFactory = Callable[[], Awaitable[DeviceManager]]


async def manager_from_credentials(path: Path = CREDENTIALS_FILE) -> DeviceManager:
    if not path.is_file():
        raise ToolError(f"No Roborock login found at {path}. Run `uv run roborock login --email <your email>` first.")
    data = json.loads(path.read_text())
    params = UserParams(username=data["email"], user_data=UserData.from_dict(data["user_data"]))
    return await create_device_manager(params, cache=FileCache(CACHE_FILE))


def build_server(create_manager: ManagerFactory) -> MCPServer:
    server = MCPServer("roborock")
    read_only = ToolAnnotations(readOnlyHint=True)
    lock = asyncio.Lock()
    manager: DeviceManager | None = None

    async def get_manager() -> DeviceManager:
        # One manager per server: the Roborock cloud allows only 5 home-data requests per hour.
        nonlocal manager
        async with lock:
            if manager is None:
                manager = await create_manager()
            return manager

    @server.tool(annotations=read_only)
    async def list_devices() -> list[dict]:
        """List the Roborock devices on your account: name, device id, model, online."""
        manager = await get_manager()
        return [
            {"name": d.name, "duid": d.duid, "model": d.product.model, "online": d.device_info.online}
            for d in await manager.get_devices()
        ]

    @server.tool(annotations=read_only)
    async def get_status(duid: str) -> dict:
        """Get the live status of one device: battery, state, fan speed, errors. Get `duid` from list_devices."""
        manager = await get_manager()
        device = await manager.get_device(duid)
        if device is None:
            raise ToolError(f"No device with id {duid}. Call list_devices to see valid ids.")
        if device.v1_properties is None:
            # shortcut: only V1-protocol vacuums (S/Q5/Q7 Max…), add Q10/B01 when we meet one
            raise ToolError(f"{device.name} does not use the V1 protocol; status is not supported yet.")
        await device.v1_properties.start()
        await device.v1_properties.status.refresh()
        return device.v1_properties.status.as_dict()

    return server


def main() -> None:
    build_server(manager_from_credentials).run()
