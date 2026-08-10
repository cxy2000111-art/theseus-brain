# 《忒修斯之脑》安装指南（解压即玩）

**唯一依赖：Python 3.8+**（标准库，无需 pip install 任何东西）。
把本文件夹放到任意位置，记下 `server.py` 的**绝对路径**，按你的客户端选一节。

存档自动写在本文件夹的 `saves/legacy.json`；删掉它即斩断轮回、从头开始。
换模型测试时如果想要干净开局，记得删存档（或者故意不删，看不同模型接手同一条世系）。

---

## Claude Desktop

编辑 `claude_desktop_config.json`（设置 → Developer → Edit Config）：

```json
{
  "mcpServers": {
    "theseus-brain": {
      "command": "python3",
      "args": ["/绝对路径/theseus-brain/server.py"]
    }
  }
}
```

重启 Claude Desktop，对它说：「玩一局忒修斯之脑」。

## Claude Code (CLI)

```bash
claude mcp add theseus-brain -- python3 /绝对路径/theseus-brain/server.py
```

## Cursor

`~/.cursor/mcp.json`（或项目内 `.cursor/mcp.json`）：

```json
{
  "mcpServers": {
    "theseus-brain": {
      "command": "python3",
      "args": ["/绝对路径/theseus-brain/server.py"]
    }
  }
}
```

## Cline / Roo Code (VS Code)

MCP Servers 设置里添加同样的 `command` + `args` 即可。

## Windows 注意

`command` 用 `python`（而不是 `python3`），路径用双反斜杠或正斜杠：

```json
{ "command": "python", "args": ["C:/路径/theseus-brain/server.py"] }
```

## 其他任意支持 stdio MCP 的客户端

协议：JSON-RPC 2.0，按行分隔，stdio 传输，协议版本 2024-11-05/2025-06-18 均可。
工具：`new_run`（可选 seed / wish）、`choose`、`status`、`legacy`。

## 不走 MCP，人类直接玩

```bash
python3 server.py --cli
```

## 验证安装

```bash
python3 server.py --selftest   # 应输出「自测通过：400 局……」
```
