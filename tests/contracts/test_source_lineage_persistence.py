def _post_upload(auth_client, project_title: str) -> list[dict]:
    project_response = auth_client.post(
        "/api/projects",
        json={"title": project_title},
    )
    assert project_response.status_code == 201, project_response.text

    upload_response = auth_client.post(
        f"/api/projects/{project_response.json()['id']}/assets/upload",
        files={
            "files": (
                "synthetic-lineage.png",
                b"rights-safe synthetic upload",
                "image/png",
            )
        },
        data={
            "rights_owner": "Test owner",
            "license_type": "owned",
            "declared": "true",
        },
    )
    assert upload_response.status_code == 201, upload_response.text
    return upload_response.json()


def test_upload_persists_all_lineage_fields(auth_client, monkeypatch):
    from app.db import SessionLocal
    from app.models import SourceAsset
    from app.services.ingest import IngestedAsset

    result = IngestedAsset(
        type="image",
        original_filename="source-strip.png",
        mime_type="image/png",
        storage_key="tests/source-strip.png",
        size_bytes=29,
        checksum="derived-checksum-a",
        width=900,
        height=1200,
        original_checksum="original-checksum-a",
        original_width=4096,
        original_height=12000,
        source_bounds=(17, 211, 903, 1207),
        strip_order=13,
        region_order=29,
        trim_classification="canonical_panel",
        coverage_map_hash="coverage-map-hash-a",
    )
    monkeypatch.setattr(
        "app.routers.projects.ingest.ingest_upload_sources",
        lambda *args, **kwargs: [result],
    )

    response_rows = _post_upload(auth_client, "Lineage single result")

    with SessionLocal() as db:
        asset = db.get(SourceAsset, response_rows[0]["id"])
        assert asset is not None
        assert asset.original_checksum == "original-checksum-a"
        assert asset.original_width == 4096
        assert asset.original_height == 12000
        assert asset.source_bounds_json == {
            "x": 17,
            "y": 211,
            "width": 886,
            "height": 996,
        }
        assert asset.strip_order == 13
        assert asset.region_order == 29
        assert asset.trim_classification == "canonical_panel"
        assert asset.coverage_map_hash == "coverage-map-hash-a"


def test_upload_preserves_legacy_derived_result_bounds_and_order(
    auth_client, monkeypatch
):
    from app.db import SessionLocal
    from app.models import SourceAsset
    from app.services.ingest import IngestedAsset

    first = IngestedAsset(
        type="image",
        original_filename="source-strip_p01.png",
        mime_type="image/png",
        storage_key="tests/source-strip_p01.png",
        size_bytes=31,
        checksum="derived-checksum-b1",
        width=2048,
        height=4200,
        original_checksum="original-checksum-b",
        original_width=2048,
        original_height=9000,
        source_bounds=(0, 0, 2048, 4200),
        strip_order=7,
        region_order=2,
        trim_classification="canonical_panel",
        coverage_map_hash="coverage-map-hash-b",
    )
    second = IngestedAsset(
        type="image",
        original_filename="source-strip_p02.png",
        mime_type="image/png",
        storage_key="tests/source-strip_p02.png",
        size_bytes=37,
        checksum="derived-checksum-b2",
        width=2048,
        height=4800,
        original_checksum="original-checksum-b",
        original_width=2048,
        original_height=9000,
        source_bounds=(0, 4200, 2048, 4800),
        strip_order=7,
        region_order=3,
        trim_classification="canonical_panel",
        coverage_map_hash="coverage-map-hash-b",
    )
    monkeypatch.setattr(
        "app.routers.projects.ingest.ingest_upload_sources",
        lambda *args, **kwargs: [first, second],
    )

    response_rows = _post_upload(auth_client, "Lineage sliced results")

    with SessionLocal() as db:
        assets = [
            db.get(SourceAsset, row["id"])
            for row in response_rows
        ]
        assert all(asset is not None for asset in assets)
        assert [
            (
                asset.source_bounds_json,
                asset.strip_order,
                asset.region_order,
                asset.original_checksum,
                asset.coverage_map_hash,
            )
            for asset in assets
        ] == [
            (
                {"x": 0, "y": 0, "width": 2048, "height": 4200},
                7,
                2,
                "original-checksum-b",
                "coverage-map-hash-b",
            ),
            (
                {"x": 0, "y": 4200, "width": 2048, "height": 600},
                7,
                3,
                "original-checksum-b",
                "coverage-map-hash-b",
            ),
        ]
