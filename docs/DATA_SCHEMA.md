# Data Schema

Each element in the client's JSON dump represents one **medical entry**. The dump is a JSON
array of these objects.

## Top-level shape

```json
[
  { "id": "...", ... },
  { "id": "...", ... }
]
```

## Entry object

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Stable unique identifier. Used by `GET /entries/{id}`. |
| `doctor_name` | string | yes | May contain transcription typos (e.g. `Dobert` for `Robert`). Indexed for fuzzy search. |
| `specialty` | string | no | e.g. `Cardiology`. Indexed for search/filtering. |
| `location` | string | yes | City/region. May contain typos (e.g. `Bukalest` for `Bucharest`). Indexed for fuzzy search. |
| `facility` | string | no | Clinic/hospital name. |
| `phone` | string | no | Free-form phone string. |
| `notes` | string | no | Free text. |

> The implementing team should treat fields beyond `id`, `doctor_name`, and `location` as
> best-effort: validate types but don't reject an entry for missing optional fields. The
> schema in `medical_app/models.py` is the authoritative definition once feat-002/feat-003
> land.

## Example entry (synthetic)

```json
{
  "id": "ent-0001",
  "doctor_name": "Dobert Pop",        // typo of "Robert"
  "specialty": "Cardiology",
  "location": "Bukalest",             // typo of "Bucharest"
  "facility": "Central Clinic",
  "phone": "+40 21 000 0000",
  "notes": "Follow-up in two weeks."
}
```

## Validation rules

- `id` must be a non-empty string and unique within the dump.
- `doctor_name` and `location` must be non-empty strings.
- Unknown extra fields are allowed (forward-compatible) but ignored by search unless
  explicitly added to the index.
- Entries that fail validation are skipped during load, **counted**, and logged; they do not
  abort the whole load (resilience over strictness).

## Searchable fields

The fuzzy index covers `doctor_name` and `location` by default, plus `specialty` for
filtering. These are the fields most affected by speech-to-text transcription errors, which
is the core motivation for typo-tolerant search.
