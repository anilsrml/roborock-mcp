import base64
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

    async def refresh(self):
        self.image_content = PNG


class FakeCommand:
    def __init__(self):
        self.sent = []

    async def send(self, command, params=None):
        self.sent.append(command)


class FakeV1:
    def __init__(self):
        self.status = FakeStatus()
        self.map_content = FakeMapContent()
        self.command = FakeCommand()

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
