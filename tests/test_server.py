"""Tests for FastAPI server, dashboard routes, and JSON API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from anomalog.server import create_app

if TYPE_CHECKING:
    from anomalog.config import AnomalogConfig


class TestHealthz:
    def test_healthz_returns_ok(self, test_config: AnomalogConfig) -> None:
        app = create_app(test_config)
        with TestClient(app) as client:
            resp = client.get("/healthz")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["sources"] == 1
            assert data["models_healthy"] == 0


class TestDashboardRoutes:
    def test_dashboard_index_renders(self, test_config: AnomalogConfig) -> None:
        app = create_app(test_config)
        with TestClient(app) as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "anomalog" in resp.text

    def test_dashboard_alerts_renders(self, test_config: AnomalogConfig) -> None:
        app = create_app(test_config)
        with TestClient(app) as client:
            resp = client.get("/alerts")
            assert resp.status_code == 200
            assert "Alerts" in resp.text


class TestDashboardAPI:
    def test_api_anomalies_empty(self, test_config: AnomalogConfig) -> None:
        app = create_app(test_config)
        with TestClient(app) as client:
            resp = client.get("/api/anomalies")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_api_sources_returns_config(self, test_config: AnomalogConfig) -> None:
        app = create_app(test_config)
        with TestClient(app) as client:
            resp = client.get("/api/sources")
            assert resp.status_code == 200
            sources = resp.json()
            assert len(sources) == 1
            assert sources[0]["name"] == "test"
            assert sources[0]["method"] == "http"
            assert sources[0]["has_baseline"] is False
            assert sources[0]["sensitivity"] == 0.5
