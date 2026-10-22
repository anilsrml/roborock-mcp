"""MCP server for Roborock vacuums, built on python-roborock."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image
from mcp.types import ToolAnnotations
from roborock import RoborockCommand
from roborock.cli import ConnectionCache
from roborock.devices.device_manager import DeviceManager, UserParams, create_device_manager
from roborock.devices.file_cache import FileCache

# Written by `roborock login --email <email>` (python-roborock CLI).
CREDENTIALS_FILE = Path("~/.roborock").expanduser()
# Home data, device IPs and local keys, so restarts do not hit the cloud again.
CACHE_FILE = Path("~/.roborock-mcp.cache").expanduser()

ManagerFactory = Callable[[], Awaitable[DeviceManager]]


def load_user_params(path: Path) -> UserParams:
    login = ConnectionCache.from_dict(json.loads(path.read_text()))
    return UserParams(username=login.email, user_data=login.user_data)


async def manager_from_credentials(path: Path = CREDENTIALS_FILE) -> DeviceManager:
    if not path.is_file():
        raise ToolError(f"No Roborock login found at {path}. Run `uv run roborock login --email <your email>` first.")
    return await create_device_manager(load_user_params(path), cache=FileCache(CACHE_FILE))


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

    async def v1_device(duid: str):
        manager = await get_manager()
        device = await manager.get_device(duid)
        if device is None:
            raise ToolError(f"No device with id {duid}. Call list_devices to see valid ids.")
        if device.v1_properties is None:
            # shortcut: only V1-protocol vacuums (S/Q5/Q7 Max…), add Q10/B01 when we meet one
            raise ToolError(f"{device.name} does not use the V1 protocol; it is not supported yet.")
        await device.v1_properties.start()
        return device

    @server.tool(annotations=read_only)
    async def get_status(duid: str) -> dict:
        """Get the live status of one device: battery, state, fan speed, errors. Get `duid` from list_devices."""
        status = (await v1_device(duid)).v1_properties.status
        await status.refresh()
        return status.as_dict()

    @server.tool(annotations=read_only)
    async def get_map(duid: str) -> Image:
        """Get the current home map of one vacuum as a PNG image: rooms, walls, dock, robot position. Get `duid` from list_devices."""
        map_content = (await v1_device(duid)).v1_properties.map_content
        await map_content.refresh()
        if not map_content.image_content:
            raise ToolError("The vacuum has no saved map yet. Run one full cleaning to create it.")
        return Image(data=map_content.image_content, format="png")

    control = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

    async def send(duid: str, command: RoborockCommand, done: str) -> str:
        device = await v1_device(duid)
        await device.v1_properties.command.send(command)
        return f"{device.name}: {done}"

    @server.tool(annotations=control)
    async def start_cleaning(duid: str) -> str:
        """Start a full-home cleaning (or resume a paused one). Get `duid` from list_devices."""
        return await send(duid, RoborockCommand.APP_START, "cleaning started.")

    @server.tool(annotations=control)
    async def pause_cleaning(duid: str) -> str:
        """Pause the current cleaning; the vacuum stays where it is. Get `duid` from list_devices."""
        return await send(duid, RoborockCommand.APP_PAUSE, "cleaning paused.")

    @server.tool(annotations=control)
    async def return_to_dock(duid: str) -> str:
        """Send the vacuum back to its charging dock. Get `duid` from list_devices."""
        return await send(duid, RoborockCommand.APP_CHARGE, "returning to the dock.")

    return server


def main() -> None:
    build_server(manager_from_credentials).run()
