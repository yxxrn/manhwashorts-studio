#!/usr/bin/env python3
"""Seed a demo workspace, project, and rights-declared fixture assets.

Everything created here is synthetic: the recap text is original filler and the
panels are generated gradients, so the demo never depends on third-party
material.

Usage:
    python scripts/seed_demo.py                 # create/refresh the demo project
    python scripts/seed_demo.py --draft         # also run analyse->script->voice->timeline
    python scripts/seed_demo.py --render        # ...and render the final video
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.constants import (  # noqa: E402
    AssetType,
    ContentType,
    LicenseType,
    NarrationStyle,
    RightsStatus,
    SpoilerLevel,
)
from app.db import init_db, session_scope  # noqa: E402
from app.models import Project, SourceAsset, User, Workspace  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import ingest, pipeline, storage  # noqa: E402

DEMO_EMAIL = "demo@manhwashorts.local"
DEMO_PASSWORD = "demo12345"

RECAP = """Bab ini dibuka dengan Rian, pemburu peringkat E yang namanya jarang
disebut siapa pun di asosiasi. Selama tiga tahun dia hanya mendapat misi sisa
di pinggiran kota, membersihkan gerbang kecil yang bahkan tidak dianggap
berbahaya.

Ketika sebuah gerbang tak terdaftar muncul di bawah stasiun tua, tim peringkat A
menolak masuk karena bayarannya terlalu kecil. Rian menerima misi itu sendirian.
Di dalam, struktur dungeon tidak cocok dengan catatan asosiasi mana pun.

Rian terpisah dari jalur keluar ketika lantai runtuh. Di ruang paling bawah dia
menemukan papan bercahaya yang hanya bisa dilihat olehnya. Papan itu memberi satu
syarat: selesaikan latihan harian, atau dipindahkan ke zona hukuman.

Latihan hari pertama hampir membunuhnya. Dia harus bertahan melawan tiga monster
sekaligus tanpa senjata, dan setiap luka terasa nyata. Rian gagal di percobaan
pertama dan langsung merasakan apa arti zona hukuman.

Ternyata setiap kegagalan tidak menghapus kemajuannya. Papan itu menyimpan
hasilnya dan menaikkan batas kekuatan Rian sedikit demi sedikit, sesuatu yang
menurut catatan asosiasi tidak mungkin terjadi pada pemburu peringkat E.

Di akhir bab, Sera, ketua tim peringkat A yang sebelumnya menolak misi itu,
berdiri di depan gerbang yang sudah tertutup. Dia membaca laporan bahwa hanya
satu orang masuk, dan tidak ada yang keluar."""


def seed(db) -> Project:
    user = db.query(User).filter(User.email == DEMO_EMAIL).one_or_none()
    if user is None:
        user = User(
            email=DEMO_EMAIL,
            name="Demo Creator",
            password_hash=hash_password(DEMO_PASSWORD),
        )
        db.add(user)
        db.flush()

    workspace = db.query(Workspace).filter(Workspace.owner_id == user.id).first()
    if workspace is None:
        workspace = Workspace(owner_id=user.id, name="Demo Workspace", timezone="Asia/Jakarta")
        db.add(workspace)
        db.flush()

    # Rebuild the demo project from scratch each run so the seed is idempotent.
    existing = (
        db.query(Project)
        .filter(Project.workspace_id == workspace.id, Project.title == "Demo: Rian Chapter 12")
        .all()
    )
    for old in existing:
        db.delete(old)
    db.flush()

    project = Project(
        workspace_id=workspace.id,
        title="Demo: Rian Chapter 12",
        manhwa_title="Peringkat Terakhir",
        chapter="12",
        content_type=ContentType.CHAPTER_RECAP,
        language="id",
        spoiler_level=SpoilerLevel.MEDIUM,
        narration_style=NarrationStyle.DRAMATIC,
        target_duration=60,
        voice_id="id",
        cta_text="Menurutmu Sera akan jadi sekutu atau musuh? Komentar di bawah.",
    )
    db.add(project)
    db.flush()

    # Text source
    text_asset = ingest.ingest_text(project.id, RECAP, "recap_ch12.txt")
    db.add(
        SourceAsset(
            project_id=project.id,
            type=text_asset.type,
            original_filename=text_asset.original_filename,
            storage_key=text_asset.storage_key,
            mime_type=text_asset.mime_type,
            size_bytes=text_asset.size_bytes,
            checksum=text_asset.checksum,
            extracted_text=text_asset.extracted_text,
            source_name="Original recap written by the demo creator",
            rights_owner="Demo Creator",
            license_type=LicenseType.OWNED,
            permission_reference="Authored in-house for demo purposes",
            permission_date=datetime.now(UTC).date().isoformat(),
            rights_status=RightsStatus.DECLARED,
            order_index=0,
        )
    )

    # Image panels (generated fixtures, not third-party artwork)
    panel_dir = ROOT / "data" / "fixtures" / "panels"
    panels = sorted(panel_dir.glob("*.jpg"))
    if not panels:
        print(
            "No fixture panels found. Run: python scripts/make_fixtures.py",
            file=sys.stderr,
        )
    for i, panel in enumerate(panels):
        stored = storage.put_file(f"projects/{project.id}/images", panel, panel.name)
        from PIL import Image

        with Image.open(panel) as img:
            width, height = img.size
        db.add(
            SourceAsset(
                project_id=project.id,
                type=AssetType.IMAGE,
                original_filename=panel.name,
                storage_key=stored.storage_key,
                mime_type="image/jpeg",
                size_bytes=stored.size_bytes,
                checksum=stored.checksum,
                width=width,
                height=height,
                source_name="Synthetic test panel generated by scripts/make_fixtures.py",
                rights_owner="Demo Creator",
                license_type=LicenseType.OWNED,
                permission_reference="Generated locally; contains no third-party art",
                permission_date=datetime.now(UTC).date().isoformat(),
                rights_status=RightsStatus.DECLARED,
                order_index=i + 1,
            )
        )

    db.flush()
    return project


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", action="store_true", help="run the draft pipeline")
    parser.add_argument("--render", action="store_true", help="render the final video")
    args = parser.parse_args()

    init_db()
    with session_scope() as db:
        project = seed(db)
        print(f"user      : {DEMO_EMAIL} / {DEMO_PASSWORD}")
        print(f"project   : {project.id}  {project.title}")
        print(f"assets    : {len(pipeline.project_assets(db, project.id))}")
        project_id = project.id

    if args.draft or args.render:
        with session_scope() as db:
            summary = pipeline.generate_draft(db, project_id, seed=42)
            print("\ndraft:")
            for key, value in summary.items():
                if key != "warnings":
                    print(f"  {key:20} {value}")
            if summary["warnings"]:
                print("  warnings:")
                for w in summary["warnings"]:
                    print(f"    - {w['code']}: {w['message'][:80]}")

    if args.render:
        with session_scope() as db:
            script = pipeline.get_project(db, project_id).latest_script
            if script is None:
                print("no script to approve; run with --draft first", file=sys.stderr)
                raise SystemExit(1)
            pipeline.approve_script(db, script.id, actor_id="seed")
            print(f"\napproved script v{script.version}")

        with session_scope() as db:
            results = pipeline.run_quality_checks(db, project_id)
            from app.services.quality import summarise

            summary = summarise(results)
            print(f"quality   : {summary}")
            for r in results:
                if not r.passed:
                    print(f"  [{r.severity:7}] {r.code}: {r.message[:90]}")

        with session_scope() as db:
            job = pipeline.enqueue_render(db, project_id, "final", actor_id="seed")
            job_id = job.id
        print(f"\nqueued render {job_id}")

        with session_scope() as db:
            job = pipeline.execute_render(db, job_id)
            print(f"render    : {job.status} {job.progress}% {job.stage}")
            if job.status == "succeeded":
                print(f"  output   : {job.output_key}")
                print(f"  duration : {job.duration}s  {job.width}x{job.height}")
                print(f"  sha256   : {job.checksum[:16]}")
                print(f"  srt      : {job.subtitle_key}")
            else:
                print(f"  error    : {job.error_code}: {job.error_message[:200]}")


if __name__ == "__main__":
    main()
