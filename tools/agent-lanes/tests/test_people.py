"""people.py (tools/telegram/people.yaml): falla cerrado, user_id null ignorado, capacidades fijas por rol."""
from __future__ import annotations

from agent_lanes import people
from agent_lanes.people import BASE_CAPS, CARRIL, PRODUCCION, load_people

OWNER = "6744452215"


def _write(tmp_path, text):
    p = tmp_path / "people.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_versioned_people_yaml_is_structurally_valid():
    """Valida la ESTRUCTURA del fichero versionado, no sus ids (Oscar los rellena a mano y esto no debe romper)."""
    import yaml
    entries = yaml.safe_load(people.PEOPLE_EXAMPLE.read_text(encoding="utf-8"))["people"]
    assert entries
    for key, cfg in entries.items():
        assert cfg["role"] in ("coo", "cmo", "marketing", "dev"), key  # nunca owner ni desconocido
        assert set(cfg.get("can") or ()) <= {"carril"}, key
        assert not cfg.get("can") or cfg["role"] == "coo", key
        assert cfg["user_id"] is None or str(cfg["user_id"]).isdigit(), key
    ppl = load_people(OWNER)  # y el lector la acepta tal cual, sea cual sea el número de ids rellenados
    assert ppl.lookup(OWNER).is_owner and OWNER not in ppl.collaborator_ids()
    assert len(ppl) == sum(1 for c in entries.values() if c["user_id"] is not None)


def test_entry_without_user_id_is_ignored(tmp_path):
    p = _write(tmp_path, "people:\n  maria: {user_id: null, role: coo}\n  x: {role: cmo}\n  y: {user_id: '', role: cmo}\n")
    assert len(load_people(OWNER, p)) == 0


def test_valid_entries_and_str_normalisation(tmp_path):
    p = _write(tmp_path, "people:\n  ingerber: {name: Ingerber, user_id: 111, role: cmo, brands: [Píldora]}\n"
                         "  andrea: {user_id: '222', role: marketing}\n")
    ppl = load_people(OWNER, p)
    assert ppl.lookup(111).name == "Ingerber" and ppl.lookup("111").brands == ("Píldora",)
    assert ppl.lookup(222).role == "marketing" and ppl.lookup(333) is None


def test_production_is_never_granted_to_collaborators(tmp_path):
    p = _write(tmp_path, "people:\n  m: {user_id: 1, role: coo, can: [produccion, carril, deploy]}\n"
                         "  i: {user_id: 2, role: cmo, can: [carril, produccion]}\n")
    ppl = load_people(OWNER, p)
    for uid in (1, 2):
        assert not ppl.lookup(uid).can(PRODUCCION)
    assert ppl.lookup(1).can(CARRIL)       # coo puede activar `carril` (reservado)
    assert not ppl.lookup(2).can(CARRIL)   # cmo/marketing nunca
    assert PRODUCCION in BASE_CAPS["owner"]
    assert all(PRODUCCION not in BASE_CAPS[r] for r in ("coo", "cmo", "marketing"))
    assert not ppl.lookup(1).is_owner


def test_carril_off_by_default_for_coo(tmp_path):
    ppl = load_people(OWNER, _write(tmp_path, "people:\n  m: {user_id: 1, role: coo}\n"))
    assert not ppl.lookup(1).can(CARRIL)


def test_owner_cannot_be_declared_or_duplicated(tmp_path):
    p = _write(tmp_path, f"people:\n  a: {{user_id: {OWNER}, role: cmo}}\n  b: {{user_id: 5, role: owner}}\n"
                         "  c: {user_id: 7, role: coo}\n  d: {user_id: 7, role: cmo}\n  e: {user_id: 8, role: root}\n")
    ppl = load_people(OWNER, p)
    assert ppl.collaborator_ids() == {"7"} and ppl.lookup(7).role == "coo"
    assert ppl.lookup(OWNER).is_owner and ppl.lookup(5) is None and ppl.lookup(8) is None


def test_bad_user_ids_are_ignored(tmp_path):
    p = _write(tmp_path, "people:\n  a: {user_id: -5, role: cmo}\n  b: {user_id: true, role: cmo}\n"
                         "  c: {user_id: abc, role: cmo}\n  d: {user_id: 0, role: cmo}\n")
    assert len(load_people(OWNER, p)) == 0


def test_missing_or_broken_file_fails_closed(tmp_path):
    for text in (None, "people: [\n", "- a\n- b\n", "people: 3\n", ""):
        path = tmp_path / "x.yaml"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        ppl = load_people(OWNER, path)
        assert len(ppl) == 0 and ppl.lookup(OWNER).is_owner and ppl.lookup(1) is None


def test_example_file_is_versioned_and_default_is_outside_repo_tracking():
    assert people.PEOPLE_EXAMPLE.name == "people.example.yaml" and people.PEOPLE_EXAMPLE.exists()
    assert people.PEOPLE_DEFAULT.name == "people.yaml" and people.PEOPLE_DEFAULT.parent.name == ".state"


def test_people_file_resolves_env_var_then_default(monkeypatch, tmp_path):
    monkeypatch.setenv(people.PEOPLE_ENV, str(tmp_path / "otro.yaml"))
    assert people.people_file() == tmp_path / "otro.yaml"
    monkeypatch.setenv(people.PEOPLE_ENV, "  ")
    assert people.people_file() == people.PEOPLE_DEFAULT
    monkeypatch.delenv(people.PEOPLE_ENV)
    assert people.people_file() == people.PEOPLE_DEFAULT


def test_load_people_reads_the_path_from_env(monkeypatch, tmp_path):
    """El runner (load_people sin ruta) arranca leyendo el people.yaml de la ruta nueva."""
    real = _write(tmp_path, "people:\n  zoe:\n    name: Zoe\n    user_id: '424242'\n    role: cmo\n")
    monkeypatch.setenv(people.PEOPLE_ENV, str(real))
    ppl = load_people(OWNER)
    assert ppl.lookup("424242").name == "Zoe" and ppl.collaborator_ids() == {"424242"}


def test_missing_real_file_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(people.PEOPLE_ENV, str(tmp_path / "no-existe.yaml"))
    ppl = load_people(OWNER)
    assert len(ppl) == 0 and ppl.lookup(OWNER).is_owner  # solo Oscar; el ejemplo no se usa como respaldo


def test_dev_entries_mauredy_angel_are_scoped_devs():
    """Mauredy (solo MigraTeam, sin id aún) y Angel (OscarHQ/Signal, con id y operador desde el OK final de Oscar del
    22-10): ACOTADOS por marca (`brands` limita lo que ejecutan; ver test_operador_marca.py)."""
    import yaml
    entries = yaml.safe_load(people.PEOPLE_EXAMPLE.read_text(encoding="utf-8"))["people"]
    assert entries["mauredy"]["brands"] == ["MigraTeam"]
    assert "MigraTeam" not in entries["angel"]["brands"] and "Signal" in entries["angel"]["brands"]
    for key in ("mauredy", "angel"):
        assert entries[key]["role"] == "dev" and not entries[key].get("can")
    assert entries["mauredy"]["user_id"] is None and entries["mauredy"].get("operador") is True
    assert entries["angel"]["user_id"] == "5000000004"
    assert entries["angel"].get("operador") is True  # OK final de Oscar 22-10
    ppl = load_people(OWNER)
    assert not any(p.key == "mauredy" for p in ppl._by_id.values())  # sin id = ignorado
    angel = ppl.lookup("5000000004")
    assert angel.key == "angel" and angel.brands == ("Píldora", "Signal", "Hub") and ppl.es_operador("5000000004")


def test_dev_role_has_no_carril_or_production_and_is_not_operator(tmp_path):
    p = _write(tmp_path, "people:\n  m:\n    user_id: '111'\n    role: dev\n    brands: [MigraTeam]\n    can: [carril]\n")
    ppl = load_people(OWNER, p)
    person = ppl.lookup("111")
    assert person.role == "dev" and not person.can(CARRIL) and not person.can(PRODUCCION)
    assert not ppl.es_operador("111") and person.brands == ("MigraTeam",)
