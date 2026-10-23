"""CLI parallelism must be explicit, bounded and forwarded to the batch engine."""

from unittest.mock import patch

import pytest

from opendicom_deid.cli import main, parser
from opendicom_deid.core.report import Report


def test_cli_keeps_sequential_default():
    assert parser().parse_args(["deidentify", "input", "output"]).workers == 1


@pytest.mark.parametrize("workers", [1, 2, 8])
def test_cli_forwards_worker_count(workers, capsys):
    with patch(
        "opendicom_deid.core.anonymizer.process_batch",
        return_value=Report(status="COMPLETED"),
    ) as process:
        assert main(["deidentify", "input", "output", "--workers", str(workers)]) == 0
    assert process.call_args.args[2].workers == workers
    assert "Technical processing completed" in capsys.readouterr().out


@pytest.mark.parametrize("workers", ["0", "-1", "9", "1.5", "auto"])
def test_cli_rejects_invalid_parallelism(workers):
    with pytest.raises(SystemExit) as error:
        parser().parse_args(["deidentify", "input", "output", "--workers", workers])
    assert error.value.code == 2
