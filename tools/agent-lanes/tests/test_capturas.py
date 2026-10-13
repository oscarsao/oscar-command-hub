"""Capturas por Telegram: foto/documento del dueño → .state/adjuntos/ → punto de la sesión o tarjeta [CAPTURA]."""
from __future__ import annotations

import logging
import urllib.request

import pytest

from agent_lanes import capturas, sesion
from agent_lanes.telegram import TelegramNotifier
from tests.test_ayuda_y_log import GROUP, send
from tests.test_commands import OSCAR, center

CAPTION = "caption-secreto-XYZ"
TOKEN = "123456:SECRETTOKEN"


@pytest.fixture
def env(tmp_path):
    cc, desk, bot, h, _ = center(tmp_path, {})
    creates, downloads = [], []

    def create(title, body, assignee, *, priority=None, key=None, created_by=None):
        creates.append({"title": title, "body": body, "assignee": assignee, "key": key})
        return "t_cap00001"

    def download_file(file_id, dest, max_bytes):
        downloads.append((file_id, dest, max_bytes))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"img")
        return 3

    h.create = create
    bot.download_file = download_file
    cc._sesiones = sesion.Sesiones(tmp_path / "sesiones", now=lambda: 1_800_000_000.0)
    return cc, desk, bot, creates, downloads, tmp_path


PHOTO = {"photo": [{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 2000}]}


def test_photo_creates_card_with_caption_and_path(env):
    cc, desk, bot, creates, downloads, tmp = env
    send(desk, None, mid=5, caption=CAPTION, **PHOTO)
    assert downloads[0][0] == "big" and downloads[0][1].parent == tmp / "adjuntos"
    assert downloads[0][1].suffix == ".jpg" and downloads[0][2] == 10 * 1024 * 1024
    card = creates[0]
    assert card["title"] == f"[CAPTURA] {CAPTION}" and card["assignee"] == "oscar"
    assert CAPTION in card["body"] and str(downloads[0][1].resolve()) in card["body"]
    assert bot.sent[-1]["text"] == "Recibida, tarjeta <code>t_cap00001</code>"


def test_no_caption_says_sin_texto(env):
    cc, desk, bot, creates, *_ = env
    send(desk, None, mid=5, **PHOTO)
    assert creates[0]["title"] == "[CAPTURA] sin texto" and "sin texto" in creates[0]["body"]


def test_same_update_same_key(env):
    cc, desk, bot, creates, *_ = env
    send(desk, None, mid=5, **PHOTO)
    send(desk, None, mid=5, **PHOTO)
    assert creates[0]["key"] == creates[1]["key"]
    send(desk, None, mid=6, **PHOTO)
    assert creates[2]["key"] != creates[0]["key"]


def test_document_extension_whitelist_and_name_not_used(env):
    cc, desk, bot, creates, downloads, tmp = env
    send(desk, None, mid=1, document={"file_id": "d1", "file_name": "../../evil.PDF", "file_size": 5})
    send(desk, None, mid=2, document={"file_id": "d2", "file_name": "run.exe", "file_size": 5})
    assert downloads[0][1].suffix == ".pdf" and downloads[0][1].parent == tmp / "adjuntos"
    assert "evil" not in downloads[0][1].name
    assert downloads[1][1].suffix == ".bin"


def test_open_session_gets_attachment_instead_of_card(env):
    cc, desk, bot, creates, downloads, tmp = env
    send(desk, "/sesion Revisión", mid=1)
    send(desk, None, mid=2, caption="mira el botón", **PHOTO)
    assert creates == []
    punto = cc.sesiones.current()["puntos"][0]["texto"]
    assert "mira el botón" in punto and str(downloads[0][1].resolve()) in punto
    assert "punto 1" in bot.sent[-1]["text"]


def test_too_big_declared_not_downloaded(env):
    cc, desk, bot, creates, downloads, _ = env
    send(desk, None, mid=1, document={"file_id": "x", "file_name": "a.pdf", "file_size": 11 * 1024 * 1024})
    assert downloads == [] and creates == [] and "10 MB" in bot.sent[-1]["text"]


def test_too_big_during_download_and_failure(env):
    cc, desk, bot, creates, downloads, _ = env
    bot.download_file = lambda *a: (_ for _ in ()).throw(ValueError("archivo demasiado grande"))
    send(desk, None, mid=1, **PHOTO)
    assert "10 MB" in bot.sent[-1]["text"] and creates == []
    bot.download_file = lambda *a: (_ for _ in ()).throw(RuntimeError(f"fallo {TOKEN}"))
    send(desk, None, mid=2, **PHOTO)
    assert "no pude descargarlo" in bot.sent[-1]["text"] and TOKEN not in bot.sent[-1]["text"] and creates == []


def test_non_owner_and_group_and_bot_ignored(env):
    cc, desk, bot, creates, downloads, _ = env
    n = len(bot.sent)
    send(desk, None, mid=1, user=111, chat=111, **PHOTO)
    send(desk, None, mid=2, chat=GROUP, ctype="supergroup", **PHOTO)
    send(desk, None, mid=3, is_bot=True, **PHOTO)
    assert downloads == [] and creates == [] and len(bot.sent) == n


def test_log_has_no_content(env, caplog):
    cc, desk, bot, *_ = env
    with caplog.at_level(logging.INFO, logger="agent_lanes"):
        send(desk, None, mid=1, caption=CAPTION, **PHOTO)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "tipo=adjunto" in text and CAPTION not in text and "big" not in text


# --- descarga real del notificador (urllib simulado) ---------------------------------------------------

class Resp:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        return self.data if n < 0 else self.data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def notifier_with(monkeypatch, file_info, blob):
    n = TelegramNotifier(TOKEN, "1")
    monkeypatch.setattr(n, "_api", lambda method, payload, timeout=None: file_info)
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: Resp(blob))
    return n


def test_download_writes_file(monkeypatch, tmp_path):
    n = notifier_with(monkeypatch, {"file_path": "photos/a.jpg", "file_size": 3}, b"abc")
    dest = tmp_path / "adj" / "a.jpg"
    assert n.download_file("f", dest, 10) == 3 and dest.read_bytes() == b"abc"
    assert list(dest.parent.glob("*.part")) == []


def test_download_rejects_oversize_and_traversal(monkeypatch, tmp_path):
    n = notifier_with(monkeypatch, {"file_path": "photos/a.jpg"}, b"x" * 11)
    with pytest.raises(ValueError):
        n.download_file("f", tmp_path / "a.jpg", 10)
    assert not (tmp_path / "a.jpg").exists() and list(tmp_path.glob("*.part")) == []
    n = notifier_with(monkeypatch, {"file_path": "../x"}, b"x")
    with pytest.raises(RuntimeError):
        n.download_file("f", tmp_path / "b.jpg", 10)


def test_pick_helpers():
    assert capturas.pick({"text": "hola"}) is None
    assert capturas.pick({"photo": []}) is None
    assert not capturas.has_attachment({"text": "x"})
    assert capturas.safe_name({"message_id": "7; rm"}, ".jpg", 0).endswith("-7.jpg")
