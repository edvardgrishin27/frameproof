# frameproof

**Your coding agent did not watch that video. It guessed.**

Ask Claude Code to "watch this tutorial" and it samples frames on a scene-change
threshold. On a screencast that threshold cannot fire. A tool may warn that coverage is
sparse, but it will not tell you WHERE the hole is — so the agent cannot tell "few frames"
from "no frames for twenty minutes straight".

Measured on a real 38-minute tutorial, the most popular tool in this niche extracted
**17 frames by default**, with a **20-minute 51-second gap**. `frameproof` extracted 220
with a **14-second** maximum gap — first command, no flags.

The full table, including their best mode where they lead on coverage, is in
[bench/RESULTS.md](bench/RESULTS.md). Hiding it would be less interesting.

```bash
pip install frameproof
frameproof index "https://youtube.com/watch?v=..." --ocr
```

## Why the threshold cannot work

ffmpeg's `scene` filter measures the **mean** delta across the whole frame. Measured on
ffmpeg 8.0.1 with real terminal colours (`#cccccc` on `#1e1e1e`, 640×360):

| what changed on screen | scene score | threshold 0.3 |
|---|---|---|
| one full-width line of text | 0.0579 | no |
| three lines | 0.149 | no |
| half the screen | 0.745 | yes |

Real glyphs cover 10–15 % of a line's area, so a typed command scores around **0.006 —
off by a factor of about 40**. Lowering the threshold does not help: what rescues a
screencast buries a fast-cut video under thousands of frames.

`frameproof` measures the **fraction of changed pixels per grid cell**, calibrated
against each cell's own baseline. A cell that moves constantly — the presenter's
webcam, a running timer, a cursor — is suppressed automatically. A cell that is quiet
most of the time and then changes is an event.

## The guarantee

**No stretch of the timeline is left without a frame for longer than `--max-gap`
seconds** (15 by default). When detectors stay silent, frames are placed on a grid.
Coverage is not a matter of picking a lucky threshold.

And when the guarantee cannot be met, the tool **says so**:

```
покрытие: 97 % — 2 участка без кадров (57 с). НЕ утверждай, что показано на экране в них.
    БЕЗ КАДРА  25:30 – 25:59   (29 с)
```

Silent blindness is worse than an honest "I did not look here".

## Three commands, on purpose

| command | what it does | images |
|---|---|---|
| `index` | builds the index, prints coverage | none |
| `search` | searches speech **and on-screen text** | none |
| `frames` | returns images | yes — the only one |

If search could return pictures, the savings would vanish on the first query. A frame
at 1280×720 costs about 1196 visual tokens; the transcript of an hour is about 50 KB.
Most questions are answered without loading a single image.

```bash
frameproof search "openrouter" --out ~/.frameproof/hermes
# [9:57 / f0050] screen: ... OpenRouter • дешевле напрямую ...

frameproof frames --at 18:38 --out ~/.frameproof/hermes
# [18:38 / f0097] .../frames/f0097.jpg  (1196 токенов)
```

## Two speed tiers

```bash
frameproof index <url> --fast     # 1 second
frameproof index <url>            # 32 seconds, frames land better
```

`--fast` takes candidates from keyframes instead of decoding the whole video.
Measured on a 38-minute tutorial:

| mode | frames | reliable on-screen terms | per frame | time |
|---|---|---|---|---|
| `--fast` | 231 | 672 | 2.9 | **1.1 s** |
| default | 225 | **789** | **3.5** | 32 s |

The fast tier returns 85 % of the information for 3 % of the time. The trade is honest:
frames land where the encoder put a keyframe, not where the thought on screen finished.

The frame budget scales with duration instead of being a constant: a one-minute clip
gets 40, a 38-minute tutorial 231, a three-hour lecture 600.


## A citation you can check

`[18:38 / f0097]` is not decoration. It points at a row of the index, and arithmetic checks it:

```bash
frameproof verify answer.md --out ~/.frameproof/hermes
```

```
✗ [20:00 / f9999] The memory architecture diagram is on screen.
      FAIL  FRAME_NOT_FOUND: no frame f9999 in the index — the reference is invented
✗ [5:00 / f0097] Here he opens the router settings.
      FAIL  TIME_MISMATCH: the tag says 5:00, frame f0097 was taken at 18:38
?  [29:31 / f0160] A list of ten skills is shown.
      WARN  NEVER_OPENED: the frame exists but was never requested —
            the claim was made without looking
```

Six checks, zero model calls: does the frame exist · does the timestamp match · does the
moment fall in a coverage gap · **was the frame ever served to the agent** · does the quoted
string appear in the frame's OCR · does it appear in nearby speech.

## A blind second look

Meaning is beyond arithmetic. For that there is a separate subagent that sees **only the
frame and the claim** — not the user's question, not the author's reasoning, not the rest
of the answer. Its job is to refute.

```bash
frameproof verify answer.md --out <index> --plan   # tasks carrying no context at all
```

It runs **only when explicitly asked**. Refuted claims are **flagged, not hidden**: measured
adversarial panels raise false alarms on up to a third of correct claims, so the call stays
with the human.


## Install

```bash
pip install frameproof          # core
pip install "frameproof[net]"   # + yt-dlp for links
pip install "frameproof[mlx]"   # + fast local transcription on Apple Silicon

frameproof doctor               # check what is available
frameproof install              # install the skill into Claude Code
```

```bash
npx skills add edvardgrishin27/frameproof -g   # Codex, Cursor, Copilot, others
```

> We have **not** verified this outside Claude Code. The `SKILL.md` format is portable and
> the manifests are in place, but we will not claim support we did not test — see [CLAIMS.md](CLAIMS.md).

Requires `ffmpeg`. Everything else is optional and degrades gracefully.
**No API keys, ever.** Subtitles come free from `yt-dlp`; when there are none,
transcription runs locally.

## Kinescope

A Russian video host carrying courses and webinars. `yt-dlp` cannot fetch it: the
extractor request has been open since 2022 and the page returns "Unsupported URL".
Handing it the manifest directly does not help either — it lists the formats but
downloads the wrong thing: 1243 "segments" point at one file through byte ranges, and
the downloader ignores the ranges. Measured on an 82-minute lecture: `yt-dlp` estimated
**96 GiB** for a video that weighs **121 MB**
([yt-dlp#12687](https://github.com/yt-dlp/yt-dlp/issues/12687)).

So the fetch is our own, and it is simpler: the server honours any range asked of it,
so the whole file comes down in a single request.

```bash
frameproof index "https://kinescope.io/embed/<id>" --ocr
```

No keys, no auth. ClearKey-encrypted videos are refused out loud rather than half
downloaded: decryption needs `mp4decrypt` from Bento4, a separate binary we do not ship.

## Off the Mac

Two places grew up on a MacBook: text recognition went through Apple Vision, and local
transcription through mlx-whisper on Apple Silicon. Both doors now open outward, with
the core untouched.

```bash
# your own recognizer: takes image paths, prints "path<TAB>text"
frameproof index video.mp4 --ocr --ocr-command "python ocr_windows.py"

# your own subtitles instead of transcription — .vtt, .srt or .json3
frameproof index video.mp4 --subs speech.srt
```

`--ocr-command` is the same contract the internal Swift binary already speaks, simply
exposed. On Windows 10 and 11 the built-in offline `Windows.Media.Ocr` fits it directly:
no keys, no install.

On resolution. Display frames are scaled down to `--width` (1280 is a token-cost
decision), and small interface text does not survive that: the same frame of a GitHub
page yielded one word at 1280 and full filenames and commit lines at 2560. Recognition
therefore runs on a separate full-resolution copy that is deleted right after, controlled
by `--ocr-width`. What you show stays cheap.

## Use in Claude Code

After `frameproof install`, just ask: *"watch this video and tell me which command he
shows at 4:12"*. The skill enforces one rule the agent cannot skip:

> Never claim what was on screen without having seen a frame. Every statement about
> the screen carries a `[MM:SS / fNNNN]` tag so a human can check it.

## Honest limits

The full list is in [CLAIMS.md](CLAIMS.md). The short version: this tool guarantees
*coverage*, not that no change was ever missed; OCR is for **finding** frames, not for
reading code verbatim; and the benchmark is one video of the class where the gap is
widest.

Russian documentation: [README.ru.md](README.ru.md)

MIT
