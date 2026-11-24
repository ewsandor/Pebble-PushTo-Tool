#!/usr/bin/env python3
"""Compile registered, attributed CSV catalogs into the watch resource."""

import argparse
import csv
import io
import json
import math
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK_HEADER = struct.Struct("<4sBBH")
DIRECTORY_ENTRY = struct.Struct("<IIBBH")
CATALOG_HEADER = struct.Struct("<HHIIHH")
FIXED_RECORD = struct.Struct("<iihhhBBHH")
BODY_RECORD = struct.Struct("<BBHH10x")
UNKNOWN_MAGNITUDE = -32768
PROVIDERS = {"fixed": 0, "solar_system": 1}
BODY_KINDS = {"sun": 0, "moon": 1, "planet": 2, "pluto": 3, "minor": 4}
OBJECT_TYPES = {
    "star": 0,
    "galaxy": 1,
    "open_cluster": 2,
    "globular_cluster": 3,
    "nebula": 4,
    "planetary_nebula": 5,
    "constellation": 6,
    "other": 7,
    "double_star": 8,
    "asterism": 9,
}


def _number(value, label, minimum, maximum, default=None):
    if not value and default is not None:
        return default
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return number


def _read_catalog(path):
    metadata = {}
    data = []
    with path.open(encoding="utf-8-sig", newline="") as source:
        for line in source:
            if line.startswith("#"):
                key, separator, value = line[1:].partition(":")
                if not separator or not value.strip():
                    raise ValueError(f"{path}: invalid metadata line {line.strip()!r}")
                key = key.strip()
                if key in metadata:
                    raise ValueError(f"{path}: duplicate metadata {key!r}")
                metadata[key] = value.strip()
            else:
                data.append(line)
    for key in ("title", "short_title", "provider", "source", "license"):
        if not metadata.get(key):
            raise ValueError(f"{path}: missing {key} metadata")
    if metadata["provider"] not in PROVIDERS:
        raise ValueError(f"{path}: unknown provider {metadata['provider']!r}")
    for key, limit in (("title", 39), ("short_title", 23), ("source", 127)):
        if len(metadata[key].encode("utf-8")) > limit:
            raise ValueError(f"{path}: {key} exceeds {limit} UTF-8 bytes")
    reader = csv.DictReader(io.StringIO("".join(data)))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError(f"{path}: missing or duplicate CSV columns")
    rows = list(reader)
    if not rows or len(rows) > 65535:
        raise ValueError(f"{path}: catalog must contain 1 to 65535 objects")
    return metadata, rows


def _compile_catalog(path):
    metadata, rows = _read_catalog(path)
    strings = bytearray(b"\0")
    string_offsets = {"": 0}

    def intern(value):
        value = (value or "").strip()
        if "\0" in value:
            raise ValueError(f"{path}: NUL in a string")
        if value not in string_offsets:
            encoded = value.encode("utf-8") + b"\0"
            if len(strings) + len(encoded) > 65535:
                raise ValueError(f"{path}: string table exceeds 65535 bytes")
            string_offsets[value] = len(strings)
            strings.extend(encoded)
        return string_offsets[value]

    title = intern(metadata["title"])
    short_title = intern(metadata["short_title"])
    provider = metadata["provider"]
    record_format = FIXED_RECORD if provider == "fixed" else BODY_RECORD
    records = bytearray()
    seen = set()
    for row_number, row in enumerate(rows, start=1):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"{path}: malformed row {row_number}")
        object_id = row.get("id", "").strip()
        name = row.get("name", "").strip()
        if len(object_id.encode("utf-8")) > 31 or len(name.encode("utf-8")) > 63:
            raise ValueError(f"{path}: ID or name exceeds watch buffer in row {row_number}")
        identity = object_id or name
        if not identity or identity.casefold() in seen:
            raise ValueError(f"{path}: missing or duplicate object ID/name {identity!r}")
        seen.add(identity.casefold())
        id_offset, name_offset = intern(object_id), intern(name)
        if provider == "fixed":
            ra = _number(row.get("ra_deg", ""), "ra_deg", 0, 360)
            if ra == 360:
                raise ValueError(f"{path}: right ascension must be less than 360")
            dec = _number(row.get("dec_deg", ""), "dec_deg", -90, 90)
            pm_ra = _number(row.get("pm_ra_mas_yr", ""), "pm_ra_mas_yr",
                            -32768, 32767, 0)
            pm_dec = _number(row.get("pm_dec_mas_yr", ""), "pm_dec_mas_yr",
                             -32768, 32767, 0)
            mag = _number(row.get("mag", ""), "mag", -327.67, 327.67,
                          UNKNOWN_MAGNITUDE / 100)
            object_type = row.get("type", "").strip()
            if object_type not in OBJECT_TYPES:
                raise ValueError(f"{path}: unknown object type {object_type!r}")
            records.extend(record_format.pack(
                round(ra * 3600000), round(dec * 3600000),
                round(pm_ra), round(pm_dec), round(mag * 100),
                OBJECT_TYPES[object_type], 0, id_offset, name_offset))
        else:
            kind = row.get("kind", "").strip()
            if kind not in BODY_KINDS:
                raise ValueError(f"{path}: unknown body kind {kind!r}")
            index = _number(row.get("index", ""), "index", 0, 255)
            if not index.is_integer():
                raise ValueError(f"{path}: body index must be an integer")
            records.extend(record_format.pack(
                BODY_KINDS[kind], int(index), id_offset, name_offset))
    records_offset = CATALOG_HEADER.size
    strings_offset = records_offset + len(records)
    header = CATALOG_HEADER.pack(
        len(rows), record_format.size, records_offset, strings_offset,
        title, short_title)
    return PROVIDERS[provider], header + records + strings


def _write_credits(metadata, json_path, markdown_path):
    if json_path:
        credits = [{key: item[key] for key in ("title", "source", "license")}
                   for item in metadata]
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(json.dumps(credits, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    if markdown_path:
        def cell(text):
            return text.replace("|", r"\|")

        rows = "\n".join(
            f"| {cell(item['title'])} | {cell(item['source'])} | {cell(item['license'])} |"
            for item in metadata
        )
        document = """\
# Credits and source terms

Generated by `tools/build_catalogs.py` from the catalog headers. Edit the headers,
not this file, to update catalog credits.

Catalog data are included for personal and scientific use only. Review each
source's terms before any other use. The project's MIT code license does not
apply to third-party data.

## Registered catalogs

| Catalog | Source | Stated terms |
|---|---|---|
""" + rows + """

## Other sources and acknowledgements

- Jean Meeus, *Astronomical Algorithms*, 2nd edition (1998); lunar theory
  ELP-2000/82 by Chapront-Touze and Chapront; Pluto series after Goffin,
  Meeus and Steyaert.
- P. Bretagnon and G. Francou, VSOP87, Astronomy & Astrophysics 202 (1988),
  309; IMCCE / Paris Observatory, distributed by CDS/VizieR VI/81.
- NASA/JPL Solar System Dynamics: Horizons and Small-Body Database. The
  retrieval date for the bundled orbital elements was not recorded.
- D. Hoffleit and W. H. Warren Jr., Yale Bright Star Catalogue, 5th revised
  edition (1991), via NASA HEASARC; IAU Working Group on Star Names.
- A. Hirshfeld and R. W. Sinnott, *Sky Catalogue 2000.0* (1985), via NASA
  HEASARC Messier; NGC 2000.0, Sky Publishing.
- IAU constellation boundaries, E. Delporte (1930), digitized by A. C.
  Davenhall and S. K. Leggett (1989), CDS/VizieR VI/49; constellation names
  from Astropy.
- IERS Bulletin C; F. Espenak and J. Meeus, NASA/TP-2006-214141 (delta T).
- Lunar and Pluto coefficient tables adapted from Sonia Keys' Meeus
  implementation; see [its MIT license](LICENSES/soniakeys-meeus.txt).
- Status icons from Pebble/Rebble
  [iconography](https://github.com/pebble-dev/iconography), resized and
  recolored; see [Apache-2.0 terms](LICENSES/pebble-iconography.txt).
- Settings page generated by [Clay for Pebble](https://github.com/pebble-dev/clay)
  (`@rebble/clay`); see [its MIT license](LICENSES/rebble-clay.txt).
- Weather is supplied at runtime by the user's Pebble weather plugin.
See the [design](docs/DESIGN.md#13-dependencies-data-sources-and-attribution)
for further source references.
"""
        Path(markdown_path).write_text(document, encoding="utf-8")


def build(registry_path, output_path, json_path=None, markdown_path=None):
    registry_path = Path(registry_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    names = registry.get("catalogs")
    if not isinstance(names, list) or not 1 <= len(names) <= 255:
        raise ValueError("registry must contain 1 to 255 catalogs")
    if any(not isinstance(name, str) or Path(name).name != name for name in names):
        raise ValueError("registry entries must be unique local CSV filenames")
    if len(set(names)) != len(names):
        raise ValueError("registry entries must be unique local CSV filenames")
    catalogs = [_compile_catalog(registry_path.parent / name) for name in names]
    offset = PACK_HEADER.size + DIRECTORY_ENTRY.size * len(catalogs)
    entries = bytearray()
    blobs = bytearray()
    for provider, blob in catalogs:
        entries.extend(DIRECTORY_ENTRY.pack(offset, len(blob), provider, 0, 0))
        blobs.extend(blob)
        offset += len(blob)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(PACK_HEADER.pack(b"ASCP", 1, len(catalogs), 0) +
                            entries + blobs)
    if json_path or markdown_path:
        metadata = [_read_catalog(registry_path.parent / name)[0] for name in names]
        _write_credits(metadata, json_path, markdown_path)
    return len(catalogs), offset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "catalogs/catalogs.json")
    parser.add_argument("--output", type=Path, default=ROOT / "resources/catalogs.bin")
    parser.add_argument("--credits-json", type=Path)
    parser.add_argument("--credits-md", type=Path)
    args = parser.parse_args()
    try:
        count, size = build(args.registry, args.output, args.credits_json, args.credits_md)
    except (OSError, ValueError, json.JSONDecodeError, struct.error) as exc:
        parser.exit(1, f"catalog build failed: {exc}\n")
    print(f"Built {count} catalogs ({size} bytes): {args.output}")


if __name__ == "__main__":
    main()
