"""Smoke test: the test runner works and the project imports."""

import importlib.metadata

import sqlalchemy


def test_dependencies_importable() -> None:
    assert importlib.metadata.version("fastapi")
    assert sqlalchemy.__version__
