from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import dict as dict_route
from app.api.routes.dict import router as dict_router
from app.services.dictionary.errors import WordNotFoundError


class StubDictionaryService:
    async def lookup(self, request) -> dict[str, object]:
        word = request.query
        if word == "unknown":
            raise WordNotFoundError("Word not found: unknown")
        if word == "anti":
            return {
                "result_type": "disambiguation",
                "query": word,
                "provider": "tecd3",
                "cached": False,
                "candidates": [
                    {
                        "entry_id": 11,
                        "label": "anti",
                        "part_of_speech": "n.,a.,prep.",
                        "preview": "1. 反对者,反对分子,持反对论者",
                        "entry_kind": "entry",
                    },
                    {
                        "entry_id": 12,
                        "label": "anti-",
                        "part_of_speech": "pref.",
                        "preview": "1. 表示“反”“抗”“阻”",
                        "entry_kind": "entry",
                    },
                ],
            }
        return {
            "result_type": "entry",
            "query": word,
            "provider": "tecd3",
            "cached": False,
            "entry": {
                "id": 7,
                "word": word,
                "base_word": word,
                "homograph_no": None,
                "phonetic": "/test/",
                "meanings": [
                    {
                        "part_of_speech": "n.",
                        "definitions": [
                            {
                                "meaning": "测试释义",
                                "example": "test example",
                                "example_translation": "测试例句",
                            }
                        ],
                    }
                ],
                "examples": [
                    {
                        "example": "test example",
                        "example_translation": "测试例句",
                    }
                ],
                "phrases": [
                    {
                        "phrase": "test phrase",
                        "meaning": "测试短语",
                    }
                ],
                "entry_kind": "entry",
            },
        }

    async def lookup_entry(self, entry_id: int) -> dict[str, object]:
        if entry_id == 404:
            raise WordNotFoundError("Entry not found")
        return {
            "result_type": "entry",
            "query": "anti-",
            "provider": "tecd3",
            "cached": False,
            "entry": {
                "id": entry_id,
                "word": "anti-",
                "base_word": "anti-",
                "homograph_no": None,
                "phonetic": None,
                "meanings": [
                    {
                        "part_of_speech": "pref.",
                        "definitions": [
                            {
                                "meaning": "1. 表示“反”“抗”“阻”",
                                "example": None,
                                "example_translation": None,
                            }
                        ],
                    }
                ],
                "examples": [],
                "phrases": [],
                "entry_kind": "entry",
            },
        }


def create_client() -> TestClient:
    app = FastAPI()
    app.include_router(dict_router)
    dict_route._service = StubDictionaryService()
    return TestClient(app)


class TestDictProxy:
    def test_dict_lookup_returns_entry_result(self) -> None:
        client = create_client()
        response = client.get("/dict?q=hello&type=word")

        assert response.status_code == 200
        data = response.json()
        assert data["result_type"] == "entry"
        assert data["entry"]["word"] == "hello"
        assert data["entry"]["meanings"][0]["part_of_speech"] == "n."

    def test_dict_lookup_returns_disambiguation_result(self) -> None:
        client = create_client()
        response = client.get("/dict?q=anti&type=word")

        assert response.status_code == 200
        data = response.json()
        assert data["result_type"] == "disambiguation"
        assert len(data["candidates"]) == 2
        assert data["candidates"][0]["label"] == "anti"

    def test_dict_lookup_entry_endpoint(self) -> None:
        client = create_client()
        response = client.get("/dict/entry?id=12")

        assert response.status_code == 200
        data = response.json()
        assert data["result_type"] == "entry"
        assert data["entry"]["id"] == 12
        assert data["entry"]["word"] == "anti-"

    def test_dict_lookup_not_found(self) -> None:
        client = create_client()
        response = client.get("/dict?q=unknown&type=word")

        assert response.status_code == 200
        data = response.json()
        assert data["result_type"] == "not_found"
        assert data["query"] == "unknown"
        assert data["reason"] == "not_in_dictionary"
        assert "message" not in data

    def test_dict_entry_not_found(self) -> None:
        client = create_client()
        response = client.get("/dict/entry?id=404")

        assert response.status_code == 404
