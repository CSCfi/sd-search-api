---
description: Change-scope, refactoring, validation and risk rules for sd-search-api.
---

## Change scope

Existing code is presumed reviewed and working. Make the smallest change that completes the task without changing unrelated behavior.

- Change only what the task requires. Every hunk in `git diff` must be explainable from the task. Do not perform unrelated cleanup, renaming, reformatting, import sorting, dependency updates, or type changes.
- Preserve the public API contract and existing client compatibility unless the task explicitly requests a contract change. A contract includes request and response schemas, field names, defaults, validation, status codes, error bodies, headers, pagination, ordering, authentication, authorization, scope handling, and conformance to the Beacon V2 specification.
- Do not extract, move, rename, inline, or split existing code without explicit user approval. If it seems necessary:
  1. explain why;
  2. show the smallest alternative that avoids it;
  3. ask for approval before proceeding.
- Duplication, unclear code, and unrelated defects outside the task remain unchanged. Mention them in the final summary; do not fix them opportunistically.
- There is no migration tool. The schema is `database/schema/create.sql` (applied by docker-compose initdb and by `admin.py recreate`, which is refused in prod), with `drop.sql` beside it. Editing `create.sql` never alters a deployed database, so any schema change needs explicit approval and an agreed plan for existing data, deployment order, rollback, and recovery.
- Do not edit generated files by hand. The index mapping `api/bigpicture/index/bp-image-index.json` is generated from `config/fields.yaml` by `index generate`; field or analyzer changes also require `index recreate`, a sync, and a reload.

## Required investigation before an approved structural refactor

Before an approved extract, move, rename, or signature change:

- Find every caller, including tests, `monkeypatch.setattr` and `patch.object` targets, configuration, docs, `scripts/admin.py`, and side-effect imports.
- Check implicit dependencies: closure/local variables, decorators, default arguments, transaction and connection boundaries, exception propagation, side-effect order, caching, retries, async behavior, logging, and import-time registration.
- Identify whether the server, the admin CLI, background pollers, or a deployed version depend on the old behavior.
- When changing imports, module boundaries, or a symbol's lookup location, verify that every affected `monkeypatch.setattr` or `patch.object` still replaces the exact reference used at runtime, not merely an unused module attribute.

## Validation

- Make one logical change at a time. Run relevant tests after each logical change.
- `tox` modifies files: the ruff environment runs `ruff format` and `ruff check --fix` over `search_api/`, `scripts/`, and `tests/`. To check without changing files, run `ruff format --check` and `ruff check` without `--fix`. After running `tox`, review `git diff` and revert any hunk the task did not need.
- `tox` runs only `tests/unit/`. A green `tox` does not validate SQL, OpenSearch queries, Snowstorm, routing integration, or the load/sync path.
- For changes touching SQL, OpenSearch, Snowstorm, routes, loading, syncing, authentication, authorization, or the submit API, run the narrowest applicable `tests/integration/` coverage, plus any broader integration suite needed to validate the changed behavior. If it cannot be run, state exactly what was not run, why, and what evidence was used instead.
- Before finishing, review `git diff` and revert every hunk the task does not need.

## Risk

For any change that is not clearly behavior-preserving, including changes to API behavior, persistence, imports, dependency wiring, concurrency, or external-service calls, include a visible **Risk** section in the final summary with:

- at least one concrete failure mode specific to the change;
- its impact;
- the validation performed; and
- the mitigation or rollback path.

## Repository-specific hazards

- `api/domain.py` imports `services/ontology/registrations.py` only for its side effect (`# noqa: F401`). Do not move that import or remove the `noqa`: `ruff check --fix` would delete it as unused, and no ontology would be registered.
- Tests stub mostly with `monkeypatch.setattr` and `patch.object` on module attributes; see the patch-target check above.
- Routes must read the single `app.state.beacon_service`. Value counts are cached on that instance and filled in the background, so a per-request instance starts empty.
- Load and query share one resolution cascade: `ontology/values.py` and `resolve_concept_ids`. Changing one side can make indexed values diverge from search behavior.
- `UpdatedPoller` reads `updated_at` before refresh and records it only after success. Reordering this can lose writes that land during refresh.
- The load marker is written only after the complete run, and `--full` deletes it before loading. Postgres is primary. Moving the marker write earlier, or syncing before storing, changes failure behavior.
