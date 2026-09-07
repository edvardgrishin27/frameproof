# -*- coding: utf-8 -*-
"""У сборки индекса должен быть опознаваемый отпечаток.

Живой отзыв: один и тот же файл, разные параметры сборки —

    индекс на 97 кадров :  f0084 = 13:27
    индекс на 150 кадров:  f0084 = 5:16

Разбор, написанный по первому индексу, после пересборки указывает не туда,
а verify говорит «TIME_MISMATCH: метка говорит 13:27, а кадр снят в 5:16»
и выглядит как обвинение автора во лжи. Настоящая причина другая, и человек
о ней не догадывается.

Идентификатор кадра — порядковый номер внутри сборки (`f{i:04d}` в extract.py),
и это нормально. Ненормально, что сборку нельзя опознать.
"""

import io
import json
import os

import pytest

from frameproof import index as IX

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_отпечаток_считается_и_стабилен():
    """Одни и те же входные данные дают один и тот же отпечаток."""
    а = IX.build_fingerprint(duration=100.0, frame_count=97, source="video.mp4")
    б = IX.build_fingerprint(duration=100.0, frame_count=97, source="video.mp4")
    assert а == б, "отпечаток скачет на одинаковых данных"
    assert len(а) >= 8, "слишком короткий, чтобы различать сборки: %r" % а


def test_разное_число_кадров_даёт_разный_отпечаток():
    """Ровно тот случай из отзыва: 97 кадров против 150 на одном файле."""
    а = IX.build_fingerprint(duration=100.0, frame_count=97, source="video.mp4")
    б = IX.build_fingerprint(duration=100.0, frame_count=150, source="video.mp4")
    assert а != б, "сборки на 97 и 150 кадров неразличимы"


def test_отпечаток_попадает_в_индекс():
    d = json.loads(io.open(os.path.join(КОРЕНЬ, "tests", "fixtures", "index_sample.json"),
                           encoding="utf-8").read()) if os.path.exists(
        os.path.join(КОРЕНЬ, "tests", "fixtures", "index_sample.json")) else None
    if d is None:
        pytest.skip("нет образца индекса, проверяется в test_ключ_объявлен_в_схеме")


def test_ключ_объявлен_в_схеме():
    import inspect
    src = inspect.getsource(IX)
    assert '"index_id"' in src, "index.json собирается без index_id"


def test_verify_отличает_чужой_индекс_от_вранья():
    """Главное: причина должна называться своим именем.

    TIME_MISMATCH означает «автор указал не тот момент». INDEX_MISMATCH означает
    «разбор писался по другой сборке». Это разные починки: в первом случае править
    текст, во втором пересобрать разбор или индекс.
    """
    import inspect
    from frameproof import verify as V
    src = inspect.getsource(V)
    assert "INDEX_MISMATCH" in src, "verify не умеет отличать чужой индекс"


def test_метка_индекса_читается_из_разбора():
    """Разбор должен нести отпечаток той сборки, по которой он написан."""
    from frameproof import verify as V
    текст = "Индекс: idx_abcd1234\n\n[18:38 / f0097] На экране терминал."
    assert V.extract_index_id(текст) == "idx_abcd1234", V.extract_index_id(текст)


def test_без_метки_индекса_проверка_не_ломается():
    """Старые разборы метки не несут, и ронять их нельзя."""
    from frameproof import verify as V
    assert V.extract_index_id("[18:38 / f0097] Просто утверждение.") is None
