"""Pinned provenance for deterministic eligibility parsing."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ParserConfiguration:
    """Version every parser input contract, including deliberate non-use of an LLM."""

    parser_version: str
    prompt_version: str
    model_configuration_version: str
    output_schema_version: str
    automated_criterion_use_enabled: bool

    def snapshot(self) -> dict[str, str]:
        """Return immutable parser provenance without retaining a prompt body."""
        return asdict(self)


# This build has no generative parser. The explicit versions prevent a later model
# rollout from being confused with the deterministic source-span parser history.
ELIGIBILITY_PARSER_CONFIGURATION = ParserConfiguration(
    parser_version="deterministic-eligibility-age-v1",
    prompt_version="deterministic-no-prompt-v1",
    model_configuration_version="deterministic-no-model-v1",
    output_schema_version="trial-criteria-parser-output-v1",
    # Passing a narrow synthetic parser fixture is not clinical validation. This
    # remains false until source-span and safety acceptance are explicitly reviewed.
    automated_criterion_use_enabled=False,
)
