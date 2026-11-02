# roborock-mcp

MCP server for Roborock vacuums, built on [python-roborock](https://github.com/python-roborock/python-roborock).
Ask your AI assistant for the vacuum's status, a labelled map of your home, or to clean only the kitchen.

Unofficial project, not affiliated with Roborock.

<!-- mcp-name: io.github.anilsrml/roborock-mcp -->

## Tools

| Tool | What it does |
|---|---|
| `list_devices` | Devices on your account: name, `duid`, model, online |
| `get_status` | Live status of one device (battery, state, fan speed, errors) |
| `get_map` | Current home map as a PNG image, with each room's name written on it |
| `start_cleaning` | Clean the whole home, or only the given `rooms` (names from the Roborock app; unnamed rooms are `Room <number>`) |
| `pause_cleaning` | Pause the current cleaning |
| `return_to_dock` | Send the vacuum back to its charging dock |

Supported: vacuums that use Roborock's V1 protocol (S, Q5, Q7 Max series and similar).
Other models show up in `list_devices` but the other tools return "not supported yet".

## Setup

Needs [uv](https://docs.astral.sh/uv/) and git. uv downloads Python 3.12+ if you do not have it.

1. Log in once. Roborock sends a code to your email; type it in:

   ```bash
   uvx --from git+https://github.com/anilsrml/roborock-mcp roborock login --email you@example.com
   ```

   This writes your token to `~/.roborock`. Keep that file private (`chmod 600 ~/.roborock`).

2. Add the server to your MCP client.

   Claude Code:

   ```bash
   claude mcp add roborock -s user -- uvx --from git+https://github.com/anilsrml/roborock-mcp roborock-mcp
   ```

   Claude Desktop, Cursor and other clients (JSON config):

   ```json
   {
     "mcpServers": {
       "roborock": {
         "command": "uvx",
         "args": ["--from", "git+https://github.com/anilsrml/roborock-mcp", "roborock-mcp"]
       }
     }
   }
   ```

## Development

```bash
git clone https://github.com/anilsrml/roborock-mcp.git
cd roborock-mcp
uv run pytest
```

## License

MIT
