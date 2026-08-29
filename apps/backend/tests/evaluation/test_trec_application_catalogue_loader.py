"""Public-source conversion checks for the isolated TREC benchmark catalogue."""

from scripts.load_trec_application_catalogue import _psycopg_url, _trial


def test_trec_trial_conversion_preserves_public_searchable_fields() -> None:
    nct_id, raw_study, title, conditions, interventions, source_hash = _trial(
        b"""
        <clinical_study>
          <id_info><nct_id>NCT00000001</nct_id></id_info>
          <brief_title>Public melanoma study</brief_title>
          <condition>Melanoma</condition>
          <intervention>
            <intervention_name>Public treatment</intervention_name>
            <description>Public description</description>
          </intervention>
          <eligibility><criteria>
            <textblock>Adults only</textblock>
          </criteria></eligibility>
        </clinical_study>
        """
    )

    assert nct_id == "NCT00000001"
    assert title == "Public melanoma study"
    assert conditions == ["Melanoma"]
    assert interventions == [
        {"name": "Public treatment", "description": "Public description"}
    ]
    assert source_hash
    assert raw_study["protocolSection"]["identificationModule"]["nctId"] == nct_id


def test_loader_accepts_the_application_async_database_url() -> None:
    assert _psycopg_url("postgresql+asyncpg://app:app@localhost:5432/trec") == (
        "postgresql://app:app@localhost:5432/trec"
    )
