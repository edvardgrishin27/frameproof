"""Раздача: копии скилла не должны разъезжаться, манифесты должны быть валидны."""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_копии_скилла_совпадают():
    """SKILL.md лежит в двух местах: в пакете (для pip) и в skills/ (для npx skills add).

    Разъехавшись, они дадут разное поведение в разных хостах — и никто этого не заметит,
    пока пользователь не пожалуется, что «в Cursor работает иначе».
    """
    packaged = os.path.join(ROOT, "frameproof", "assets", "skill", "SKILL.md")
    shipped = os.path.join(ROOT, "skills", "frameproof", "SKILL.md")
    with open(packaged, encoding="utf-8") as a, open(shipped, encoding="utf-8") as b:
        assert a.read() == b.read(), (
            "копии SKILL.md разошлись — обновите skills/frameproof/SKILL.md "
            "из frameproof/assets/skill/SKILL.md"
        )


def test_версия_в_манифесте_совпадает_с_пакетом():
    from frameproof import __version__

    with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
        assert json.load(fh)["version"] == __version__


def test_манифесты_валидный_json():
    for name in ("plugin.json", "marketplace.json"):
        with open(os.path.join(ROOT, ".claude-plugin", name), encoding="utf-8") as fh:
            data = json.load(fh)
        assert data.get("name") == "frameproof"


def test_скилл_несёт_обязательный_frontmatter():
    path = os.path.join(ROOT, "frameproof", "assets", "skill", "SKILL.md")
    with open(path, encoding="utf-8") as fh:
        head = fh.read(1200)
    assert head.startswith("---"), "нет frontmatter"
    for field in ("name:", "description:"):
        assert field in head, f"в frontmatter нет {field} — хост не подхватит скилл"


def test_значок_версии_в_readme_совпадает_с_пакетом():
    """Значок версии — четвёртое место, где живёт номер, и оно тоже разъезжалось.

    Случай 0.7.2: пакет поднят, а оба README показывали 0.7.1. Человек на GitHub
    видел одну версию, `pip` ставил другую.
    """
    import re

    from frameproof import __version__

    for name in ("README.md", "README.en.md"):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            m = re.search(r"badge/version-([\d.]+)-", fh.read())
        assert m, f"в {name} нет значка версии"
        assert m.group(1) == __version__, f"{name}: значок {m.group(1)}, пакет {__version__}"
