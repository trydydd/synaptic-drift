from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--network",
        action="store_true",
        default=False,
        help="Run tests that make real network requests",
    )
    parser.addoption(
        "--benchmark",
        action="store_true",
        default=False,
        help="Run token overhead benchmarks (writes results to tests/benchmarks/results/)",
    )
    parser.addoption(
        "--evals",
        action="store_true",
        default=False,
        help="Run retrieval quality evals (tests/evals/)",
    )
    parser.addoption(
        "--live-model",
        action="store_true",
        default=False,
        help="Run end-task evals that drive a real model endpoint (never in CI)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    skip_network = pytest.mark.skip(reason="pass --network to run network tests")
    skip_benchmark = pytest.mark.skip(reason="pass --benchmark to run benchmarks")
    skip_evals = pytest.mark.skip(reason="pass --evals to run retrieval quality evals")
    skip_live_model = pytest.mark.skip(
        reason="pass --live-model to run end-task evals against a real model endpoint"
    )
    for item in items:
        # get_closest_marker() checks for an actually-applied marker, unlike
        # `"x" in item.keywords`, which also matches path/module name
        # components — tests/evals/ would make every test in that directory
        # match the string "evals" even without @pytest.mark.evals applied.
        if item.get_closest_marker("network") and not config.getoption("--network"):
            item.add_marker(skip_network)
        if item.get_closest_marker("benchmark") and not config.getoption("--benchmark"):
            item.add_marker(skip_benchmark)
        if item.get_closest_marker("evals") and not config.getoption("--evals"):
            item.add_marker(skip_evals)
        if item.get_closest_marker("live_model") and not config.getoption(
            "--live-model"
        ):
            item.add_marker(skip_live_model)
