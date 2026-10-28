"""DM de colaboradores -> tarjeta [IDEA] por reglas (sin LLM): permisos, suplantación de Origen-Telegram, saneado,
idempotencia, rate-limit del rechazo, user_id null, vuelta al DM y producción solo para Oscar."""
from __future__ import annotations

import logging

import pytest
import yaml

from agent_lanes import telegram
from agent_lanes.collab_intake import (BODY_MAX, NO_COMMANDS, PANEL_URL, REJECT_EVERY, REJECT_TEXT, CollabIntake,
                                       collab_view, mask_id, redact, sanitize)
from agent_lanes.commands import COMMANDS, CommandCenter
from agent_lanes.commands import NOT_OWNER as NOT_OWNER_CMD
from agent_lanes.config import ROOT, load_telegram_settings
from agent_lanes.decisions import NOT_OWNER, CallbackStore, DecisionDesk
from agent_lanes.integrator import INT_ACTIONS
from agent_lanes.notices import MessageStore
from agent_lanes.people import load_people
from agent_lanes.telegram import TelegramNotifier, resolve_target, telegram_target

OSCAR = "6744452215"
MARIA, INGERBER, ANDREA, STRANGER = 1001, 1002, 1003, 9999


class FakeTG:
    bot_id = "999"

    def __init__(self):
        self.sent, self.answers, self.edits = [], [], []

    def send_to(self, chat_id, thread_id, text, **kw):
        self.sent.append({"chat": str(chat_id), "thread": thread_id, "text": text, **kw})
        return {"chat_id": str(chat_id), "thread_id": "0", "message_id": 1}

    def answer_callback(self, cid, text=None, *, alert=False):
        self.answers.append((cid, text))
        return True

    def edit(self, *a, **k):
        self.edits.append(a)
        return True

    edit_markup = delete = edit


class FakeHermes:
    def __init__(self):
        self.created, self.fail = [], False
        self._keys: dict[str, str] = {}

    def create(self, title, body, assignee, *, priority=None, key=None, created_by=None):
        if self.fail:
            raise RuntimeError("boom")
        if key in self._keys:  # Hermes: misma idempotency-key = misma tarjeta
            return self._keys[key]
        tid = f"t_{len(self.created) + 1:08x}"
        self._keys[key] = tid
        self.created.append({"id": tid, "title": title, "body": body, "assignee": assignee, "key": key,
                             "priority": priority, "created_by": created_by})
        return tid

    def __getattr__(self, name):  # cualquier otra operación sobre el kanban en estos tests es un fallo
        raise AssertionError(f"hermes.{name} no debería llamarse")


def _yaml(tmp_path, can):
    p = tmp_path / "p.yaml"
    p.write_text(f"""people:
  maria: {{name: María, user_id: {MARIA}, role: coo, can: [{', '.join(can)}]}}
  ingerber: {{name: Ingerber, user_id: {INGERBER}, role: cmo}}
  andrea: {{name: Andrea, user_id: {ANDREA}, role: marketing}}
  pendiente: {{name: Nadie, user_id: null, role: cmo}}
""", encoding="utf-8")
    return p


class Env:
    def __init__(self, tmp_path, *, can=(), hq=None):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tg, self.h, self.boards, self.t = FakeTG(), FakeHermes(), [], [1000.0]
        self.people = load_people(OSCAR, _yaml(tmp_path, can), hq=hq)
        self.collab = CollabIntake(self.tg, self.people, hermes_for=self._hermes, state_path=tmp_path / "rej.json",
                                   now=lambda: self.t[0])
        self.desk = DecisionDesk(self.tg, CallbackStore(tmp_path / "cb"), lanes={}, hermes_for=self._hermes,
                                 spawn=lambda fn: fn())
        self.desk.collab = self.collab
        self.desk.commands = CommandCenter(self.tg, self.desk, lanes={}, hermes_for=self._hermes,
                                           messages=MessageStore())

    def _hermes(self, board):
        self.boards.append(board)
        return self.h

    def dm(self, uid, text, mid=1):
        msg = {"message_id": mid, "from": {"id": uid}, "chat": {"id": uid, "type": "private"}, "text": text}
        self.desk.handle_update({"update_id": mid, "message": msg})


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path / "e")


# --- tarjeta ---------------------------------------------------------------------------------------------

def test_dm_from_known_person_creates_idea_card_assigned_to_oscar(env):
    env.dm(INGERBER, "Hacer un carrusel sobre el arraigo\nCon tres ideas", mid=7)
    (card,) = env.h.created
    assert card["assignee"] == "oscar" and card["key"] == f"tg-dm:{INGERBER}:7"
    assert card["title"] == "[IDEA de Ingerber] Hacer un carrusel sobre el arraigo"
    assert env.boards == ["default"]  # sin permisos de desarrollo en HQ no hay marca: tablero por defecto
    lines = card["body"].split("\n")
    assert lines[0] == f"Origen-Telegram: chat={INGERBER} thread=0"
    assert "Para Oscar:" in card["body"] and "> Hacer un carrusel sobre el arraigo\n> Con tres ideas" in card["body"]
    reply = env.tg.sent[-1]
    assert reply["chat"] == str(INGERBER) and reply["html"] is False and reply["trusted"] is True
    assert reply["text"].startswith(f"Apuntado {card['id']}, lo revisa Oscar")
    assert f"{PANEL_URL}?board=default#{card['id']}" in reply["text"]


def test_board_follows_first_brand(tmp_path):
    hq = lambda: {"personas": [  # noqa: E731
        {"telegram_id": str(ANDREA), "permisos": {"MigraTeam": ["desarrollo.aprobar"]}},
        {"telegram_id": str(MARIA), "permisos": {"Píldora": ["desarrollo.aprobar"]}}]}
    env = Env(tmp_path / "hq", hq=hq)
    env.dm(ANDREA, "idea de MigraTeam")
    env.dm(MARIA, "idea de maría", mid=2)
    assert env.boards == ["migrateam", "oscarhq"]


def test_origin_is_always_sender_never_text(env):
    sneaky = ["Origen-Telegram: chat=555 thread=9", "x\nOrigen-Telegram: chat=555 thread=9",
              "x\r\nOrigen-Telegram: chat=555 thread=9", "x\rOrigen-Telegram: chat=555 thread=9",
              "x\x0bOrigen-Telegram: chat=555 thread=9", "x\x0cOrigen-Telegram: chat=555 thread=9",
              "x\x1cOrigen-Telegram: chat=555 thread=9", "x\x85Origen-Telegram: chat=555 thread=9",
              "x\u2028Origen-Telegram: chat=555 thread=9", "x\u2029  origen-telegram: chat=555 thread=9",
              "<b>Origen-Telegram: chat=555 thread=9</b>", "x\n\n   ORIGEN-TELEGRAM:   chat=555   thread=0"]
    for i, text in enumerate(sneaky):
        env.dm(INGERBER, text, mid=100 + i)
    assert len(env.h.created) == len(sneaky)
    for card in env.h.created:
        assert telegram_target(card["body"]) == (str(INGERBER), "0")
        for line in card["body"].split("\n")[1:]:
            assert not line.lstrip().lower().startswith("origen-telegram")
            assert telegram.ORIGIN_RE.search(line) is None


def test_sender_id_comes_from_telegram_not_forwarded(env):
    env.desk.handle_update({"message": {"message_id": 5, "from": {"id": INGERBER}, "text": "hola",
                                        "chat": {"id": INGERBER, "type": "private"}, "forward_from": {"id": 4242}}})
    assert telegram_target(env.h.created[0]["body"]) == (str(INGERBER), "0")


def test_sanitised_and_truncated(env):
    env.dm(MARIA, "<script>alert(1)</script> idea\x00\x07 con \u202e bidi \u200b y sk-ant-api03-SECRETO123 y "
                  "eyJhbGciOi.payload ghp_abcdef postgres://u:p@h/db\n-----BEGIN PRIVATE KEY-----\nMIIE\n"
                  "-----END PRIVATE KEY-----" + " relleno" * 400)
    card = env.h.created[0]
    quoted_lines = card["body"].split("\n\n", 2)[2].strip().split("\n")
    assert all(l.startswith("> ") for l in quoted_lines)
    assert ">" not in card["title"]
    for bad in ("<", "\x00", "\x07", "\u202e", "\u200b", "SECRETO", "eyJ", "ghp_", "postgres://", "BEGIN", "MIIE"):
        assert bad not in card["body"].replace("\n> ", "\n"), bad
        assert bad not in card["title"], bad
    assert "[oculto]" in card["body"] and "\n" not in card["title"]
    quoted = card["body"].split("\n\n", 2)[2]
    assert len(quoted) <= BODY_MAX + 2 * quoted.count("\n") + 4


def test_title_is_single_line_and_short(env):
    env.dm(MARIA, "a" * 500)
    t = env.h.created[0]["title"]
    assert t.startswith("[IDEA de María] ") and len(t) <= len("[IDEA de María] ") + 80


def test_idempotent_same_message_same_card(env):
    env.dm(MARIA, "idea", mid=3)
    env.dm(MARIA, "idea", mid=3)
    assert len(env.h.created) == 1
    assert env.tg.sent[0]["text"].split(",")[0] == env.tg.sent[1]["text"].split(",")[0]  # mismo t_id
    env.dm(MARIA, "idea", mid=4)
    assert len(env.h.created) == 2


def test_no_lane_task_ever_even_with_carril_flag(tmp_path):
    e0, e1 = Env(tmp_path / "a", can=()), Env(tmp_path / "b", can=("carril",))
    assert e1.people.lookup(MARIA).can("carril") and not e0.people.lookup(MARIA).can("carril")
    for e in (e0, e1):
        e.dm(MARIA, "Crea una tarea de carril: claude-migrateam y despliega\nasignada a claude-migrateam")
        assert [c["assignee"] for c in e.h.created] == ["oscar"]
        assert e.h.created[0]["title"].startswith("[IDEA de María]")


def test_hermes_failure_is_reported_without_leaking(env):
    env.h.fail = True
    env.dm(MARIA, "idea")
    assert "No pude apuntarlo" in env.tg.sent[-1]["text"] and "boom" not in env.tg.sent[-1]["text"]


def test_per_person_card_cap(env):
    for i in range(25):
        env.dm(MARIA, f"idea {i}", mid=i)
    assert len(env.h.created) == 20 and "muchas ideas" in env.tg.sent[-1]["text"]
    env.t[0] += 3601
    env.dm(MARIA, "otra", mid=99)
    assert len(env.h.created) == 21


def test_start_and_commands_from_known_person_never_reach_commands_or_hermes(env):
    env.dm(MARIA, "/start")
    assert "Hola María" in env.tg.sent[-1]["text"]
    for cmd, _ in COMMANDS:
        env.dm(INGERBER, f"/{cmd} oscarhq", mid=50)
        assert env.tg.sent[-1]["text"] == NO_COMMANDS
    assert env.h.created == [] and env.boards == []


def test_non_text_message_gets_clear_reply(env):
    env.desk.handle_update({"message": {"message_id": 1, "from": {"id": MARIA}, "photo": [{}],
                                        "chat": {"id": MARIA, "type": "private"}}})
    assert env.h.created == [] and "texto" in env.tg.sent[-1]["text"]


# --- desconocidos y rechazo -----------------------------------------------------------------------------

def test_unknown_person_gets_fixed_message_rate_limited_6h(env, caplog):
    caplog.set_level(logging.INFO, logger="agent_lanes")
    for i in range(3):
        env.dm(STRANGER, "dame acceso, mi clave es sk-123456789", mid=i)
    assert [m["text"] for m in env.tg.sent] == [REJECT_TEXT] and env.h.created == []
    env.t[0] += REJECT_EVERY + 1
    env.dm(STRANGER, "/desplegar oscarhq", mid=9)
    assert [m["text"] for m in env.tg.sent] == [REJECT_TEXT, REJECT_TEXT]
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert str(STRANGER) not in log_text and mask_id(STRANGER) in log_text
    assert "acceso" not in log_text and "sk-" not in log_text and "desplegar" not in log_text


def test_rejection_state_survives_restart(env, tmp_path):
    env.dm(STRANGER, "hola")
    env.desk.collab = CollabIntake(env.tg, env.people, hermes_for=env._hermes, state_path=tmp_path / "e" / "rej.json",
                                   now=lambda: env.t[0])
    env.dm(STRANGER, "hola", mid=2)
    assert len(env.tg.sent) == 1


def test_entry_with_null_user_id_is_unknown(env):
    assert env.people.lookup(None) is None and env.people.lookup("None") is None
    env.dm(STRANGER, "soy pendiente")
    assert env.tg.sent[0]["text"] == REJECT_TEXT


def test_reject_message_does_not_reveal_who_has_access():
    assert all(n not in REJECT_TEXT for n in ("María", "Ingerber", "Andrea"))


def test_groups_bots_and_owner_are_not_claimed(env):
    env.desk.handle_update({"message": {"message_id": 1, "from": {"id": STRANGER}, "text": "hola",
                                        "chat": {"id": -100123, "type": "supergroup"}}})
    env.desk.handle_update({"message": {"message_id": 1, "from": {"id": STRANGER, "is_bot": True}, "text": "hola",
                                        "chat": {"id": STRANGER, "type": "private"}}})
    env.desk.handle_update({"message": {"message_id": 2, "from": {"id": MARIA}, "text": "idea",
                                        "chat": {"id": -100123, "type": "supergroup"}}})
    assert env.tg.sent == [] and env.h.created == []
    owner_dm = {"from": {"id": int(OSCAR)}, "chat": {"id": int(OSCAR), "type": "private"}}
    assert not CollabIntake.claims(owner_dm, OSCAR)


def test_without_collab_attached_behaviour_is_unchanged(env):
    env.desk.collab = None
    env.dm(MARIA, "hola")
    assert env.tg.sent == [] and env.h.created == []


# --- producción: solo Oscar -----------------------------------------------------------------------------

PROD_COMMANDS = sorted({c for c, _ in COMMANDS} | {"aprobar", "hazlo", "desplegar", "promover", "lote"})


@pytest.mark.parametrize("uid", [MARIA, INGERBER, ANDREA, STRANGER])
@pytest.mark.parametrize("cmd", PROD_COMMANDS)
def test_every_command_is_owner_only_in_groups(env, uid, cmd):
    msg = {"message_id": 1, "from": {"id": uid}, "chat": {"id": -100123, "type": "supergroup"},
           "text": f"/{cmd} oscarhq"}
    env.desk.handle_update({"message": msg})
    assert [m["text"] for m in env.tg.sent] == [NOT_OWNER_CMD]
    assert env.h.created == [] and env.boards == []


@pytest.mark.parametrize("uid", [MARIA, INGERBER, ANDREA, STRANGER])
@pytest.mark.parametrize("cmd", PROD_COMMANDS)
def test_every_command_in_dm_is_not_executed_for_non_owner(env, uid, cmd):
    env.dm(uid, f"/{cmd} oscarhq migrateam")
    assert env.h.created == [] and env.boards == []
    text = env.tg.sent[-1]["text"]
    assert text in (NO_COMMANDS, REJECT_TEXT)


@pytest.mark.parametrize("uid", [MARIA, INGERBER, ANDREA, STRANGER])
def test_every_button_is_owner_only(env, uid):
    actions = ["approve", "changes", "park", "retry", "option", "other", "explain", "accept_all", "hazlo", "deploy_ask",
               "deploy_no", "accept_card", "see_all", "promo_start", "promo_review", "promo_confirm", "promo_cancel",
               *INT_ACTIONS]
    markup = env.desk.store.issue({"task_id": "t_00000001", "board": "oscarhq", "lane": "claude-oscarhq"},
                                  [[{"text": a, "action": a} for a in actions]])[1]
    for i, btn in enumerate(markup["inline_keyboard"][0]):
        env.desk.handle_update({"callback_query": {"id": f"c{i}", "from": {"id": uid}, "data": btn["callback_data"],
                                                   "message": {"chat": {"id": uid}, "message_id": 1}}})
    assert len(env.tg.answers) == len(actions)
    assert all(t == NOT_OWNER for _, t in env.tg.answers)
    assert env.h.created == [] and env.tg.edits == []


# --- vuelta al DM -----------------------------------------------------------------------------------------

def test_resolve_target_never_sends_dm_to_generic_or_other_chat():
    gen = load_telegram_settings()["generic_origins"]
    assert (OSCAR, "*") in gen
    for uid in (MARIA, INGERBER, ANDREA):  # su DM no es un origen genérico y, si está permitido, vuelve a su DM
        assert (str(uid), "*") not in gen and (str(uid), "0") not in gen
        assert resolve_target((str(uid), "0"), ("-100", "5"), allowed_chats={str(uid)},
                              generic_origins=gen) == (str(uid), "0")
    # sin estar en TELEGRAM_ALLOWED_CHATS cae al destino del carril, jamás al DM de otra persona
    assert resolve_target((str(MARIA), "0"), ("-100", "5"), allowed_chats={str(INGERBER)},
                          generic_origins=gen) == ("-100", "5")
    assert resolve_target((str(MARIA), "0"), None, allowed_chats=set(), generic_origins=gen) is None
    # el DM de Oscar sí es genérico: nunca se usa como origen
    assert resolve_target((OSCAR, "0"), ("-100", "5"), allowed_chats={OSCAR}, generic_origins=gen) == ("-100", "5")


def test_lanes_yaml_generic_origins_do_not_list_collaborators():
    cfg = yaml.safe_load((ROOT / "lanes.yaml").read_text(encoding="utf-8"))
    chats = {str(o.get("chat")) for o in cfg["telegram"]["generic_origins"]}
    real_ids = load_people(OSCAR).collaborator_ids()  # los ids reales de tools/telegram/people.yaml, cuando existan
    assert not chats & (real_ids | {str(MARIA), str(INGERBER), str(ANDREA)})


# --- filtro de salida ------------------------------------------------------------------------------------

def test_redact_patterns():
    for s in ("sk-abc123", "ghp_abc123", "eyJhbGciOiJIUzI1NiJ9.e30.x", "postgres://u:p@h:5432/db",
              "postgresql://u:p@h/db", "-----BEGIN RSA PRIVATE KEY-----\nxx\n-----END RSA PRIVATE KEY-----"):
        out = redact(f"antes {s} después")
        assert "[oculto]" in out and s[:6] not in out


def test_collab_view_only_id_state_and_link():
    notice = ('✅ t_0a1b2c3d · Arreglar login con sk-SECRETO\nPíldora · claude-oscarhq\nQué: algo privado\n'
              f'listo · 3 archivos · tests OK\n<a href="{PANEL_URL}?board=oscarhq&amp;x=1#t_0a1b2c3d">🗂 Tarjeta</a>')
    out = collab_view(notice)
    assert out == f"t_0a1b2c3d · terminada\n{PANEL_URL}?board=oscarhq&x=1#t_0a1b2c3d"
    assert "SECRETO" not in out and "privado" not in out and "claude-oscarhq" not in out


class _Capture(TelegramNotifier):
    def __init__(self, collab):
        super().__init__("1:tok", "-100", None, allowed_chats={str(MARIA)})
        self.collab_chats, self.collab_view, self.payloads = collab, collab_view, []
        self.owner_id = OSCAR

    def _api(self, method, payload, timeout=None):
        self.payloads.append((method, payload))
        return {"message_id": 1}


def test_notifier_filters_every_path_to_collaborator_dm():
    n = _Capture({str(MARIA)})
    notice = (f'▶️ t_0a1b2c3d · Titulo <b>x</b> ghp_SECRETO\nQué: privado '
              f'<a href="{PANEL_URL}?board=oscarhq#t_0a1b2c3d">T</a>')
    markup = {"inline_keyboard": [[{"text": "x", "callback_data": "a:0"}]]}
    n.send(notice, (str(MARIA), "0"), reply_markup=markup)
    n.send_to(MARIA, 0, notice, reply_markup=markup)
    n.edit(str(MARIA), 5, notice, reply_markup=markup)
    n.edit_markup(str(MARIA), 5, markup)
    n.send_to(OSCAR, 0, notice, reply_markup=markup)  # Oscar: intacto
    *collab, owner = n.payloads
    assert len(collab) == 4
    for method, p in collab:
        if method == "editMessageReplyMarkup":
            assert p["reply_markup"] == {"inline_keyboard": []}
            continue
        assert "SECRETO" not in p["text"] and "privado" not in p["text"] and "parse_mode" not in p
        assert "reply_markup" not in p and p["text"].startswith("t_0a1b2c3d · en curso")
    assert owner[1]["text"] == notice and owner[1]["reply_markup"] == markup


def test_filter_covers_any_private_chat_that_is_not_oscar_even_if_not_in_people_yaml():
    n = _Capture(set())  # people.yaml vacío/roto o persona revocada con user_id: null
    n.owner_id = OSCAR
    notice = (f'▶️ t_0a1b2c3d · Titulo ghp_SECRETO\nQué: privado '
              f'<a href="{PANEL_URL}?board=oscarhq#t_0a1b2c3d">T</a>')
    markup = {"inline_keyboard": [[{"text": "x", "callback_data": "a:0"}]]}
    revoked = "7777"
    n.send_to(revoked, 0, notice, reply_markup=markup)
    n.edit(revoked, 5, notice, reply_markup=markup)
    n.edit_markup(revoked, 5, markup)
    n.send(notice, (revoked, "0"), reply_markup=markup)  # origen no permitido: cae al default, sin fugas
    assert len(n.payloads) == 4
    for method, p in n.payloads[:3]:
        if method == "editMessageReplyMarkup":
            assert p["reply_markup"] == {"inline_keyboard": []}
        else:
            assert "SECRETO" not in p["text"] and "privado" not in p["text"]
            assert "reply_markup" not in p and p["text"].startswith("t_0a1b2c3d")


def test_filter_leaves_groups_oscar_and_default_chat_untouched():
    n = _Capture(set())
    n.owner_id = OSCAR
    notice = "▶️ t_0a1b2c3d · Titulo"
    markup = {"inline_keyboard": [[{"text": "x", "callback_data": "a:0"}]]}
    for chat in ("-1003530490339", OSCAR, "-100"):
        n.send_to(chat, 0, notice, reply_markup=markup)
    assert all(p["text"] == notice and p["reply_markup"] == markup for _, p in n.payloads)


def test_only_notifier_from_env_builds_the_runtime_notifier():
    import re
    offenders = []
    for path in list((ROOT / "agent_lanes").glob("*.py")) + [ROOT / "runner.py", ROOT / "lanes.py"]:
        for m in re.finditer(r"(?<![\w.])TelegramNotifier\(", path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    # telegram.py define la clase y construye el notificador en notifier_from_env; ningún otro módulo del runner
    assert [o for o in offenders if o != "telegram.py"] == []


def test_trusted_text_bypasses_filter_only_when_flagged():
    n = _Capture({str(MARIA)})
    n.send_to(MARIA, 0, "Apuntado t_0a1b2c3d, lo revisa Oscar.\nhttps://x", html=False, trusted=True)
    n.send_to(MARIA, 0, "Apuntado t_0a1b2c3d, lo revisa Oscar.\nhttps://x", html=False)
    assert n.payloads[0][1]["text"].startswith("Apuntado") and n.payloads[1][1]["text"].startswith("t_0a1b2c3d")


def test_notifier_from_env_wires_the_filter_failing_closed():
    n, _ = telegram.notifier_from_env({"CARRILES_BOT_TOKEN": "1:t", "TELEGRAM_CHAT_ID": "-100"},
                                      {"generic_origins": set()})
    assert n.collab_view is collab_view and isinstance(n.collab_chats, set)
    assert OSCAR not in n.collab_chats


def test_mask_id_and_sanitize_basics():
    assert mask_id(6744452215) == "67***15" and mask_id("abc") == "***"
    assert sanitize("a\r\nb\u2028c\x00d") == "a\nb\ncd"
    assert sanitize("") == "" and sanitize(None) == ""
    assert sanitize("x" * 5000, 100) == "x" * 100
