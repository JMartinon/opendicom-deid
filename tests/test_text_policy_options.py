"""The CLI and public options choose the same clinical-text policy."""

from pathlib import Path
from unittest.mock import patch

import pytest

from opendicom_deid.cli import main, parser
from opendicom_deid.core.models import Options
from opendicom_deid.core.report import Report


def test_clinical_text_is_preserved_by_default():
    assert Options().text_policy == "clinical"
    assert parser().parse_args(["deidentify", "input", "output"]).text_policy == "clinical"


@pytest.mark.parametrize("invalid", ["", "preserve", "CLINICAL", None, True])
def test_unknown_text_policy_is_rejected(invalid):
    with pytest.raises(ValueError, match="Text policy"):
        Options(text_policy=invalid)


@pytest.mark.parametrize("policy", ["clinical", "strict"])
def test_cli_forwards_text_policy_to_batch_engine(policy, capsys):
    with patch(
        "opendicom_deid.core.anonymizer.process_batch",
        return_value=Report(status="COMPLETED"),
    ) as process:
        assert main(["deidentify", "input", "output", "--text-policy", policy]) == 0
    process.assert_called_once()
    assert process.call_args.args[:2] == (Path("input"), Path("output"))
    assert process.call_args.args[2].text_policy == policy
    assert "Technical processing completed" in capsys.readouterr().out


def test_cli_rejects_unknown_text_policy():
    with pytest.raises(SystemExit) as error:
        parser().parse_args(["deidentify", "input", "output", "--text-policy", "unknown"])
    assert error.value.code == 2
