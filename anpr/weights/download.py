"""Download and verify the ONNX model weights listed in HASHES.txt.

Usage:
    python anpr/weights/download.py [--dir /app/weights] [--only yolox_s.onnx] [--require] [--no-download]

Every entry in HASHES.txt is ``<sha256>  <file>  <url>  <licence>``. A file that already
exists with the right hash is left alone; a wrong hash is re-downloaded once. With
``--require`` any missing, mismatched or undownloadable weight exits 1 - the image build uses
this so a failed GitHub download can never produce a green image without the ONNX detector.
Without ``--require`` the script only reports (used by hand for diagnostics).

The worker calls :func:`verify_weight_file` at start-up for every model it loads, so a file
that was tampered with or truncated after the build is refused (see ``anpr/detector.py``).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
HASHES_FILE = HERE / "HASHES.txt"


@dataclass(frozen=True)
class WeightSpec:
    sha256: str
    name: str
    url: str
    licence: str


def read_specs(path: Path = HASHES_FILE) -> list[WeightSpec]:
    """Parse HASHES.txt into WeightSpec entries (comments and blank lines ignored)."""
    specs: list[WeightSpec] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 3)
        if len(parts) < 3:
            raise ValueError(f"malformed line in {path}: {line!r}")
        licence = parts[3] if len(parts) == 4 else ""
        specs.append(WeightSpec(parts[0].lower(), parts[1], parts[2], licence))
    return specs


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path, timeout: float = 120.0) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "sentinel-gujarat-anpr/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    os.replace(tmp, dest)


def ensure_weight(spec: WeightSpec, weights_dir: Path, download: bool = True) -> tuple[bool, str]:
    """Make sure ``weights_dir/spec.name`` exists with the expected hash.

    Returns (ok, message). Never raises on network errors.
    """
    dest = weights_dir / spec.name
    if dest.exists():
        actual = sha256_of(dest)
        if actual == spec.sha256:
            return True, f"{spec.name}: present, sha256 verified"
        if not download:
            return False, f"{spec.name}: hash mismatch ({actual[:16]}... != {spec.sha256[:16]}...)"
        print(f"{spec.name}: hash mismatch, re-downloading", file=sys.stderr)
    elif not download:
        return False, f"{spec.name}: missing"
    try:
        weights_dir.mkdir(parents=True, exist_ok=True)
        _download(spec.url, dest)
    except Exception as exc:  # noqa: BLE001 - report any network/IO failure
        return False, f"{spec.name}: download failed: {exc}"
    actual = sha256_of(dest)
    if actual != spec.sha256:
        dest.unlink(missing_ok=True)
        return False, f"{spec.name}: downloaded file hash {actual} does not match {spec.sha256}"
    return True, f"{spec.name}: downloaded, sha256 verified"


VERIFY_STATES = ("verified", "unlisted", "missing", "mismatch")


@dataclass(frozen=True)
class WeightStatus:
    """Result of :func:`verify_weight_file`.

    ``state`` is one of ``verified`` (listed in HASHES.txt and the SHA-256 matches), ``unlisted``
    (file exists but HASHES.txt has no entry for that file name - accepted, e.g. a custom model),
    ``missing`` (no such file) or ``mismatch`` (listed, but the SHA-256 differs).
    """

    path: str
    state: str
    sha256: str | None = None
    expected: str | None = None
    licence: str = ""

    @property
    def ok(self) -> bool:
        return self.state in ("verified", "unlisted")

    def describe(self) -> str:
        name = Path(self.path).name
        if self.state == "verified":
            return f"{name}: sha256 verified ({self.sha256[:16]}...)"
        if self.state == "unlisted":
            return f"{name}: not listed in HASHES.txt, hash not checked ({self.sha256[:16]}...)"
        if self.state == "missing":
            return f"{name}: missing ({self.path})"
        return f"{name}: SHA-256 MISMATCH {self.sha256[:16]}... != expected {self.expected[:16]}..."


def verify_weight_file(path: str | os.PathLike[str], hashes_file: Path = HASHES_FILE) -> WeightStatus:
    """Check one model file against the HASHES.txt ledger without downloading anything.

    Never raises for missing or corrupt files (the caller decides whether that is fatal);
    only a malformed ledger raises ``ValueError``.
    """
    target = Path(path)
    if not target.is_file():
        return WeightStatus(str(target), "missing")
    actual = sha256_of(target)
    specs = {spec.name: spec for spec in read_specs(hashes_file)} if hashes_file.is_file() else {}
    spec = specs.get(target.name)
    if spec is None:
        return WeightStatus(str(target), "unlisted", sha256=actual)
    if actual != spec.sha256:
        return WeightStatus(str(target), "mismatch", sha256=actual, expected=spec.sha256, licence=spec.licence)
    return WeightStatus(str(target), "verified", sha256=actual, expected=spec.sha256, licence=spec.licence)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default=os.environ.get("ANPR_WEIGHTS_DIR", str(HERE)), help="target directory")
    parser.add_argument("--only", action="append", default=[], help="restrict to these file names")
    parser.add_argument("--require", action="store_true", help="exit 1 if any weight is unavailable")
    parser.add_argument("--no-download", action="store_true", help="verify only")
    args = parser.parse_args(argv)

    weights_dir = Path(args.dir)
    all_ok = True
    for spec in read_specs():
        if args.only and spec.name not in args.only:
            continue
        ok, message = ensure_weight(spec, weights_dir, download=not args.no_download)
        print(message)
        all_ok &= ok
    if not all_ok and args.require:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
