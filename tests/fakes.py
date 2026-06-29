"""Tiny test doubles for the boto3 calls our detectors make.

We deliberately avoid mocking libraries (and avoid pulling in `moto`) — the
project's dependency rule is stdlib + boto3 + anthropic. The fakes here only
implement the surface area each detector actually touches.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class FakePaginator:
    """Mimics a boto3 paginator: ``paginate(...)`` yields page dicts."""

    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **_kwargs):
        for page in self._pages:
            yield page


class FakeClient:
    """A boto3 client stub. Each kwarg becomes either a method or a paginator.

    - For a regular method, pass a callable or a static return value.
    - For paginators, pass ``paginators={"op_name": [page1, page2]}``.
    """

    def __init__(self, *, paginators=None, **methods):
        self._paginators = paginators or {}
        for name, value in methods.items():
            if callable(value):
                setattr(self, name, value)
            else:
                setattr(self, name, _const(value))

    def get_paginator(self, op_name: str) -> FakePaginator:
        if op_name not in self._paginators:
            raise KeyError(f"no fake paginator for {op_name!r}")
        return FakePaginator(self._paginators[op_name])


class FakeSession:
    """Returns the right FakeClient based on the service name."""

    def __init__(self, **clients):
        self._clients = clients

    def client(self, name: str, region_name: str | None = None, **_kwargs):
        # Real boto3 accepts extra kwargs like ``config=...``; ignore them here.
        if name not in self._clients:
            raise KeyError(f"no fake client configured for service {name!r}")
        return self._clients[name]


def _const(value):
    def _f(**_kwargs):
        return value
    return _f


def cpu_datapoints(*, hours: int, peak: float, avg: float | None = None):
    """Build a CloudWatch ``GetMetricStatistics`` response with N hourly points."""
    avg = avg if avg is not None else peak / 2
    now = datetime.now(timezone.utc)
    return {
        "Datapoints": [
            {
                "Timestamp": now - timedelta(hours=i),
                "Maximum": peak,
                "Average": avg,
                "Sum": 0.0,
            }
            for i in range(hours)
        ]
    }
