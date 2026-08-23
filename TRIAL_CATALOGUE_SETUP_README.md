# Trial Catalogue Setup and Administration

## Purpose

The patient-facing workflow is only useful when the local catalogue already
contains public trial records. At present, the application can queue and run
bounded ClinicalTrials.gov synchronizations through the API and background
worker, but the web interface has no way to start or monitor that work.

This document maps the missing administrator-facing experience. It is intended
for the local, research-only, synthetic-patient demonstration. It does not
authorize real patient data, clinical decisions, outreach, or enrollment.

## The Problem This Solves

Today a reviewer can:

1. Import a synthetic patient Bundle.
2. Inspect the normalized patient timeline.
3. Start a match run.

If no trials have been loaded, the match run correctly completes with zero
results. That is technically accurate but confusing: the reviewer cannot tell
whether there were no relevant trials or no trials in the catalogue at all.

The missing flow is:

```text
Administrator loads a small public trial collection
        ↓
Worker stores versioned trial records and reports its status
        ↓
Catalogue status confirms that trials are available
        ↓
Reviewer imports a synthetic patient and runs a match
        ↓
Reviewer sees candidates, source evidence, and uncertainty
```

## Who Uses This Screen

### Administrator or demo operator

This person maintains the public trial catalogue. They choose a bounded search
or a fixed demo collection, start the update, and inspect the resulting counts.
They do not upload patient information on this screen.

### Reviewer

This person imports synthetic patient data and reviews possible trial
candidates. They should only see a clear catalogue-ready indicator, not the
controls for broad or repeated source synchronization.

### Current local-demo constraint

Authentication and roles are not implemented yet. Until they are, this screen
must be visibly labelled **Local demo administration** and must not imply that
any user is authorized to operate a real clinical service.

## Current Backend Capabilities

The following pieces already exist and should be reused rather than replaced:

| Capability | Current location | What it does |
| --- | --- | --- |
| Queue a sync | `POST /trial-syncs` | Accepts a finite NCT ID, query, condition, or explicit page range. |
| Read sync status | `GET /trial-syncs/{job_id}` | Returns queued, running, completed, or failed status and safe aggregate counts. |
| Ingestion worker | `apps/backend/app/workers/trial_ingestion.py` | Fetches public trial data, creates immutable source snapshots, and records freshness. |
| Local dispatcher | `apps/backend/app/workers/dispatcher.py` | Picks up queued trial-sync jobs. |
| Fixed development collection | `apps/backend/app/trials/development_collection.py` | Defines a small, versioned melanoma collection for reproducible development work. |

The backend deliberately keeps raw public trial responses out of job-status
messages. The new UI must use the existing safe response fields rather than
trying to display worker logs or raw payloads.

## Target User Experience

Add a fifth navigation item named **Trial catalogue**. It should come before
the patient-import step in the visual workflow, because it is a prerequisite
for useful matching.

### Empty catalogue state

When there are no searchable trials, show a clear message:

> No trials are loaded yet. Load a bounded public trial collection before
> running patient matches.

Show one primary action for the local demo:

> Load demo trials

Also provide an expandable **Advanced selection** area for a permitted local
operator:

- Specific NCT ID
- Condition
- Search phrase
- Explicit page range and page size

Do not provide an unrestricted "download all trials" action.

### Ready catalogue state

Once at least one trial is available, show:

- Number of current searchable trials
- Most recent successful update time
- Source freshness summary
- Last sync outcome and safe counts
- A secondary **Update trials** action

The patient match page should show a small, plain-language status such as:

> Trial catalogue ready: 24 public trial records available.

or, when empty:

> Trial catalogue is empty. Matching may return no results.

### Synchronization progress

After the user starts a trial update, show a status card similar to the
existing match-run card:

| Status | Plain-language display |
| --- | --- |
| Queued | Waiting to start |
| Running | Loading public trial records |
| Completed | Update completed |
| Failed | Update could not be completed |

The card should show only safe operational details:

- Selected source scope, for example “Condition: diabetes”
- When it was queued, started, and completed
- Trials processed
- New versions stored
- Unchanged records
- Missing or invalid source-update dates
- A static, non-sensitive failure message when applicable

Polling every few seconds while status is queued or running is sufficient for
the local demo. Stop polling at completed or failed.

## Screen Layout

```text
Trial catalogue
Public ClinicalTrials.gov records for local research demonstration

[ Catalogue status: Empty / Ready / Update in progress ]
[ trial count ] [ last update ] [ freshness summary ]

Local demo collection
Small, reproducible public trial set for checking the match workflow.
[ Load demo trials ]

Advanced bounded update
Condition       [                    ]
Search phrase   [                    ]
Specific NCT ID [                    ]
Page range      [ start ] to [ end ]     Page size [ 100 ]
[ Queue trial update ]

Latest update
[ status ] [ timestamps ] [ safe counts ] [ refresh ]
```

The form must explain that these selections control public trial retrieval,
not patient matching. A patient ID or patient import ID must never be part of
the catalogue-update request.

## API Mapping

### Create a bounded sync

Use the existing endpoint:

```http
POST /api/trial-syncs
Content-Type: application/json
```

Examples of valid requests:

```json
{ "nct_id": "NCT02434107" }
```

```json
{ "condition": "diabetes", "page_size": 25 }
```

```json
{ "query_term": "melanoma immunotherapy", "start_page": 1, "end_page": 2, "page_size": 50 }
```

The form must submit only one clear, finite source selection. The API already
rejects invalid, ambiguous, or unbounded requests. The UI should surface its
safe validation message rather than inventing a broader fallback search.

### Read status

After receiving the sync ID, poll:

```http
GET /api/trial-syncs/{job_id}
```

The response already contains the status, timestamps, selection, counts,
source-lag metrics, and a safe failure summary. It must not be treated as a
trial list and must not be mixed with patient data.

### Demo collection endpoint to add

The fixed development collection is currently a backend helper only; the
general `POST /trial-syncs` schema does not expose `collection_id`. Add one
small, explicit endpoint for the UI:

```text
POST /trial-syncs/development-collection
```

It should queue the source-controlled collection defined in
`development_collection.py` and return the queued sync records. The UI should
display the collection name and one progress card per selected NCT ID, or a
single grouped progress view. Do not accept arbitrary client-supplied lists in
this endpoint.

## Data and Safety Invariants

1. Only public ClinicalTrials.gov records are retrieved by this workflow.
2. Every source selection is bounded: one NCT ID, a meaningful search, or an
   explicit limited page range.
3. The raw public source response remains an immutable backend snapshot.
4. The UI displays source-update time separately from retrieval time and from
   matching outcome.
5. A failed update must not erase previously available trials.
6. Empty catalogue status must be visible before a reviewer queues a match.
7. The catalogue UI never accepts, displays, or logs patient facts.
8. Trial retrieval produces review candidates only; it does not make an
   eligibility, treatment, or enrollment decision.

## Implementation Plan

### Step 1 — Catalogue status API

Add a read-only endpoint that returns:

- Current searchable trial count
- Latest completed successful trial-sync timestamp
- Latest sync status and safe failure state
- Freshness summary for current trial records

Do not make the web client infer this by downloading trial records or reading
worker logs.

### Step 2 — Fixed demo-collection route

Expose the existing fixed development collection through the explicit route
described above. Keep its membership in source control so demo behavior is
repeatable.

### Step 3 — Web catalogue screen

Add a `Trial catalogue` screen with empty, ready, progress, and failed states.
Reuse the existing `requestJson` API client, status-card patterns, date
formatting, and polling behavior from the match-run screen.

### Step 4 — Patient-flow guardrail

On the match-run screen, request catalogue status once:

- If empty, show the explanation and link to Trial catalogue.
- Do not silently queue a match that is guaranteed to search no trials.
- Preserve the option to inspect a historical completed match run.

### Step 5 — Tests

Add focused checks for:

- Empty versus ready catalogue status
- Demo collection route queues only source-controlled trial IDs
- Advanced form rejects invalid selections before queueing
- Progress polling stops at a terminal state
- Empty-catalogue notice appears on the match-run screen
- A failed update leaves existing searchable trials available

## Acceptance Checks

- [ ] A local operator can load the fixed demo collection from the web UI.
- [ ] The operator can see whether an update is waiting, working, completed, or failed.
- [ ] The screen reports safe counts and source freshness without showing worker logs.
- [ ] A reviewer sees a clear notice when the catalogue is empty before matching.
- [ ] A completed demo load makes at least one public trial searchable locally.
- [ ] Re-running an unchanged collection preserves immutable source history without misleading duplicate results.
- [ ] The UI remains explicit that matching produces review candidates, not eligibility decisions.

## Deliberately Out of Scope

- Real-patient data
- Authentication, organization roles, and production authorization
- An unrestricted public trial crawl
- Automatic enrollment, outreach, or treatment recommendations
- Automated parsing of all trial eligibility text
- Semantic or LLM-based trial retrieval

## Recommended Demo Script After Implementation

1. Open **Trial catalogue**.
2. Explain that it is empty and patient matching would therefore return no trials.
3. Click **Load demo trials** and wait for completion.
4. Confirm the catalogue-ready count and source-update time.
5. Import a synthetic patient.
6. Review the patient timeline and any stale or conflicting evidence.
7. Start a match run.
8. Explain that results are candidates for human review, then open their source
   trial and patient-fact evidence.
