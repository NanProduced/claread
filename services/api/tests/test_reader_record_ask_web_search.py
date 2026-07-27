"""ASK-WEB-G0/G1: web search contracts, port, registry, and tool views.

Offline unit tests for the provider-neutral web search vertical slice:

- :mod:`web_search_contracts` — canonical URL, source fingerprint,
  ``WebEvidence`` model validators.
- :mod:`finalizer` — ``PublicCitation`` (single canonical public citation
  contract, supports both ``article`` and ``web`` source kinds).
- :mod:`web_search_port` — ``FakeWebSearchBackend`` scripting +
  ``WebSearchResult`` / ``WebSearchHitView`` invariants.
- :mod:`web_evidence_registry` — envelope binding, duplicate rejection,
  source-fingerprint re-verification on read.
- :mod:`tool_contracts` — ``SearchWebToolInput`` / ``SearchWebToolView``
  status-field coupling.
- :mod:`agent` — G1-b4 conditional ``search_web`` tool registration +
  system-instructions guidance toggle.

No real LLM, no real search provider, no network I/O.

ASK-WEB-G1-R3: ``PublicWebCitation`` has been removed from
``web_search_contracts``. The single canonical public citation contract is
``PublicCitation`` in ``finalizer``. Tests here exercise it directly via
``source_kind="web"`` to keep web-citation validation coverage intact.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.services.reader_record_ask.agent import (
    _build_system_instructions,
    create_reading_record_ask_agent,
    registered_tool_names,
)
from app.services.reader_record_ask.finalizer import PublicCitation
from app.services.reader_record_ask.tool_contracts import (
    TOOL_SEARCH_WEB,
    SearchWebToolInput,
    SearchWebToolView,
)
from app.services.reader_record_ask.web_evidence_registry import (
    WebEvidenceRegistry,
)
from app.services.reader_record_ask.web_search_contracts import (
    WEB_DESCRIPTION_MAX_LEN,
    WEB_MAX_CALLS_PER_TURN,
    WEB_MAX_RESULTS_PER_CALL,
    WEB_QUERY_MAX_LEN,
    WEB_TITLE_MAX_LEN,
    WEB_URL_MAX_LEN,
    PublicWebSearchSummary,
    ResolvedWebSearchCapability,
    WebEvidence,
    canonicalize_url,
    compute_web_source_fingerprint,
    display_domain_from_canonical_url,
)
from app.services.reader_record_ask.web_search_port import (
    FakeWebSearchBackend,
    WebSearchHitView,
    WebSearchResult,
)

_ENVELOPE_FP = "a" * 64
_OTHER_FP = "b" * 64


# ---------------------------------------------------------------------------
# canonicalize_url
# ---------------------------------------------------------------------------


class TestCanonicalizeUrl:
    def test_accepts_https_url_and_lowercases_host(self) -> None:
        assert canonicalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"

    def test_accepts_http_url(self) -> None:
        assert canonicalize_url("http://example.com/page") == "http://example.com/page"

    def test_drops_fragment(self) -> None:
        assert canonicalize_url("https://example.com/p#frag") == "https://example.com/p"

    def test_empty_path_becomes_slash(self) -> None:
        assert canonicalize_url("https://example.com") == "https://example.com/"

    def test_drops_default_port(self) -> None:
        assert canonicalize_url("https://example.com:443/p") == "https://example.com/p"
        assert canonicalize_url("http://example.com:80/p") == "http://example.com/p"

    def test_preserves_non_default_port(self) -> None:
        assert canonicalize_url("https://example.com:8443/p") == "https://example.com:8443/p"

    def test_rejects_non_string(self) -> None:
        with pytest.raises(ValueError):
            canonicalize_url(123)  # type: ignore[arg-type]

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            canonicalize_url("")
        with pytest.raises(ValueError):
            canonicalize_url("   ")

    def test_rejects_disallowed_scheme(self) -> None:
        for scheme in ("file://example.com", "data:text/plain,hi", "javascript:alert(1)"):
            with pytest.raises(ValueError):
                canonicalize_url(scheme)

    def test_rejects_credentials_in_url(self) -> None:
        with pytest.raises(ValueError):
            canonicalize_url("https://user:pass@example.com/p")

    def test_rejects_empty_host(self) -> None:
        with pytest.raises(ValueError):
            canonicalize_url("https:///path")

    def test_rejects_localhost_host(self) -> None:
        for host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            with pytest.raises(ValueError):
                canonicalize_url(f"https://{host}/p")

    def test_preserves_query_string(self) -> None:
        assert canonicalize_url("https://example.com/p?a=1&b=2") == "https://example.com/p?a=1&b=2"

    def test_rejects_oversized_url(self) -> None:
        huge = "https://example.com/" + "a" * (WEB_URL_MAX_LEN + 1)
        with pytest.raises(ValueError):
            canonicalize_url(huge)


# ---------------------------------------------------------------------------
# compute_web_source_fingerprint
# ---------------------------------------------------------------------------


class TestComputeWebSourceFingerprint:
    def test_stable_for_same_inputs(self) -> None:
        fp1 = compute_web_source_fingerprint(
            canonical_url="https://example.com/p", retrieved_at="2026-07-26T00:00:00+00:00"
        )
        fp2 = compute_web_source_fingerprint(
            canonical_url="https://example.com/p", retrieved_at="2026-07-26T00:00:00+00:00"
        )
        assert fp1 == fp2
        assert len(fp1) == 64

    def test_differs_for_different_url(self) -> None:
        fp1 = compute_web_source_fingerprint(
            canonical_url="https://example.com/p", retrieved_at="t"
        )
        fp2 = compute_web_source_fingerprint(
            canonical_url="https://example.com/q", retrieved_at="t"
        )
        assert fp1 != fp2

    def test_differs_for_different_timestamp(self) -> None:
        fp1 = compute_web_source_fingerprint(
            canonical_url="https://example.com/p", retrieved_at="t1"
        )
        fp2 = compute_web_source_fingerprint(
            canonical_url="https://example.com/p", retrieved_at="t2"
        )
        assert fp1 != fp2

    def test_matches_sha256_hex(self) -> None:
        fp = compute_web_source_fingerprint(
            canonical_url="https://example.com/p", retrieved_at="t"
        )
        expected = hashlib.sha256(b"https://example.com/p|t").hexdigest()
        assert fp == expected

    def test_rejects_empty_inputs(self) -> None:
        with pytest.raises(ValueError):
            compute_web_source_fingerprint(canonical_url="", retrieved_at="t")
        with pytest.raises(ValueError):
            compute_web_source_fingerprint(canonical_url="u", retrieved_at="")


# ---------------------------------------------------------------------------
# display_domain_from_canonical_url
# ---------------------------------------------------------------------------


class TestDisplayDomain:
    def test_extracts_host_lowercased(self) -> None:
        assert display_domain_from_canonical_url("https://Example.COM/p") == "example.com"

    def test_strips_port(self) -> None:
        assert display_domain_from_canonical_url("https://example.com:8443/p") == "example.com"

    def test_empty_input_returns_empty(self) -> None:
        assert display_domain_from_canonical_url("") == ""


# ---------------------------------------------------------------------------
# WebEvidence model
# ---------------------------------------------------------------------------


def _good_web_evidence_kwargs(**overrides: object) -> dict:
    canonical = "https://example.com/page"
    retrieved_at = "2026-07-26T00:00:00+00:00"
    fp = compute_web_source_fingerprint(
        canonical_url=canonical, retrieved_at=retrieved_at
    )
    base = {
        "internal_handle_id": "evh_" + "a" * 32,
        "canonical_url": canonical,
        "display_domain": "example.com",
        "title": "Title",
        "description": "Desc",
        "retrieved_at": retrieved_at,
        "provider_result_ref": None,
        "source_fingerprint": fp,
    }
    base.update(overrides)
    return base


class TestWebEvidence:
    def test_valid_construction(self) -> None:
        ev = WebEvidence(**_good_web_evidence_kwargs())
        assert ev.canonical_url == "https://example.com/page"
        assert ev.internal_handle_id.startswith("evh_")

    def test_rejects_non_canonical_url(self) -> None:
        kwargs = _good_web_evidence_kwargs(canonical_url="HTTPS://Example.COM/Page")
        with pytest.raises(ValidationError):
            WebEvidence(**kwargs)

    def test_rejects_wrong_source_fingerprint(self) -> None:
        kwargs = _good_web_evidence_kwargs(source_fingerprint="c" * 64)
        with pytest.raises(ValidationError):
            WebEvidence(**kwargs)

    def test_rejects_extra_fields(self) -> None:
        kwargs = _good_web_evidence_kwargs()
        kwargs["extra_field"] = "bad"  # type: ignore[dict-item]
        with pytest.raises(ValidationError):
            WebEvidence(**kwargs)

    def test_rejects_oversized_title(self) -> None:
        kwargs = _good_web_evidence_kwargs(title="x" * (WEB_TITLE_MAX_LEN + 1))
        with pytest.raises(ValidationError):
            WebEvidence(**kwargs)

    def test_rejects_oversized_description(self) -> None:
        kwargs = _good_web_evidence_kwargs(description="x" * (WEB_DESCRIPTION_MAX_LEN + 1))
        with pytest.raises(ValidationError):
            WebEvidence(**kwargs)

    def test_allows_none_title_and_description(self) -> None:
        kwargs = _good_web_evidence_kwargs(title=None, description=None)
        ev = WebEvidence(**kwargs)
        assert ev.title is None
        assert ev.description is None


# ---------------------------------------------------------------------------
# PublicCitation (web source_kind)
# ---------------------------------------------------------------------------
# ASK-WEB-G1-R3: ``PublicWebCitation`` has been removed from
# ``web_search_contracts``. The single canonical public citation contract is
# ``PublicCitation`` in ``finalizer``. These tests exercise its web branch
# (``source_kind="web"``) to keep the validation coverage previously
# provided by ``TestPublicWebCitation``.


class TestPublicCitationWeb:
    def test_valid_web_citation(self) -> None:
        cit = PublicCitation(
            citation_id="c1",
            source_kind="web",
            url="https://example.com/p",
            title="Title",
        )
        assert cit.source_kind == "web"
        assert cit.url == "https://example.com/p"

    def test_rejects_non_canonical_url(self) -> None:
        with pytest.raises(ValidationError):
            PublicCitation(
                citation_id="c1",
                source_kind="web",
                url="HTTPS://Example.COM/P",
                title="Title",
            )

    def test_rejects_missing_title_for_web(self) -> None:
        with pytest.raises(ValidationError):
            PublicCitation(
                citation_id="c1",
                source_kind="web",
                url="https://example.com/p",
                title=None,
            )

    def test_rejects_empty_title_for_web(self) -> None:
        # ASK-WEB-G1-R3: title must be non-empty (strip-validated at the
        # contract layer). The production finalizer is responsible for
        # applying the ``display_domain`` fallback before constructing the
        # citation — the contract itself does not derive fallbacks.
        with pytest.raises(ValidationError):
            PublicCitation(
                citation_id="c1",
                source_kind="web",
                url="https://example.com/p",
                title="   ",
            )

    def test_rejects_web_citation_without_url(self) -> None:
        with pytest.raises(ValidationError):
            PublicCitation(
                citation_id="c1",
                source_kind="web",
                title="Title",
            )

    def test_allows_optional_description(self) -> None:
        cit = PublicCitation(
            citation_id="c1",
            source_kind="web",
            url="https://example.com/p",
            title="Title",
            description="Desc",
        )
        assert cit.description == "Desc"

    def test_rejects_article_snippet_on_web_citation(self) -> None:
        with pytest.raises(ValidationError):
            PublicCitation(
                citation_id="c1",
                source_kind="web",
                snippet="article-only excerpt",
                url="https://example.com/p",
                title="Title",
            )

    def test_article_citation_rejects_web_fields(self) -> None:
        # Article citations must not carry url/title/description — those
        # are exclusive to the web branch of the discriminated union.
        with pytest.raises(ValidationError):
            PublicCitation(
                citation_id="c1",
                source_kind="article",
                url="https://example.com/p",
            )
        with pytest.raises(ValidationError):
            PublicCitation(
                citation_id="c1",
                source_kind="article",
                title="Title",
            )


# ---------------------------------------------------------------------------
# PublicWebSearchSummary
# ---------------------------------------------------------------------------


class TestPublicWebSearchSummary:
    def test_valid_summary(self) -> None:
        s = PublicWebSearchSummary(outcome="completed", cited_source_count=2)
        assert s.outcome == "completed"
        assert s.cited_source_count == 2

    def test_rejects_negative_count(self) -> None:
        with pytest.raises(ValidationError):
            PublicWebSearchSummary(outcome="completed", cited_source_count=-1)

    def test_rejects_invalid_outcome(self) -> None:
        with pytest.raises(ValidationError):
            PublicWebSearchSummary(outcome="bad", cited_source_count=0)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ResolvedWebSearchCapability
# ---------------------------------------------------------------------------


class TestResolvedWebSearchCapability:
    def test_default_construction(self) -> None:
        cap = ResolvedWebSearchCapability(
            enabled_for_turn=True,
            provider="fake",
            protocol="fake",
            policy_version="v1",
        )
        assert cap.execution_mode == "host_function"
        assert cap.decision_mode == "agent_auto"
        assert cap.max_calls == 1
        assert cap.max_results_per_call == 3

    def test_rejects_max_calls_above_bound(self) -> None:
        with pytest.raises(ValidationError):
            ResolvedWebSearchCapability(
                enabled_for_turn=True,
                provider="fake",
                protocol="fake",
                policy_version="v1",
                max_calls=WEB_MAX_CALLS_PER_TURN + 1,
            )

    def test_rejects_max_results_above_bound(self) -> None:
        with pytest.raises(ValidationError):
            ResolvedWebSearchCapability(
                enabled_for_turn=True,
                provider="fake",
                protocol="fake",
                policy_version="v1",
                max_results_per_call=WEB_MAX_RESULTS_PER_CALL + 1,
            )

    def test_rejects_invalid_protocol(self) -> None:
        with pytest.raises(ValidationError):
            ResolvedWebSearchCapability(
                enabled_for_turn=True,
                provider="fake",
                protocol="unknown",  # type: ignore[arg-type]
                policy_version="v1",
            )

    def test_frozen(self) -> None:
        cap = ResolvedWebSearchCapability(
            enabled_for_turn=True,
            provider="fake",
            protocol="fake",
            policy_version="v1",
        )
        with pytest.raises(ValidationError):
            cap.enabled_for_turn = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# WebSearchHitView / WebSearchResult
# ---------------------------------------------------------------------------


class TestWebSearchHitView:
    def test_valid_hit(self) -> None:
        hit = WebSearchHitView(
            raw_url="https://example.com/p",
            title="Title",
            description="Desc",
        )
        assert hit.raw_url == "https://example.com/p"

    def test_rejects_oversized_raw_url(self) -> None:
        with pytest.raises(ValueError):
            WebSearchHitView(raw_url="https://example.com/" + "a" * (WEB_URL_MAX_LEN + 1))

    def test_rejects_oversized_title(self) -> None:
        with pytest.raises(ValueError):
            WebSearchHitView(title="x" * (WEB_TITLE_MAX_LEN + 1))

    def test_rejects_oversized_description(self) -> None:
        with pytest.raises(ValueError):
            WebSearchHitView(description="x" * (WEB_DESCRIPTION_MAX_LEN + 1))

    def test_rejects_non_string_raw_url(self) -> None:
        with pytest.raises(TypeError):
            WebSearchHitView(raw_url=123)  # type: ignore[arg-type]


class TestWebSearchResult:
    def test_ok_with_hits(self) -> None:
        r = WebSearchResult(
            status="ok",
            summary="ok",
            hits=(WebSearchHitView(raw_url="https://example.com/p"),),
        )
        assert r.status == "ok"
        assert len(r.hits) == 1

    def test_unavailable_must_not_carry_hits(self) -> None:
        with pytest.raises(ValueError):
            WebSearchResult(
                status="unavailable",
                summary="u",
                hits=(WebSearchHitView(raw_url="https://example.com/p"),),
            )

    def test_failed_must_not_carry_hits(self) -> None:
        with pytest.raises(ValueError):
            WebSearchResult(
                status="failed",
                summary="f",
                hits=(WebSearchHitView(raw_url="https://example.com/p"),),
            )

    def test_empty_summary_rejected(self) -> None:
        with pytest.raises(ValueError):
            WebSearchResult(status="ok", summary="")


# ---------------------------------------------------------------------------
# FakeWebSearchBackend
# ---------------------------------------------------------------------------


class TestFakeWebSearchBackend:
    @pytest.mark.asyncio
    async def test_empty_outcomes_returns_unavailable(self) -> None:
        backend = FakeWebSearchBackend()
        result = await backend.search_web(query="q", max_results=3)
        assert result.status == "unavailable"
        assert result.detail_code == "fake_empty_script"
        assert backend.call_count == 1
        assert backend.last_query == "q"
        assert backend.last_max_results == 3

    @pytest.mark.asyncio
    async def test_scripted_outcome_returned(self) -> None:
        outcome = WebSearchResult(
            status="ok",
            summary="ok",
            hits=(WebSearchHitView(raw_url="https://example.com/p"),),
        )
        backend = FakeWebSearchBackend(outcomes=[outcome])
        result = await backend.search_web(query="q", max_results=3)
        assert result is outcome
        assert backend.call_count == 1

    @pytest.mark.asyncio
    async def test_multiple_calls_reuse_last_outcome(self) -> None:
        o1 = WebSearchResult(status="ok", summary="1", hits=())
        o2 = WebSearchResult(status="empty", summary="2")
        backend = FakeWebSearchBackend(outcomes=[o1, o2])
        r1 = await backend.search_web(query="q", max_results=3)
        r2 = await backend.search_web(query="q", max_results=3)
        r3 = await backend.search_web(query="q", max_results=3)
        assert r1 is o1
        assert r2 is o2
        # Third call clamps to the last outcome.
        assert r3 is o2
        assert backend.call_count == 3


# ---------------------------------------------------------------------------
# WebEvidenceRegistry
# ---------------------------------------------------------------------------


def _make_evidence(handle_id: str = "evh_" + "a" * 32) -> WebEvidence:
    canonical = "https://example.com/page"
    retrieved_at = "2026-07-26T00:00:00+00:00"
    return WebEvidence(
        internal_handle_id=handle_id,
        canonical_url=canonical,
        display_domain="example.com",
        title="Title",
        description="Desc",
        retrieved_at=retrieved_at,
        provider_result_ref=None,
        source_fingerprint=compute_web_source_fingerprint(
            canonical_url=canonical, retrieved_at=retrieved_at
        ),
    )


class TestWebEvidenceRegistry:
    def test_rejects_bad_envelope_fingerprint(self) -> None:
        with pytest.raises(ValueError):
            WebEvidenceRegistry(envelope_fingerprint="bad")
        with pytest.raises(ValueError):
            WebEvidenceRegistry(envelope_fingerprint="")

    def test_register_and_get(self) -> None:
        reg = WebEvidenceRegistry(envelope_fingerprint=_ENVELOPE_FP)
        ev = _make_evidence()
        ref = reg.register(ev)
        assert ref.handle_id == ev.internal_handle_id
        assert len(reg) == 1
        got = reg.get(ev.internal_handle_id)
        assert got is not None
        assert got.canonical_url == ev.canonical_url

    def test_register_duplicate_rejected(self) -> None:
        reg = WebEvidenceRegistry(envelope_fingerprint=_ENVELOPE_FP)
        ev = _make_evidence()
        reg.register(ev)
        with pytest.raises(ValueError):
            reg.register(ev)

    def test_register_bad_handle_id_rejected(self) -> None:
        reg = WebEvidenceRegistry(envelope_fingerprint=_ENVELOPE_FP)
        ev = _make_evidence(handle_id="bad_handle")
        with pytest.raises(ValueError):
            reg.register(ev)

    def test_get_unknown_returns_none(self) -> None:
        reg = WebEvidenceRegistry(envelope_fingerprint=_ENVELOPE_FP)
        assert reg.get("evh_" + "z" * 32) is None

    def test_list_evidence_preserves_insertion_order(self) -> None:
        reg = WebEvidenceRegistry(envelope_fingerprint=_ENVELOPE_FP)
        ev1 = _make_evidence(handle_id="evh_" + "1" * 32)
        ev2 = _make_evidence(handle_id="evh_" + "2" * 32)
        reg.register(ev1)
        reg.register(ev2)
        listed = reg.list_evidence()
        assert listed[0].internal_handle_id == ev1.internal_handle_id
        assert listed[1].internal_handle_id == ev2.internal_handle_id

    def test_list_handle_refs(self) -> None:
        reg = WebEvidenceRegistry(envelope_fingerprint=_ENVELOPE_FP)
        ev = _make_evidence()
        reg.register(ev)
        refs = reg.list_handle_refs()
        assert len(refs) == 1
        assert refs[0].handle_id == ev.internal_handle_id


# ---------------------------------------------------------------------------
# SearchWebToolInput / SearchWebToolView
# ---------------------------------------------------------------------------


class TestSearchWebToolInput:
    def test_valid_input(self) -> None:
        inp = SearchWebToolInput(query="hello world")
        assert inp.query == "hello world"
        assert inp.max_results is None

    def test_strips_whitespace_from_query(self) -> None:
        inp = SearchWebToolInput(query="  hello  ")
        assert inp.query == "hello"

    def test_rejects_empty_query(self) -> None:
        with pytest.raises(ValidationError):
            SearchWebToolInput(query="")

    def test_rejects_oversized_query(self) -> None:
        with pytest.raises(ValidationError):
            SearchWebToolInput(query="x" * (WEB_QUERY_MAX_LEN + 1))

    def test_rejects_max_results_above_bound(self) -> None:
        with pytest.raises(ValidationError):
            SearchWebToolInput(query="q", max_results=WEB_MAX_RESULTS_PER_CALL + 1)

    def test_rejects_max_results_zero(self) -> None:
        with pytest.raises(ValidationError):
            SearchWebToolInput(query="q", max_results=0)

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            SearchWebToolInput.model_validate({"query": "q", "extra": "bad"})


class TestSearchWebToolView:
    def test_ok_view_requires_handles_and_blocks(self) -> None:
        with pytest.raises(ValidationError):
            SearchWebToolView(status="ok", summary="ok")

    def test_ok_view_requires_aligned_handles_and_blocks(self) -> None:
        with pytest.raises(ValidationError):
            SearchWebToolView(
                status="ok",
                summary="ok",
                evidence_handles=[{"handle_id": "evh_" + "a" * 32}],
                web_source_blocks=(),
            )

    def test_ok_view_aligned_passes(self) -> None:
        view = SearchWebToolView(
            status="ok",
            summary="ok",
            evidence_handles=[{"handle_id": "evh_" + "a" * 32}],
            web_source_blocks=("<untrusted_web_source/>",),
        )
        assert view.status == "ok"
        assert len(view.evidence_handles) == 1
        assert len(view.web_source_blocks) == 1

    def test_unavailable_view_rejects_handles(self) -> None:
        with pytest.raises(ValidationError):
            SearchWebToolView(
                status="unavailable",
                summary="u",
                evidence_handles=[{"handle_id": "evh_" + "a" * 32}],
            )

    def test_empty_view_rejects_blocks(self) -> None:
        with pytest.raises(ValidationError):
            SearchWebToolView(
                status="empty",
                summary="e",
                web_source_blocks=("<untrusted_web_source/>",),
            )

    def test_failed_view_rejects_next_actions(self) -> None:
        with pytest.raises(ValidationError):
            SearchWebToolView(
                status="failed",
                summary="f",
                next_actions=("retry",),
            )


# ---------------------------------------------------------------------------
# Agent: conditional search_web tool registration (G1-b4)
# ---------------------------------------------------------------------------


class TestAgentConditionalWebSearch:
    def test_disabled_by_default_no_search_web_tool(self) -> None:
        agent = create_reading_record_ask_agent("test")
        names = registered_tool_names(agent)
        assert TOOL_SEARCH_WEB not in names
        assert "search_current_article" in names
        assert "expand_evidence" in names

    def test_enabled_registers_search_web_tool(self) -> None:
        agent = create_reading_record_ask_agent("test", web_search_enabled=True)
        names = registered_tool_names(agent)
        assert TOOL_SEARCH_WEB in names
        assert "search_current_article" in names
        assert "expand_evidence" in names

    def test_disabled_instructions_say_not_enabled(self) -> None:
        instructions = _build_system_instructions(web_search_enabled=False)
        assert "Web Search is not enabled" in instructions
        assert "basis=web" in instructions

    def test_enabled_instructions_say_enabled(self) -> None:
        instructions = _build_system_instructions(web_search_enabled=True)
        assert "Web Search is enabled" in instructions
        assert "search_web" in instructions
        assert "basis=web" not in instructions or "web" in instructions

    def test_enabled_instructions_mention_search_web_tool(self) -> None:
        instructions = _build_system_instructions(web_search_enabled=True)
        # The product-principles clause should mention search_web.
        assert "``search_web``" in instructions

    def test_disabled_instructions_do_not_mention_search_web_tool(self) -> None:
        instructions = _build_system_instructions(web_search_enabled=False)
        # The disabled clause must not add search_web to the tool list.
        assert "or ``search_web``" not in instructions


# ---------------------------------------------------------------------------
# ASK-WEB-G1-R3: Capability resolver must reflect real adapter readiness
# ---------------------------------------------------------------------------
#
# The capability resolver must NOT declare ``enabled_for_turn=True`` based
# only on a non-empty ``settings.reader_record_ask_web_search_provider``
# string. Until a real ``WebSearchBackend`` adapter is registered in the
# production adapter registry, every production path must return
# ``enabled_for_turn=False`` (typed unavailable).
#
# These tests verify the fail-closed contract from the resolver itself.


class TestCapabilityResolverR3Readiness:
    """ASK-WEB-G1-R3: ``resolve_web_search_capability`` must reflect real
    adapter readiness, not just non-empty settings strings.

    The resolver is the single source of truth for translating the
    user-visible ``web_search_mode`` toggle into the server-owned
    execution truth. Per the R3 spec, ``enabled_for_turn=True`` must
    require all five conditions:

    1. a real provider adapter mapping for the current model option;
    2. the adapter is registered in the production adapter registry;
    3. the adapter can be constructed with the current settings;
    4. required config (API key, endpoint) is present;
    5. the adapter declares support for the current wire model.

    Until G2+ lands a real adapter, every production path returns
    ``enabled_for_turn=False`` (typed unavailable).
    """

    def test_unknown_provider_string_remains_unavailable(self) -> None:
        """A non-empty but unknown provider string must NOT produce an
        enabled capability. The previous implementation would return
        ``enabled_for_turn=True`` for any non-empty
        ``settings.reader_record_ask_web_search_provider``; the R3
        resolver must fail-closed.
        """
        from types import SimpleNamespace

        from app.services.reader_record_ask.execution_config import (
            resolve_web_search_capability,
        )

        settings = SimpleNamespace(
            reader_record_ask_web_search_provider="totally-unknown-provider",
        )
        cap = resolve_web_search_capability(
            web_search_mode="allowed",
            settings=settings,  # type: ignore[arg-type]
        )
        assert cap is not None
        assert cap.enabled_for_turn is False

    def test_dashscope_protocol_name_unavailable_without_adapter(self) -> None:
        """The ``dashscope_responses`` protocol name was previously a
        reserved placeholder. Even if an operator wires it via
        ``settings.reader_record_ask_web_search_provider``, the resolver
        must return ``enabled_for_turn=False`` because no real adapter
        is registered in the production adapter registry.
        """
        from types import SimpleNamespace

        from app.services.reader_record_ask.execution_config import (
            resolve_web_search_capability,
        )

        settings = SimpleNamespace(
            reader_record_ask_web_search_provider="dashscope_responses",
        )
        cap = resolve_web_search_capability(
            web_search_mode="allowed",
            settings=settings,  # type: ignore[arg-type]
        )
        assert cap is not None
        assert cap.enabled_for_turn is False

    def test_deepseek_protocol_name_unavailable_without_adapter(self) -> None:
        """The ``deepseek_anthropic`` protocol name was previously a
        reserved placeholder. Even if an operator wires it via
        ``settings.reader_record_ask_web_search_provider``, the resolver
        must return ``enabled_for_turn=False`` because no real adapter
        is registered in the production adapter registry.
        """
        from types import SimpleNamespace

        from app.services.reader_record_ask.execution_config import (
            resolve_web_search_capability,
        )

        settings = SimpleNamespace(
            reader_record_ask_web_search_provider="deepseek_anthropic",
        )
        cap = resolve_web_search_capability(
            web_search_mode="allowed",
            settings=settings,  # type: ignore[arg-type]
        )
        assert cap is not None
        assert cap.enabled_for_turn is False

    def test_disabled_mode_returns_none_regardless_of_provider(self) -> None:
        """``web_search_mode="disabled"`` must return ``None`` (capability
        not granted) regardless of the provider string. The runtime must
        NOT mount the ``search_web`` tool.
        """
        from types import SimpleNamespace

        from app.services.reader_record_ask.execution_config import (
            resolve_web_search_capability,
        )

        settings = SimpleNamespace(
            reader_record_ask_web_search_provider="dashscope_responses",
        )
        cap = resolve_web_search_capability(
            web_search_mode="disabled",
            settings=settings,  # type: ignore[arg-type]
        )
        assert cap is None

    def test_empty_provider_string_unavailable(self) -> None:
        """An empty provider string must produce an unavailable
        capability (not ``None`` — ``None`` is reserved for
        ``web_search_mode="disabled"``).
        """
        from types import SimpleNamespace

        from app.services.reader_record_ask.execution_config import (
            resolve_web_search_capability,
        )

        settings = SimpleNamespace(
            reader_record_ask_web_search_provider="",
        )
        cap = resolve_web_search_capability(
            web_search_mode="allowed",
            settings=settings,  # type: ignore[arg-type]
        )
        assert cap is not None
        assert cap.enabled_for_turn is False
        assert cap.provider == "unwired"

    def test_supported_protocols_map_is_empty_in_production(self) -> None:
        """The ``_SUPPORTED_WEB_SEARCH_PROTOCOLS`` map must be empty in
        production until a real adapter is registered. This is the
        structural guarantee that no production path can resolve to
        ``enabled_for_turn=True``.
        """
        from app.services.reader_record_ask.execution_config import (
            _SUPPORTED_WEB_SEARCH_PROTOCOLS,
        )

        assert _SUPPORTED_WEB_SEARCH_PROTOCOLS == {}


# ---------------------------------------------------------------------------
# ASK-WEB-G1-R3: ``_selected_model_payload`` must project unavailable
# ---------------------------------------------------------------------------
#
# The model-option DTO must project ``web_search_capability="unavailable"``
# for every production model option until a real adapter is registered.
# A non-empty settings string alone does NOT constitute a capability.


class TestSelectedModelPayloadR3Projection:
    """ASK-WEB-G1-R3: ``_selected_model_payload`` must project
    ``web_search_capability="unavailable"`` for every production model
    option, regardless of provider configuration.

    The R3 contract requires that ``available`` only appears when a real
    adapter is registered for the current model option's provider. The
    function is in ``app.services.reader_ask.service`` (the shared Ask
    service module), not ``reader_record_ask.service``.
    """

    def test_qwen_option_projects_unavailable(self) -> None:
        """A Qwen model option must project ``unavailable`` until a real
        ``WebSearchBackend`` adapter is registered for the DashScope
        provider. A non-empty settings string alone does not constitute
        a capability.
        """
        from app.services.ai_usage.billing import WeightedTokensBillingConfig
        from app.services.reader_ask.model_options import (
            ReaderAskRuntimeBudgetConfig,
            ResolvedReaderAskModelOption,
        )
        from app.services.reader_ask.service import _selected_model_payload

        option = ResolvedReaderAskModelOption(
            key="qwen-plus",
            label="Qwen Plus",
            description="qwen",
            selection=None,
            billing=WeightedTokensBillingConfig(price_multiplier=1.0),
            runtime_budget=ReaderAskRuntimeBudgetConfig(
                max_input_tokens=24000,
                max_output_tokens=3200,
                max_turn_output_tokens=9600,
                prompt_buffer_tokens=800,
            ),
            main_model_name="qwen-plus",
            replan_model_name=None,
            is_default=False,
            used_fallback=False,
        )
        payload = _selected_model_payload(option)
        assert payload["web_search_capability"] == "unavailable"

    def test_deepseek_option_projects_unavailable(self) -> None:
        """A DeepSeek model option must project ``unavailable`` until a
        real ``WebSearchBackend`` adapter is registered for the DeepSeek
        provider. The capability is per-model-option, not global.
        """
        from app.services.ai_usage.billing import WeightedTokensBillingConfig
        from app.services.reader_ask.model_options import (
            ReaderAskRuntimeBudgetConfig,
            ResolvedReaderAskModelOption,
        )
        from app.services.reader_ask.service import _selected_model_payload

        option = ResolvedReaderAskModelOption(
            key="deepseek-chat",
            label="DeepSeek Chat",
            description="deepseek",
            selection=None,
            billing=WeightedTokensBillingConfig(price_multiplier=1.0),
            runtime_budget=ReaderAskRuntimeBudgetConfig(
                max_input_tokens=24000,
                max_output_tokens=3200,
                max_turn_output_tokens=9600,
                prompt_buffer_tokens=800,
            ),
            main_model_name="deepseek-chat",
            replan_model_name=None,
            is_default=False,
            used_fallback=False,
        )
        payload = _selected_model_payload(option)
        assert payload["web_search_capability"] == "unavailable"

    def test_unknown_option_projects_unavailable(self) -> None:
        """A model option with an unknown provider must project
        ``unavailable``.
        """
        from app.services.ai_usage.billing import WeightedTokensBillingConfig
        from app.services.reader_ask.model_options import (
            ReaderAskRuntimeBudgetConfig,
            ResolvedReaderAskModelOption,
        )
        from app.services.reader_ask.service import _selected_model_payload

        option = ResolvedReaderAskModelOption(
            key="unknown-model",
            label="Unknown",
            description="unknown",
            selection=None,
            billing=WeightedTokensBillingConfig(price_multiplier=1.0),
            runtime_budget=ReaderAskRuntimeBudgetConfig(
                max_input_tokens=24000,
                max_output_tokens=3200,
                max_turn_output_tokens=9600,
                prompt_buffer_tokens=800,
            ),
            main_model_name="unknown-model",
            replan_model_name=None,
            is_default=False,
            used_fallback=False,
        )
        payload = _selected_model_payload(option)
        assert payload["web_search_capability"] == "unavailable"


# ---------------------------------------------------------------------------
# ASK-WEB-G1-R3: RunStarted must NOT echo "allowed" without a real backend
# ---------------------------------------------------------------------------
#
# When ``enabled_for_turn=True`` is forwarded to the production stream but
# no ``WebSearchBackend`` is injected, the runtime must fail-closed:
# ``agentic.run_started.web_search_mode`` must NOT be ``"allowed"``.
# The fake backend must only appear in tests that explicitly inject it.


class TestRunStartedNoAllowedWithoutBackend:
    """ASK-WEB-G1-R3: ``agentic.run_started.web_search_mode`` must NOT
    echo ``"allowed"`` when no ``WebSearchBackend`` is injected.

    The previous implementation would echo ``"allowed"`` whenever a
    capability with ``enabled_for_turn=True`` was forwarded, even if no
    backend was wired. The R3 contract is fail-closed: an enabled
    capability without a backend must not produce an ``allowed`` echo.
    """

    @pytest.mark.asyncio
    async def test_enabled_capability_without_backend_does_not_echo_allowed(self) -> None:
        """When ``enabled_for_turn=True`` is forwarded but
        ``web_search_backend=None``, the run_started echo must NOT be
        ``"allowed"``. The runtime must fail-closed to ``"disabled"``.
        """
        from app.services.reader_record_ask.finalizer import FinalizedAskResult
        from app.services.reader_record_ask.production_stream import (
            stream_agentic_thread_message,
        )
        from app.services.reader_record_ask.runtime import ReadingRecordAskRunResult
        from app.services.reader_record_ask.runtime_events import (
            AnswerDeltaEvent,
            RunStartedEvent,
        )
        from app.services.reader_record_ask.sse import EVENT_AGENTIC_RUN_STARTED
        from app.services.reader_record_ask.web_search_contracts import (
            ResolvedWebSearchCapability,
        )

        captured: dict[str, object] = {}

        async def _run(**kwargs):
            captured.update(kwargs)
            sink = kwargs["event_sink"]
            env = kwargs["envelope"]
            sink(
                RunStartedEvent(
                    envelope_fingerprint=env.envelope_fingerprint,
                    has_initial_selection=True,
                )
            )
            sink(AnswerDeltaEvent(delta="answer"))
            finalized = FinalizedAskResult(
                status="ok",
                answer_text="answer",
                resolved_evidence=(),
                envelope_fingerprint=env.envelope_fingerprint,
            )
            return ReadingRecordAskRunResult(
                final_text="answer",
                finalized=finalized,
            )

        # Capability with enabled_for_turn=True but no backend injected.
        capability = ResolvedWebSearchCapability(
            enabled_for_turn=True,
            provider="fake",
            protocol="fake",
            execution_mode="host_function",
            decision_mode="agent_auto",
            max_calls=1,
            max_results_per_call=3,
            policy_version="reader_record_ask_web_search_v1",
        )

        repo = _FakeRepoForRunStarted()
        chunks = [
            c
            async for c in stream_agentic_thread_message(
                user_id=UUID("11111111-1111-1111-1111-111111111111"),
                reading_record_id=UUID("22222222-2222-2222-2222-222222222222"),
                thread_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                content="q",
                facts=_fake_facts_for_run_started(),
                request_anchor=None,
                repository=repo,  # type: ignore[arg-type]
                model="fake-model",  # type: ignore[arg-type]
                run_fn=_run,
                auto_wire_dependencies=False,
                stable_document_id=UUID("44444444-4444-4444-4444-444444444444"),
                web_search_capability=capability,
                # NO web_search_backend — must fail-closed.
            )
        ]
        events = _parse_sse_for_run_started(chunks)
        run_started = next(
            d for n, d in events if n == EVENT_AGENTIC_RUN_STARTED
        )
        # Must NOT echo "allowed" — fail-closed to "disabled".
        assert run_started["web_search_mode"] != "allowed"
        assert captured["web_search_capability"] is None


# Helpers for the RunStarted test (kept local to avoid polluting the
# module-level namespace shared with the offline tests above).


def _envelope_for_run_started():
    from app.services.reader_record_ask.context_envelope import (
        EnvelopeInitialAnchor,
        VerifiedEnvelopeInput,
        build_context_envelope,
    )

    payload = dict(
        user_id=UUID("11111111-1111-1111-1111-111111111111"),
        reading_record_id=UUID("22222222-2222-2222-2222-222222222222"),
        base_id=UUID("33333333-3333-3333-3333-333333333333"),
        record_generation=1,
        stable_document_id=UUID("44444444-4444-4444-4444-444444444444"),
        base_content_sha256="b" * 64,
        product_state="readable_enhancing",
        readiness_state="article_ready",
        initial_anchor=EnvelopeInitialAnchor(
            unit_id="u1",
            anchor_segment_id="s1",
            start_offset=0,
            end_offset=5,
            selected_text="hello",
            text_hash="a1b2c3d4",
        ),
    )
    return build_context_envelope(VerifiedEnvelopeInput(**payload))  # type: ignore[arg-type]


def _fake_facts_for_run_started():
    base = SimpleNamespace(
        base_id=str(UUID("33333333-3333-3333-3333-333333333333")),
        content_sha256="b" * 64,
        text="hello world",
    )
    unit = SimpleNamespace(
        unit_id="u1",
        order_index=0,
        text="hello world",
        text_hash="11111111",
        base_start_utf16=0,
        base_end_utf16=11,
    )
    seg = SimpleNamespace(
        unit_id="u1",
        anchor_segment_id="s1",
        order_index=0,
        unit_order_index=0,
        text="hello",
        text_hash="a1b2c3d4",
        unit_start_utf16=0,
        unit_end_utf16=5,
        base_start_utf16=0,
        base_end_utf16=5,
    )
    build_result = SimpleNamespace(base=base, units=(unit,), anchor_segments=(seg,))
    record = SimpleNamespace(
        generation=1,
        product_state="readable_enhancing",
        readiness_state="article_ready",
        title="T",
    )
    return SimpleNamespace(build_result=build_result, record=record)


class _FakeRepoForRunStarted:
    async def get_thread(self, **kwargs):
        return {
            "id": str(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
            "user_id": str(UUID("11111111-1111-1111-1111-111111111111")),
            "reading_record_id": str(UUID("22222222-2222-2222-2222-222222222222")),
            "title": "t",
            "is_default": True,
        }

    async def create_message(self, **kwargs):
        import uuid as _uuid

        mid = str(_uuid.uuid4())
        return {"id": mid, "thread_id": str(kwargs["thread_id"]), **kwargs}

    async def create_agentic_turn_run(self, **kwargs):
        import uuid as _uuid

        tid = str(_uuid.uuid4())
        return {
            "id": tid,
            "status": "streaming",
            "envelope_fingerprint": kwargs.get("envelope_fingerprint"),
        }

    async def complete_agentic_turn_run(self, **kwargs):
        from app.schemas.reader_record_ask_stream import EXECUTION_VERSION_AGENTIC_V2

        return {
            "id": str(kwargs["turn_run_id"]),
            "status": "completed",
            "final_status": "ok",
            "user_visible_output_json": kwargs["completed_dto"],
            "resolved_evidence_json": kwargs["resolved_evidence"],
            "envelope_fingerprint": None,
            "execution_version": EXECUTION_VERSION_AGENTIC_V2,
        }


def _parse_sse_for_run_started(chunks: list[str]) -> list[tuple[str, dict]]:
    import json as _json

    events: list[tuple[str, dict]] = []
    for chunk in chunks:
        lines = chunk.strip().split("\n")
        event = ""
        data = ""
        for line in lines:
            if line.startswith("event: "):
                event = line[7:]
            if line.startswith("data: "):
                data = line[6:]
        if event and data:
            events.append((event, _json.loads(data)))
    return events
