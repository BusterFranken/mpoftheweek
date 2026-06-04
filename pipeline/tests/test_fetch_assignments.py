from pipeline import fetch_assignments


def _participation(**kw):
    base = {
        "id": "eli/dl/participation/x",
        "type": "Participation",
        "activity_date": "2024-09-19",
        "had_participant_person": ["person/256891"],
        "parliamentary_term": "org/ep-10",
        "participation_role": "def/ep-roles/RAPPORTEUR_SHADOW",
        "politicalGroup": "org/RENEW",
    }
    base.update(kw)
    return base


PROC = {
    "id": "eli/dl/proc/2023-0448",
    "process_id": "2023-0448",
    "process_type": "def/ep-procedure-types/COD",
    "label": "2023/0448(COD)",
    "process_title": {"en": "Protection of animals during transport", "fr": "..." },
    "had_participation": [
        _participation(),
        _participation(  # opinion shadow in name of PECH
            had_participant_person=["person/214839"],
            participation_role="def/ep-roles/RAPPORTEUR_SHADOW_OPINION",
            participation_in_name_of="org/PECH",
            activity_date="2024-11-28",
        ),
        _participation(  # previous-term appointment must be excluded
            had_participant_person=["person/99999"],
            parliamentary_term="org/ep-9",
        ),
        _participation(  # plain rapporteur is not a shadow assignment
            had_participant_person=["person/12345"],
            participation_role="def/ep-roles/RAPPORTEUR_CO",
        ),
        {  # lead committee (organisation participation)
            "id": "eli/dl/participation/2023-0448-MAIN-AGRI",
            "type": "Participation",
            "had_participant_organization": ["org/AGRI"],
            "participation_role": "def/ep-roles/COMMITTEE_LEAD",
        },
    ],
}


def test_extract_assignments_roles_term_and_committee():
    rows = fetch_assignments._extract_assignments(PROC)
    assert len(rows) == 2  # ep-9 row and RAPPORTEUR_CO excluded

    report = next(r for r in rows if r["role"] == "shadow_rapporteur")
    assert report["mep_id"] == "256891"
    assert report["procedure_key"] == "2023-0448"
    assert report["procedure_code"] == "2023/0448(COD)"
    assert report["procedure_title"] == "Protection of animals during transport"
    assert report["committee"] == "AGRI"  # falls back to the lead committee
    assert report["group_at_appointment"] == "RENEW"

    opinion = next(r for r in rows if r["role"] == "shadow_rapporteur_opinion")
    assert opinion["mep_id"] == "214839"
    assert opinion["committee"] == "PECH"  # explicit in-name-of wins


def test_extract_assignments_display_code_fallback():
    proc = dict(PROC, label=None)
    rows = fetch_assignments._extract_assignments(proc)
    assert rows[0]["procedure_code"] == "2023/0448(COD)"  # rebuilt from key + type


def test_dedupe_assignments_keeps_earliest():
    a = {
        "procedure_key": "2023-0448", "mep_id": "1", "role": "shadow_rapporteur",
        "committee": "AGRI", "appointed": "2024-12-01", "procedure_code": "x",
        "procedure_title": None, "procedure_type": "COD", "group_at_appointment": "PPE",
    }
    b = dict(a, appointed="2024-09-19")
    c = dict(a, mep_id="2", appointed=None)
    out = fetch_assignments.dedupe_assignments([a, b, c, dict(c)])
    assert len(out) == 2
    kept = next(r for r in out if r["mep_id"] == "1")
    assert kept["appointed"] == "2024-09-19"
    assert out == sorted(out, key=lambda r: (r["procedure_key"], r["mep_id"], r["role"], r["committee"] or ""))
