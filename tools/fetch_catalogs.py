#!/usr/bin/env python3
"""Recreate the checked-in fixed-object catalogs from their cited sources."""

import base64
import csv
import math
import re
import struct
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "catalogs"
TAP = "https://heasarc.gsfc.nasa.gov/xamin/vo/tap/sync"
WGSN = "https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt"
BOUNDARIES = (
    "https://raw.githubusercontent.com/pavolgaj/ObsPlanner/master/data/bound_20.dat"
)
CONSTELLATION_NAMES = (
    "https://raw.githubusercontent.com/astropy/astropy/v7.2.0/"
    "astropy/coordinates/data/constellation_names.dat"
)
NON_STELLAR_HR = {
    92, 95, 182, 1057, 1841, 2472, 2496, 3515,
    3671, 6309, 6515, 7189, 7539, 8296,
}
GREEK = {
    "Alp": "alf", "Bet": "bet", "Gam": "gam", "Del": "del", "Eps": "eps",
    "Zet": "zet", "Eta": "eta", "The": "the", "Iot": "iot", "Kap": "kap",
    "Lam": "lam", "Mu": "mu", "Nu": "nu", "Xi": "xi", "Omi": "omi",
    "Pi": "pi", "Rho": "rho", "Sig": "sig", "Tau": "tau", "Ups": "ups",
    "Phi": "phi", "Chi": "chi", "Psi": "psi", "Ome": "ome",
}


def download(url):
    with urllib.request.urlopen(url, timeout=90) as response:
        return response.read()


def parse_votable(content):
    root = ET.fromstring(content)
    for info in root.findall(".//{*}INFO"):
        if info.get("name") == "QUERY_STATUS" and info.get("value") != "OK":
            raise ValueError(f"TAP query failed: {info.get('value')}: {info.text}")
    table = root.find(".//{*}TABLE")
    if table is None:
        raise ValueError("TAP response contains no table")
    fields = table.findall("{*}FIELD")
    stream = table.find(".//{*}BINARY/{*}STREAM")
    if stream is None or stream.get("encoding") != "base64" or not stream.text:
        raise ValueError("TAP response does not contain a base64 BINARY table")
    data = memoryview(base64.b64decode(stream.text))
    formats = {"short": ">h", "int": ">i", "long": ">q",
               "float": ">f", "double": ">d"}
    offset = 0
    records = []
    while offset < len(data):
        record = {}
        for field in fields:
            datatype = field.get("datatype")
            if datatype == "char" and field.get("arraysize") == "*":
                length = struct.unpack_from(">i", data, offset)[0]
                offset += 4
                if length < 0 or offset + length > len(data):
                    raise ValueError("Invalid VOTable string length")
                value = bytes(data[offset:offset + length]).decode("ascii").strip()
                offset += length
            elif datatype in formats and field.get("arraysize") is None:
                fmt = formats[datatype]
                value = struct.unpack_from(fmt, data, offset)[0]
                offset += struct.calcsize(fmt)
                if isinstance(value, float) and not math.isfinite(value):
                    value = None
            else:
                raise ValueError(f"Unsupported VOTable field: {ET.tostring(field)}")
            record[field.get("name")] = value
        records.append(record)
    return records


def tap(query):
    params = urllib.parse.urlencode({
        "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable",
        "MAXREC": "20000", "QUERY": query,
    })
    return parse_votable(download(f"{TAP}?{params}"))


def ascii_name(value):
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def wgsn_names(content):
    names = {}
    for line in content.decode("utf-8").splitlines():
        match = re.search(r"\bHR\s+(\d+)\b", line[30:])
        if match:
            names[int(match.group(1))] = ascii_name(line[:18].strip())
    return names


def star_id(star):
    alt = star["alt_name"]
    match = re.fullmatch(r"\d*([A-Za-z]{2,3})(\d*)\s*([A-Za-z]{3})", alt)
    if match and match.group(1) in GREEK:
        return f"{GREEK[match.group(1)]}{match.group(2)} {match.group(3)}"
    match = re.fullmatch(r"(\d+)\s*([A-Za-z]{3})", alt)
    return f"{match.group(1)} {match.group(2)}" if match else f"HR {star['hr']}"


def angular_distance(a, b):
    dec1, dec2 = math.radians(a["dec"]), math.radians(b["dec"])
    delta_ra = math.radians(a["ra"] - b["ra"])
    hav = (math.sin((dec1 - dec2) / 2) ** 2 +
           math.cos(dec1) * math.cos(dec2) * math.sin(delta_ra / 2) ** 2)
    return 2 * math.asin(min(1.0, math.sqrt(hav)))


def group_close_stars(stars):
    """Combine stellar components within one arcminute using summed flux."""
    ordered = sorted(stars, key=lambda star: star["dec"])
    parents = list(range(len(ordered)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    limit = math.radians(1 / 60)
    for i, star in enumerate(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j]["dec"] - star["dec"] <= 1 / 60:
            if angular_distance(star, ordered[j]) <= limit:
                parents[find(j)] = find(i)
            j += 1
    groups = {}
    for i, star in enumerate(ordered):
        groups.setdefault(find(i), []).append(star)
    combined = []
    for members in groups.values():
        primary = min(members, key=lambda star: (star["vmag"], star["hr"]))
        fluxes = [10 ** (-0.4 * star["vmag"]) for star in members]
        total = sum(fluxes)
        result = dict(primary)
        result["vmag"] = -2.5 * math.log10(total)
        for field in ("pmra", "pmdec"):
            result[field] = primary[field] or 0
        combined.append(result)
    return sorted(combined, key=lambda star: (star["vmag"], star["hr"]))


def fixed_row(identifier, name, ra, dec, mag=None, pmra=0, pmdec=0,
              object_type="other"):
    return {
        "id": identifier, "name": name, "ra_deg": f"{ra:.7f}",
        "dec_deg": f"{dec:.7f}", "pm_ra_mas_yr": f"{pmra:.2f}",
        "pm_dec_mas_yr": f"{pmdec:.2f}",
        "mag": "" if mag is None else f"{mag:.2f}", "type": object_type,
    }


def bright_stars():
    print("Fetching bright stars and IAU names...")
    rows = tap(
        "SELECT name,alt_name,ra,dec,pmra,pmdec,vmag,hr "
        "FROM bsc5p WHERE vmag IS NOT NULL AND ra IS NOT NULL "
        "AND dec IS NOT NULL AND hr IS NOT NULL"
    )
    names = wgsn_names(download(WGSN))
    stars = [row for row in rows if row["hr"] not in NON_STELLAR_HR]
    stars = group_close_stars(stars)[:100]
    if len(stars) != 100:
        raise ValueError(f"Expected 100 bright stars, got {len(stars)}")
    identifiers = set()
    result = []
    for star in stars:
        identifier = star_id(star)
        if identifier in identifiers:
            identifier = f"HR {star['hr']}"
        identifiers.add(identifier)
        result.append(fixed_row(identifier, names.get(star["hr"], ""),
                                star["ra"], star["dec"], star["vmag"],
                                star["pmra"] * 1000, star["pmdec"] * 1000,
                                "star"))
    return result


MESSIER_NAMES = {
    1: "Crab Nebula", 8: "Lagoon Nebula", 13: "Hercules Cluster",
    16: "Eagle Nebula", 17: "Omega Nebula", 20: "Trifid Nebula",
    27: "Dumbbell Nebula", 31: "Andromeda Galaxy", 33: "Triangulum Galaxy",
    42: "Orion Nebula", 51: "Whirlpool Galaxy", 57: "Ring Nebula",
    63: "Sunflower Galaxy", 64: "Black Eye Galaxy", 81: "Bode's Galaxy",
    82: "Cigar Galaxy", 97: "Owl Nebula", 101: "Pinwheel Galaxy",
    102: "Spindle Galaxy", 104: "Sombrero Galaxy",
}


def messier():
    print("Fetching Messier and NGC 5866...")
    rows = tap("SELECT name,ra,dec,vmag,object_type FROM messier")
    ngc = tap("SELECT name,ra,dec,app_mag FROM ngc2000 WHERE name='NGC 5866'")
    if len(ngc) != 1:
        raise ValueError(f"Expected one NGC 5866 record, got {len(ngc)}")
    by_id = {}
    for row in rows:
        match = re.fullmatch(r"M\s*(\d+)", row["name"], re.IGNORECASE)
        if not match:
            raise ValueError(f"Unexpected Messier designation: {row['name']!r}")
        number = int(match.group(1))
        if number in by_id:
            raise ValueError(f"Duplicate Messier designation M{number}")
        by_id[number] = row
    if 102 in by_id:
        raise ValueError("HEASARC now supplies M102; review its identification")
    by_id[102] = {"ra": ngc[0]["ra"], "dec": ngc[0]["dec"],
                  "vmag": ngc[0]["app_mag"], "object_type": "S"}
    if set(by_id) != set(range(1, 111)):
        raise ValueError(f"Incomplete Messier catalog: {sorted(set(range(1, 111)) - set(by_id))}")
    types = {"Di": "nebula", "Gb": "globular_cluster", "OC": "open_cluster",
             "Pl": "planetary_nebula", "E": "galaxy", "Ir": "galaxy",
             "S": "galaxy"}
    return [fixed_row(f"M{i}", MESSIER_NAMES.get(i, ""), by_id[i]["ra"],
                      by_id[i]["dec"], by_id[i]["vmag"],
                      object_type=types.get(by_id[i]["object_type"], "other"))
            for i in range(1, 111)]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def polygon_center(vertices):
    """Direction of the spherical polygon's area-weighted vector centroid."""
    points = []
    for ra_hours, dec_degrees in vertices:
        ra, dec = math.radians(ra_hours * 15), math.radians(dec_degrees)
        points.append((math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra),
                       math.sin(dec)))
    if len(points) < 3:
        raise ValueError("A constellation boundary has fewer than three vertices")
    moment = [0.0, 0.0, 0.0]
    for a, b in zip(points, points[1:] + points[:1]):
        normal = cross(a, b)
        sin_angle = math.sqrt(sum(v * v for v in normal))
        if sin_angle:
            angle = math.atan2(sin_angle, sum(x * y for x, y in zip(a, b)))
            for i in range(3):
                moment[i] += normal[i] * angle / sin_angle
    if sum(moment[i] * sum(point[i] for point in points) for i in range(3)) < 0:
        moment = [-value for value in moment]
    if math.hypot(*moment) < 1e-12:
        raise ValueError("Degenerate constellation boundary")
    return (math.degrees(math.atan2(moment[1], moment[0])) % 360,
            math.degrees(math.atan2(moment[2], math.hypot(*moment[:2]))))


def constellations():
    print("Fetching IAU constellation boundaries and names...")
    groups = {}
    for line in download(BOUNDARIES).decode("ascii").splitlines():
        ra, dec, abbreviation = line.split()
        groups.setdefault(abbreviation, []).append((float(ra), float(dec)))
    names = {}
    for line in download(CONSTELLATION_NAMES).decode("utf-8").splitlines():
        if line and not line.startswith("#"):
            abbreviation, name = line.split(maxsplit=1)
            names[abbreviation] = ascii_name(name)
    if len(names) != 88 or "Ser1" not in groups or "Ser2" not in groups:
        raise ValueError("Incomplete IAU constellation data")
    result = []
    for abbreviation, name in sorted(names.items(), key=lambda item: item[1].casefold()):
        key = "Ser1" if abbreviation == "Ser" else abbreviation
        if key not in groups:
            raise ValueError(f"Missing constellation boundary: {key}")
        ra, dec = polygon_center(groups[key])
        result.append(fixed_row(abbreviation, name, ra, dec,
                                object_type="constellation"))
    return result


def rasc_finest_ngc():
    print("Fetching RASC Finest NGC objects...")
    catalog_path = CATALOG_DIR / "rasc_finest_ngc.csv"
    with catalog_path.open(encoding="utf-8-sig", newline="") as source:
        identifiers = [
            row["id"]
            for row in csv.DictReader(
                line for line in source if not line.startswith("#")
            )
        ]
    if len(identifiers) != 110 or len(set(identifiers)) != 110:
        raise ValueError("RASC Finest NGC catalog must contain 110 unique objects")
    quoted = ", ".join(repr(identifier) for identifier in identifiers)
    rows = tap(
        f"SELECT name,ra,dec,app_mag,object_type FROM ngc2000 "
        f"WHERE name IN ({quoted})"
    )
    by_id = {}
    for row in rows:
        if row["name"] in by_id:
            raise ValueError(f"Duplicate NGC designation: {row['name']}")
        by_id[row["name"]] = row
    missing = set(identifiers) - set(by_id)
    if missing:
        raise ValueError(f"Missing RASC Finest NGC objects: {sorted(missing)}")
    types = {
        "Di": "nebula", "Gb": "globular_cluster", "OC": "open_cluster",
        "Pl": "planetary_nebula", "E": "galaxy", "Ir": "galaxy", "S": "galaxy",
    }
    return [
        fixed_row(
            identifier, "", by_id[identifier]["ra"], by_id[identifier]["dec"],
            by_id[identifier]["app_mag"],
            object_type=types.get(by_id[identifier]["object_type"], "other"),
        )
        for identifier in identifiers
    ]


def write_catalog(filename, title, short_title, source, license_name, rows):
    fields = ("id", "name", "ra_deg", "dec_deg", "pm_ra_mas_yr",
              "pm_dec_mas_yr", "mag", "type")
    path = CATALOG_DIR / filename
    with path.open("w", encoding="utf-8", newline="") as output:
        output.write(f"# title: {title}\n# short_title: {short_title}\n"
                     f"# provider: fixed\n# source: {source}\n"
                     f"# license: {license_name}\n")
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} records to {path}")


def main():
    stars = bright_stars()
    objects = messier()
    finest_ngc = rasc_finest_ngc()
    areas = constellations()
    write_catalog(
        "brightest_stars.csv", "Brightest Stars", "Stars",
        "Yale BSC5 via NASA HEASARC; IAU WGSN stellar names",
        "BSC5 research use; IAU names CC BY", stars,
    )
    write_catalog(
        "messier.csv", "Messier", "Messier",
        "NASA HEASARC Messier and NGC 2000.0 (NGC 5866 for M102)",
        "NGC 2000.0: Sky Publishing; scientific research only", objects,
    )
    write_catalog(
        "rasc_finest_ngc.csv", "RASC Finest NGC", "Finest NGC",
        "Royal Astronomical Society of Canada Finest NGC checklist; NASA HEASARC NGC 2000.0",
        "RASC observing-program list; NGC 2000.0: Sky Publishing; scientific research only",
        finest_ngc,
    )
    write_catalog(
        "constellations.csv", "IAU Constellations", "Constellations",
        "IAU boundaries, Davenhall and Leggett 1989 CDS VI/49; IAU names via Astropy",
        "CDS distribution; Astropy BSD-3-Clause", areas,
    )


if __name__ == "__main__":
    main()
