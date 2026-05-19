import type { Meta } from "@ladle/react";
import { useMemo, useState } from "react";

import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import {
  annotationToTargetRef,
  favoriteToTargetRef,
  hashAnchorText,
  jumpToTargetRef,
  projectReaderAssets,
  renderSceneToPlateDocument,
  type ReaderJumpTarget,
} from "@/lib/reader-plate";
import type { ReaderMockVm } from "@/types/view/ReaderMockVm";
import { PlateReaderSurface } from "./PlateReaderSurface";

function createScene(): ReaderMockVm {
  return {
    schemaVersion: "3.0.0",
    request: {
      requestId: "req-1",
      sourceType: "user_input",
      readingGoal: "daily_reading",
      readingVariant: "intermediate_reading",
      profileId: "upstream",
    },
    article: {
      paragraphs: [
        {
          paragraphId: "p1",
          sentenceIds: ["s1", "s2"],
        },
      ],
      sentences: [
        {
          sentenceId: "s1",
          paragraphId: "p1",
          text: "Institutional memory shapes policy choices.",
        },
        {
          sentenceId: "s2",
          paragraphId: "p1",
          text: "These choices persist across administrations.",
        },
      ],
    },
    userFacingState: "normal",
    translations: [
      {
        sentenceId: "s1",
        translationZh: "制度记忆会塑造政策选择。",
      },
    ],
    inlineMarks: [
      {
        id: "mark-1",
        annotationType: "grammar_note",
        anchor: {
          kind: "text",
          sentenceId: "s1",
          anchorText: "shapes",
          occurrence: 1,
        },
        renderType: "underline",
        visualTone: "grammar",
        clickable: false,
      },
    ],
    sentenceEntries: [],
    warnings: [],
  };
}

const readerDocument = renderSceneToPlateDocument(createScene());

function SurfaceStory({ jumpTarget }: { jumpTarget: ReaderJumpTarget | null }) {
  return (
    <div className="reading-paper min-h-screen p-6">
      <PlateReaderSurface
        document={readerDocument}
        showTranslation
        readingClassName="reader-serif text-ink text-[1.12rem] leading-[1.88]"
        jumpTarget={jumpTarget}
      />
    </div>
  );
}

function createHighlight(): WebAnnotationVm {
  return {
    id: "ann-highlight",
    recordId: "record-1",
    type: "highlight",
    anchorType: "text_range",
    targetKey: "record:record-1:range:s1:14:20:hash-memory",
    paragraphId: "p1",
    sentenceId: "s1",
    selectedText: "memory",
    startOffset: 14,
    endOffset: 20,
    textHash: hashAnchorText("memory"),
    segments: [],
    color: "warm_yellow",
    note: null,
    createdAt: "2026-05-19T00:00:00Z",
    updatedAt: "2026-05-19T00:00:00Z",
  };
}

function createNote(): WebAnnotationVm {
  return {
    id: "ann-note",
    recordId: "record-1",
    type: "note",
    anchorType: "sentence",
    targetKey: "record:record-1:sentence:s2",
    paragraphId: "p1",
    sentenceId: "s2",
    selectedText: "These choices persist across administrations.",
    startOffset: null,
    endOffset: null,
    textHash: null,
    segments: [],
    color: "soft_blue",
    note: "这里承接上句，强调政策路径延续。",
    createdAt: "2026-05-19T00:00:00Z",
    updatedAt: "2026-05-19T00:00:00Z",
  };
}

function createFavorite(): WebFavoriteTargetVm {
  return {
    id: "fav-1",
    targetType: "text_range",
    targetKey: "record:record-1:range:s1:28:43:hash-policy",
    recordId: "record-1",
    anchorType: "text_range",
    sentenceId: "s1",
    selectedText: "policy choices.",
    startOffset: 28,
    endOffset: 43,
    textHash: hashAnchorText("policy choices."),
    segments: [],
  };
}

function AssetRecoveryStory() {
  const [annotations, setAnnotations] = useState<WebAnnotationVm[]>([]);
  const [favorites, setFavorites] = useState<WebFavoriteTargetVm[]>([]);
  const [jumpTarget, setJumpTarget] = useState<ReaderJumpTarget | null>(null);

  const assetProjection = useMemo(
    () =>
      projectReaderAssets({
        annotations,
        favoriteTargets: favorites,
        recordId: "record-1",
      }),
    [annotations, favorites],
  );

  return (
    <div className="reading-paper min-h-screen p-6">
      <div className="mx-auto mb-5 flex max-w-[96ch] flex-wrap gap-3">
        <button
          type="button"
          className="rounded-full border border-hairline bg-white px-4 py-2 text-sm text-ink"
          onClick={() => setAnnotations((current) => [createHighlight(), ...current.filter((item) => item.id !== "ann-highlight")])}
        >
          Add Highlight
        </button>
        <button
          type="button"
          className="rounded-full border border-hairline bg-white px-4 py-2 text-sm text-ink"
          onClick={() => setAnnotations((current) => [createNote(), ...current.filter((item) => item.id !== "ann-note")])}
        >
          Add Note
        </button>
        <button
          type="button"
          className="rounded-full border border-hairline bg-white px-4 py-2 text-sm text-ink"
          onClick={() =>
            setFavorites((current) => (current.length > 0 ? [] : [createFavorite()]))
          }
        >
          Toggle Favorite
        </button>
        <span data-testid="asset-jump-target" className="self-center text-xs text-muted">
          {jumpTarget?.targetKey ?? "none"}
        </span>
      </div>
      <PlateReaderSurface
        document={readerDocument}
        showTranslation
        readingClassName="reader-serif text-ink text-[1.12rem] leading-[1.88]"
        jumpTarget={jumpTarget}
        assetProjection={assetProjection}
        onAnnotationJump={(annotation) => setJumpTarget(jumpToTargetRef(annotationToTargetRef(annotation)))}
        onFavoriteJump={(favorite) => setJumpTarget(jumpToTargetRef(favoriteToTargetRef(favorite)))}
      />
    </div>
  );
}

export default {
  title: "Reader/PlateReaderSurface",
} satisfies Meta;

export const SentenceJump = () => (
  <SurfaceStory
    jumpTarget={{
      targetType: "sentence",
      targetKey: "record:record-1:sentence:s1",
      sentenceIds: ["s1"],
      primarySentenceId: "s1",
      highlightMode: "sentence_frame",
      scrollStrategy: "center",
    }}
  />
);

export const TextRangeJump = () => (
  <SurfaceStory
    jumpTarget={{
      targetType: "text_range",
      targetKey: "record:record-1:range:s1:14:20:hash",
      sentenceIds: ["s1"],
      rangeSegments: [
        {
          sentenceId: "s1",
          startOffset: 14,
          endOffset: 20,
        },
      ],
      primarySentenceId: "s1",
      highlightMode: "range_segments",
      scrollStrategy: "center",
    }}
  />
);

export const MultiTextJump = () => (
  <SurfaceStory
    jumpTarget={{
      targetType: "multi_text",
      targetKey: "record:record-1:multi_text:2:hash",
      sentenceIds: ["s1", "s2"],
      rangeSegments: [
        {
          sentenceId: "s1",
          startOffset: 28,
          endOffset: 43,
        },
        {
          sentenceId: "s2",
          startOffset: 0,
          endOffset: 13,
        },
      ],
      primarySentenceId: "s1",
      highlightMode: "range_segments",
      scrollStrategy: "center",
    }}
  />
);

export const AssetRecovery = () => <AssetRecoveryStory />;
