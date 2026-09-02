from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("app_settings")


class FakeConnector:
    def __init__(self, page_bytes: bytes):
        self.page_bytes = page_bytes

    def search(self, title, language=None, source_id=None):
        return [{"id": 7, "title": title, "sourceId": "42", "thumbnailUrl": "", "source": {"id": "42", "displayName": "Test Source", "lang": language or "en"}}]

    def resolve_range(self, title, chapter_from, chapter_to, language=None, source_id=None):
        from app.services import suwayomi
        chapters = tuple({"id": n, "chapterNumber": float(n), "name": f"Chapter {n}", "sourceOrder": n} for n in range(int(chapter_from), int(chapter_to) + 1))
        return suwayomi.ResolvedRange(
            manga={"id": 7, "title": title, "sourceId": "42"},
            source={"id": "42", "displayName": "Test Source", "lang": language or "en"},
            chapters=chapters,
        )

    def download_range(self, resolved):
        from app.services import suwayomi
        return [
            suwayomi.DownloadedPage(int(ch["id"]), str(ch["chapterNumber"]), str(ch["name"]), 1, f"ch{ch['chapterNumber']}__0001.jpg", self.page_bytes)
            for ch in resolved.chapters
        ]


def _register(client):
    response = client.post("/api/auth/register", json={"email": "source-agent@example.com", "password": "agentpass1234"})
    assert response.status_code == 201


def _project(client):
    return client.post("/api/projects", json={"title": "Source Test", "manhwa_title": "Infinite Mage", "chapter": "20-22"}).json()

def test_suwayomi_search_is_exposed_through_manhwashorts(client, panel_bytes, monkeypatch):
    _register(client)
    from app.routers import sources

    monkeypatch.setattr(sources, "_ready_client", lambda: FakeConnector(panel_bytes))
    response = client.post("/api/sources/suwayomi/search", json={"title": "Infinite Mage", "language": "en"})
    assert response.status_code == 200, response.text
    assert response.json()[0] == {
        "manga_id": 7,
        "title": "Infinite Mage",
        "source_id": "42",
        "source": "Test Source",
        "language": "en",
        "thumbnail_url": "",
    }


def test_import_range_creates_ordered_normal_assets_and_is_idempotent(client, panel_bytes, monkeypatch):
    _register(client)
    project = _project(client)
    from app.routers import sources

    monkeypatch.setattr(sources, "_ready_client", lambda: FakeConnector(panel_bytes))
    url = f"/api/projects/{project['id']}/sources/suwayomi/import"
    payload = {"title": "Infinite Mage", "chapter_from": 20, "chapter_to": 22, "language": "en"}
    first = client.post(url, json=payload)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["chapters"] == ["20.0", "21.0", "22.0"]
    assert body["pages_downloaded"] == 3
    assert body["assets_created"] == 3
    assert body["rights_status"] == "undeclared"

    assets = client.get(f"/api/projects/{project['id']}/assets").json()
    assert [asset["order_index"] for asset in assets] == [0, 1, 2]
    assert all(asset["source_name"].startswith("Suwayomi / Test Source") for asset in assets)

    second = client.post(url, json=payload)
    assert second.status_code == 200, second.text
    assert second.json()["assets_created"] == 0
    assert second.json()["duplicates_skipped"] == 3
    assert len(client.get(f"/api/projects/{project['id']}/assets").json()) == 3


def test_import_refuses_to_mutate_an_already_analyzed_corpus(client, panel_bytes, monkeypatch):
    _register(client)
    project = _project(client)
    from app.routers import sources
    from tests.factories.vision_api import seed_reconciled_analysis_for_project_images

    monkeypatch.setattr(sources, "_ready_client", lambda: FakeConnector(panel_bytes))
    # Add one image so the vision fixture can seed a reconciled analysis.
    client.post(f"/api/projects/{project['id']}/assets/upload", files=[("files", ("panel.jpg", panel_bytes, "image/jpeg"))])
    seed_reconciled_analysis_for_project_images(project["id"])
    response = client.post(
        f"/api/projects/{project['id']}/sources/suwayomi/import",
        json={"title": "Infinite Mage", "chapter_from": 20, "chapter_to": 22},
    )
    assert response.status_code == 409
    assert "before analysis" in response.json()["detail"]

def test_range_resolution_keeps_decimal_chapters_in_reading_order(monkeypatch):
    from app.services import suwayomi
    connector = suwayomi.SuwayomiClient("http://127.0.0.1:4567")
    monkeypatch.setattr(connector, "search", lambda *args, **kwargs: [{"id": 7, "title": "Infinite Mage", "sourceId": "42", "source": {"id": "42", "lang": "en"}}])
    chapters = [
        {"id": 25, "chapterNumber": 25.0, "sourceOrder": 5},
        {"id": 205, "chapterNumber": 20.5, "sourceOrder": 2},
        {"id": 20, "chapterNumber": 20.0, "sourceOrder": 1},
        {"id": 21, "chapterNumber": 21.0, "sourceOrder": 3},
        {"id": 22, "chapterNumber": 22.0, "sourceOrder": 4},
        {"id": 23, "chapterNumber": 23.0, "sourceOrder": 5},
        {"id": 24, "chapterNumber": 24.0, "sourceOrder": 6},
    ]
    monkeypatch.setattr(connector, "manga_and_chapters", lambda _id: ({"id": 7, "title": "Infinite Mage", "sourceId": "42", "source": {"id": "42", "lang": "en"}}, chapters))
    resolved = connector.resolve_range("Infinite Mage", 20, 25, "en")
    assert [str(ch["chapterNumber"]) for ch in resolved.chapters] == ["20.0", "20.5", "21.0", "22.0", "23.0", "24.0", "25.0"]


def test_openapi_advertises_suwayomi_surface(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/sources/suwayomi/status" in paths
    assert "/api/sources/suwayomi/search" in paths
    assert "/api/projects/{project_id}/sources/suwayomi/import" in paths
    capabilities = client.get("/api/capabilities").json()
    assert "suwayomi" in capabilities["source_connectors"]



def test_managed_sidecar_target_is_loopback_only_and_honors_port():
    from app.services import suwayomi

    assert suwayomi._managed_bind_target("http://127.0.0.1:6789") == ("127.0.0.1", 6789)
    assert suwayomi._managed_bind_target("http://localhost:4567") == ("127.0.0.1", 4567)
    assert suwayomi._managed_bind_target("http://10.0.0.5:4567") is None
    assert suwayomi._managed_bind_target("https://127.0.0.1:4567") is None
    assert suwayomi._managed_bind_target("http://127.0.0.1:4567/subpath") is None


def test_suwayomi_page_identity_includes_source_and_manga():
    from app.services import suwayomi

    first = suwayomi.provenance_filename("42", 7, "ch20__0001.jpg")
    assert first == "s42_m7__ch20__0001.jpg"
    assert suwayomi.provenance_filename("43", 7, "ch20__0001.jpg") != first
    assert suwayomi.provenance_filename("42", 8, "ch20__0001.jpg") != first


def test_managed_sidecar_config_is_loopback_and_headless(tmp_path):
    from app.services import suwayomi
    config = tmp_path / "server.conf"
    config.write_text('server.extensionStores = ["https://example.invalid/index.json"]\n')
    suwayomi._ensure_managed_config(tmp_path)
    text = config.read_text()
    assert 'server.ip = "127.0.0.1"' in text
    assert 'server.webUIEnabled = false' in text
    assert 'server.systemTrayEnabled = false' in text
    assert 'server.kcefEnabled = false' in text
    assert 'https://example.invalid/index.json' in text
    assert "0.0.0.0" not in text


def test_status_reports_when_extension_setup_is_still_needed(client, monkeypatch):
    _register(client)
    from app.routers import sources

    monkeypatch.setattr(
        sources.suwayomi,
        "ensure_sidecar",
        lambda: {
            "available": True,
            "url": "http://127.0.0.1:4567",
            "sources": 1,
            "searchable_sources": 0,
            "needs_extension_setup": True,
            "managed": True,
            "installed": True,
        },
    )
    body = client.get("/api/sources/suwayomi/status").json()
    assert body["available"] is True
    assert body["searchable_sources"] == 0
    assert body["needs_extension_setup"] is True
