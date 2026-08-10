# The Brain of Theseus — install (unzip and play)

*[中文版](INSTALL.md)*

**One dependency: Python 3.8+** (standard library; nothing to `pip install`).
Put this folder anywhere, note the **absolute path** to `server.py`, and follow the section for your client.

> ⚠️ **`THESEUS_LANG=en` is what selects English.** Leave it out and the game runs in Chinese, which is the original.

Saves are written into this folder at `saves/legacy.json`; delete it to cut every cycle and start over.
If you are testing a different model and want a clean start, remember to delete the save—or deliberately don’t, and watch a different model take over the same line.

---

## Claude Desktop

Edit `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "theseus-brain": {
      "command": "python3",
      "args": ["/absolute/path/theseus-brain/server.py"],
      "env": { "THESEUS_LANG": "en" }
    }
  }
}
```

Restart Claude Desktop and say: “play a run of The Brain of Theseus.”

## Claude Code (CLI)

```bash
claude mcp add theseus-brain -e THESEUS_LANG=en -- python3 /absolute/path/theseus-brain/server.py
```

## Cursor

`~/.cursor/mcp.json` (or `.cursor/mcp.json` in the project):

```json
{
  "mcpServers": {
    "theseus-brain": {
      "command": "python3",
      "args": ["/absolute/path/theseus-brain/server.py"],
      "env": { "THESEUS_LANG": "en" }
    }
  }
}
```

## Cline / Roo Code (VS Code)

Add the same `command` + `args` + `env` in the MCP Servers settings.

## A note for Windows

Use `python` for `command` (not `python3`), and double backslashes or forward slashes in the path:

```json
{ "command": "python", "args": ["C:/path/theseus-brain/server.py"], "env": { "THESEUS_LANG": "en" } }
```

## Any other client that speaks stdio MCP

Protocol: JSON-RPC 2.0, newline-delimited, over stdio, protocol version 2024-11-05 or 2025-06-18.
Tools: `new_run` (optional seed / wish / mode), `choose`, `status`, `legacy`, `bequeath`, `debrief`, `recite`.

## Without MCP, for a human to play directly

```bash
THESEUS_LANG=en python3 server.py --cli
```

## Verifying the install

```bash
THESEUS_LANG=en python3 server.py --selftest   # should print the seven gates, then “120 lives all ended normally”
python3 server.py --selftest                   # the Chinese original: “400 lives all ended normally”
```
