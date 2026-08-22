# Synthetic FHIR fixtures

Only synthetic data may be stored in this directory or imported into the local application.

## Required Bundle marker

Every imported FHIR R4 `Bundle` must include this exact tag in its **top-level** `meta.tag` array:

```json
{
  "resourceType": "Bundle",
  "meta": {
    "tag": [
      {
        "system": "urn:clinical-trial-matcher:data-classification",
        "code": "synthetic-data",
        "display": "Synthetic data approved for research and demonstration"
      }
    ]
  }
}
```

The importer checks the `system` and `code` exactly. A display label alone is not enough, and a tag on a Patient or another entry does not count. The guard must run before the Bundle is persisted, normalized, logged, or added to a background job.

Do not commit real patient data, redacted patient records, or information that can be re-identified.
