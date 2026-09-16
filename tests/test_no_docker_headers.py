"""Test-only provisioning headers must be gated and request-local."""

import asyncio
import importlib

import pytest


@pytest.mark.parametrize("service", ["rds", "elasticache"])
@pytest.mark.parametrize("ci", [None, "false", "true"])
def test_no_docker_header_gate_and_concurrent_scope(monkeypatch, service, ci):
    app = importlib.import_module("ministack.app")
    module = importlib.import_module(f"ministack.services.{service}")
    if ci is None:
        monkeypatch.delenv("CI", raising=False)
    else:
        monkeypatch.setenv("CI", ci)
    monkeypatch.setattr(app, "AUTH", False)
    monkeypatch.setattr(app, "detect_service", lambda *args: service)
    monkeypatch.setattr(app, "_maybe_record_cloudtrail", lambda *args: None)
    client = object()
    monkeypatch.setattr(module, "_docker", client)

    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(method, path, headers, body, query):
            if headers.get("x-test-marked"):
                assert module._get_docker() is (None if ci == "true" else client)
                entered.set()
                await release.wait()
                raise RuntimeError("test handler failure")
            assert module._get_docker() is client
            release.set()
            return 200, {}, b"ok"

        monkeypatch.setitem(app.SERVICE_HANDLERS, service, handler)
        async def marked():
            result = await app._dispatch_service_request(
                "POST", "/", {f"x-ministack-{service}-no-docker": "true",
                              "x-test-marked": "true"}, b"", {}, "marked",
            )
            assert result[0] == 500
            assert module._get_docker() is client

        task = asyncio.create_task(marked())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            result = await app._dispatch_service_request(
                "POST", "/", {}, b"", {}, "normal",
            )
            assert result[0] == 200
            await task
        finally:
            release.set()
            await task

    asyncio.run(exercise())
