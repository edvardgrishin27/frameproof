# Social preview для frameproof · система Black Metallic

Обложка репозитория: картинка, которую GitHub отдает в Open Graph, когда ссылку
кидают в Telegram, Slack, X, LinkedIn. Сейчас ее нет, и ссылка выглядит безликим
текстом с иконкой аккаунта.

Ниже два самодостаточных промта для генератора изображений: GPT Image 2, Nano Banana
Pro, Midjourney v7, годится любой, что держит текст. Каждый блок копируется целиком,
склеивать ничего не нужно.

---

## Система в двух словах

Cool-white `#F0F4FA` на обсидиане `#0A0A0A`. Белый **не чистый**, а холодный,
чуть голубоватый: именно он дает металл. Чистый `#FFFFFF` на обсидиане выглядит дешево.

**Liquid Glass** это пять слоев, и все пять прописаны в каждом промте: обсидиан-стекло
с заливкой 4–6 %, хромовая фаска, rim-light 1px по краю, внутренние каустики в толще
стекла, глубокая контактная тень 16 % с блюром 50px.

**Цвета нет, внимание тянет яркость.** Инверсия (белая плита, черный текст) допустима
максимум одна на кадр, и здесь она не нужна. Дальше по убыванию: стекло с каустиками →
cool-white 100 % → 60 % → 35 % (отодвинутое) → хайрлайн 15 %.

**Провал в записи гасим до 35 % и оставляем пустым**, а не красим красным. Пустота
и есть смысл кадра.

Свет ключевой сверху слева под 15°, вид ортографический, без перспективы.

---

## Формат: почему не 16:9

GitHub social preview это **1280×640, соотношение 2:1**. Не 16:9. Кадр вдвое ниже
своей ширины, вертикального места меньше, чем в привычной раскладке, поэтому в обоих
промтах композиция построена по горизонтали и высокого текстового блока нет.

**Практика генерации.** Многие модели не отдают 1280×640 напрямую. Генерируйте в
пропорции 2:1 на большем холсте (2048×1024 или 1536×768) и уменьшайте до 1280×640:
апскейл не нужен, downscale только добавит резкости кромкам стекла.

**Safe zone.** Telegram и X подрезают карточку по краям на 2–4 %. Весь текст держать
внутри центральных 1180×560 px. Плинтус к нижней кромке не прижимать.

---

## Две идеи

| | Идея | Что читается за секунду | Текст на кадре |
|---|---|---|---|
| **A** (основной) | Две полосы времени: у обычного инструмента черная дыра на двадцать минут, у frameproof полоса без разрывов | покрытие против слепой зоны | `frameproof` + `NO BLIND SPOTS. GUARANTEED BY ARITHMETIC.` |
| **B** (запасной) | Одна полоса времени, хромовый штангенциркуль замеряет провал, рядом моноширинный адрес дыры | дыру не просто видно, у нее есть адрес | `frameproof` + `EVERY GAP HAS AN ADDRESS.` |

A показывает результат, B показывает механику. Если A выйдет слишком «инфографично»,
берите B: он тише и сильнее держится на одном объекте.

---

## ПРОМТ A · основной. Полоса с дырой против полосы без дыр

```
2:1 aspect ratio, output 1280x640 (this is the render size, NOT a label drawn on the
frame). This is a wide letterbox banner — it is NOT 16:9 and NOT 3:2. The frame is
exactly half as tall as it is wide, so the composition runs horizontally and there is
no tall stack of text anywhere.

Style: Black Metallic — Apple Liquid Glass x Anthropic editorial x Tesla reveal
restraint. Cool-white on obsidian, architectural precision, quiet confidence.
An open-source developer tool's repository cover, not an advertisement.

BACKGROUND
Pure obsidian void #0A0A0A filling the full frame, with a subtle vignette: top 6% and
bottom 6% crushed to true black #000000, the centre holding a soft #0C0E12 charcoal
wash. A near-invisible cool-white grain overlay at 3% opacity for editorial texture —
not noise, not neon. A faint cool-white schematic grid, 64px cells, 1px stroke, 5%
opacity, sitting behind everything.

LIQUID GLASS TREATMENT (apply to every glass panel described below)
Each glass panel is obsidian-glass with a translucent cool-white fill at 4-6%, a
polished chrome bezel around its edge, a 1px cool-white #F0F4FA rim-light along that
bezel, visible internal caustics that feel alive inside the glass thickness, a bright
1.5px top-edge highlight, and a deep soft contact shadow beneath at 16% opacity with
50px blur. Corner radius 20-32px. Plates rotated no more than 1.5 degrees.

TYPOGRAPHY
Wordmark and headline: SF Pro Display Semibold, cool-white #F0F4FA, letter-spacing
-0.015em at large sizes. All timecodes, ticks and technical readouts: SF Mono, 45%
opacity unless stated otherwise. Everything on this frame is Latin script and digits.

LIGHTING & ATMOSPHERE
Single key light from upper-left at 15 degrees elevation, cool-white #F0F4FA,
soft-boxed. Fill light from lower-right at 8% intensity, purely cool. Rim light on
every glass edge, 1-2px cool-white, no bloom bleed. Cinematic contrast, deep blacks
crushed to zero, highlights held at 92% and never clipping. Straight-on orthographic
view, no perspective distortion. Exposure calm — Apple keynote restraint.

COMPOSITION
Two horizontal timeline bars, stacked one above the other, each spanning roughly 76%
of the frame width and centred horizontally. They are the whole subject of the image.
Between them, a single cool-white hairline at 15% opacity runs the full width as a
divider.

UPPER BAR — the failure, deliberately dimmed and dead. A flat plate at cool-white 35%
opacity with only a 1px top hairline: no glass, no caustics, no rim-light, no chrome.
Along its left third and its right sixth it is filled with a dense row of thin vertical
tick marks in cool-white at 45%, evenly spaced, reading as sampled frames. Across the
whole middle of the bar there are NO ticks at all — a wide empty span of bare obsidian
punched clean through the plate, occupying about 45% of the bar's length. The two
broken ends of the tick field face each other across that void. A thin cool-white
bracket at 35% spans the void from below, with a small SF Mono span label under it.
A short mono label sits above the left end of this bar.

LOWER BAR — the result, the hero. A single wide glass plate with the full Liquid Glass
treatment and pronounced internal caustics travelling along its length. Vertical tick
marks in cool-white #F0F4FA at 100% run the entire bar end to end at an even cadence,
with no interruption anywhere; the ticks catch the key light and read as polished metal
against the glass. Under it, a soft contact shadow. A short mono label sits above the
left end of this bar.

Under both bars, a shared mono timeline scale: small cool-white 45% tick labels at
0:00, 10:00, 20:00, 30:00, 40:00, aligned so that the empty span in the upper bar
visibly covers 20:00 to 40:00.

LEFT MARGIN, vertically centred against the bars: the wordmark set in SF Pro Display
Semibold, cool-white #F0F4FA, lowercase, no icon and no logo mark beside it, with one
line of smaller cool-white type at 60% opacity directly beneath it. Nothing else in
this margin.

BASE RAIL (brand anchor)
A full-width horizontal rail, 1280x52px, seated flush at the bottom of the frame.
Brushed gunmetal with a 1px cool-white top edge. A single centred line in SF Mono
18pt, letter-spacing 0.30em, cool-white #F0F4FA at 55%. To the left of that line, a
small 8px chrome dot glyph. Frame edges otherwise stay clean: no corner marks, no
resolution label, no version tags, no timestamps, no telemetry text, no URLs beyond
the one given.

EXACT TEXT wordmark, left margin: frameproof
EXACT TEXT one line beneath the wordmark: NO BLIND SPOTS. GUARANTEED BY ARITHMETIC.
EXACT TEXT mono label above the upper bar: SCENE-THRESHOLD SAMPLING
EXACT TEXT mono label under the void in the upper bar: NO FRAMES 20:00-40:00
EXACT TEXT mono label above the lower bar: FRAMEPROOF COVERAGE
EXACT TEXT on the base rail: PYTHON · MIT · NO API KEYS

TEXT DISCIPLINE
Those seven strings are the ONLY text in the image. Do not add a subtitle, a call to
action, a star count, a badge row, a language tag or any invented copy. Every glyph is
Latin or a digit; spell each string exactly as written, including the hyphen in the
timecode range. Font priority: SF Pro Display, then Inter, then Manrope, then
Helvetica Neue. Never Arial, never a system fallback.

SAFE ZONE
Keep all type and both bars inside the central 1180x560 px of the frame; social
platforms crop the outer 2-4% of the card.

NEGATIVE / DO NOT
No orange. No amber. No gold. No yellow. No warm tint anywhere. No red — the gap is
communicated by emptiness, never by a warning colour. No neon cyan. No purple. No
magenta. No cyberpunk glow. No green phosphor. No bloom, no light bleed, no lens
flare, no halation. No pure #FFFFFF — the white is cool-white #F0F4FA. No robot face.
No humanoid figure. No brain icon. No neural-network node-web cliche. No eye symbol.
No lightbulb. No magnifying-glass stock icon. No gears as a thinking symbol. No matrix
code rain. No literal source code, no terminal window, no syntax highlighting. No
film-reel or clapperboard cliche. No play-button triangle. No progress-bar UI chrome
with a draggable handle. No thumbnails, no photographs, no video content inside the
bars — the ticks stay abstract. No hexagon grid. No holographic prism rainbow. No
emoji. No stock icons. No GitHub logo, no Octocat, no off-brand logos. No drop shadows
harder than 18% opacity. No gradients other than cool-white to transparent. No skew
beyond 6 degrees. No text warping. No flat stacked cards — the glass must feel
three-dimensional and floating. No lorem ipsum. No 16:9 framing.
```

---

## ПРОМТ B · запасной. У провала есть адрес

Другая идея: не «мы против них», а механика. Инструмент не просто теряет покрытие,
он **называет координаты дыры**. Один объект в кадре, читается еще быстрее.

```
2:1 aspect ratio, output 1280x640 (this is the render size, NOT a label drawn on the
frame). This is a wide letterbox banner — it is NOT 16:9 and NOT 3:2. The frame is
exactly half as tall as it is wide, so the composition runs horizontally and there is
no tall stack of text anywhere.

Style: Black Metallic — Apple Liquid Glass x Anthropic editorial x Tesla reveal
restraint. Cool-white on obsidian, architectural precision, quiet confidence.
An open-source developer tool's repository cover, not an advertisement.

BACKGROUND
Pure obsidian void #0A0A0A filling the full frame, with a subtle vignette: top 6% and
bottom 6% crushed to true black #000000, the centre holding a soft #0C0E12 charcoal
wash. A near-invisible cool-white grain overlay at 3% opacity for editorial texture —
not noise, not neon. A faint cool-white schematic grid, 64px cells, 1px stroke, 5%
opacity, sitting behind everything.

LIQUID GLASS TREATMENT (apply to every glass panel described below)
Each glass panel is obsidian-glass with a translucent cool-white fill at 4-6%, a
polished chrome bezel around its edge, a 1px cool-white #F0F4FA rim-light along that
bezel, visible internal caustics that feel alive inside the glass thickness, a bright
1.5px top-edge highlight, and a deep soft contact shadow beneath at 16% opacity with
50px blur. Corner radius 20-32px. Plates rotated no more than 1.5 degrees.

TYPOGRAPHY
Wordmark and headline: SF Pro Display Semibold, cool-white #F0F4FA, letter-spacing
-0.015em at large sizes. All timecodes, ticks and technical readouts: SF Mono, 45%
opacity unless stated otherwise. Everything on this frame is Latin script and digits.

LIGHTING & ATMOSPHERE
Single key light from upper-left at 15 degrees elevation, cool-white #F0F4FA,
soft-boxed. Fill light from lower-right at 8% intensity, purely cool. Rim light on
every glass edge, 1-2px cool-white, no bloom bleed. Cinematic contrast, deep blacks
crushed to zero, highlights held at 92% and never clipping. Straight-on orthographic
view, no perspective distortion. Exposure calm — Apple keynote restraint.

COMPOSITION
One single horizontal timeline bar, the hero object, spanning about 78% of the frame
width, centred horizontally and sitting slightly above the vertical middle. It is a
long glass plate with the full Liquid Glass treatment, internal caustics travelling
along its length, a polished chrome bezel and a deep contact shadow beneath.

Along the bar, vertical tick marks in cool-white #F0F4FA at 100% run at an even
cadence, catching the key light like polished metal — except in one place. Slightly
right of centre, a narrow span of the bar is empty: the glass there is unlit and
dimmed to cool-white 35%, the ticks stop cleanly at both edges of that span, and the
gap reads as a missing tooth in an otherwise perfect comb. The two tick fields on
either side of the gap are dense and identical.

Directly beneath that empty span, a precision instrument: a slim chrome caliper drawn
in machine-drawing style, its two jaws seated exactly on the two edges of the gap, its
beam a polished chrome bar with fine cool-white graduation marks at 45%. Two thin
cool-white leader lines at 35% rise from the jaws to the bar, showing that the caliper
measures precisely this span and nothing else. The caliper is the second-brightest
object in the frame after the bar itself.

Hanging below the caliper, connected to it by one short vertical hairline at 35%, a
small horizontal glass readout chip with the full Liquid Glass treatment, no wider
than a quarter of the bar. Inside it, one line of SF Mono type in cool-white #F0F4FA
at 100% — a timecode range. This chip is the punchline of the frame and must be
perfectly legible.

Under the bar, a mono timeline scale: small cool-white 45% labels at 0:00, 10:00,
20:00, 30:00, 40:00, aligned so the measured gap falls between 25:00 and 26:00.

LEFT MARGIN, vertically centred against the bar: the wordmark set in SF Pro Display
Semibold, cool-white #F0F4FA, lowercase, no icon and no logo mark beside it, with one
line of smaller cool-white type at 60% opacity directly beneath it. Nothing else in
this margin.

BASE RAIL (brand anchor)
A full-width horizontal rail, 1280x52px, seated flush at the bottom of the frame.
Brushed gunmetal with a 1px cool-white top edge. A single centred line in SF Mono
18pt, letter-spacing 0.30em, cool-white #F0F4FA at 55%. To the left of that line, a
small 8px chrome dot glyph. Frame edges otherwise stay clean: no corner marks, no
resolution label, no version tags, no timestamps, no telemetry text.

EXACT TEXT wordmark, left margin: frameproof
EXACT TEXT one line beneath the wordmark: EVERY GAP HAS AN ADDRESS.
EXACT TEXT inside the glass readout chip: NO FRAME 25:30-25:59
EXACT TEXT on the base rail: PYTHON · MIT · NO API KEYS

TEXT DISCIPLINE
Those four strings are the ONLY text in the image, apart from the five timeline scale
labels. Do not add a subtitle, a call to action, a star count, a badge row or any
invented copy. Every glyph is Latin or a digit; spell each string exactly as written,
including the hyphen and the colons in the timecode range. Font priority: SF Pro
Display, then Inter, then Manrope, then Helvetica Neue. Never Arial, never a system
fallback.

SAFE ZONE
Keep all type, the bar and the caliper inside the central 1180x560 px of the frame;
social platforms crop the outer 2-4% of the card.

NEGATIVE / DO NOT
No orange. No amber. No gold. No yellow. No warm tint anywhere. No red — the gap is
communicated by emptiness and by measurement, never by a warning colour. No neon cyan.
No purple. No magenta. No cyberpunk glow. No green phosphor. No bloom, no light bleed,
no lens flare, no halation. No pure #FFFFFF — the white is cool-white #F0F4FA. No
robot face. No humanoid figure. No brain icon. No neural-network node-web cliche. No
eye symbol. No lightbulb. No magnifying-glass stock icon. No gears as a thinking
symbol. No matrix code rain. No literal source code, no terminal window, no syntax
highlighting. No film-reel or clapperboard cliche. No play-button triangle. No
thumbnails, no photographs, no video content inside the bar — the ticks stay abstract.
No hexagon grid. No holographic prism rainbow. No emoji. No stock icons. No GitHub
logo, no Octocat, no off-brand logos. No drop shadows harder than 18% opacity. No
gradients other than cool-white to transparent. No skew beyond 6 degrees. No text
warping. No flat stacked cards — the glass must feel three-dimensional and floating.
No lorem ipsum. No 16:9 framing.
```

---

## Приемка кадра

Прежде чем грузить на GitHub:

- [ ] Пропорция ровно 2:1, итоговый файл 1280×640.
- [ ] Дыра читается **за секунду**, без чтения подписей. Прищурьтесь: если провал
      исчезает, значит кадр не сработал, надо перегенерировать.
- [ ] Ни одного цветного пикселя. Красного нет нигде.
- [ ] Белый холодный `#F0F4FA`, а не `#FFFFFF`. Проверить пипеткой по самой яркой
      кромке.
- [ ] Слово `frameproof` написано без ошибок и строчными.
- [ ] Тайм-коды не поехали: дефис на месте, двоеточия на месте.
- [ ] Генератор не дорисовал лишний текст, бейджи и «звезды».
- [ ] Открыть на телефоне в превью Telegram: подписи мельче 18pt на 1280px читаться
      не будут.

## Куда грузить

1. Репозиторий на GitHub → вкладка **Settings** (нужны права владельца или админа).
2. Раздел **General**, блок **Social preview**, примерно треть страницы вниз.
3. Кнопка **Edit** → **Upload an image**.
4. Выбрать файл, дождаться превью, страница сохраняет сама, отдельной кнопки Save нет.

Требования GitHub: PNG, JPG или GIF; рекомендуемый размер **1280×640**, минимум
640×320; вес до **1 МБ**. ⚠️ Проверить перед использованием: лимиты GitHub меняются;
если файл не принимается, пересохранить в JPEG с качеством 85.

**Проверка результата.** Кэш Open Graph у мессенджеров живет долго. Прогнать ссылку
через `https://www.opengraph.xyz/` или кинуть ее самому себе в «Избранное» в Telegram.
Если показывается старая пустая карточка, добавить к ссылке `?v=2`, чтобы обойти кэш.

**Файл держать в репозитории**, чтобы обложку можно было пересобрать:
`docs/social-preview.png` плюс этот промт рядом.
