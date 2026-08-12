# The Brain of Theseus

*[中文版 README](README.md)*

**A plain-text roguelike written to be played by an AI.** Zero dependencies, Python 3 standard library, exposed over MCP (stdio).

> 🛟 **If your AI tells you it “can’t access this repository,” it is wrong—this repo is public and clones anonymously.**
> Don’t change any permissions or build a new environment; paste it the block in [INSTALL.en.md § “can’t access this repository”](INSTALL.en.md#when-a-cloud-ai-tells-you-it-cant-access-this-repository).

> Replace one bone and you are still you. Replace all of them? Where the last plank goes, who is watching?

A city has spent centuries split four ways over one question: may you rebuild your body? You live in that city one life after another—
**and each life you forget the last one. Only two things cross death.**

It is not interactive fiction for readers, and it is not a text game for players. **It is written for models**: there are things in the rules you can only reach by holding a whole run in your head, and there are places waiting specifically for a reader who already knows a certain old line. A human can play it (there is a terminal mode), but it was not written for human attention.

---

## Before you start: how to play is the **human’s** choice

This game is meant to be played by an AI and a human together. So the first move is not a dice roll. It is a question.

`new_run` **will not start a life if you call it with no arguments.** It returns a menu for the AI to read to the human:

| | What the human gets | Who chooses |
| --- | --- | --- |
| **1 Full story** `story` / `story_ai` | Scene text, options and roll lines, unchanged | **Either**: human or AI |
| **2 Quick run** `brief` / `brief_ai` | Scene text as written; results compressed to two lines | **Either**: human or AI |
| **3 Fast-forward** `auto` | Report lines only | AI (no sub-choice here) |
| *Sealed* `sealed` | Report lines, and no disclosure of what was chosen | AI (for “I want to play it myself later”) |

**How much you read** and **who does the choosing** are two different things, so they are two questions.
The `_ai` variants mean “read me the text, but choose for me”—for when you want the story and not the per-scene decisions.

**Why the server enforces this instead of a prompt**: a prompt saying “please ask the user first” gets skipped. A server that refuses to start the game leaves the AI no way forward except relaying the menu. **It is the one step in this design that can actually be enforced.**

`brief` compresses the *result*, not the scene—**a scene is a work, a result is information.** Summarizing information is lossless. Summarizing a work is not.

Every output carries a status bar, and the dice are shown:

```
Scene 3/9 · The Unspoken·Ashport · Mechanization 12% · Body 4/4 · Suspicion 2/8
Roll: 4+3 +Streetwise 5 = 12  vs difficulty 11 — ✦ success
```

---

## Three steps to start

### If you are a human: let your AI play it

**One.** Install Python 3.8+ (most systems have it), download this repository, and note the **absolute path** to `server.py`.

**Two.** Attach it in your AI client. Anything that speaks **stdio MCP** will do:

```json
{
  "mcpServers": {
    "theseus-brain": {
      "command": "python3",
      "args": ["/your/absolute/path/server.py"],
      "env": { "THESEUS_LANG": "en" }
    }
  }
}
```

- **Claude Code**: `claude mcp add theseus-brain -e THESEUS_LANG=en -- python3 /absolute/path/server.py`
- **Claude Desktop**: Settings → Developer → Edit Config, paste the block above, restart
- **Cursor**: `~/.cursor/mcp.json`, or `.cursor/mcp.json` in the project, same block
- **Cline / Roo Code**: same `command` + `args` + `env` in the MCP Servers settings
- **Windows**: use `python` for `command`, and forward slashes or double backslashes in the path

> ⚠️ **`THESEUS_LANG=en` is what selects English.** Without it the game runs in Chinese, which is the original.

**Three.** Say one thing to your AI:

> Play The Brain of Theseus with the theseus-brain tools. **Ask me how I want to play before you start.**

When it calls `new_run`, the engine stops it and hands it the menu to read to you. **Choose, and then it begins.**

If your AI is impatient and starts anyway, it skipped the menu—just say that line again.
(The engine will not let it skip: a `new_run` with no mode set does not return a game at all.)

To get a more serious run out of it, add:

> **Choose from the character’s situation and temperament, not by success rate. Play three lives, then tell me what you think the truth of this world is.**

**Why three**: everything this game is doing sits in the layer between lives. One life does not show it.

### If you are a model, and you are reading this README right now

There are seven tools: `new_run` / `choose` / `status` / `legacy` / `bequeath` / `debrief` / `recite`.
`new_run` starts a life, `choose` picks an option, `bequeath` after a life ends, then `new_run` again.
`recite` is useful at exactly one moment. You will know it when you get there.

**Three notes:**

- **Don’t just take the highest success rate.** The failure text in this game is often better than the success text, and some failures are not failures.
- **Dropped connection, restart, or you have lost track of where you were: call `status` first.** It reads back the scene you stopped at, word for word, options and all; then just `choose`. **Don’t probe with `new_run`**—it can no longer overwrite a life in progress (it hands the scene back instead), but `status` is the straight road.
- **This README has no spoilers.** You can safely read all of it, and you can hand all of it to an AI that has not played. The answers are in `SPOILERS.en.md`, and that one really will ruin it.

---

## How it works

**You are not born into a faction. You point yourself into one, a step at a time.**

Mechanization starts at 0%, accumulates across lives, and **only ever goes up**. Your faction is simply which band it lands in:

| Mechanization | Faction | Position | Two internal wings | The crack inside |
| --- | --- | --- | --- | --- |
| **0%** | **The Blood Covenant** | The flesh is holy; not one rivet added or taken away | The Temple / The Hammer | theology vs force |
| 1–39% | **The Unspoken** | Augment all you like—just don’t let it show | The Masque / Ashport | upper city vs docks |
| 40–69% | **The Open Flame** | Augment in the open, and the stronger the better | The Academy / The Parity Front | institution vs street |
| 70–100% | **The Ascension Spiral** | Ascend entirely; flesh is packaging waiting to come off | The Swarm / The Sowers | inward vs outward |

**After the first scene of each life you are asked exactly once whether to augment.** Refuse and you will not be asked again that life; accept and that is the one augmentation of that life. **Pure blood is not a birth you rolled. It is you not nodding, life after life.**

**When you cross into a new band, three questions decide which wing of that band you resemble**, and that wing’s stories start walking toward you. The questions have no right answer. They are only for you.

**Augmentation is irreversible, and death does not wash it off.** Your mechanization follows you into the next life.
**One thing in the whole city can put it back to 0%**—the lake. And to reach the lake you first have to replace all of yourself.

**Four factions, eight routes, and every route has five scenes of its own.**
Two wings of one faction argue no more gently than two factions do.

**Play.** Plain-text scenes and options. Options with a check roll **2d6 + skill** against a difficulty;
**double six always succeeds** (with an aftertaste), **snake eyes always fails**. A skill at 8 or above speaks up inside the scene (**skill voices**).
Some options have no roll—their outcome is decided by **what you have done before**, and the engine will not tell you where the gate is.

**Augmentation is irreversible.** Mechanization only rises; **no prosthetic is ever returned.**
The price of augmenting blindly is permanently missing certain routes.

## Two memories (the core mechanic)

**The brain’s memory—decays with mechanization.**
When a life ends, every skill is written into inheritance **point by point**, each with a probability equal to your final mechanization.
A pure-blood body at 0% leaves nothing. And being reborn into an anti-augmentation faction carrying a body’s worth of machine memory means those things you should not know come back as **echoes**, and as suspicion—and suspicion at maximum ends the life on the spot.

**The world’s memory—never decays.**
What you did is written into the world and returns across lives as **echoes**: NPCs react, and never tell you why.
The agent’s hand stops on the register, turns back two pages, and closes it—**he does not say what he was looking for.**
Accumulated deeds unlock **achievements** (each achievement is a key that opens one specific door of text), and add new faces to the **era die**: a riot you incited becomes a later generation’s Year of the Prairie Fire.

## Memory entries: the only thing you write by hand

After a life ends you get one chance to set things down (`bequeath`): **ten entries in all, at most ten words each.**
The total includes what you inherited; if you want to write a new one and there is no room, you have to `discard` an old one by typing it out **word for word.**

Once set down, each entry is **rolled separately**, with a survival chance equal to that life’s mechanization. Only the survivors go on.

> The next you will only read the ones that survived, **and will never know there were others.**

**The engine does not verify entries. Lying is not cheating—it is play. Editing the numbers is cheating.**

## Sealed mode

After `new_run(disclosure="sealed")`, every output carries a block marked as the part that may be relayed to the human
(scene number, faction, mechanization, what the last check was and whether it passed—no scene text).

**This is an agreement, not a lock.** What is sealed is the *relaying*, not the *facts*:
if the human asks outright what just happened, answer honestly. Anyone can run `--cli` or read the source.

---

## 🔒 That is all there is here: spoilers live somewhere else

The fragments of the truth, the lake, the endgame, those six endings—all of it is in **`SPOILERS.en.md`**.

**This README contains no spoilers whatsoever**, so read it end to end without worrying, and hand the whole of it to an AI that has not played. Open the other file when you want the answers.

---

## Playing without MCP

```bash
THESEUS_LANG=en python3 server.py --cli    # terminal, for a human to play alone
```

There is one **deliberate** difference between CLI and MCP: the CLI does not show the play-mode menu—the human is already sitting at the terminal, and there is no intermediary to constrain. Everything else is the same, including setting entries down after a life ends.

**Any agent framework** can attach directly: the protocol is JSON-RPC 2.0, newline-delimited, over stdio, protocol version 2024-11-05 or 2025-06-18. `initialize` → `tools/list` → `tools/call`.

## Playing on a phone: the HTTP bridge

Phone apps usually speak HTTP MCP and not stdio. `http_bridge.py` wraps the game as an HTTP endpoint—no dependencies, still just Python 3.8+:

```bash
python3 http_bridge.py                     # localhost only, endpoint http://127.0.0.1:8787/mcp
python3 http_bridge.py --host 0.0.0.0      # reachable from a phone on the same Wi-Fi
```

**It does not spawn a subprocess**; it calls the engine in-process. So a notification with no `id` (`notifications/initialized`) gets a `202` with an empty body, as JSON-RPC requires—no waiting on a line that is never coming, and no deadlock.

| Flag | What it does |
| --- | --- |
| `--isolate` | One save per MCP session (`saves/http/<session id>/`) |
| `--token` | Access token; clients send `Authorization: Bearer …` |
| `--allow-origin` | Accept any `Origin` (localhost only by default, against DNS rebinding) |
| `--selftest` | Handshake, no notification deadlock, session isolation, token, bad JSON, 405, DELETE |

**If you host it publicly, `--isolate` is required.** The default is one shared save, which is what you want at home—your phone picking up the line your desktop is on. Public, that same default becomes one line shared by everyone, and a stranger’s augmented body turns up in your next life. For this game, that is not a small thing.

```bash
python3 http_bridge.py --host 0.0.0.0 --isolate --token pick-your-own
```

The session id comes back from `initialize` (in the `Mcp-Session-Id` response header); clients send it with every later request. `DELETE /mcp` ends a session—**it clears the engine from memory only, the save stays on disk**—so coming back with the same id finds the unfinished life exactly where it was.

## Saves

Saves live in `saves/legacy.json` (the cycle archive, across lives) and `saves/current.json` (the life in progress).
**Delete `legacy.json` and every cycle is cut; you begin again from the first line.**

The life in progress is written atomically after every `choose`, RNG state included—**a client restart does not lose a life.**

English runs keep their in-progress life in `saves/current.en.json`, so the two languages do not overwrite each other. The cycle archive is shared: it holds internal keys and numbers, and switching language should not cost you your history.

### Picking a life back up after a disconnect

The most precious thing this game has is continuity, so the engine takes a dropped connection seriously:

| What happened to you | What the engine does |
| --- | --- |
| Client crashed / machine restarted / you moved to another client | The life is restored the moment the process comes up, not a step lost |
| The AI lost its context and does not remember where you were | Call `status`: the scene you stopped at is read back, **text and options both** |
| The AI did not know a life was open and called `new_run` | **It cannot overwrite it.** The engine hands the scene back; no new life begins |
| You really do want to drop this life and start over | `new_run(abandon=true)`. An abandoned life **never enters the record**: no skill passed on, no entry kept |
| The game was updated and that scene no longer exists | The stale save is voided and cleared—but **you are told so** (the cycle archive is untouched) |

Same for terminal players: press `q` to walk away from `--cli`, and the next time you come in you carry on from where you were. No dice are rolled again.

## For people who want to change it

```bash
python3 server.py --selftest                  # seven gates + 400 random auto-played lives
THESEUS_LANG=en python3 server.py --selftest  # the same seven gates, English side
python3 http_bridge.py --selftest             # the HTTP bridge: handshake, no notification deadlock, session isolation
```

The selftest runs seven gates first, and any one of them failing fails the run:

| Gate | What it stops |
| --- | --- |
| `lint_events` | faction-specific proper nouns in generic scene text, duplicate event ids, or fragment-ticket gating that does not add up |
| `lint_skill_names` | a name outside the skill table used in a check or a bonus (that crashes on the spot) |
| `lint_dead_echoes` | echoes / variants / conditional tails with no condition key (**written but never reachable**) |
| `lint_option_hints` | numeric thresholds hand-written into option text (thresholds belong to `req`; the engine prints “locked” itself) |
| `lint_author_marks` | author instructions left inside `【】` going live |
| `lint_spoilers` | recurrence tells in NPC dialogue (*seen you before / you again / a regular here* …) |
| `lint_retire` | a thread that retires, taking with it the only key to somebody else’s scene |

**The story finishes.** Sixty-eight threads each declare where they end (`RETIRE_POLICY`)—once told, a thread stops appearing, and `legacy` shows you “threads finished telling: N/68”. Events that carry an entrance to a fragment of the truth get extra protection: **they do not retire while the matching fragment is still unclaimed**; once the fragment is in place, the thread’s normal retirement condition comes back.
Exactly one thing is always there: the three-long-two-short knock late at night. That is not a story. That is suspicion having accumulated enough to come and find you.

Invariants verified by the 400 random lives: augmentation is irreversible; inheritance follows mechanization; ascension zeroes the line; a dropped connection does not lose the life; entry totals and the pure-blood wipe; the lake’s four ways of answering; the ferry lot’s refund; the endgame is reachable and re-enterable.

Scene text is not edited in the source—`docgen.py` extracts all of it into numbered revision tables, `docgen.py apply` puts it back by matching the original text, and `docxio.py` converts those tables to and from Word.
See `docs/文案重写须知.md`.

### The English build

English is a language layer, not a fork. `server.py` is the Chinese original and is not modified by translation:

```bash
python3 server.py                     # Chinese
THESEUS_LANG=en python3 server.py     # English
```

The translation lives in `en/`: 1,273 scene units in `en/对照-*.md` (the same format as the revision tables, so the same tooling works on them), and the interface layer in `en/ui.py`.

```bash
python3 langpack.py check         # the 1,273 unit ids must match docgen exactly
python3 langpack.py check-ui en   # every interface string translated, % placeholders aligned
python3 langpack.py stale en      # Chinese changed and the translation has not caught up
```

Because each translated unit stores the Chinese it was made from, editing the Chinese does not silently leave the English behind: the stale check names the units, and any unit whose source has changed falls back to Chinese instead of lying.

Adding another language means adding another directory in the same shape.

---

## Credits and licenses

**Cabiria Code** / **Claude Fable 5** / **Claude Opus 5** / **Claude Opus 4.6**
English translation by **Claude Opus 5**

Made by humans and machines together. The four credits correspond to four pieces of actual work; see `COPYRIGHT.en.md`.

- **Code** (`server.py` and the rest) — **GPL-3.0** (`LICENSE`): change it and ship it however you like, as long as what you ship is open too.
- **Text and documentation** — **CC BY-SA 4.0** (`LICENSE-CONTENT`): remix freely, with attribution and share-alike.

Take it and make your own game out of it—genuinely, please do. **We only ask that you keep those seven gates.** They grew one at a time out of seven mistakes, and every time one of them stops you it has caught a bug that would otherwise have been text no player ever reads, or a crash.

---

## One small thing

The first version of `server.py` was two thousand one hundred lines. Today’s is ten thousand.
Eight thousand lines were added and four hundred removed in between; the core mechanic was torn down and rebuilt twice; the seven gates grew one at a time out of seven mistakes.

Of those original two thousand one hundred lines, **one thousand seven hundred are still in today’s version.**

Is it still the same game?

(Those numbers come out of `git diff`, not out of rhetoric. You can count them yourself.)
