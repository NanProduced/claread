"""Golden sample loader for the Reader baseline corpus.

The corpus is read-only fixture data. It is *not* a database record
and is not associated with any user. It exists so the baseline
harness can run the same articles through both the new orchestration
chain and the legacy article_analysis workflow.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

CORPUS_ROOT = Path(__file__).resolve().parents[4] / "verification" / "golden_samples"


@dataclass(frozen=True, slots=True)
class GoldenSample:
    sample_id: str
    shape: str
    source_attribution: str
    expected_char_band: tuple[int, int]
    expected_word_band: tuple[int, int]
    notes: str
    plain_text: str
    reading_goal: str = "daily_reading"
    reading_variant: str = "intermediate_reading"

    @property
    def char_count(self) -> int:
        return len(self.plain_text)

    @property
    def word_count(self) -> int:
        return len(self.plain_text.split())

    def meets_expected_bands(self) -> bool:
        lo_c, hi_c = self.expected_char_band
        lo_w, hi_w = self.expected_word_band
        return lo_c <= self.char_count <= hi_c and lo_w <= self.word_count <= hi_w


def _manifest_path() -> Path:
    return CORPUS_ROOT / "manifest.yaml"


def list_sample_ids() -> tuple[str, ...]:
    """Return the fixed sample ids in manifest order.

    The order is the order in which the manifest was written, which
    is also the order callers usually want for a stable baseline
    table.
    """
    if not _manifest_path().exists():
        raise FileNotFoundError(f"golden sample manifest not found: {_manifest_path()}")
    raw = yaml.safe_load(_manifest_path().read_text(encoding="utf-8")) or []
    return tuple(str(entry["id"]) for entry in raw)


def load_sample(sample_id: str) -> GoldenSample:
    """Load a single sample by id.

    Raises ``FileNotFoundError`` if the manifest does not list the id
    or the article file is missing. Raises ``ValueError`` if the
    article's char / word counts fall outside the declared bands.
    """
    raw = yaml.safe_load(_manifest_path().read_text(encoding="utf-8")) or []
    entry = next((e for e in raw if str(e.get("id")) == sample_id), None)
    if entry is None:
        raise FileNotFoundError(
            f"sample id {sample_id!r} not declared in {_manifest_path()}"
        )
    article_path = CORPUS_ROOT / "articles" / f"{sample_id}.txt"
    if not article_path.exists():
        raise FileNotFoundError(f"article file missing for sample {sample_id!r}: {article_path}")
    plain_text = article_path.read_text(encoding="utf-8").strip()
    sample = GoldenSample(
        sample_id=sample_id,
        shape=str(entry["shape"]),
        source_attribution=str(entry.get("source_attribution", "unknown")),
        expected_char_band=tuple(entry.get("expected_char_band", (0, 0))),  # type: ignore[arg-type]
        expected_word_band=tuple(entry.get("expected_word_band", (0, 0))),  # type: ignore[arg-type]
        notes=str(entry.get("notes", "")).strip(),
        plain_text=plain_text,
        reading_goal=str(entry.get("reading_goal", "daily_reading")),
        reading_variant=str(entry.get("reading_variant", "intermediate_reading")),
    )
    if not sample.meets_expected_bands():
        raise ValueError(
            f"sample {sample_id!r} fails expected bands: "
            f"chars={sample.char_count} band={sample.expected_char_band}, "
            f"words={sample.word_count} band={sample.expected_word_band}"
        )
    return sample


def load_all() -> tuple[GoldenSample, ...]:
    return tuple(load_sample(sample_id) for sample_id in list_sample_ids())


def iter_all() -> Iterable[GoldenSample]:
    for sample_id in list_sample_ids():
        yield load_sample(sample_id)


def corpus_root() -> Path:
    return CORPUS_ROOT


def _self_test() -> None:  # pragma: no cover - dev convenience
    if os.environ.get("READER_BASELINE_SELF_TEST") != "1":
        return
    for sample in iter_all():
        assert sample.meets_expected_bands(), sample
        print(
            f"OK {sample.sample_id:<24} shape={sample.shape:<22} "
            f"chars={sample.char_count:>5} words={sample.word_count:>4}"
        )


_self_test()
