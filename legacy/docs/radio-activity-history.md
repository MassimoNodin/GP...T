# Radio activity history

The standalone radio reports only messages whose playback has started. Queue drops,
expired messages, and interpreted/model answers that were never dispatched are not
reported as spoken. Dry-run playback reports the same lifecycle for smoke testing.

## API

POST /api/v2/engineer/radio/events requires the configured control token as
Authorization: Bearer TOKEN and Content-Type: application/json. Query parameters
are rejected. The streamed body is limited to 32 KiB and 16 items:

    {
      "events": [{
        "client_event_id": "7934cdef0984425d8f42c22fc6984a21",
        "kind": "answer",
        "owner": "automatic_gaps",
        "text": "Gap ahead two seconds.",
        "scope": "[generation,session_id,scope,driver_identity]",
        "session_id": "session-id",
        "lap": 3,
        "related_id": 42,
        "started_at": "2026-10-16T01:00:00.000+00:00",
        "repeat": false
      }]
    }

Required fields: client_event_id (1–128 ASCII letters/digits/colon/underscore/hyphen),
kind (proactive or answer), nonblank text (at most 2,000 characters), and started_at
(timezone-aware ISO datetime). Optional nullable fields: owner (128 characters),
scope (4,096 characters), session_id (1–256 characters), lap (integer 0–1,000,000),
related_id (positive integer up to 2^62). Optional repeat is a boolean, default false.
Unknown fields, booleans used as integers, invalid timestamps, and empty batches
are rejected with 422 before any writes. Other errors: 401 unauthorized, 400 query,
415 content type, 413 oversized body, 408 body timeout, 503 activity store unavailable.

Success: {"status":"ok","data":{"event_ids":[43]}}. Each corresponding id is
null for an already-recorded client event id. Retries reuse the same id. If a batch
partially persists before a storage failure, retrying the whole batch is safe.
Dedupe lasts for retained activity rows (the existing 5,000-row retention policy).

Both radio-intent and radio-answer retain their current request contracts. Successful
responses add optional data.question_event_id (integer or null). Their model intent
and reply objects are unchanged, including their strict parsers. The radio carries
this id into playback submissions and reports it as related_id. Successful model
generation alone does not create a response event; actual playback does. Failed
validated questions create a related issue. Unauthenticated/malformed requests are
not recorded as driver questions.

GET /api/v2/engineer/events retains its current shape. Radio questions have
kind=question, source=radio. Spoken answers have kind=response, proactive speech
has kind=proactive; both have source=radio and detail containing spoken=true,
owner, scope, started_at, repeat, client_event_id. created_at_utc is receipt time;
detail.started_at is the original playback-start timestamp. Session/lap fields are
best-effort: session comes from the radio's scope binding, lap from its last live
player lap observation. Without a live session they remain null. The existing
session-filter policy excludes unbound radio questions/messages.

## Owner-to-topic mapping

| Owner | Activity topic |
| --- | --- |
| automatic_gaps | gap |
| gap_changes | gap |
| opponent_regions | gap |
| lap_time | lap_time |
| pace_sectors | lap_time |
| lap_reports | lap_time |
| pit_benchmark | pit |
| null or any other owner | general |

Question topics continue to use the shared question-text classifier.

## Reporter lifecycle

The playback notification only enqueues in memory. A daemon thread uses a separate
HTTP client with a one-second timeout; it never shares the telemetry polling client.
It sends a batch of one (within the batched API) and preserves event identity on
retries. The queue holds 64 items including in-flight work, dropping oldest on
overflow with radio_activity_dropped/queue_full. Each item gets up to 12 attempts,
with backoff of 1, 2, 4, then at most 5 seconds; final failure emits
radio_activity_dropped/retry_limit. This buffers short outages, not indefinite
offline operation. Shutdown allows two seconds to drain and joins for at most 2.5
seconds. Unsent memory backlog is not persisted across restarts. Each deliberate
repeat has a new event id, kind=answer, repeat=true, and retains the original
related question id. Transport retries do not create duplicate history rows.

## Schema migration

Activity schema version 2 adds radio to the source CHECK constraint. Version 1
databases are upgraded under BEGIN IMMEDIATE in one transaction: create a replacement
table, copy every column and id, replace the table, and recreate all explicit
indexes and triggers. The unique dedupe index is recreated by the table definition.
The AUTOINCREMENT high-water mark is preserved even if its highest row was deleted.
Metadata remains untouched. Any migration failure rolls back; version 2 openings
do not rebuild. Unsupported future versions are still rejected. No archive/evidence
database migration is involved.
