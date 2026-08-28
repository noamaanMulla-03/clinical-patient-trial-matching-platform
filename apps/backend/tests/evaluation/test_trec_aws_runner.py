"""Regression checks for the ephemeral public-TREC AWS runner."""

from pathlib import Path


def test_aws_runner_supports_ubuntu_and_amazon_linux_package_managers() -> None:
    runner = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "run_trec_fielded_benchmark_aws.sh"
    )
    source = runner.read_text()

    assert "command -v apt-get" in source
    assert "command -v dnf" in source
    assert "install_with_system_package_manager" in source
    assert 'log_path="/var/tmp/trec-fielded-benchmark.log"' in source
    assert 'export PATH="${PATH:+${PATH}:}' in source
    assert "--semantic-field-fusion weighted-rrf" in source
    assert "trec-fielded-weighted-rrf.json" in source
