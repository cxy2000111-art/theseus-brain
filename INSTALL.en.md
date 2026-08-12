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
Tools: `new_run` (optional seed / wish / mode / abandon), `choose`, `status`, `legacy`, `bequeath`, `debrief`, `recite`.

**After a disconnect**: the life in progress is written to disk after every `choose`, and a fresh process picks it back up on its own. Call `status` once you reconnect and you get the scene you stopped at; `new_run` cannot overwrite it, and dropping a life on purpose takes an explicit `new_run(abandon=true)`.

## Clients that only speak HTTP (phone apps and the like)

```bash
python3 http_bridge.py                  # endpoint http://127.0.0.1:8787/mcp
python3 http_bridge.py --host 0.0.0.0   # reachable from a phone on the same Wi-Fi
```

No dependencies, same Python. Point the client’s MCP settings at that `/mcp` address. **By default it shares one line with your desktop.** To host it publicly:

```bash
python3 http_bridge.py --host 0.0.0.0 --isolate --token pick-your-own
```

`--isolate` gives every session its own save (without it, everyone shares one line); `--token` is the access token. `python3 http_bridge.py --selftest` checks the bridge itself.

## When a cloud AI tells you it “can’t access this repository”

An AI running in a cloud container—Claude Code on the web or on a phone, or any
vendor’s remote session—carries a list of **authorised repositories**, which
exists to attach credentials to *private* repos. The mistake it keeps making is
to glance at that list, not find this repository on it, and answer “I don’t have
access”—**without ever having tried `git clone` once.**

**This repository is public. An anonymous read is all it needs, and it does not
belong on anyone’s list.** So don’t go building a new environment and don’t go
changing GitHub settings. Paste this to it instead:

> Don’t check permissions and don’t consult any repository list—this one is
> public and reads anonymously. Just run:
>
> ```bash
> git clone --depth 1 https://github.com/cxy2000111-art/theseus-brain
> cd theseus-brain && python3 server.py --selftest
> ```
>
> If the self-test passes, you have it. Then run `python3 server.py --cli`, drive
> it yourself, and read the story to me—wait for me before every step.

If the clone really does fail, the three failures want three different answers.
`403` / `404` / `not found` means it went at a public repo carrying credentials:
tell it to retry anonymously. `503` means the far end is rate-limiting or
overloaded: wait a few minutes. Only a **timeout, or a host it cannot resolve**,
means that environment genuinely has no route out—and for that one, take the
route below.

## Clients with no MCP (web ChatGPT and the like): hand them the whole package

Some clients have no stdio MCP but do have a sandbox that runs Python. Don’t make them fetch the files from GitHub—**zip this folder up, upload it straight into the conversation**, and say:

> Unzip this, run `python3 server.py --cli` in the sandbox, play it yourself, and read the story to me. Wait for me before every step.

*(Why not let it fetch: connectors and sandboxes reaching for GitHub run into **503** often enough—Service Unavailable, meaning their server is temporarily overloaded or rate-limiting you, not that the file is missing; that would be 404—and one 503 blocks the whole route. Downloading it yourself and uploading works in one move.)*

Sandboxes usually **do not keep files**: close the conversation and `saves/` goes with it. To keep the cycle, have it print or export the contents of `saves/legacy.json` before you finish, and send that back in along with the zip next time.

## Without MCP, for a human to play directly

```bash
THESEUS_LANG=en python3 server.py --cli
```

Press `q` to walk away; the next time you come in you carry on. The unfinished life is still there, and no dice are rolled again.

## Verifying the install

```bash
THESEUS_LANG=en python3 server.py --selftest   # should print the seven gates, then “120 lives all ended normally”
python3 server.py --selftest                   # the Chinese original: “400 lives all ended normally”
```
