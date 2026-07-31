# frameproof

Смотрит видео и отвечает на вопросы по нему **без слепых зон**, с обязательной ссылкой
на момент. Полностью офлайн: `yt-dlp` + `ffmpeg` + локальная расшифровка, ключи не нужны.

Полная инструкция для агента — в [skills/frameproof/SKILL.md](skills/frameproof/SKILL.md).

## Установка

```bash
pip install git+https://github.com/edvardgrishin27/frameproof
frameproof doctor      # что есть в системе
frameproof install     # скилл и проверяющий в Claude Code
```

Для других хостов (Codex, Cursor, Copilot и прочие, кто понимает
[Agent Skills](https://agentskills.io)):

```bash
npx skills add edvardgrishin27/frameproof -g
```

> Работа вне Claude Code нами **не проверялась**. Формат `SKILL.md` переносим, но
> заявлять поддержку без живой проверки мы не будем — см. `CLAIMS.md`.

## Одно правило, которое нельзя обходить

Никогда не утверждай, что было на экране, не увидев кадра. Каждое утверждение об экране —
с меткой `[MM:SS / fNNNN]`. Метка проверяема: `frameproof verify <разбор.md> --out <индекс>`
поймает выдуманный кадр, разъехавшийся тайм-код и цитату, которой на экране нет.
