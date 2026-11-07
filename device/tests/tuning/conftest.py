from __future__ import annotations

import pytest

from argus.tuning.evaluate import discover, load_dataset
from argus.tuning.export import export_all


@pytest.fixture(scope="session")
def scenario_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("scenario_recordings")
    export_all(d)
    return d


@pytest.fixture(scope="session")
def scenario_paths(scenario_dir):
    labelled, unlabelled = discover(scenario_dir)
    assert not unlabelled
    return labelled


@pytest.fixture(scope="session")
def scenario_dataset(scenario_paths):
    return load_dataset(scenario_paths)
