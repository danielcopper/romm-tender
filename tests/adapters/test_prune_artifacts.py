import os
from pathlib import Path

from adapters.prune_artifacts import PruneArtifactAdapter


def test_discovers_and_removes_only_named_rom_cache_files(tmp_path):
    covers = tmp_path / "covers"
    artwork = tmp_path / "artwork"
    covers.mkdir()
    artwork.mkdir()
    expected = [covers / "7.png", covers / "7.cover-meta.json", artwork / "7_grid.png", artwork / "7_logo_5.png"]
    for path in expected:
        path.write_bytes(b"x")
    unrelated = artwork / "8_grid.png"
    unrelated.write_bytes(b"keep")
    adapter = PruneArtifactAdapter(cache_dir=str(tmp_path))

    artifacts = adapter.recovery_artifacts([7])
    sources = {Path(item["source_path"]) for item in artifacts}
    assert set(expected) <= sources
    assert artwork / "7_icon.png" in sources
    assert adapter.remove([7])["success"] is True
    assert all(not path.exists() for path in expected)
    assert unrelated.exists()


def test_unsealed_cleanup_does_not_follow_symlinked_cache_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "7.png"
    target.write_bytes(b"keep")
    (tmp_path / "covers").symlink_to(outside, target_is_directory=True)
    (tmp_path / "artwork").mkdir()
    adapter = PruneArtifactAdapter(cache_dir=str(tmp_path))

    result = adapter.remove([7])

    assert result["success"] is False
    assert target.read_bytes() == b"keep"


def test_preopened_cache_writer_prevents_artifact_deletion(tmp_path):
    covers = tmp_path / "covers"
    artwork = tmp_path / "artwork"
    covers.mkdir()
    artwork.mkdir()
    target = covers / "7.png"
    target.write_bytes(b"cached")
    adapter = PruneArtifactAdapter(cache_dir=str(tmp_path))
    writer = os.open(target, os.O_WRONLY)
    try:
        result = adapter.remove([7])
    finally:
        os.close(writer)

    assert result["success"] is False
    assert "active writer" in result["message"]
    assert target.read_bytes() == b"cached"


def test_discovers_and_removes_the_wide_grid_and_logo_named_by_their_image_id(tmp_path):
    artwork = tmp_path / "artwork"
    artwork.mkdir()
    (tmp_path / "covers").mkdir()
    owned = [artwork / "7_grid_12.png", artwork / "7_logo_5.png", artwork / "7_grid.png"]
    for path in owned:
        path.write_bytes(b"x")
    unrelated = [artwork / "70_grid_12.png", artwork / "8_logo_5.png"]
    for path in unrelated:
        path.write_bytes(b"keep")
    adapter = PruneArtifactAdapter(cache_dir=str(tmp_path))

    sources = {Path(item["source_path"]) for item in adapter.recovery_artifacts([7])}

    assert set(owned) <= sources
    assert not set(unrelated) & sources
    assert adapter.remove([7])["success"] is True
    assert [path for path in owned if path.exists()] == []
    assert all(path.exists() for path in unrelated)


def test_discovers_and_removes_the_icon_named_by_its_image_id(tmp_path):
    artwork = tmp_path / "artwork"
    artwork.mkdir()
    (tmp_path / "covers").mkdir()
    owned = artwork / "7_icon_34.png"
    owned.write_bytes(b"x")
    unrelated = [artwork / "70_icon_34.png", artwork / "8_icon_34.png"]
    for path in unrelated:
        path.write_bytes(b"keep")
    adapter = PruneArtifactAdapter(cache_dir=str(tmp_path))

    sources = {Path(item["source_path"]) for item in adapter.recovery_artifacts([7])}

    assert owned in sources
    assert not set(unrelated) & sources
    assert adapter.remove([7])["success"] is True
    assert not owned.exists()
    assert all(path.exists() for path in unrelated)


def test_no_artwork_directory_lists_only_the_names_it_always_composes(tmp_path):
    adapter = PruneArtifactAdapter(cache_dir=str(tmp_path))

    names = {Path(item["source_path"]).name for item in adapter.recovery_artifacts([7])}

    assert names == {"7.png", "7.cover-meta.json", "7_hero.png", "7_logo.png", "7_grid.png", "7_icon.png"}
