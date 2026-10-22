# roborock-mcp

MCP server for Roborock vacuums, built on [python-roborock](https://github.com/python-roborock/python-roborock).

## Tools

| Tool | What it does |
|---|---|
| `list_devices` | Devices on your account: name, `duid`, model, online |
| `get_status` | Live status of one device (battery, state, fan speed, errors) |
| `get_map` | Current home map as a PNG image (rooms, walls, dock, robot, last path) |
| `start_cleaning` | Clean the whole home, or only the given `rooms` (names from the Roborock app) |
| `pause_cleaning` | Pause the current cleaning |
| `return_to_dock` | Send the vacuum back to its charging dock |

## Setup

1. Log in once. Roborock sends a code to your email; type it in:

   ```bash
   uv run roborock login --email you@example.com
   ```

   This writes your token to `~/.roborock`. Keep that file private (`chmod 600 ~/.roborock`).

2. Add the server to Claude Code:

   ```bash
   claude mcp add roborock -s user -- uv --directory /home/anilsrml/Desktop/roborock-mcp run roborock-mcp
   ```

## Test

```bash
uv run pytest
```
