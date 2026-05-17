"""词典 Proxy API。"""

from __future__ import annotations

from logging import getLogger
from time import perf_counter
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.services.dictionary import get_service
from app.services.dictionary.schemas import DictionaryEntryResult, DictionaryLookupResult, DictionaryNotFoundResult
from app.services.dictionary.errors import WordNotFoundError, ServiceUnavailableError
from app.services.dictionary_ai import (
    CanonicalDictionaryAvailableError,
    DictionaryAIEntryMismatchError,
    DictionaryAIRequest,
    DictionaryAIResponse,
    get_service as get_dictionary_ai_service,
    insert_candidate_entry,
)
from app.services.auth.dependencies import AuthUserDep
from app.services.analysis.credit_service import (
    LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
    check_quota,
    ensure_credit_account,
    refund_reserved_points,
    reserve_points,
)
from app.services.ai_usage import (
    AIUsageEventCreate,
    BILLING_MODE_USER_POINTS,
    CAPABILITY_DICT_AI_LOOKUP,
    DICT_AI_FIXED_POINTS,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    USAGE_SCOPE_USER_BILLED,
    build_dict_ai_billing_metadata,
    build_model_metadata,
    compute_dict_ai_cost_points,
    record_ai_usage_event,
)
from app.services.analysis.prompting.prompt_loader import get_prompt_version

logger = getLogger("app.api")

router = APIRouter(prefix="/dict", tags=["dict"])

_service = get_service()
_dict_ai_service = get_dictionary_ai_service()

_DICT_CACHE_CONTROL = "public, max-age=3600"


def _insufficient_dict_ai_credits_response(remaining: int) -> JSONResponse:
    return JSONResponse(
        status_code=402,
        content={
            "error": "INSUFFICIENT_CREDITS",
            "detail": "Not enough credits for this dictionary AI action.",
            "remaining_points": remaining,
            "required_points": DICT_AI_FIXED_POINTS,
        },
    )


async def _refund_dict_ai_reservation(
    *,
    user_id: UUID,
    reservation,
    body: DictionaryAIRequest,
    reason: str,
) -> None:
    if reservation is None:
        return
    await refund_reserved_points(
        user_id,
        reservation,
        metadata={
            "reason": reason,
            "query": body.query,
            "mode": body.mode,
        },
    )


async def _record_dict_ai_failure_event(
    *,
    user_id: UUID,
    body: DictionaryAIRequest,
    start_perf: float,
    error_code: str,
    error_message: str,
) -> None:
    await record_ai_usage_event(
        AIUsageEventCreate(
            usage_scope=USAGE_SCOPE_USER_BILLED,
            capability_code=CAPABILITY_DICT_AI_LOOKUP,
            billing_mode=BILLING_MODE_USER_POINTS,
            status=STATUS_FAILED,
            user_id=user_id,
            record_id=body.record_id,
            workflow_name="dictionary_ai",
            workflow_version="1.0.0",
            schema_version="dict-ai-v1",
            prompt_version=get_prompt_version(),
            latency_ms=int((perf_counter() - start_perf) * 1000),
            error_code=error_code,
            error_message=error_message,
            metadata_json={
                "entrypoint": "/dict/ai",
                "mode": body.mode,
                "query": body.query,
                "query_type": body.query_type,
                "source": body.source,
                "sentence_id": body.sentence_id,
            },
        )
    )


@router.get("", response_model=DictionaryLookupResult, summary="查词")
async def lookup_word(
    q: str = Query(..., description="要查询的单词或短语", min_length=1, max_length=100),
    type: Literal["word", "phrase"] = Query(default="word", description="查询类型"),
    context_sentence: str | None = Query(default=None, description="点击词所在的句子"),
    occurrence: int | None = Query(default=None, description="在句子中的第几次出现"),
) -> JSONResponse:
    """查询单词或短语的词典释义，支持语境感知。"""
    word = q.strip()
    from app.services.dictionary.schemas import DictionaryLookupRequest
    request = DictionaryLookupRequest(
        query=word,
        query_type=type,
        context_sentence=context_sentence,
        occurrence=occurrence,
    )
    try:
        result = await _service.lookup(request)
        response = JSONResponse(content=result)
        response.headers["Cache-Control"] = _DICT_CACHE_CONTROL
        return response
    except WordNotFoundError:
        result = DictionaryNotFoundResult(
            query=word,
            provider="tecd3",
            cached=False,
        ).model_dump()
        response = JSONResponse(content=result)
        response.headers["Cache-Control"] = _DICT_CACHE_CONTROL
        return response
    except ServiceUnavailableError:
        raise HTTPException(status_code=503, detail="Dictionary service temporarily unavailable") from None
    except Exception as exc:
        logger.error("lookup_word failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Dictionary service temporarily unavailable") from None


@router.get("/entry", response_model=DictionaryEntryResult, summary="词条详情")
async def lookup_entry(
    id: int = Query(..., description="词条 ID", ge=1),
) -> JSONResponse:
    """根据词条 ID 获取完整词典条目。"""
    try:
        result = await _service.lookup_entry(id)
        response = JSONResponse(content=result)
        response.headers["Cache-Control"] = _DICT_CACHE_CONTROL
        return response
    except WordNotFoundError:
        raise HTTPException(status_code=404, detail=f"Entry not found: {id}") from None
    except ServiceUnavailableError:
        raise HTTPException(status_code=503, detail="Dictionary service temporarily unavailable") from None
    except Exception as exc:
        logger.error("lookup_entry failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Dictionary service temporarily unavailable") from None


@router.post("/ai", response_model=DictionaryAIResponse, summary="词典 AI 增强")
async def dictionary_ai(
    body: DictionaryAIRequest,
    current_user: AuthUserDep,
) -> DictionaryAIResponse | JSONResponse:
    user_id = UUID(current_user.user_id)
    start_perf = perf_counter()
    reservation = None

    try:
        await ensure_credit_account(user_id)
        remaining = await check_quota(user_id)
        if remaining < DICT_AI_FIXED_POINTS:
            return _insufficient_dict_ai_credits_response(remaining)

        reservation_billing_policy_version = build_dict_ai_billing_metadata(None).get(
            "billing_policy_version"
        )
        reservation_metadata = {
            "capability_code": CAPABILITY_DICT_AI_LOOKUP,
            "mode": body.mode,
            "query": body.query,
            "query_type": body.query_type,
            "entry_id": getattr(body, "entry_id", None),
            "billing_policy_version": reservation_billing_policy_version,
            "fixed_points": DICT_AI_FIXED_POINTS,
        }
        reservation = await reserve_points(
            user_id,
            DICT_AI_FIXED_POINTS,
            task_id=None,
            entry_type=LEDGER_ENTRY_TYPE_AI_CAPABILITY_DEDUCT,
            metadata=reservation_metadata,
        )
        if reservation is None:
            remaining = await check_quota(user_id)
            return _insufficient_dict_ai_credits_response(remaining)

        if body.mode == "context_explain":
            run_result = await _dict_ai_service.run_context_explain(body)
        else:
            run_result = await _dict_ai_service.run_missing_fallback(body)

        computed_cost_points = compute_dict_ai_cost_points(run_result.usage_data)
        deduction_metadata = build_dict_ai_billing_metadata(run_result.usage_data)
        deduction_metadata.update(
            {
                "capability_code": CAPABILITY_DICT_AI_LOOKUP,
                "mode": body.mode,
                "query": body.query,
                "query_type": body.query_type,
                "entry_id": getattr(body, "entry_id", None),
                "classification": getattr(run_result.response, "classification", None),
                "confidence": getattr(run_result.response, "confidence", None),
                "result_kind": getattr(run_result.response, "result_kind", None),
            }
        )

        usage_event_id = await record_ai_usage_event(
            AIUsageEventCreate(
                usage_scope=USAGE_SCOPE_USER_BILLED,
                capability_code=CAPABILITY_DICT_AI_LOOKUP,
                billing_mode=BILLING_MODE_USER_POINTS,
                status=STATUS_SUCCEEDED,
                user_id=user_id,
                record_id=body.record_id,
                request_id=None,
                workflow_name="dictionary_ai",
                workflow_version="1.0.0",
                schema_version="dict-ai-v1",
                prompt_version=get_prompt_version(),
                usage_data=run_result.usage_data,
                latency_ms=int((perf_counter() - start_perf) * 1000),
                billed_points=computed_cost_points,
                billing_policy_version=deduction_metadata.get("billing_policy_version"),
                metadata_json={
                    "entrypoint": "/dict/ai",
                    "mode": body.mode,
                    "query": body.query,
                    "query_type": body.query_type,
                    "entry_id": getattr(body, "entry_id", None),
                    "source": body.source,
                    "sentence_id": body.sentence_id,
                    "classification": getattr(run_result.response, "classification", None),
                    "confidence": getattr(run_result.response, "confidence", None),
                    "result_kind": getattr(run_result.response, "result_kind", None),
                    "computed_cost_points": computed_cost_points,
                },
                **build_model_metadata(run_result.model_config),
            )
        )

        if body.mode == "missing_fallback":
            await insert_candidate_entry(
                query=body.query,
                normalized_query=_dict_ai_service.normalize_query(body.query),
                query_type=body.query_type,
                classification=run_result.response.classification,
                result_kind=run_result.response.result_kind,
                confidence=run_result.response.confidence,
                generated_payload_json=run_result.response.model_dump(mode="json", exclude_none=True),
                context_sentence=body.context_sentence,
                record_id=body.record_id,
                sentence_id=body.sentence_id,
                usage_event_id=usage_event_id,
            )

        return run_result.response
    except CanonicalDictionaryAvailableError:
        await _refund_dict_ai_reservation(
            user_id=user_id,
            reservation=reservation,
            body=body,
            reason="canonical_dictionary_available",
        )
        return JSONResponse(
            status_code=409,
            content={
                "error": "CANONICAL_DICTIONARY_AVAILABLE",
                "detail": "This query can now be resolved by the canonical dictionary.",
            },
        )
    except DictionaryAIEntryMismatchError as exc:
        await _refund_dict_ai_reservation(
            user_id=user_id,
            reservation=reservation,
            body=body,
            reason="entry_query_mismatch",
        )
        return JSONResponse(
            status_code=409,
            content={
                "error": "ENTRY_QUERY_MISMATCH",
                "detail": str(exc),
            },
        )
    except WordNotFoundError:
        await _refund_dict_ai_reservation(
            user_id=user_id,
            reservation=reservation,
            body=body,
            reason="entry_not_found",
        )
        raise HTTPException(status_code=404, detail="Dictionary entry not found") from None
    except ServiceUnavailableError as exc:
        await _refund_dict_ai_reservation(
            user_id=user_id,
            reservation=reservation,
            body=body,
            reason="service_unavailable",
        )
        await _record_dict_ai_failure_event(
            user_id=user_id,
            body=body,
            start_perf=start_perf,
            error_code="service_unavailable",
            error_message=str(exc),
        )
        raise HTTPException(status_code=503, detail="Dictionary service temporarily unavailable") from None
    except RuntimeError as exc:
        await _refund_dict_ai_reservation(
            user_id=user_id,
            reservation=reservation,
            body=body,
            reason="runtime_error",
        )
        await _record_dict_ai_failure_event(
            user_id=user_id,
            body=body,
            start_perf=start_perf,
            error_code="runtime_error",
            error_message=str(exc),
        )
        if "model route is not configured" in str(exc):
            raise HTTPException(status_code=503, detail="Dictionary AI temporarily unavailable") from None
        logger.error("dictionary_ai failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Dictionary AI temporarily unavailable") from None
    except Exception as exc:
        logger.error("dictionary_ai failed: %s", exc, exc_info=True)
        await _refund_dict_ai_reservation(
            user_id=user_id,
            reservation=reservation,
            body=body,
            reason="unexpected_error",
        )
        await _record_dict_ai_failure_event(
            user_id=user_id,
            body=body,
            start_perf=start_perf,
            error_code="unexpected_error",
            error_message=str(exc),
        )
        raise HTTPException(status_code=502, detail="Dictionary AI temporarily unavailable") from None
