from pipeline import normalize


def test_strip_accents():
    assert normalize.strip_accents("Heinäluoma") == "Heinaluoma"
    assert normalize.strip_accents("Šefčovič") == "Sefcovic"
    assert normalize.strip_accents("Çağ") == "Cag"
    assert normalize.strip_accents("MESURE") == "MESURE"


def test_norm_name():
    assert normalize.norm_name("  Marina   MESURE ") == "MARINA MESURE"
    assert normalize.norm_name("Heinäluoma, Eero") == "HEINALUOMA EERO"
    assert normalize.norm_name("O'Reilly-Smith") == "O REILLY SMITH"
    assert normalize.norm_name("") == ""


def test_extract_procedure_refs():
    assert normalize.extract_procedure_refs("2023/0448(COD)") == [("2023/0448(COD)", "2023-0448")]
    # multiple, with junk and duplicates
    text = "Trilogue on 2023/0448(COD); see also 2024/2005(INI) and again 2023/0448(COD)"
    assert normalize.extract_procedure_refs(text) == [
        ("2023/0448(COD)", "2023-0448"),
        ("2024/2005(INI)", "2024-2005"),
    ]
    # tolerant of stray spacing
    assert normalize.extract_procedure_refs("2023 / 0448 ( COD )") == [("2023/0448(COD)", "2023-0448")]
    assert normalize.extract_procedure_refs("no reference here") == []
    assert normalize.extract_procedure_refs("") == []


def test_proc_display_to_key_and_back():
    assert normalize.proc_display_to_key("2023/0448(COD)") == "2023-0448"
    assert normalize.proc_display_to_key("free text") is None
    assert normalize.proc_key_to_display("2023-0448", "COD") == "2023/0448(COD)"
    assert normalize.proc_key_to_display("2023-0448") == "2023/0448"
    assert normalize.proc_key_to_display("garbage") == "garbage"


def test_capacity_bucket():
    assert normalize.capacity_bucket("Shadow rapporteur") == "shadow_rapporteur"
    assert normalize.capacity_bucket("Rapporteur") == "rapporteur"
    assert normalize.capacity_bucket("Committee chair") == "committee_chair"
    assert normalize.capacity_bucket("Member") == "member"
    assert normalize.capacity_bucket("Member Staff meeting") == "other"
    assert normalize.capacity_bucket("Delegation chair") == "other"
    assert normalize.capacity_bucket("") == "other"
    # never confuse shadow with plain rapporteur regardless of case
    assert normalize.capacity_bucket("SHADOW RAPPORTEUR") == "shadow_rapporteur"


def test_clean_org():
    assert normalize.clean_org("  ACME   Corp \n Brussels ") == "ACME Corp Brussels"


def test_meeting_dedupe_key_identity():
    base = {
        "mep_id": "1",
        "mep_name": "A B",
        "date": "2025-01-01",
        "title": "Banking",
        "attendees": "ACME",
        "capacity": "Member",
        "procedure_keys": ["2023-0448"],
    }
    same = dict(base, title="BANKING ", attendees=" acme")  # case/space-insensitive
    other = dict(base, date="2025-01-02")
    assert normalize.meeting_dedupe_key(base) == normalize.meeting_dedupe_key(same)
    assert normalize.meeting_dedupe_key(base) != normalize.meeting_dedupe_key(other)
