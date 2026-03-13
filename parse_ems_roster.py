#!/usr/bin/env python3
"""
Extract Nebraska DHHS EMS Services Employee/Volunteer Roster from PDF to CSV.
Source: https://dhhs.ne.gov/licensure/Documents/EMS_Roster.pdf

PDF type: Type A Roster + Type D Hierarchical.
Each page has a 10-line service header (context), followed by employee records.
Each employee record is 2 lines: name+lic_no+license_type+expiration_date, then address.
Some license types ("Emergency Medical Responder", "APRN-Nurse Practitioner") wrap
onto the address line; detected by matching the last token against WRAPPED_CONTINUATIONS.
"""

import argparse
import csv
import re
from datetime import date
from pathlib import Path

import pdfplumber
import requests

PDF_URL = "https://dhhs.ne.gov/licensure/Documents/EMS_Roster.pdf"
PDF_DIR = Path("pdfs")
DATA_DIR = Path("data")
SLUG = "ems_roster"

# ── Page header patterns ──────────────────────────────────────────────────────

SERVICE_LEVEL_RE = re.compile(r"^(EMS (?:Basic|Advanced) Service)$")
# \s* (zero or more spaces) handles cases where "Transport" is concatenated without a space
SERVICE_NAME_RE = re.compile(r"^(.+?)\s*((?:Non-)?Transport)$")
# Matches ZIP-4 with or without dash (e.g., "69121-8640" or "691218640")
CITY_RE = re.compile(r"^City:\s+(.+?)\s+(\d{5}(?:-?\d{4})?)$")
LICENSE_CONTACT_RE = re.compile(
    r"^Service License No:\s+(\S+)\s+Service Contact Person:\s+(.+)$"
)
# Medical director name is optional (some services list none)
PHONE_DIRECTOR_RE = re.compile(
    r"^Service Phone No:\s+(.+?)\s+Medical Director:\s*(.*)$"
)

# ── Employee record patterns ──────────────────────────────────────────────────

# Name (Last, First [MI] [creds])  LIC_NO  LICENSE_TYPE  MM/DD/YYYY
EMPLOYEE_RE = re.compile(
    r"^(.+?)\s+(\d+)\s+(.+?)\s+(\d{2}/\d{2}/\d{4})\s*$"
)

# ZIP code: 5 digits, optionally followed by -XXXX (or trailing dash for truncated ZIPs)
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?-?$")

# Words that can appear at the end of an address line as a wrapped license type continuation.
# Only these exact tokens trigger the join — avoids false positives from odd address text.
# Known wraps: "Emergency Medical Responder", "APRN-Nurse Practitioner",
#              "APRN-Clinical Nurse Specialist", "Osteopathic Physician & Surgeon"
WRAPPED_CONTINUATIONS = {"Responder", "Practitioner", "Specialist", "Surgeon"}

# Lines to skip anywhere in the data section
_SKIP_EXACT = {
    "Division of Public Health",
    "EMS Services Employee/Volunteer Roster",
    "SERVICE NAME AND INFORMATION",
    "EMPLOYEE/VOLUNTEER NAME",
    "This list includes employees with active licenses only",
}
_SKIP_PREFIXES = ("and ADDRESS", "SERVICE NAME")
_FOOTER_RE = re.compile(r"^\d{1,2}/\d{2}/\d{2}\s+\d+$")  # "2/17/26 556"


def _is_skip_line(line: str) -> bool:
    if line in _SKIP_EXACT:
        return True
    if any(line.startswith(p) for p in _SKIP_PREFIXES):
        return True
    if SERVICE_LEVEL_RE.match(line):
        return True
    if _FOOTER_RE.match(line):
        return True
    return False


# ── Core extraction ───────────────────────────────────────────────────────────


def _parse_page_header(lines: list[str]) -> dict:
    """Return service context dict parsed from the page's header block."""
    service: dict = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = SERVICE_LEVEL_RE.match(line)
        if m:
            service["service_level"] = m.group(1)
            continue
        m = SERVICE_NAME_RE.match(line)
        if m and "service_level" in service and "service_name" not in service:
            service["service_name"] = m.group(1).strip()
            service["service_transport_type"] = m.group(2).strip()
            continue
        m = CITY_RE.match(line)
        if m:
            service["service_city"] = m.group(1).strip()
            service["service_zip"] = m.group(2).strip()
            continue
        m = LICENSE_CONTACT_RE.match(line)
        if m:
            service["service_license_no"] = m.group(1).strip()
            service["service_contact_person"] = m.group(2).strip()
            continue
        m = PHONE_DIRECTOR_RE.match(line)
        if m:
            service["service_phone"] = m.group(1).strip()
            service["medical_director"] = m.group(2).strip()
            continue
        if line.startswith("and ADDRESS"):
            break
    return service


# X-coordinate boundary separating the left (service name) column from the
# right (Transport type / city) column.  Confirmed across all sampled pages.
_NAME_COL_MAX_X = 390.0


def _service_name_from_words(page) -> tuple[str, str]:
    """
    Fall back to word-position extraction when text extraction garbles the
    service name line (PDF font-rendering artefact interleaves two words).

    Returns (service_name, service_transport_type).
    """
    words = page.extract_words()

    # Group words by rounded y-position (tolerance ±2 pts)
    groups: dict[int, list] = {}
    for w in words:
        y = round(w["top"] / 2) * 2  # round to nearest even integer
        groups.setdefault(y, []).append(w)

    sorted_ys = sorted(groups)

    # Find the y-group containing "EMS Basic/Advanced Service" (right-hand header)
    ems_y = None
    for y in sorted_ys:
        row_text = " ".join(w["text"] for w in sorted(groups[y], key=lambda w: w["x0"]))
        if SERVICE_LEVEL_RE.search(row_text):
            ems_y = y
            break
    if ems_y is None:
        return "", ""

    # The service name row is the next y-group after the EMS level row
    ems_idx = sorted_ys.index(ems_y)
    if ems_idx + 1 >= len(sorted_ys):
        return "", ""
    sn_y = sorted_ys[ems_idx + 1]
    sn_words = sorted(groups[sn_y], key=lambda w: w["x0"])

    name_words = [w["text"] for w in sn_words if w["x0"] < _NAME_COL_MAX_X]
    right_words = [w["text"] for w in sn_words if w["x0"] >= _NAME_COL_MAX_X]
    right_text = " ".join(right_words)

    service_name = " ".join(name_words).strip()
    if "Non-Transport" in right_text:
        transport_type = "Non-Transport"
    else:
        # Default to Transport (most common; garbled text obscures exact value)
        transport_type = "Transport"

    return service_name, transport_type


def extract_records(pdf_path: Path, date_str: str = None) -> list[dict]:
    """Extract all employee/volunteer records from the PDF."""
    records: list[dict] = []
    service: dict = {}
    pending: dict | None = None  # employee waiting for its address line

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            lines = [ln.strip() for ln in text.split("\n")]

            # Update service context from page header
            page_service = _parse_page_header(lines)
            if page_service:
                service = page_service
                # If service_name is still missing (garbled line in text extraction),
                # fall back to word-position extraction for just the name + transport type.
                if not service.get("service_name"):
                    sn, tt = _service_name_from_words(page)
                    if sn:
                        service["service_name"] = sn
                        service["service_transport_type"] = tt

            # Find first employee data line (after "and ADDRESS …" column header)
            data_start = 0
            for i, ln in enumerate(lines):
                if ln.startswith("and ADDRESS"):
                    data_start = i + 1
                    break

            # Process employee records
            for line in lines[data_start:]:
                if not line:
                    continue
                if _is_skip_line(line):
                    continue

                m = EMPLOYEE_RE.match(line)
                if m:
                    # Flush previous record
                    if pending:
                        records.append(pending)
                    pending = {
                        "service_level": service.get("service_level", ""),
                        "service_name": service.get("service_name", ""),
                        "service_transport_type": service.get("service_transport_type", ""),
                        "service_city": service.get("service_city", ""),
                        "service_zip": service.get("service_zip", ""),
                        "service_license_no": service.get("service_license_no", ""),
                        "service_contact_person": service.get(
                            "service_contact_person", ""
                        ),
                        "service_phone": service.get("service_phone", ""),
                        "medical_director": service.get("medical_director", ""),
                        "employee_name": m.group(1).strip(),
                        "license_no": m.group(2).strip(),
                        "license_type": m.group(3).strip(),
                        "expiration_date": m.group(4).strip(),
                        "address": "",
                    }
                elif pending is not None and not pending["address"]:
                    # Address line — check for wrapped license type continuation.
                    # Only known continuation words trigger the join; avoids false positives
                    # from unusual address text (e.g., "USA Benkelman" at line end).
                    tokens = line.split()
                    if tokens and tokens[-1] in WRAPPED_CONTINUATIONS:
                        pending["license_type"] += " " + tokens[-1]
                        pending["address"] = " ".join(tokens[:-1])
                    else:
                        pending["address"] = line
                    records.append(pending)
                    pending = None

    # Flush any trailing record (address-less edge case)
    if pending:
        records.append(pending)

    return records


# ── I/O helpers ───────────────────────────────────────────────────────────────


def download_pdf(url: str = PDF_URL) -> Path:
    """Download the current PDF and save with today's date stamp."""
    PDF_DIR.mkdir(exist_ok=True)
    date_str = date.today().strftime("%Y-%m-%d")
    dest = PDF_DIR / f"{SLUG}_{date_str}.pdf"
    if dest.exists():
        print(f"Already downloaded: {dest}")
        return dest
    print(f"Downloading {url} ...")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    dest.write_bytes(r.content)
    print(f"Saved: {dest}")
    return dest


def save_to_csv(records: list[dict], output_path: Path = None) -> Path:
    """Write records to a date-stamped CSV in data/."""
    DATA_DIR.mkdir(exist_ok=True)
    if output_path is None:
        date_str = date.today().strftime("%Y-%m-%d")
        output_path = DATA_DIR / f"{SLUG}_{date_str}.csv"
    if not records:
        print("No records to write.")
        return output_path
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    print(f"Wrote {len(records)} records to {output_path}")
    return output_path


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract Nebraska DHHS EMS roster from PDF to CSV."
    )
    parser.add_argument(
        "pdf_path",
        nargs="?",
        help="Path to local PDF (downloads current if omitted)",
    )
    parser.add_argument("-o", "--output", help="Output CSV path")
    parser.add_argument("--date", help="Date string for filename (YYYY-MM-DD)")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path) if args.pdf_path else download_pdf()
    date_str = args.date or date.today().strftime("%Y-%m-%d")

    records = extract_records(pdf_path, date_str=date_str)
    print(f"Extracted {len(records)} records")

    output_path = Path(args.output) if args.output else None
    save_to_csv(records, output_path)


if __name__ == "__main__":
    main()
