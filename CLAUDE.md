# Nebraska DHHS EMS Roster

## Purpose
Extract the Nebraska Division of Public Health EMS Services Employee/Volunteer Roster from PDF to structured CSV.

## Source PDF
https://dhhs.ne.gov/licensure/Documents/EMS_Roster.pdf
Saved to: `pdfs/ems_roster_YYYY-MM-DD.pdf`

## Running
```bash
python parse_ems_roster.py
```

## Testing
```bash
pytest test_parse_ems_roster.py -v
```

## Output
CSV written to: `data/ems_roster_YYYY-MM-DD.csv`

## Key Implementation Notes
- PDF type: Type A Roster + Type D Hierarchical (service context repeats in header on every page)
- Parsing strategy: line-by-line `extract_text()` (plain mode); no tables detected
- 556 pages; no cover/TOC pages to skip — every page is data
- Each page begins with a 10-line service header block (repeated when a service spans multiple pages)
- Employee records are 2 lines: name+lic_no+license_type+expiration_date, then address
- Wrapped license types split across lines — known continuation words are: Responder, Practitioner, Specialist, Surgeon
- One row per person+license combination (same person may appear multiple times with different license types)
- Fields: service_level, service_name, service_transport_type, service_city, service_zip, service_license_no, service_contact_person, service_phone, medical_director, employee_name, license_no, license_type, expiration_date, address

## Known Data Quality Issues (Source PDF)
- 4 services (5115, 5116, 5143, 5212) have garbled service_name values due to PDF font
  rendering artifacts where "Transport" characters are interleaved with the last word of the
  service name. Example: "DeparTtramnsepnortt" = "Department" + "Transport" merged.
  These are unfixable via text extraction. The word-extraction fallback is used for these
  pages but still captures the garbled word since it falls in the left column (x < 390).
- 1 service (5133) has "Flight" missing from its name ("Black Hills Life" instead of
  "Black Hills Life Flight") because "Flight" merged with "Transport" into "FTlriagnshptort"
  which falls at x ≥ 390 and is excluded.
- Service 1489 (Hemingford) name is truncated by the same issue.
- All employee data (name, license, expiration, address) is unaffected by these artifacts.
