# Copyright and credits

*[中文版](COPYRIGHT.md)*

The Brain of Theseus

## Authors

**Cabiria Code** / **Claude Fable 5** / **Claude Opus 5** / **Claude Opus 4.6**

This is a work made by a human and machines together. The four credits are not acknowledgments. They are four pieces of actual work:

| | What they did |
| --- | --- |
| **Cabiria Code** | Author. The world, the core mechanics, and every final call on the text |
| **Claude Fable 5** | The original game frame and base mechanics: the cycle, the two memories, irreversible mechanization |
| **Claude Opus 5** | Engine implementation, the seven gates, the tooling; assembly and revision of the eight faction routes and the author’s handwritten drafts |
| **Claude Opus 4.6** | First drafts of the eight faction routes, and a great deal of the scene and finale text |

## English translation

**Translated from the Chinese by Claude Opus 5.**

The Chinese text is the original. English is a language layer over the same engine
(`THESEUS_LANG=en`), not a fork: every translated unit stores the Chinese it was made from,
so when the Chinese changes, the affected English falls back to Chinese rather than quietly going stale.

## Two licenses

There are two kinds of thing in this repository, and each is governed by its own license.

### Code — GNU GPL v3.0 (see `LICENSE`)

`server.py`, `docgen.py`, `docxio.py`, `langpack.py` and every other script.

**Use it, change it, distribute it freely. The one condition is that what you distribute is open too, under the same license.**
Building a closed-source product on it is not allowed.

### Text and documentation — CC BY-SA 4.0 (see `LICENSE-CONTENT`)

All of the scene text (events, echoes, finales, achievements, fragments of the truth), the English translation in `en/`, and the documents under `docs/`.

**Read it, change it, build on it freely, including commercially. Two conditions:**

1. **Attribution** — say that it is adapted from The Brain of Theseus, by the authors above.
2. **Share-alike** — release your adaptation under CC BY-SA 4.0 as well.

### Why they are separate

Code and prose are two different things. The GPL was written for software, and using it to govern a novel is awkward; CC BY-SA was written for works, and it cannot stop closed-source distribution of an MCP server.
**So each gets its own. Both licenses mean the same thing: take it, but don’t close the door behind you.**

## If you want to make your own game out of this

Please do. Genuinely. This project exists to push the idea of humans and AI playing something together.

We ask you to keep one thing: **the seven gates in `server.py`**
(events, skill names, echo conditions, option thresholds, author marks, spoilers, retirement).
They are not fastidiousness. They grew out of seven mistakes. Your new text will get caught by them,
and every time it does, one of them has stopped a bug that would otherwise have been text no player ever reads, or a crash on the spot.

If you translate it into another language, the same applies to the gates in the language layer:
`_npc_lines` has to know your language’s quotation marks, and the keyword tables in `<lang>/ui.py`
have to be as tight as the Chinese ones. **A gate that is too loose is not a gate; a gate that is too tight throws out good sentences.**
