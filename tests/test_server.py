import json

from mcp import Client

from roborock_mcp.server import build_server, manager_from_credentials


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


class FakeV1:
    def __init__(self):
        self.status = FakeStatus()

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
