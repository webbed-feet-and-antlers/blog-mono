"""ICEWS fetch (network-only; not exercised by tests).

Downloads a pinned ICEWS release from the Harvard Dataverse
(DOI 10.7910/DVN/28075) into data/raw/icews/. Dataverse file ids differ per
release — find the .tab.zip file id on the dataset's file listing page and
pass it (or a full access URL) here. All requests go through guarded_urlopen
(public http/https only, redirects blocked).
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kg_pipeline.httpguard import guarded_urlopen  # noqa: E402
from kg_pipeline.paths import safe_out_dir  # noqa: E402

DATAVERSE_API = "https://dataverse.harvard.edu/api/access/datafile/{file_id}"
MAX_DOWNLOAD_BYTES = 8 << 30  # ICEWS yearly archives are large; safety cap


def _validate_members(zf: zipfile.ZipFile) -> None:
    for info in zf.infolist():
        name = info.filename
        parts = Path(name).parts
        if name.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
            raise ValueError(f"unsafe zip member {name!r}")


def fetch_icews(file_id_or_url, out_dir, allowed_root=None) -> Path:
    """Download and extract one ICEWS .tab.zip archive from Dataverse."""
    if str(file_id_or_url).isdigit():
        url = DATAVERSE_API.format(file_id=file_id_or_url)
    else:
        url = str(file_id_or_url)

    out = safe_out_dir(out_dir, allowed_root or Path.cwd())
    out.mkdir(parents=True, exist_ok=True)

    req_url = url if "api/access" in url else f"{url}?gbrecs=true"
    with guarded_urlopen(req_url, timeout=600) as resp:
        data = resp.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("download exceeds cap; fetch per-year archives instead")

    archive_path = out / "icews.zip"
    archive_path.write_bytes(data)
    with zipfile.ZipFile(archive_path) as zf:
        _validate_members(zf)
        zf.extractall(out)
    return out
