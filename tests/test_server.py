import base64
import io
import json

import pytest
from mcp import Client

from roborock.cli import ConnectionCache, RoborockContext
from roborock.data import Reference, RRiot, UserData

from roborock_mcp.server import build_server, load_user_params, manager_from_credentials


async def test_list_devices_without_login_tells_user_to_login(tmp_path):
    server = build_server(lambda: manager_from_credentials(tmp_path / "missing"))

    async with Client(server) as client:
        result = await client.call_tool("list_devices", {})

    assert result.is_error
    assert "roborock login" in result.content[0].text


class FakeStatus:
    async def refresh(self):
        self.battery = 87

    def as_dict(self):
        return {"battery": self.battery, "state": "charging"}


PNG = b"\x89PNG\r\n\x1a\nfake-map"


class FakeMapContent:
    image_content = None
    map_data = None

    async def refresh(self):
        self.image_content = PNG


class FakeCommand:
    def __init__(self):
        self.sent = []
        self.params = []

    async def send(self, command, params=None):
        self.sent.append(command)
        self.params.append(params)


class FakeRooms:
    rooms = None

    async def refresh(self):
        from types import SimpleNamespace

        self.rooms = [SimpleNamespace(segment_id=16, name="Salon"), SimpleNamespace(segment_id=17, name="Mutfak")]


class FakeV1:
    def __init__(self):
        self.status = FakeStatus()
        self.map_content = FakeMapContent()
        self.command = FakeCommand()
        self.rooms = FakeRooms()

    async def start(self):
        pass


class FakeManager:
    """Stands in for python-roborock's DeviceManager, which talks to the Roborock cloud."""

    def __init__(self, devices):
        self._devices = {d.duid: d for d in devices}

    async def get_devices(self):
        return list(self._devices.values())

    async def get_device(self, duid):
        return self._devices.get(duid)


def fake_vacuum():
    from types import SimpleNamespace

    return SimpleNamespace(
        name="Salon",
        duid="abc123",
        product=SimpleNamespace(model="roborock.vacuum.a15"),
        device_info=SimpleNamespace(online=True),
        v1_properties=FakeV1(),
    )


def server_with(devices):
    async def get_manager():
        return FakeManager(devices)

    return build_server(get_manager)


async def test_list_devices_returns_name_id_model_online():
    async with Client(server_with([fake_vacuum()])) as client:
        result = await client.call_tool("list_devices", {})

    assert not result.is_error
    assert result.structured_content["result"] == [
        {"name": "Salon", "duid": "abc123", "model": "roborock.vacuum.a15", "online": True}
    ]


async def test_get_status_returns_fresh_status():
    async with Client(server_with([fake_vacuum()])) as client:
        result = await client.call_tool("get_status", {"duid": "abc123"})

    assert not result.is_error
    assert json.loads(result.content[0].text) == {"battery": 87, "state": "charging"}


async def test_get_status_unknown_device_is_error():
    async with Client(server_with([fake_vacuum()])) as client:
        result = await client.call_tool("get_status", {"duid": "nope"})

    assert result.is_error
    assert "list_devices" in result.content[0].text


async def test_manager_is_created_once_and_reused():
    calls = []

    async def get_manager():
        calls.append(1)
        return FakeManager([fake_vacuum()])

    async with Client(build_server(get_manager)) as client:
        await client.call_tool("list_devices", {})
        await client.call_tool("get_status", {"duid": "abc123"})

    assert len(calls) == 1


def test_load_user_params_reads_file_written_by_roborock_login(tmp_path, monkeypatch):
    path = tmp_path / ".roborock"
    monkeypatch.setattr(RoborockContext, "roborock_file", path)
    user_data = UserData(rriot=RRiot(u="u", s="s", h="h", k="k", r=Reference(a="https://api")), rruid="r1")
    RoborockContext().update(ConnectionCache(user_data=user_data, email="me@example.com"))

    params = load_user_params(path)

    assert params.username == "me@example.com"
    assert params.user_data == user_data


async def test_get_map_returns_png_image():
    async with Client(server_with([fake_vacuum()])) as client:
        result = await client.call_tool("get_map", {"duid": "abc123"})

    assert not result.is_error
    image = result.content[0]
    assert image.type == "image"
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data) == PNG


async def test_get_map_without_saved_map_is_error():
    vacuum = fake_vacuum()

    async def no_map():
        pass

    vacuum.v1_properties.map_content.refresh = no_map

    async with Client(server_with([vacuum])) as client:
        result = await client.call_tool("get_map", {"duid": "abc123"})

    assert result.is_error
    assert "no saved map" in result.content[0].text


@pytest.mark.parametrize(
    ("tool", "command"),
    [("start_cleaning", "app_start"), ("pause_cleaning", "app_pause"), ("return_to_dock", "app_charge")],
)
async def test_control_tool_sends_command_to_vacuum(tool, command):
    vacuum = fake_vacuum()

    async with Client(server_with([vacuum])) as client:
        result = await client.call_tool(tool, {"duid": "abc123"})

    assert not result.is_error
    assert vacuum.v1_properties.command.sent == [command]
    assert "Salon" in result.content[0].text


async def test_start_cleaning_with_rooms_cleans_only_those_rooms():
    vacuum = fake_vacuum()

    async with Client(server_with([vacuum])) as client:
        result = await client.call_tool("start_cleaning", {"duid": "abc123", "rooms": ["salon", "Mutfak"]})

    assert not result.is_error
    assert vacuum.v1_properties.command.sent == ["app_segment_clean"]
    assert vacuum.v1_properties.command.params == [[{"segments": [16, 17]}]]
    assert "Salon, Mutfak" in result.content[0].text


async def test_start_cleaning_unknown_room_is_error_and_sends_nothing():
    vacuum = fake_vacuum()

    async with Client(server_with([vacuum])) as client:
        result = await client.call_tool("start_cleaning", {"duid": "abc123", "rooms": ["Banyo"]})

    assert result.is_error
    assert "Banyo" in result.content[0].text
    assert "Salon, Mutfak" in result.content[0].text
    assert vacuum.v1_properties.command.sent == []


def unnamed_rooms_vacuum():
    """Like the real Q7 Max: the device has no room names, the map has segments 16 and 17."""
    from types import SimpleNamespace

    vacuum = fake_vacuum()

    class EmptyRooms:
        rooms = []

        async def refresh(self):
            pass

    vacuum.v1_properties.rooms = EmptyRooms()
    vacuum.v1_properties.map_content.map_data = SimpleNamespace(rooms={17: object(), 16: object()})
    return vacuum


async def test_start_cleaning_unnamed_rooms_use_room_number_names():
    vacuum = unnamed_rooms_vacuum()

    async with Client(server_with([vacuum])) as client:
        result = await client.call_tool("start_cleaning", {"duid": "abc123", "rooms": ["room 16"]})

    assert not result.is_error
    assert vacuum.v1_properties.command.params == [[{"segments": [16]}]]
    assert "Room 16" in result.content[0].text


async def test_start_cleaning_unnamed_unknown_room_lists_room_numbers():
    vacuum = unnamed_rooms_vacuum()

    async with Client(server_with([vacuum])) as client:
        result = await client.call_tool("start_cleaning", {"duid": "abc123", "rooms": ["Salon"]})

    assert result.is_error
    assert "Room 16, Room 17" in result.content[0].text
    assert vacuum.v1_properties.command.sent == []


async def test_get_map_draws_room_names_on_the_image():
    from types import SimpleNamespace

    from PIL import Image as PILImage
    from vacuum_map_parser_base.map_data import ImageDimensions, Room

    blank = io.BytesIO()
    PILImage.new("RGB", (100, 100), "blue").save(blank, "PNG")
    vacuum = unnamed_rooms_vacuum()
    map_content = vacuum.v1_properties.map_content
    map_content.map_data = SimpleNamespace(
        rooms={16: Room(0, 0, 100, 100, 16)},
        image=SimpleNamespace(dimensions=ImageDimensions(0, 0, 100, 100, 1, 0, lambda p: p)),
    )

    async def refresh():
        map_content.image_content = blank.getvalue()

    map_content.refresh = refresh

    async with Client(server_with([vacuum])) as client:
        result = await client.call_tool("get_map", {"duid": "abc123"})

    assert not result.is_error
    labelled = PILImage.open(io.BytesIO(base64.b64decode(result.content[0].data)))
    assert labelled.size == (100, 100)
    assert len(labelled.getcolors(maxcolors=100 * 100)) > 1
