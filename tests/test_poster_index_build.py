"""The acquisition-to-runtime index command owns a reproducible data contract."""

import gzip
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "invalid",
    [None, "filename", "extension", "oversize", "status", "duplicate"],
)
def test_index_command_validates_batch_before_replacing_output(
    tmp_path: Path, invalid: str | None
) -> None:
    digest = sha256(b"image").hexdigest()
    record = {
        "title": "Un été",
        "year": None,
        "status": "stored",
        "source_synopsis_sha256": sha256(b"A story.").hexdigest(),
        "filename": digest[:20] + ".jpg",
        "bytes": 5,
        "sha256": digest,
    }
    if invalid == "filename":
        record["filename"] = "../outside.jpg"
    if invalid == "extension":
        record["filename"] = digest[:20] + ".svg"
    if invalid == "oversize":
        record["bytes"] = 2_000_001
    if invalid == "status":
        record["status"] = "needs_review"
    records = [record, record] if invalid == "duplicate" else [record]
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "index.json.gz"
    manifest.write_text(json.dumps(records))
    output.write_bytes(b"previous output")
    command = [
        sys.executable,
        str(ROOT / "scripts/build_poster_index.py"),
        str(manifest),
        str(output),
    ]
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    if invalid:
        assert result.returncode != 0
        assert output.read_bytes() == b"previous output"
        return
    assert result.returncode == 0, result.stderr
    data = json.loads(gzip.decompress(output.read_bytes()))
    assert data["version"] == 1
    assert (
        data["source_manifest_sha256"]
        == sha256(manifest.read_bytes()).hexdigest()
    )
    identity = (
        "d3f2465204fded8c82229425fe98e4451ef6326565e44698a014f96c3f9df0cf"
    )
    assert data["films"] == {identity: [record["filename"], 5, digest]}
    first = output.read_bytes()
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
    assert output.read_bytes() == first


@pytest.mark.parametrize("manifest_json", ["{}", '""', "null", "0", "false"])
def test_index_command_rejects_non_array_manifests_before_replacement(
    tmp_path: Path, manifest_json: str
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(manifest_json)
    output = tmp_path / "index.json.gz"
    output.write_bytes(b"previous index")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_poster_index.py"),
            str(manifest),
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert output.read_bytes() == b"previous index"


def test_index_command_preserves_previous_output_on_disk_write_failure(
    tmp_path: Path,
) -> None:
    """A real OS file-size limit interrupts the generated gzip write."""
    digest = sha256(b"image").hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "title": f"Film {number}",
                    "year": 2001,
                    "status": "stored",
                    "source_synopsis_sha256": sha256(b"Story.").hexdigest(),
                    "filename": digest[:20] + ".jpg",
                    "bytes": 5,
                    "sha256": digest,
                }
                for number in range(200)
            ]
        )
    )
    output = tmp_path / "index.json.gz"
    output.write_bytes(b"previous index")
    wrapper = """
import resource
import runpy
import signal
import sys
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (512, 512))
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            wrapper,
            str(ROOT / "scripts/build_poster_index.py"),
            str(manifest),
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert output.read_bytes() == b"previous index"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "index.json.gz",
        "manifest.json",
    ]
