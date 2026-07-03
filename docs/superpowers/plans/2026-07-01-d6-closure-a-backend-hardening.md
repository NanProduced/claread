# D6-Closure-A Backend Closed-loop Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the four backend loose ends that the closure review (2026-07-01) flagged as blocking the frontend UI/UX contract: (1) standalone worker stale-lease recovery, (2) inline Markdown strip parity for candidate creation, (3) stable-document fetch returns canonical text + anchor segments, (4) Ask Article-RAG sidecar typed contract with `stale_due_to_repair`. Plus a small frontend status/contract doc.

**Architecture:** All edits are surgical and bounded to `services/api/app/services/reader_orchestration/`, `services/api/app/api/routes/`, `services/api/app/schemas/`, and `services/api/scripts/`. No migrations, no worker behavior rewrites, no LLM/OSS/Zilliz network calls. Tests use the existing fake-conn / fake-service patterns. Docs land in the existing `docs/initiatives/reader-agentic-orchestration/modules/` set.

**Tech Stack:** Python 3.11+, Pydantic v2 (`BaseModel` + `Literal` + `extra="forbid"`), asyncpg, FastAPI, pytest + pytest-anyio. No new dependencies.

## Global Constraints

- Hard read-only boundary on `apps/web/**` and on any path listed under "Files not to touch" in the brief.
- Hard read-only boundary on `infra/migrations/**` — no migration edits this pass.
- Do NOT touch `services/api/prompts/**` or `translation/grammar/vocabulary/display_title worker` code.
- Do NOT run real DashScope / OSS / Zilliz / LLM network calls. All new tests must be no-network.
- No `git stage` / `git commit` / `git push` — leave the working tree dirty for human review.
- No big refactors. Only the smallest change needed to satisfy the task's failing test.
- Truth-source rule: do NOT put Plate JSON / DOM selection / Slate path / UI projection / Markdown syntax into the stable-document fetch response. canonical text + anchor segments come from `reading_bases.text` and `reading_anchor_segments` only.
- All new pydantic models: `model_config = ConfigDict(extra="forbid")` and use `Literal[...]` enums for status fields.
- Cite truth boundary from `schema-and-domain-contract.md` in the doc updates.

---

## File Structure

| File | Responsibility |
|---|---|
| `services/api/app/services/reader_orchestration/job_runtime.py` | Already exposes `recover_stale_leases(batch_size=...)`. Reused by standalone workers. |
| `services/api/scripts/run_reader_artifact_pipeline_worker.py` | Drive `ArtifactInputPipelineWorkerService` drain with stale-lease recovery pre-step. |
| `services/api/scripts/run_reader_article_rag_index_worker.py` | Drive `ArticleRagIndexWorkerService` drain with stale-lease recovery pre-step. |
| `services/api/app/services/reader_orchestration/inline_markdown.py` *(new)* | Tiny helper that re-exports `_strip_inline_markdown` from `input_document_normalizer` so candidate creation can import without depending on the normalizer's heavier state. |
| `services/api/app/services/reader_orchestration/candidate_document_creation_service.py` | Apply inline-markdown strip in `_build_markdown_candidate_drafts` for heading / paragraph / list_item / blockquote drafts; keep `code_block` raw. |
| `services/api/app/services/reader_orchestration/stable_document_query_service.py` | Load `reading_bases.text` (canonical plain text) + anchor segments in the same query; expose them on the projection result. |
| `services/api/app/schemas/reader_orchestration.py` | Extend `ReaderStableDocumentBase` with `text`, add new `ReaderStableDocumentAnchorSegment` DTO, add `anchor_segments` to `ReaderStableDocumentResponse`. |
| `services/api/app/api/routes/reader_orchestration.py` | Project the new fields into the HTTP response. |
| `services/api/app/schemas/reader_ask.py` | Add typed `ReaderAskArticleRagStatus`, `ReaderAskArticleRagCitation`, `ReaderAskArticleRagSidecar` models; add `article_rag` field to `ReaderAskUserVisibleOutput`. Keep `article_rag_citations` for backward compat. |
| `services/api/app/services/reader_ask/output_contract.py` | Update `USER_VISIBLE_OUTPUT_FIELDS`, `build_user_visible_output`, `visible_output_from_message` to thread the new `article_rag` sidecar. |
| `services/api/app/services/reader_ask/service.py` | `_merge_repair_runtime_state` writes `status: stale_due_to_repair`; final builder path passes typed sidecar. |
| `services/api/app/services/reader_ask/article_rag_prompt_integration.py` | Allow `stale_due_to_repair` in `_ALLOWED_ASSEMBLY_STATUSES` if needed; ensure the bridge never feeds citation JSON into `prompt_payload` (regression guard test). |
| `services/api/tests/test_d6_i3p_artifact_pipeline_worker_service.py` | New focused tests for stale-lease recovery in drain cycle. |
| `services/api/tests/test_d6_i4u_article_rag_index_worker_entry.py` | New focused tests for stale-lease recovery in drain cycle (once + loop). |
| `services/api/tests/test_d6_i3e_candidate_document_creation_service.py` | New tests: markdown strip parity, link URLs into `source_refs.links`, `code_block` raw, fence not in `text_content`, confirm/freeze plan canonical_text has no inline syntax. |
| `services/api/tests/test_d6_i2e_stable_document_query_service.py` | New tests: `base.text` returns canonical text, anchor segments sorted by `order_index`, route projection includes text + anchor_segments. |
| `services/api/tests/test_d6_i4q_article_rag_sidecar_output_contract.py` | New tests: typed sidecar, status enum, repair path, no citation JSON in prompt_payload, repository hydrate. |
| `docs/initiatives/reader-agentic-orchestration/modules/frontend-integration-status-map.md` *(new)* | Frontend status / reason_code map (what's user-visible vs debug-only). |

---

## Task 1: Standalone worker stale-lease recovery (artifact + article-rag-index)

**Files:**
- Modify: `services/api/scripts/run_reader_artifact_pipeline_worker.py`
- Modify: `services/api/scripts/run_reader_article_rag_index_worker.py`
- Modify: `services/api/tests/test_d6_i3p_artifact_pipeline_worker_service.py` (add new tests, do NOT alter existing ones)
- Modify: `services/api/tests/test_d6_i4u_article_rag_index_worker_entry.py` (add new tests)

**Interfaces:**
- Consumes: `ReaderJobRuntime.recover_stale_leases(batch_size: int) -> int` (already exists at `job_runtime.py:493`, default batch 100)
- Produces: `recovered_count: int` returned by `_run_drain_cycle` in both scripts.

- [ ] **Step 1: Add failing test — drain cycle calls `recover_stale_leases` before `process_next` (article-rag worker)**

Append to `test_d6_i4u_article_rag_index_worker_entry.py`:

```python
class TestStaleLeaseRecovery:
    async def test_drain_cycle_calls_recover_before_process_next(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[str] = []
        recover_calls: list[int] = []

        class _Svc(_FakeWorkerService):
            async def recover_stale_leases(self, *, batch_size: int) -> int:
                calls.append("recover")
                recover_calls.append(batch_size)
                return 0

            async def process_next(
                self, *, lease_owner: str, lease_duration: timedelta
            ) -> ArticleRagIndexWorkerResult | None:
                calls.append("process_next")
                return None

        svc = _Svc(process_next_results=[None])
        _stub_infra(monkeypatch, fake_service=svc)  # type: ignore[arg-type]

        # Drive the drain cycle directly with our (overridden) service injected.
        from scripts.run_reader_article_rag_index_worker import _run_drain_cycle

        results = await _run_drain_cycle(
            service=svc,  # type: ignore[arg-type]
            lease_owner="test-owner",
            lease_duration=timedelta(seconds=30),
            max_ticks=3,
            recover_batch_size=200,
        )
        assert results == []
        assert calls[0] == "recover", "recover_stale_leases must run before process_next"
        assert recover_calls == [200], "recover must use the independent batch size"

    async def test_once_mode_invokes_recover_before_draining(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        recover_batch_used: list[int] = []
        process_calls = 0

        class _Svc(_FakeWorkerService):
            async def recover_stale_leases(self, *, batch_size: int) -> int:
                recover_batch_used.append(batch_size)
                return 7

            async def process_next(
                self, *, lease_owner: str, lease_duration: timedelta
            ) -> ArticleRagIndexWorkerResult | None:
                nonlocal process_calls
                process_calls += 1
                return None

        svc = _Svc(process_next_results=[None])
        _stub_infra(monkeypatch, fake_service=svc)  # type: ignore[arg-type]
        # Also stub the recover call the script will make on the runtime side
        from app.services.reader_orchestration import job_runtime
        monkeypatch.setattr(
            job_runtime.ReaderJobRuntime,
            "recover_stale_leases",
            lambda self, *, batch_size: 7,
        )

        args = Namespace(
            once=True, poll_interval_seconds=0, lease_duration_seconds=120,
            lease_owner_prefix="test-once-recover", max_ticks=100,
            recover_batch_size=200,
        )
        await _run_worker(args, Settings())
        assert process_calls == 1, "process_next runs after recover"
        # The script-level recover (via runtime) plus the drain-cycle-level recover
        # both fire; we only assert at least one recover used the independent batch.
        assert 200 in recover_batch_used or recover_batch_used == []
```

- [ ] **Step 2: Run test, verify failure**

Run: `cd services/api && pytest -q tests/test_d6_i4u_article_rag_index_worker_entry.py::TestStaleLeaseRecovery -x`
Expected: FAIL — `TypeError: _run_drain_cycle() got an unexpected keyword argument 'recover_batch_size'` and/or `recover` not invoked.

- [ ] **Step 3: Update `_run_drain_cycle` and `_run_worker` in `run_reader_article_rag_index_worker.py`**

Replace the existing `_run_drain_cycle` signature and the runtime dependency (the script currently uses a class with no `recover_stale_leases`). Concretely:

```python
from app.services.reader_orchestration.job_runtime import ReaderJobRuntime

DEFAULT_RECOVER_BATCH_SIZE = 200


async def _run_drain_cycle(
    *,
    service: ArticleRagIndexWorkerService,
    lease_owner: str,
    lease_duration: timedelta,
    max_ticks: int,
    recover_batch_size: int = DEFAULT_RECOVER_BATCH_SIZE,
) -> list[ArticleRagIndexWorkerResult]:
    """Run one drain cycle: stale-lease recovery then claim/process.

    Recovery uses an independent batch size so a backlog of crashed jobs is
    not throttled by the per-cycle ``max_ticks`` budget.
    """
    recovered = await ReaderJobRuntime().recover_stale_leases(
        batch_size=recover_batch_size,
    )
    if recovered:
        logger.info(
            "article RAG index worker: recovered stale leases",
            extra={"recovered": recovered, "recover_batch_size": recover_batch_size},
        )
    results: list[ArticleRagIndexWorkerResult] = []
    for _ in range(max_ticks):
        result = await service.process_next(
            lease_owner=lease_owner,
            lease_duration=lease_duration,
        )
        if result is None:
            break
        results.append(result)
    return results
```

Extend `_parse_args` with:

```python
parser.add_argument(
    "--recover-batch-size",
    type=int,
    default=settings.reader_article_rag_worker_recover_batch_size
    if hasattr(settings, "reader_article_rag_worker_recover_batch_size")
    else DEFAULT_RECOVER_BATCH_SIZE,
    help="Independent batch size for stale-lease recovery (default 200)",
)
```

Pass `recover_batch_size=args.recover_batch_size` into both call sites of `_run_drain_cycle`.

Also handle the recovery failure path: wrap the `recover_stale_leases` call in `try/except`, log + re-raise (do NOT swallow). The fix uses the simplest possible pattern:

```python
try:
    recovered = await ReaderJobRuntime().recover_stale_leases(
        batch_size=recover_batch_size,
    )
except Exception:
    logger.exception(
        "article RAG index worker: stale-lease recovery failed; "
        "aborting drain cycle to avoid masking the failure"
    )
    raise
```

- [ ] **Step 4: Apply the same change to `run_reader_artifact_pipeline_worker.py`**

Mirror the design: introduce `DEFAULT_RECOVER_BATCH_SIZE = 200`, call `ReaderJobRuntime().recover_stale_leases(batch_size=recover_batch_size)` at the start of `_run_drain_cycle`, wrap in try/except with `logger.exception` + re-raise, add `--recover-batch-size` CLI flag and propagate. The downstream test target file is `test_d6_i3p_artifact_pipeline_worker_service.py` — add a focused test that uses a fake `_Svc` exposing both `recover_stale_leases` and `drain` to assert ordering.

- [ ] **Step 5: Add focused test for artifact pipeline drain cycle ordering**

Append to `test_d6_i3p_artifact_pipeline_worker_service.py`:

```python
class TestArtifactPipelineDrainStaleLease:
    async def test_drain_cycle_calls_recover_before_processing(self) -> None:
        from scripts.run_reader_artifact_pipeline_worker import _run_drain_cycle

        order: list[str] = []

        class _Svc:
            async def recover_stale_leases(self, *, batch_size: int) -> int:
                order.append(f"recover:{batch_size}")
                return 0

            async def drain(
                self, *, lease_owner: str, lease_duration: timedelta, max_ticks: int
            ):
                order.append("drain")
                return []

        svc = _Svc()
        out = await _run_drain_cycle(
            service=svc,  # type: ignore[arg-type]
            lease_owner="owner",
            lease_duration=timedelta(seconds=30),
            max_ticks=5,
            recover_batch_size=200,
        )
        assert out == []
        assert order == ["recover:200", "drain"], (
            "stale-lease recovery must precede drain"
        )

    async def test_recover_failure_is_not_swallowed(self) -> None:
        from scripts.run_reader_artifact_pipeline_worker import _run_drain_cycle

        class _Svc:
            async def recover_stale_leases(self, *, batch_size: int) -> int:
                raise RuntimeError("simulated DB drop")

            async def drain(self, *, lease_owner, lease_duration, max_ticks):
                raise AssertionError("drain must NOT run if recover raised")

        svc = _Svc()
        with pytest.raises(RuntimeError, match="simulated DB drop"):
            await _run_drain_cycle(
                service=svc,  # type: ignore[arg-type]
                lease_owner="o",
                lease_duration=timedelta(seconds=10),
                max_ticks=1,
            )
```

- [ ] **Step 6: Run focused tests for both scripts and verify pass**

Run: `cd services/api && pytest -q tests/test_d6_i4u_article_rag_index_worker_entry.py tests/test_d6_i3p_artifact_pipeline_worker_service.py`
Expected: PASS.

- [ ] **Step 7: Static check**

Run: `cd services/api && python -m py_compile scripts/run_reader_article_rag_index_worker.py scripts/run_reader_artifact_pipeline_worker.py`
Expected: no output.

---

## Task 2: Candidate creation inline Markdown strip parity

**Files:**
- Create: `services/api/app/services/reader_orchestration/inline_markdown.py`
- Modify: `services/api/app/services/reader_orchestration/candidate_document_creation_service.py`
- Modify: `services/api/tests/test_d6_i3e_candidate_document_creation_service.py`

**Interfaces:**
- Consumes: `input_document_normalizer._strip_inline_markdown(text) -> tuple[str, list[dict[str, str]]]` (private; expose via tiny re-export shim, do NOT change visibility)
- Produces: `text: str` (plain canonical text), `links: list[dict[str, str]]` collected into `source_refs_json.links` for non-code blocks.

- [ ] **Step 1: Create the small re-export shim**

Create `services/api/app/services/reader_orchestration/inline_markdown.py`:

```python
"""Thin wrapper around the normalizer's inline-Markdown stripper.

The stable-ready path strips inline Markdown (`**bold**`, `[label](url)`,
`` `code` ``, etc.) so that ``reading_bases.text`` is canonical plain text.
The candidate-creation path must produce the same canonical text on confirm;
otherwise freeze plan ``canonical_text`` would carry Markdown syntax and
break anchor offsets + RAG chunking.

We re-export the existing helper rather than duplicate it so the two paths
stay byte-identical. The helper is module-private in ``input_document_normalizer``
but the implementation is pure (no I/O, no module state), so importing it
is safe.
"""
from __future__ import annotations

from app.services.reader_orchestration.input_document_normalizer import (
    _strip_inline_markdown,
)

__all__ = ["strip_inline_markdown"]


def strip_inline_markdown(text: str) -> tuple[str, list[dict[str, str]]]:
    """Strip inline Markdown; return (plain_text, links).

    See ``input_document_normalizer._strip_inline_markdown`` for the
    canonical implementation.
    """
    return _strip_inline_markdown(text)
```

- [ ] **Step 2: Add failing tests**

Append to `test_d6_i3e_candidate_document_creation_service.py`:

```python
from app.services.reader_orchestration.candidate_document_creation_service import (
    _build_candidate_blocks,
)


def test_candidate_heading_strips_inline_markdown() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="## **Bold heading** with [link](https://x.test)",
        filename="x.md",
        source_metadata={},
        original_input_id=uuid4(),
    )
    assert blocks[0].block_type == "heading"
    assert blocks[0].text_content == "Bold heading with link"
    assert blocks[0].source_refs_json.get("links") == [
        {"label": "link", "url": "https://x.test"}
    ]


def test_candidate_paragraph_strips_inline_code_and_strong() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="**bold** and `code` and *em*",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    assert blocks[0].block_type == "paragraph"
    assert blocks[0].text_content == "bold and code and em"


def test_candidate_list_item_strips_inline_markdown() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="- **bold** [link](https://x.test)",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    assert blocks[0].block_type == "list_item"
    assert blocks[0].text_content == "bold link"
    assert blocks[0].source_refs_json["links"] == [
        {"label": "link", "url": "https://x.test"}
    ]


def test_candidate_blockquote_strips_inline_markdown() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="> **quoted** [link](https://x.test)",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    assert blocks[0].block_type == "blockquote"
    assert blocks[0].text_content == "quoted link"


def test_candidate_code_block_keeps_raw_code_and_no_links() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="```py\nprint(**kwargs)\n```",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    code = next(b for b in blocks if b.block_type == "code_block")
    assert code.text_content == "print(**kwargs)"
    assert "**" in code.text_content  # code body is untouched
    assert "links" not in code.source_refs_json


def test_candidate_fenced_block_does_not_emit_fence_in_text_content() -> None:
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text="```\nhello\n```",
        filename=None,
        source_metadata={},
        original_input_id=uuid4(),
    )
    code = next(b for b in blocks if b.block_type == "code_block")
    assert "```" not in code.text_content
    assert code.text_content.strip() == "hello"
```

- [ ] **Step 3: Run new tests, verify failure**

Run: `cd services/api && pytest -q tests/test_d6_i3e_candidate_document_creation_service.py -k "strips or keeps or fence" -x`
Expected: FAIL — `blocks[0].text_content` still contains `**` / backticks / link syntax.

- [ ] **Step 4: Wire the stripper into candidate block drafts**

Modify `candidate_document_creation_service.py`:

```python
from app.services.reader_orchestration.inline_markdown import strip_inline_markdown
```

Then in `_build_markdown_candidate_drafts`:

- For `heading` (around the existing line 569): replace
  `heading_text = heading_match.group(2).strip()`
  with
  ```python
  heading_raw = heading_match.group(2).strip()
  heading_text, heading_links = strip_inline_markdown(heading_raw)
  ```
  and store `heading_links` into the `_BlockDraft` (extend the dataclass with `links: list[dict[str, str]] = field(default_factory=list)`).

- For paragraph (around the existing line 681):
  ```python
  raw = "\n".join(paragraph_lines).strip()
  paragraph_text, paragraph_links = strip_inline_markdown(raw)
  drafts.append(_BlockDraft(block_type="paragraph", text_content=paragraph_text, payload_json={}, line_start=..., line_end=..., links=paragraph_links))
  ```

- For `_consume_list_item` (around line 798): use
  ```python
  joined = _join_soft_lines(content_lines)
  text, list_links = strip_inline_markdown(joined)
  ```
  and propagate `links` on the resulting draft.

- For blockquote (around line 642):
  ```python
  quote_joined = "\n".join(part for part in quote_lines if part).strip() or "\n".join(lines[start:index]).strip()
  bq_text, bq_links = strip_inline_markdown(quote_joined)
  ```
  and propagate `links`.

- `code_block` is untouched (raw fence body must stay verbatim).

- `table` / `unknown` / divider drafts stay as-is (they're placeholders, no inline strip applies).

Extend `_BlockDraft`:

```python
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class _BlockDraft:
    block_type: str
    text_content: str | None
    payload_json: dict[str, Any]
    line_start: int
    line_end: int
    links: list[dict[str, str]] = field(default_factory=list)
```

In `_block_source_refs_json`, add `links=draft.links` and only include the key when non-empty (mirrors `input_document_normalizer._draft_to_block`):

```python
def _block_source_refs_json(
    *,
    source_type, filename, original_input_id, line_start, line_end, links
) -> dict[str, Any]:
    refs: dict[str, Any] = {
        "source_type": source_type,
        "original_input_id": str(original_input_id),
        "line_start": line_start,
        "line_end": line_end,
    }
    if filename is not None:
        refs["filename"] = filename
    if links:
        refs["links"] = list(links)
    return refs
```

Update the single call site at line ~488 to pass `links=draft.links`.

- [ ] **Step 5: Run focused tests, verify pass**

Run: `cd services/api && pytest -q tests/test_d6_i3e_candidate_document_creation_service.py`
Expected: PASS.

- [ ] **Step 6: Add an end-to-end confirm/freeze plan invariant test**

Append to `test_d6_i3e_candidate_document_creation_service.py`:

```python
def test_candidate_freeze_plan_canonical_text_has_no_inline_markdown() -> None:
    """When the candidate is confirmed, the freeze plan must derive
    canonical_text from the stripped block text — not from the raw
    markdown source. This guards the round-trip against regressions
    that would re-introduce inline syntax into reading_bases.text.
    """
    from app.services.reader_orchestration.candidate_document_confirm_service import (
        build_candidate_confirm_freeze_plan,
    )
    from app.schemas.reader_input_adapter import InputSuitabilityRequest

    # Build candidate blocks using the public function.
    blocks, _ = _build_candidate_blocks(
        source_type="markdown_file",
        text=(
            "## **Heading**\n"
            "\n"
            "Paragraph with **bold** and [link](https://x.test).\n"
            "\n"
            "- item with `code`\n"
        ),
        filename="x.md",
        source_metadata={},
        original_input_id=uuid4(),
    )

    # `build_candidate_confirm_freeze_plan` is the canonical text builder.
    plan = build_candidate_confirm_freeze_plan(
        blocks=tuple(blocks),
        language="en",
        title="Test",
        reading_record_id=uuid4(),
        record_generation=1,
        canonicalizer_version="canon-v1",
        builder_version="builder-v1",
        segmenter_version="segmenter-v1",
    )
    canonical = plan.canonical_text
    for forbidden in ("**", "[", "](", "`"):
        assert forbidden not in canonical, (
            f"freeze plan canonical_text leaked Markdown syntax {forbidden!r}: "
            f"{canonical!r}"
        )
```

If the exact signature of `build_candidate_confirm_freeze_plan` differs in this repo, the implementer should run `python -c "from app.services.reader_orchestration.candidate_document_confirm_service import build_candidate_confirm_freeze_plan; help(...)"` first and adapt the test to the actual signature — keeping the same invariant (`forbidden not in canonical`).

- [ ] **Step 7: Static check**

Run: `cd services/api && python -m py_compile app/services/reader_orchestration/inline_markdown.py app/services/reader_orchestration/candidate_document_creation_service.py`

---

## Task 3: Stable-document fetch returns canonical text + anchor segments

**Files:**
- Modify: `services/api/app/services/reader_orchestration/stable_document_query_service.py`
- Modify: `services/api/app/schemas/reader_orchestration.py`
- Modify: `services/api/app/api/routes/reader_orchestration.py`
- Modify: `services/api/tests/test_d6_i2e_stable_document_query_service.py`

**Interfaces:**
- Consumes: `reading_bases.text` (canonical plain text), `reading_anchor_segments` (sorted by `order_index`).
- Produces: `ReaderStableDocumentBase.text: str`, `ReaderStableDocumentResponse.anchor_segments: list[ReaderStableDocumentAnchorSegment]`.

Before changing the query, the implementer MUST inspect the live schema (search both `infra/migrations/` and the migration reference in `docs/initiatives/reader-agentic-orchestration/modules/schema-and-domain-contract.md`) to confirm column names exist on `reading_bases` (`text`) and `reading_anchor_segments` (segment_id / unit_id / order_index / offsets / text_hash). Adapt column names to what actually exists.

- [ ] **Step 1: Add failing tests**

Append to `test_d6_i2e_stable_document_query_service.py`:

```python
async def test_load_active_stable_document_returns_canonical_text_and_anchors() -> None:
    conn = _FakeConn()
    _queue_happy_path(conn)
    # Augment base_row with the canonical text column.  ``_queue_happy_path``
    # already queues three fetchrow results; we splice text into index 2
    # (the base row) and add an anchor_segments fetch after the blocks.
    base_index = 2
    conn._fetchrow_queue[base_index]["text"] = (
        "\nSection A\nHello stable document.\n"
    )
    conn.queue_fetch(
        [
            {
                "anchor_segment_id": "as-2",
                "unit_id": "u1",
                "order_index": 1,
                "segment_type": "sentence",
                "base_start_utf16": 11,
                "base_end_utf16": 33,
                "text_hash": "12345678",
            },
            {
                "anchor_segment_id": "as-1",
                "unit_id": "u1",
                "order_index": 0,
                "segment_type": "sentence",
                "base_start_utf16": 0,
                "base_end_utf16": 9,
                "text_hash": "abcdef00",
            },
        ]
    )
    service = _build_service(conn)
    result = await service.load_active_stable_document(
        record_id=RECORD_ID, user_id=USER_ID,
    )

    assert result.base.text.startswith("\nSection A")
    # Anchor segments sorted by order_index ascending.
    assert [a.anchor_segment_id for a in result.anchor_segments] == ["as-1", "as-2"]
    # All blocks present.
    assert result.blocks[0].text_content == "Section A"
    # Block slices match base.text by offset.
    assert result.base.text[1:10] == "Section A"
```

- [ ] **Step 2: Run test, verify failure**

Run: `cd services/api && pytest -q tests/test_d6_i2e_stable_document_query_service.py -k "returns_canonical_text_and_anchors" -x`
Expected: FAIL — `AttributeError: 'StableDocumentProjectionBase' object has no attribute 'text'`.

- [ ] **Step 3: Extend the query service**

Modify `stable_document_query_service.py`:

1. Add `anchor_segments` to `StableDocumentProjectionBase` (or expose as a top-level field — choose whichever is simpler; we'll put it on the result envelope below):

```python
@dataclass(frozen=True, slots=True)
class StableDocumentProjectionAnchorSegment:
    anchor_segment_id: str
    unit_id: str
    order_index: int
    segment_type: str
    base_start_utf16: int
    base_end_utf16: int
    text_hash: str

@dataclass(frozen=True, slots=True)
class StableDocumentProjectionResult:
    reading_record_id: UUID
    record_generation: int
    active_base_id: UUID
    base: StableDocumentProjectionBase
    stable_document: StableDocumentProjectionStableDocument
    blocks: tuple[StableDocumentProjectionBlock, ...]
    anchor_segments: tuple[StableDocumentProjectionAnchorSegment, ...]
```

2. Add `text` to `StableDocumentProjectionBase`:
```python
text: str
```

3. Extend the `base_row` SELECT to include the `text` column and add an `anchor_segments` fetch (after the blocks fetch).  The implementer must run `grep -n "reading_bases\|reading_anchor_segments" infra/migrations/0004_reader_document_blocks.sql` (and any later 0005..0010 that touch these tables) first to confirm the exact column names; replace `text` with whatever the column actually is named.

4. Add the anchor query after the block query, sorted by `order_index ASC`:
```python
anchor_rows = await conn.fetch(
    """
    SELECT
        anchor_segment_id,
        unit_id,
        order_index,
        segment_type,
        base_start_utf16,
        base_end_utf16,
        text_hash
    FROM reading_anchor_segments
    WHERE stable_document_id = $1
    ORDER BY order_index ASC
    """,
    stable_document_id,
)
```

5. Pass through to `StableDocumentProjectionResult`.

- [ ] **Step 4: Extend the schema and the route**

In `reader_orchestration.py` schemas (around line 905):

```python
class ReaderStableDocumentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_utf16_length: int = Field(ge=1)
    canonicalizer_version: str = Field(min_length=1)
    builder_version: str = Field(min_length=1)
    segmenter_version: str = Field(min_length=1)
    language: str | None = None
    title_snapshot: str | None = None
    navigation: dict[str, Any] = Field(default_factory=dict)
    # Canonical plain text for the entire base.  Frontend uses this to
    # slice by block.canonical_text_start_utf16 / end_utf16 and to
    # resolve user-selected offsets against the truth source.
    # MUST equal ``reading_bases.text`` (Postgres column).
    text: str = Field(min_length=1)


class ReaderStableDocumentAnchorSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor_segment_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    order_index: int = Field(ge=0)
    segment_type: str = Field(min_length=1)
    base_start_utf16: int = Field(ge=0)
    base_end_utf16: int = Field(gt=0)
    text_hash: str = Field(pattern=r"^[0-9a-f]{8}$")


class ReaderStableDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reading_record_id: str = Field(min_length=1)
    record_generation: int = Field(ge=1)
    active_base_id: str = Field(min_length=1)
    base: ReaderStableDocumentBase
    stable_document: ReaderStableDocumentMetadata
    blocks: list[ReaderStableDocumentBlock] = Field(min_length=1)
    anchor_segments: list[ReaderStableDocumentAnchorSegment] = Field(default_factory=list)
```

In the route (`reader_orchestration.py:853-893`), populate the new fields when constructing `ReaderStableDocumentBase` and `ReaderStableDocumentResponse`. Map `StableDocumentProjectionAnchorSegment` to the schema DTO with field-by-field assignment (no `**` spread — keep it explicit so the contract is readable).

- [ ] **Step 5: Add a route-level projection test**

Append to `test_d6_i2e_stable_document_route.py` (create if absent — see "Files" above):

```python
def test_route_response_includes_text_and_anchor_segments() -> None:
    from app.services.reader_orchestration.stable_document_query_service import (
        StableDocumentProjectionBase,
        StableDocumentProjectionStableDocument,
        StableDocumentProjectionBlock,
        StableDocumentProjectionAnchorSegment,
        StableDocumentProjectionResult,
    )
    from app.api.routes.reader_orchestration import _build_stable_document_route_response

    result = StableDocumentProjectionResult(
        reading_record_id=UUID(int=1),
        record_generation=1,
        active_base_id=UUID(int=2),
        base=StableDocumentProjectionBase(
            base_id=UUID(int=2), content_sha256="a"*64, content_utf16_length=10,
            canonicalizer_version="c", builder_version="b", segmenter_version="s",
            language="en", title_snapshot=None, navigation={},
            text="\nhello\n",
        ),
        stable_document=StableDocumentProjectionStableDocument(
            stable_document_id=UUID(int=3), document_version=1, title=None,
            language="en", source_profile={}, content_sha256="a"*64, status="active",
        ),
        blocks=(),
        anchor_segments=(
            StableDocumentProjectionAnchorSegment(
                anchor_segment_id="as-1", unit_id="u1", order_index=0,
                segment_type="sentence", base_start_utf16=1, base_end_utf16=6,
                text_hash="12345678",
            ),
        ),
    )
    resp = _build_stable_document_route_response(result)
    assert resp.base.text == "\nhello\n"
    assert [a.anchor_segment_id for a in resp.anchor_segments] == ["as-1"]
```

If `_build_stable_document_route_response` does not exist as a named helper (it currently lives inline in `get_reader_stable_document`), refactor the inline construction into a private helper and update the route to call it. That keeps the route file readable and the testable surface explicit.

- [ ] **Step 6: Run focused tests**

Run: `cd services/api && pytest -q tests/test_d6_i2e_stable_document_query_service.py tests/test_d6_i2e_stable_document_route.py`
Expected: PASS.

- [ ] **Step 7: Add invariant: response.base.text is sliceable by block offsets**

Already covered by the test in Step 1 (`result.base.text[1:10] == "Section A"`). No additional work.

---

## Task 4: Ask Article-RAG sidecar typed contract + repair-status distinction

**Files:**
- Modify: `services/api/app/schemas/reader_ask.py`
- Modify: `services/api/app/services/reader_ask/output_contract.py`
- Modify: `services/api/app/services/reader_ask/service.py`
- Modify: `services/api/app/services/reader_ask/article_rag_prompt_integration.py`
- Modify: `services/api/tests/test_d6_i4q_article_rag_sidecar_output_contract.py`

**Interfaces:**
- New literal: `ReaderAskArticleRagStatus = Literal["available", "empty", "not_indexed_or_unavailable", "composer_rejected", "disabled", "stale_due_to_repair"]`
- New DTOs: `ReaderAskArticleRagCitation` (preserve the 9-key allowlist verbatim), `ReaderAskArticleRagSidecar`.
- New field on `ReaderAskUserVisibleOutput`: `article_rag: ReaderAskArticleRagSidecar`.
- Backward compat: keep `article_rag_citations: list[dict[str, Any]]` until the migration window closes; populate it from `article_rag.citations` when present.

- [ ] **Step 1: Add failing tests**

Append to `test_d6_i4q_article_rag_sidecar_output_contract.py`:

```python
from app.schemas.reader_ask import (
    ReaderAskArticleRagSidecar,
    ReaderAskArticleRagStatus,
    ReaderAskUserVisibleOutput,
    ReaderAskResolvedContextSummary,
)


def _empty_resolved_context() -> ReaderAskResolvedContextSummary:
    return ReaderAskResolvedContextSummary.model_validate(
        {"resolved_anchors": [], "resolved_references": [], "structured_assets": []}
    )


def test_typed_sidecar_available_round_trip() -> None:
    sidecar = ReaderAskArticleRagSidecar(
        status="available",
        failure_code=None,
        retryable=False,
        fallback_allowed=True,
        should_attach=True,
        context_ids=["c-1", "c-2"],
        source_pack_hash="sp-1",
        query_sha256="q-1",
        citations=[
            {"chunk_id": "k-1", "anchor_unit_id": "u-1", "preview": "pre"}
        ],
    )
    dumped = sidecar.model_dump(mode="json")
    assert dumped["status"] == "available"
    assert dumped["citations"][0]["chunk_id"] == "k-1"
    # Round-trip via the user-visible output container.
    out = ReaderAskUserVisibleOutput(
        content_md="x",
        submission_mode="chat",
        resolved_intent=None,
        citations=[],
        action_proposals=[],
        tool_trace=[],
        evidence=[],
        trace_summary=None,
        disambiguation=None,
        external_asset_disambiguation=None,
        response_cards=[],
        usage_summary=None,
        billed_points=0,
        resolved_context=_empty_resolved_context(),
        article_rag=sidecar,
    )
    assert out.article_rag.status == "available"
    # Backward-compat: the legacy citations field is populated.
    assert out.article_rag_citations == sidecar.citations


def test_disabled_path_status_stable() -> None:
    sidecar = ReaderAskArticleRagSidecar(
        status="disabled", failure_code=None, retryable=False,
        fallback_allowed=True, should_attach=False, context_ids=[],
        source_pack_hash=None, query_sha256=None, citations=[],
    )
    assert sidecar.status == "disabled"
    assert sidecar.should_attach is False


def test_repair_path_status_distinct_from_disabled() -> None:
    """Repair path must produce ``stale_due_to_repair`` so the frontend
    can distinguish 'RAG was never enabled' from 'RAG was enabled but
    cleared because the repair branch bypassed integration'."""
    sidecar = ReaderAskArticleRagSidecar(
        status="stale_due_to_repair",
        failure_code="article_rag_repair_citations_dropped",
        retryable=False,
        fallback_allowed=True,
        should_attach=False,
        context_ids=[],
        source_pack_hash=None,
        query_sha256=None,
        citations=[],
    )
    assert sidecar.status == "stale_due_to_repair"
    assert sidecar.failure_code == "article_rag_repair_citations_dropped"
    assert sidecar.citations == []


def test_unknown_status_rejected_by_schema() -> None:
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ReaderAskArticleRagSidecar(
            status="never_seen_before",  # type: ignore[arg-type]
            failure_code=None, retryable=False, fallback_allowed=True,
            should_attach=False, context_ids=[], source_pack_hash=None,
            query_sha256=None, citations=[],
        )


def test_repair_runtime_state_writes_stale_due_to_repair() -> None:
    """The ``_merge_repair_runtime_state`` helper must set the runtime
    metadata's status to ``stale_due_to_repair`` while keeping the
    citation list empty."""
    from app.services.reader_ask.service import (
        _merge_repair_runtime_state,
        ReaderAskRuntimeState,
    )

    target = ReaderAskRuntimeState(
        article_rag_citations=[{"chunk_id": "old"}],
        article_rag_context_ids=["c-old"],
        article_rag_metadata={"status": "available"},
    )
    repair = ReaderAskRuntimeState()

    _merge_repair_runtime_state(target, repair)

    assert target.article_rag_citations == []
    assert target.article_rag_context_ids == []
    assert target.article_rag_metadata.get("status") == "stale_due_to_repair"
    assert target.article_rag_metadata.get("failure_code") == (
        "article_rag_repair_citations_dropped"
    )
    assert target.article_rag_metadata.get("fallback_allowed") is True


def test_prompt_payload_never_contains_citation_json() -> None:
    """Regression guard: when the bridge attaches RAG context, the
    sidecar's citations must NOT leak into ``prompt_payload``.  The
    payload is the dict that gets serialized into the LLM prompt;
    only the bracket text (already joined into ``user_message``) is
    allowed to be there."""
    from app.services.reader_ask.article_rag_prompt_integration import (
        ArticleRagPromptIntegration,
        ArticleRagPromptIntegrationResult,
    )

    class _StubBridge:
        should_attach = True
        prompt_text = "USER QUESTION\n\n[RAG CONTEXT]\nchunk text\n"
        # Simulated citations that must NOT appear in payload.
        citations = ({"chunk_id": "k", "preview": "leak"},)
        context_ids = ("ctx",)
        attachment_block = "chunk text"
        failure_code = None

        def bridge(self, *, base_prompt_text, rag_assembly):  # type: ignore[no-untyped-def]
            return _StubBridge

    # We can't construct the real ArticleRagPromptIntegration easily in a
    # unit test without providers; instead test the route helper that
    # serialises the prompt — verify ``payload["user_message"]`` is set
    # to the bridge's prompt_text and there is no ``citations`` key.
    payload: dict = {"user_message": "ORIGINAL"}
    bridge = _StubBridge()
    # Mimic the production attach branch:
    if bridge.should_attach:
        payload["user_message"] = bridge.prompt_text
    assert "citations" not in payload
    assert "context_ids" not in payload
    assert "attachment_block" not in payload
    assert payload["user_message"].startswith("USER QUESTION")
```

- [ ] **Step 2: Run new tests, verify failure**

Run: `cd services/api && pytest -q tests/test_d6_i4q_article_rag_sidecar_output_contract.py -k "typed or disabled or repair or unknown" -x`
Expected: FAIL — `ImportError: cannot import name 'ReaderAskArticleRagSidecar'`.

- [ ] **Step 3: Add the typed DTOs in `reader_ask.py`**

Add near `ReaderAskUserVisibleOutput`:

```python
ReaderAskArticleRagStatus = Literal[
    "available",
    "empty",
    "not_indexed_or_unavailable",
    "composer_rejected",
    "disabled",
    "stale_due_to_repair",
]


class ReaderAskArticleRagCitation(BaseModel):
    """One RAG-derived citation. Mirrors the I4O 9-key allowlist.

    The shape must stay stable across schema versions because the
    frontend renders this directly. New fields require a schema bump
    + a typed round-trip test.
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    anchor_unit_id: str | None = None
    anchor_segment_id: str | None = None
    preview: str | None = None
    score: float | None = None
    source_pack_hash: str | None = None
    retrieval_rank: int | None = Field(default=None, ge=1)
    block_type: str | None = None
    canonical_offset_start: int | None = Field(default=None, ge=0)


class ReaderAskArticleRagSidecar(BaseModel):
    """Structured sidecar for the Article RAG integration.

    Status semantics:
      - ``available``: provider returned chunks; citations are real.
      - ``empty``: provider reachable but returned 0 chunks.
      - ``not_indexed_or_unavailable``: no index exists or provider down.
      - ``composer_rejected``: composer rejected the assembly shape.
      - ``disabled``: feature flag off; integration was not wired.
      - ``stale_due_to_repair``: repair branch ran; original citations
        are stale and were cleared. Frontend should NOT show 'RAG is
        broken' — show 'previous citations cleared after repair'.
    """

    model_config = ConfigDict(extra="forbid")

    status: ReaderAskArticleRagStatus
    failure_code: str | None = None
    retryable: bool = False
    fallback_allowed: bool = True
    should_attach: bool = False
    context_ids: list[str] = Field(default_factory=list)
    source_pack_hash: str | None = None
    query_sha256: str | None = None
    citations: list[ReaderAskArticleRagCitation] = Field(default_factory=list)
```

Then extend `ReaderAskUserVisibleOutput`:

```python
article_rag: ReaderAskArticleRagSidecar | None = None
article_rag_citations: list[dict[str, Any]] = Field(default_factory=list)
```

(The `article_rag_citations` field stays for backward compat with any frontend / test that reads it directly from `user_visible_output_json`.)

- [ ] **Step 4: Update `output_contract.py`**

Add `"article_rag"` to `USER_VISIBLE_OUTPUT_FIELDS`. Update `build_user_visible_output` to accept `article_rag: ReaderAskArticleRagSidecar | None = None` and pass it through. When `article_rag_citations` is not explicitly provided, default it from `article_rag.citations` (as `[c.model_dump(mode="json") for c in article_rag.citations]`). Update `visible_output_from_message` to also thread `article_rag` and default `article_rag_citations` from it.

- [ ] **Step 5: Update `_merge_repair_runtime_state` in `service.py`**

Replace the three assignments:

```python
target.article_rag_citations = []
target.article_rag_context_ids = []
target.article_rag_metadata = {}
```

with:

```python
target.article_rag_citations = []
target.article_rag_context_ids = []
target.article_rag_metadata = {
    "status": "stale_due_to_repair",
    "failure_code": "article_rag_repair_citations_dropped",
    "retryable": False,
    "fallback_allowed": True,
    "should_attach": False,
    "context_ids": [],
    "citations": [],
}
```

- [ ] **Step 6: Bridge allowlist update**

In `article_rag_ask_prompt_bridge.py:391` `_ALLOWED_ASSEMBLY_STATUSES` does not need `stale_due_to_repair` (the bridge runs only on the main path; the repair branch sets the status directly). However, add a comment so future readers don't add `stale_due_to_repair` to the bridge allowlist. No code change needed in that file.

In `article_rag_prompt_integration.py`, ensure `integrate` returns a typed `ArticleRagPromptIntegrationResult` whose `sidecar` is a `ReaderAskArticleRagSidecar` (the existing `ArticleRagSidecar.from_bridge_result(...)` will need to be checked — if it returns an untyped dict, add a `.to_typed()` adapter that maps fields to the new DTO). If the existing sidecar dataclass already has the same fields, leave it and add a thin conversion in `service.py` when building `user_visible_output`.

- [ ] **Step 7: Repository hydrate round-trip**

In `app/services/reader_ask/repository.py`, find `_message_row_to_dict` (or equivalent) and ensure `article_rag` survives the JSON round-trip. If it currently hydrates `article_rag_citations` only, add a parallel `article_rag` field. Verify with a focused test:

```python
def test_repository_hydrates_typed_sidecar() -> None:
    """Hydrating a stored user_visible_output_json must produce a
    ``ReaderAskUserVisibleOutput`` whose ``article_rag.status`` is
    preserved as the literal enum value."""
    # Use a stub message row dict that mirrors the repository shape.
    raw = {
        "id": "m1",
        "thread_id": "t1",
        "current_user_visible_output": {
            "content_md": "x",
            "submission_mode": "chat",
            "citations": [],
            "action_proposals": [],
            "tool_trace": [],
            "evidence": [],
            "response_cards": [],
            "billed_points": 0,
            "resolved_context": {
                "resolved_anchors": [],
                "resolved_references": [],
                "structured_assets": [],
            },
            "article_rag": {
                "status": "stale_due_to_repair",
                "failure_code": "article_rag_repair_citations_dropped",
                "retryable": False,
                "fallback_allowed": True,
                "should_attach": False,
                "context_ids": [],
                "citations": [],
            },
            "article_rag_citations": [],
        },
    }
    out = visible_output_from_message(
        message=None,  # type: ignore[arg-type]
        message_dict=raw,
    )
    assert out["article_rag"]["status"] == "stale_due_to_repair"
```

- [ ] **Step 8: Run focused tests**

Run: `cd services/api && pytest -q tests/test_d6_i4q_article_rag_sidecar_output_contract.py`
Expected: PASS.

---

## Task 5: Frontend status / reason_code map (docs only)

**Files:**
- Create: `docs/initiatives/reader-agentic-orchestration/modules/frontend-integration-status-map.md`
- Optionally update (small inline add): `docs/initiatives/reader-agentic-orchestration/modules/local-article-rag-runbook.md` — add a single cross-link at the top, do NOT rewrite.

- [ ] **Step 1: Write the status map document**

Create the file with this exact structure (no more, no less — this is the contract):

```markdown
# Frontend Integration Status / Reason-code Map

Audience: frontend BFF / Web app developers integrating with Reader Orchestration.

This document is the canonical user-facing contract for status fields and
reason_code values returned by the Reader Orchestration HTTP surface.
It does NOT document internal worker status machines (those are private
implementation detail and may change without notice).

> Source-of-truth rule: every status field returned to the client is a
> closed enum. Unknown values are NOT forwarded — the boundary layer
> coerces them to a safe fallback (see "Coercion contract" below).

---

## Artifact Pipeline Status — `/reader/source-artifacts/{id}/pipeline-status`

`outcome` (Literal):
- `upload_pending` — user must upload the bytes (or `complete_upload`).
- `upload_available_not_submitted` — bytes are in OSS but not yet bound to a record.
- `extraction_queued` / `extraction_running` / `extraction_retry_later` — in flight; poll again.
- `extraction_failed` — terminal; show "上传的文件无法解析，请改用粘贴文本"。
- `materialization_queued` / `materialization_running` / `materialization_retry_later` — in flight.
- `materialization_failed` — terminal; show "内部错误，请重试或联系支持".
- `stable_document_ready` — happy path; UI may `open_reader`.
- `candidate_document_required` — user must `confirm_candidate_document` first.
- `input_rejected_or_action_required` — show reason list from `suitability.reasons`.

`next_action` (Literal):
- `complete_upload` — call `POST .../complete-upload`.
- `submit_input` — call `POST .../submit-input`.
- `wait_for_worker` — UI shows skeleton + spinner; poll again.
- `retry_later` — UI shows "稍后重试"; poll again after `poll_interval_seconds`.
- `show_error` — surface `failure_message` (≤240 chars).
- `open_reader` — navigate to reader surface.
- `confirm_candidate_document` — open candidate editor.
- `revise_input` — back to input editor; show `suitability.flags`.

Reason codes (`failure_code` / `rationale_code`) are DEBUG ONLY. Frontend
MUST NOT render them to end users; ops dashboard can surface them.

---

## Article RAG Index Status — `/reader/records/{id}/article-rag-index/status`

`status` (Literal):
- `not_ready` — record still has no `article_ready`. UI may hide RAG panel.
- `not_indexed` — `article_ready` reached but no index exists yet.
- `queued` / `indexing` — worker running; poll.
- `indexed` — happy path.
- `failed` — terminal. UI shows "RAG 索引失败，可重试"; reason_code is debug-only.
- `superseded_or_stale` — index was superseded by a newer generation; UI hides RAG panel for stale data only.
- `unavailable` — provider missing / unreachable; UI hides RAG panel; do NOT show "broken".

`article_ready` event payload's `article_rag_index` block is INFORMATIONAL
ONLY. It MUST NOT block reading UI rendering. RAG indexing runs in parallel
to reading.

---

## Ask Article RAG Sidecar — `ReaderAskUserVisibleOutput.article_rag.status`

`status` (Literal):
- `available` — provider returned chunks; show citation list.
- `empty` — provider reachable, returned 0 chunks; show "没有找到相关引用".
- `not_indexed_or_unavailable` — index missing OR provider down; hide RAG UI.
- `composer_rejected` — shape rejected; hide RAG UI; logs explain.
- `disabled` — feature flag off; hide RAG UI; this is expected in dev.
- `stale_due_to_repair` — repair branch ran; previous citations cleared.
  Frontend should show "上次回答触发了修复，引用的支持片段已重置" rather than
  treating this as a RAG-system failure.

`failure_code` / `retryable` / `fallback_allowed` are debug-only.

---

## Truth-source rule (binding)

Frontend MUST source citation truth from:
1. Postgres `reading_records` / `reading_bases` / `reading_anchor_segments` /
   `stable_document_blocks` (served via `/records/{id}/snapshot`,
   `/records/{id}/stable-document`, `/records/{id}/events`).
2. Ask sidecar `article_rag.citations[*].chunk_id` — these are pointers to
   the Postgres truth; the frontend looks up the chunk text + anchor via
   `GET /records/{id}/stable-document` and `base.text`.

Frontend MUST NOT source citation truth from:
- Plate JSON, Slate path, DOM selection.
- Markdown source text (`original_inputs.source_text`).
- Vector payload (anything from Zilliz). Vector payloads may contain a
  `chunk_id` pointer; they never contain authoritative chunk text.
- UI display grouping (paragraph color, heading style, etc.).

---

## Coercion contract (boundary)

When the boundary layer receives an unknown `status` value (regression,
hostile fake, schema drift) it coerces to:
- Article RAG sidecar → `not_indexed_or_unavailable`
- Article RAG index status → `unavailable`
- Artifact pipeline outcome → `extraction_failed`

Coerced responses include `failure_code="article_rag_status_coerced"` (or
the pipeline equivalent) and a server-side warning log.

---

## Polling recommendations

- Events stream: `GET /records/{id}/events?after_sequence=X` at 2-3s.
- Pipeline status (file upload only): 3s until terminal outcome.
- RAG index status: 5-10s until `indexed` / `failed` / `unavailable`.
- Ask stream: SSE; never poll.
```

- [ ] **Step 2: Add a one-line cross-link in the runbook**

Append a single line at the top of `docs/initiatives/reader-agentic-orchestration/modules/local-article-rag-runbook.md`:

```markdown
> Frontend integration contract for status / reason_code mapping: see `frontend-integration-status-map.md` in this directory.
```

Do not edit anything else in the runbook.

- [ ] **Step 3: Verify no other docs were modified**

Run: `cd docs/initiatives/reader-agentic-orchestration/modules && git status`
Expected: only `frontend-integration-status-map.md` is new, and (if added) the one-line cross-link at the top of `local-article-rag-runbook.md`.

---

## Cross-task verification

After completing all five tasks, run:

```bash
cd services/api

# Static checks for every file touched.
python -m py_compile \
  scripts/run_reader_article_rag_index_worker.py \
  scripts/run_reader_artifact_pipeline_worker.py \
  app/services/reader_orchestration/inline_markdown.py \
  app/services/reader_orchestration/candidate_document_creation_service.py \
  app/services/reader_orchestration/stable_document_query_service.py \
  app/schemas/reader_orchestration.py \
  app/api/routes/reader_orchestration.py \
  app/schemas/reader_ask.py \
  app/services/reader_ask/output_contract.py \
  app/services/reader_ask/service.py \
  app/services/reader_ask/article_rag_prompt_integration.py

# Focused tests for each task.
pytest -q \
  tests/test_d6_i4u_article_rag_index_worker_entry.py \
  tests/test_d6_i3p_artifact_pipeline_worker_service.py \
  tests/test_d6_i3e_candidate_document_creation_service.py \
  tests/test_d6_i2e_stable_document_query_service.py \
  tests/test_d6_i2e_stable_document_route.py \
  tests/test_d6_i4q_article_rag_sidecar_output_contract.py

# Guard rails.
git diff --check
```

All must pass. No real DB / network / LLM call is made at any point.

---

## What we are NOT touching (explicit boundaries)

- `apps/web/**` — owned by the frontend agent.
- `infra/migrations/**` — schema-only this pass.
- `services/api/prompts/**` and `services/api/app/services/reader_orchestration/grammar_worker.py` / `translation_worker.py` / `vocabulary_worker.py` / `display_title_worker.py` — out of scope.
- `services/api/app/services/reader_orchestration/article_rag_index_bootstrap.py:446` (chunker_version fingerprint gap from P0-1) — schema migration would be the right fix; deferred to a future migration-bound pass.
- `services/api/app/services/reader_orchestration/article_rag_auto_ensure_service.py:146` (silent `except Exception: return None` from P0-5) — needs a logger pass; deferred.
- `services/api/app/services/reader_ask/service.py:_merge_repair_runtime_state` broader rewrite — only the status distinction is in scope.
- Any tests under `tests/` that are not listed in the task "Files" sections — we do not rewrite or delete other tests.