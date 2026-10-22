"""MCP server for Roborock vacuums, built on python-roborock."""

import asyncio
import io
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image
from mcp.types import ToolAnnotations
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from roborock import RoborockCommand
from roborock.cli import ConnectionCache
from roborock.devices.device_manager import DeviceManager, UserParams, create_device_manager
from roborock.devices.file_cache import FileCache
from vacuum_map_parser_base.map_data import MapData, Point

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


def label_rooms(png: bytes, map_data: MapData, names: dict[int, str]) -> bytes:
    """Write each room's name on the map, so users know what to pass to start_cleaning."""
    image = PILImage.open(io.BytesIO(png))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)
    for segment, room in map_data.rooms.items():
        # shortcut: label at the bounding-box centre, which can fall outside an L-shaped room
        centre = Point((room.x0 + room.x1) / 2, (room.y0 + room.y1) / 2).to_img(map_data.image.dimensions)
        name = names.get(segment, f"Room {segment}")
        draw.text((centre.x, centre.y), name, font=font, anchor="mm", fill="white", stroke_width=4, stroke_fill="black")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


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
        v1 = (await v1_device(duid)).v1_properties
        map_content = v1.map_content
        await map_content.refresh()
        if not map_content.image_content:
            raise ToolError("The vacuum has no saved map yet. Run one full cleaning to create it.")
        png = map_content.image_content
        if map_content.map_data and map_content.map_data.rooms:
            png = label_rooms(png, map_content.map_data, await room_names(v1))
        return Image(data=png, format="png")

    async def room_names(v1) -> dict[int, str]:
        """Segment id -> room name. Rooms not named in the Roborock app are called "Room <segment id>"."""
        await v1.rooms.refresh()
        if v1.rooms.rooms:
            return {room.segment_id: room.name for room in v1.rooms.rooms}
        await v1.map_content.refresh()
        map_data = v1.map_content.map_data
        return {segment: f"Room {segment}" for segment in sorted(map_data.rooms or {})} if map_data else {}

    control = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

    async def send(duid: str, command: RoborockCommand, done: str) -> str:
        device = await v1_device(duid)
        await device.v1_properties.command.send(command)
        return f"{device.name}: {done}"

    @server.tool(annotations=control)
    async def start_cleaning(duid: str, rooms: list[str] | None = None) -> str:
        """Start cleaning. Without `rooms`: the whole home (or resume a paused cleaning).
        With `rooms`: only those rooms, by the names used in the Roborock app. Get `duid` from list_devices."""
        if not rooms:
            return await send(duid, RoborockCommand.APP_START, "cleaning started.")
        device = await v1_device(duid)
        names = await room_names(device.v1_properties)
        by_name = {name.casefold(): segment for segment, name in names.items()}
        unknown = [name for name in rooms if name.casefold() not in by_name]
        if unknown:
            raise ToolError(f"Unknown room(s): {', '.join(unknown)}. Rooms on this map: {', '.join(names.values())}.")
        segments = [by_name[name.casefold()] for name in rooms]
        await device.v1_properties.command.send(RoborockCommand.APP_SEGMENT_CLEAN, [{"segments": segments}])
        return f"{device.name}: cleaning started in {', '.join(names[s] for s in segments)}."

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
