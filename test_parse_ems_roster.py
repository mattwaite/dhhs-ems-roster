"""
Tests for parse_ems_roster.py

Verifies extraction of the Nebraska DHHS EMS Services Employee/Volunteer Roster.
Run: pytest test_parse_ems_roster.py -v
"""

import csv
import re
from pathlib import Path

import pytest

from parse_ems_roster import PDF_URL, extract_records, save_to_csv

# ── Fixtures ──────────────────────────────────────────────────────────────────

_PDF_PATH = next(Path("pdfs").glob("*.pdf"), None)


@pytest.fixture(scope="module")
def records():
    """Extract records from the most recent PDF in pdfs/.

    If no PDF is present, downloads it first (mirrors what the parser does
    when run standalone).
    """
    if _PDF_PATH is None:
        import requests, tempfile
        r = requests.get(PDF_URL, timeout=60)
        r.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(r.content)
        tmp.close()
        pdf_path = Path(tmp.name)
    else:
        pdf_path = _PDF_PATH

    return extract_records(pdf_path)


@pytest.fixture(scope="module")
def csv_path(records, tmp_path_factory):
    """Write records to a temporary CSV and return its path."""
    out = tmp_path_factory.mktemp("data") / "ems_roster_test.csv"
    save_to_csv(records, out)
    return out


# ── TestRecordCount ───────────────────────────────────────────────────────────


class TestRecordCount:
    """Verify the total number of extracted records is within expected range."""

    # No footer count in PDF; using ±10% of the confirmed extraction count (6,916).
    EXPECTED_MIN = 6_200
    EXPECTED_MAX = 7_600

    def test_record_count_in_range(self, records):
        count = len(records)
        assert self.EXPECTED_MIN <= count <= self.EXPECTED_MAX, (
            f"Expected {self.EXPECTED_MIN}–{self.EXPECTED_MAX} records, got {count}"
        )

    def test_distinct_services(self, records):
        """At least 350 distinct EMS services (PDF had 411 as of 2026-02-17)."""
        services = {r["service_license_no"] for r in records}
        assert len(services) >= 350, (
            f"Expected ≥350 distinct services, got {len(services)}"
        )

    def test_duplicate_employee_license_pairs_within_expected_range(self, records):
        """The source PDF contains ~93 exact-duplicate employee rows (same person listed
        twice on the same page).  The parser faithfully preserves them.  This test ensures
        the duplicate count stays within the expected range so we detect runaway duplication.
        """
        from collections import Counter
        pairs = [(r["service_license_no"], r["license_no"]) for r in records]
        n_dupes = sum(v - 1 for v in Counter(pairs).values() if v > 1)
        assert n_dupes <= 200, (
            f"Unexpectedly high duplicate (service_license_no, license_no) count: {n_dupes}. "
            "Source PDF has ~93; investigate for parser regression."
        )


# ── TestRequiredColumns ───────────────────────────────────────────────────────


class TestRequiredColumns:
    """Verify all fields are populated at acceptable fill rates."""

    FILL_RATE_THRESHOLDS = {
        # Service-level context — all required (100% expected)
        "service_level": 1.00,
        "service_name": 1.00,
        "service_transport_type": 1.00,
        "service_city": 1.00,
        "service_zip": 1.00,
        "service_license_no": 1.00,
        "service_contact_person": 1.00,
        "service_phone": 1.00,
        # Medical director: 2 services have none listed in the PDF
        "medical_director": 0.99,
        # Employee fields — all required (100% expected)
        "employee_name": 1.00,
        "license_no": 1.00,
        "license_type": 1.00,
        "expiration_date": 1.00,
        "address": 1.00,
    }

    @pytest.mark.parametrize("field,threshold", FILL_RATE_THRESHOLDS.items())
    def test_fill_rate(self, records, field, threshold):
        total = len(records)
        filled = sum(1 for r in records if r.get(field, "").strip())
        rate = filled / total
        assert rate >= threshold, (
            f"Field '{field}' fill rate {rate:.1%} below threshold {threshold:.0%} "
            f"({filled}/{total})"
        )


# ── TestDataFormats ───────────────────────────────────────────────────────────


class TestDataFormats:
    """Verify field values conform to expected formats."""

    def test_service_level_values(self, records):
        """service_level must be one of two known values."""
        valid = {"EMS Basic Service", "EMS Advanced Service"}
        bad = [r["service_level"] for r in records if r["service_level"] not in valid]
        assert not bad, f"Unexpected service_level values: {set(bad)}"

    def test_service_transport_type_values(self, records):
        """service_transport_type must be 'Transport' or 'Non-Transport'."""
        valid = {"Transport", "Non-Transport"}
        bad = [
            r["service_transport_type"]
            for r in records
            if r["service_transport_type"] not in valid
        ]
        assert not bad, f"Unexpected service_transport_type values: {set(bad)}"

    def test_service_license_no_is_numeric(self, records):
        """Service license numbers are numeric strings."""
        bad = [
            r["service_license_no"]
            for r in records
            if not re.match(r"^\d+$", r["service_license_no"])
        ]
        assert not bad, f"Non-numeric service_license_no values: {bad[:5]}"

    def test_employee_license_no_is_numeric(self, records):
        """Employee/volunteer license numbers are numeric strings."""
        bad = [
            r["license_no"]
            for r in records
            if not re.match(r"^\d+$", r["license_no"])
        ]
        assert not bad, f"Non-numeric license_no values: {bad[:5]}"

    def test_expiration_date_format(self, records):
        """Expiration dates must be in MM/DD/YYYY format."""
        DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
        bad = [
            r["expiration_date"]
            for r in records
            if not DATE_RE.match(r["expiration_date"])
        ]
        assert not bad, f"Invalid expiration_date formats: {bad[:5]}"

    def test_phone_number_format(self, records):
        """Service phone numbers should follow (XXX) XXX-XXXX format."""
        PHONE_RE = re.compile(r"^\(\d{3}\) \d{3}-\d{4}$")
        with_phone = [r for r in records if r.get("service_phone")]
        bad = [r["service_phone"] for r in with_phone if not PHONE_RE.match(r["service_phone"])]
        # Allow a small tolerance for unusual formats
        rate = len(bad) / len(with_phone)
        assert rate < 0.02, (
            f"{len(bad)} phone numbers ({rate:.1%}) don't match (XXX) XXX-XXXX: {bad[:5]}"
        )

    def test_zip_code_format(self, records):
        """ZIP codes should be 5-digit, 9-digit (no dash), or ZIP+4 with dash."""
        ZIP_RE = re.compile(r"^\d{5}(-?\d{4})?$")
        bad = [
            r["service_zip"]
            for r in records
            if r.get("service_zip") and not ZIP_RE.match(r["service_zip"])
        ]
        assert not bad, f"Invalid service_zip values: {bad[:5]}"

    def test_license_types_are_known(self, records):
        """All license types should match the set observed in the PDF."""
        KNOWN_TYPES = {
            "EMT",
            "Advanced EMT",
            "Paramedic",
            "Critical Care Paramedic",
            "Emergency Medical Responder",
            "EMS Instructor",
            "Registered Nurse",
            "Licensed Practical Nurse",
            "APRN-Nurse Practitioner",
            "APRN-CRNA",
            "APRN-Clinical Nurse Specialist",
            "Physician",
            "Physician Assistant",
            "Osteopathic Physician & Surgeon",
        }
        unknown = {r["license_type"] for r in records if r["license_type"] not in KNOWN_TYPES}
        assert not unknown, (
            f"Unknown license_type values (possible parsing error): {unknown}"
        )

    def test_employee_name_has_comma(self, records):
        """Employee names are stored 'Last, First' — every name should have a comma."""
        bad = [r["employee_name"] for r in records if "," not in r["employee_name"]]
        # Allow a tiny tolerance for rare edge cases
        rate = len(bad) / len(records)
        assert rate < 0.01, (
            f"{len(bad)} employee names ({rate:.1%}) lack a comma: {bad[:5]}"
        )

    def test_wrapped_license_types_resolved(self, records):
        """Wrapped license type continuation words must not appear standalone."""
        # "Responder", "Practitioner", etc. should be appended to the type, not standalone
        standalone_continuations = {"Responder", "Practitioner", "Specialist", "Surgeon"}
        bad = [
            r["license_type"]
            for r in records
            if r["license_type"].strip() in standalone_continuations
        ]
        assert not bad, (
            f"Wrapped license type not resolved — found standalone continuation words: {bad[:5]}"
        )


# ── TestCSVOutput ─────────────────────────────────────────────────────────────


class TestCSVOutput:
    """Verify the CSV file is well-formed."""

    EXPECTED_COLUMNS = [
        "service_level",
        "service_name",
        "service_transport_type",
        "service_city",
        "service_zip",
        "service_license_no",
        "service_contact_person",
        "service_phone",
        "medical_director",
        "employee_name",
        "license_no",
        "license_type",
        "expiration_date",
        "address",
    ]

    def test_csv_exists_and_nonempty(self, csv_path):
        assert csv_path.exists(), "CSV file was not created"
        assert csv_path.stat().st_size > 0, "CSV file is empty"

    def test_csv_has_expected_columns(self, csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            actual = csv.DictReader(f).fieldnames
        assert actual == self.EXPECTED_COLUMNS, (
            f"Column mismatch.\nExpected: {self.EXPECTED_COLUMNS}\nActual:   {actual}"
        )

    def test_csv_row_count_matches_records(self, records, csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(records), (
            f"CSV has {len(rows)} rows but extract_records returned {len(records)}"
        )

    def test_csv_no_null_bytes(self, csv_path):
        content = csv_path.read_bytes()
        assert b"\x00" not in content, "CSV contains null bytes"


# ── TestKnownRecords ──────────────────────────────────────────────────────────


class TestKnownRecords:
    """Spot-check stable, well-known entries to catch regressions."""

    def test_omaha_fire_department_present(self, records):
        """Omaha Fire Department (license 5043) is the largest service — must appear."""
        matches = [r for r in records if r["service_license_no"] == "5043"]
        assert len(matches) >= 1, (
            "Omaha Fire Department (service_license_no=5043) not found — "
            "possible parser regression"
        )

    def test_omaha_fire_has_many_employees(self, records):
        """Omaha Fire should have at least 200 employees/volunteers."""
        count = sum(1 for r in records if r["service_license_no"] == "5043")
        assert count >= 200, (
            f"Omaha Fire Dept has only {count} employees — expected ≥200"
        )

    def test_lincoln_fire_present(self, records):
        """Lincoln Fire & Rescue (license 5031) is the second-largest — must appear."""
        matches = [r for r in records if r["service_license_no"] == "5031"]
        assert len(matches) >= 1, (
            "Lincoln Fire & Rescue (service_license_no=5031) not found"
        )

    def test_service_context_propagated(self, records):
        """All employees within a service should share the same service_name."""
        from collections import defaultdict
        svc_names: dict = defaultdict(set)
        for r in records:
            svc_names[r["service_license_no"]].add(r["service_name"])
        multi = {k: v for k, v in svc_names.items() if len(v) > 1}
        assert not multi, (
            f"Services with multiple service_name values (context leak): "
            f"{list(multi.items())[:3]}"
        )

    def test_non_transport_services_present(self, records):
        """At least 1 Non-Transport service should appear."""
        nt = [r for r in records if r["service_transport_type"] == "Non-Transport"]
        assert len(nt) >= 1, "No Non-Transport services found — check SERVICE_NAME_RE"
