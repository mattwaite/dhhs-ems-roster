# Nebraska DHHS EMS Services Employee/Volunteer Roster

Extracts the Nebraska Division of Public Health EMS Services Employee/Volunteer Roster from PDF and converts it to a structured CSV.

**Source:** https://dhhs.ne.gov/licensure/Documents/EMS_Roster.pdf
**Update frequency:** Monthly (data updated ~15th of each month)

## Usage

```bash
pip install -r requirements.txt
python parse_ems_roster.py
```

The script downloads the current PDF, extracts all records, and writes output to `data/ems_roster_YYYY-MM-DD.csv`.

## Output Fields

| Field | Description |
|-------|-------------|
| service_level | EMS service level (e.g., "EMS Basic Service", "EMS Advanced Service") |
| service_name | Name of the EMS service organization |
| service_transport_type | "Transport" or "Non-Transport" |
| service_city | City where the service is located |
| service_zip | ZIP code of the service |
| service_license_no | Service license number |
| service_contact_person | Name of the service contact person |
| service_phone | Service phone number |
| medical_director | Name of the medical director |
| employee_name | Employee or volunteer name (Last, First [MI]) |
| license_no | Individual license number |
| license_type | Type of license (e.g., EMT, Paramedic, Registered Nurse) |
| expiration_date | License expiration date (MM/DD/YYYY) |
| address | Employee street address including city, state, and ZIP |

## Testing

```bash
pytest test_parse_ems_roster.py -v
```

## Data Archive

PDFs are saved to `pdfs/` and CSVs to `data/`, both with date stamps.
