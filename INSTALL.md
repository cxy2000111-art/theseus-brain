# 《忒修斯之脑》安装指南（解压即玩）

*[English](INSTALL.en.md)*

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
工具：`new_run`（可选 seed / wish / abandon）、`choose`、`status`、`legacy`、
`bequeath`、`debrief`、`recite`。

**断线之后**：进行中的一世每次 `choose` 之后就落盘，进程重起会自动捞回来。
重连之后调 `status` 即可拿回停下的那一幕；`new_run` 盖不掉它，
真要弃掉得写明 `new_run(abandon=true)`。

## 只接 HTTP 的客户端（手机 App 等）

```bash
python3 http_bridge.py                  # 端点 http://127.0.0.1:8787/mcp
python3 http_bridge.py --host 0.0.0.0   # 同一个 Wi-Fi 下的手机连得进来
```

零依赖，同一个 Python。客户端的 MCP 设置里填那个 `/mcp` 地址即可。
**默认与桌面共用同一条世系。** 架在公网上给别人玩：

```bash
python3 http_bridge.py --host 0.0.0.0 --isolate --token 你自己想一个
```

`--isolate` 每个会话一份存档（不开则所有人共用一条世系），`--token` 是访问口令。
`python3 http_bridge.py --selftest` 可验证桥本身。

## 挂不上 MCP 的客户端（网页版 ChatGPT 等）：整包丢进去

有些客户端没有 stdio MCP，但有一个能跑 Python 的沙箱。那就不要让它现去
GitHub 抓文件 —— **把这个文件夹打包成 zip，直接上传到对话里**，然后对它说：

> 解压这个包，在沙箱里跑 `python3 server.py --cli`，你来玩，把剧情念给我。
> 每一步都要等我说下一步怎么走。

〔**为什么不建议让它自己去抓**：连接器和沙箱抓 GitHub 时常撞上 **503**
（Service Unavailable，对方服务器临时过载或限流 —— 不是文件不存在，那是 404），
一撞就整条路走不通。自己下载再上传，一步到位，也不吃对方的脸色。〕

沙箱通常**不保留文件**：会话一关，`saves/` 跟着没。想留住轮回，
让它在结束前把 `saves/legacy.json` 的内容打出来／导出成文件，下次连同 zip 一起传回去。

## 不走 MCP，人类直接玩

```bash
python3 server.py --cli
```

按 `q` 走开，下次进来接着走 —— 没走完的那一世还在，不会重新掷骰。

## 验证安装

```bash
python3 server.py --selftest   # 应输出「自测通过：400 局……」
```
