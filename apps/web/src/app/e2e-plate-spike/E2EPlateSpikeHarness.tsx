"use client";

/**
 * T4.2a-PUX-R4-R2-S2 — Test-only E2E harness for targeted Plate updates.
 *
 * This CLIENT COMPONENT is only rendered by the server-side gate in
 * `page.tsx` when the private environment variable
 * `CLAREAD_ENABLE_E2E_SPIKE === "1"`. In all other cases (dev without
 * the flag, test runner without the flag, production) the route returns
 * 404 via `notFound()` and this component is never mounted.
 *
 * It mounts the REAL ReaderRecordPlateKit + real projection fixture and
 * exposes the editor on `window.__spikeEditor` so Playwright can drive
 * `editor.tf.replaceNodes(...)` via `page.evaluate`.
 *
 * Boundary: does NOT implement the incremental applier, does NOT change
 * production default reload path, does NOT require backend/auth/network.
 */

import { useEffect, useRef } from "react";
import type { Descendant } from "platejs";

import { Editor, EditorContainer } from "@/components/ui/editor";
import {
  createPlatePlugin,
  type PlateElementProps,
  Plate,
  usePlateEditor,
} from "platejs/react";
import { ReaderRecordPlateKit } from "@/components/editor/plugins/reader-plate-kit";
import { ReaderContentSummaryElement } from "@/components/reader/plate/nodes/ReaderContentSummaryElement";
import {
  READER_CALLOUT_TYPE,
  READER_PARAGRAPH_TYPE,
  type ReaderCalloutElement,
  type ReaderParagraphElement,
  projectReaderRecordPlateToPlateValue,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";
import type {
  ReaderRecordPlateBlockquoteBlock,
  ReaderRecordPlateCalloutBlock,
  ReaderRecordPlateDocument,
  ReaderRecordPlateParagraphBlock,
  ReaderRecordPlateTextLeaf,
  ReaderRecordPlateTranslationTextLeaf,
} from "@/lib/reader-plate/projection/reader-record-plate-document";

// ---------------------------------------------------------------------------
// Spike plugin: ReaderContentSummaryElement adapter
// (same pattern as spike-targeted-slate-ops-prod-kit.test.tsx)
// ---------------------------------------------------------------------------

function SpikeContentSummaryComponent({
  element,
  children,
  attributes,
}: PlateElementProps) {
  return (
    <ReaderContentSummaryElement
      props={{ element, children, attributes } as never}
    />
  );
}

const SpikeContentSummaryPlugin = createPlatePlugin({
  key: "reader_content_summary",
  node: { isElement: true, component: SpikeContentSummaryComponent },
});

const SpikePlugins = [
  ...ReaderRecordPlateKit,
  SpikeContentSummaryPlugin,
];

// ---------------------------------------------------------------------------
// Real snapshot/projection shape builders
// ---------------------------------------------------------------------------

const SOURCE_TEXT = "Institutional memory shapes policy choices.";
const TRANSLATION_TEXT = "制度记忆会塑造政策选择。";

function makeTextLeaf(
  overrides: Partial<ReaderRecordPlateTextLeaf> = {},
): ReaderRecordPlateTextLeaf {
  return {
    text: SOURCE_TEXT,
    owner: "stable",
    lockSource: true,
    sourceRole: "segment_text",
    baseRange: { startUtf16: 0, endUtf16: SOURCE_TEXT.length },
    anchorSegmentId: "seg_1",
    segmentRange: { startUtf16: 0, endUtf16: SOURCE_TEXT.length },
    marks: [],
    ...overrides,
  };
}

function makeTranslationLeaf(
  overrides: Partial<ReaderRecordPlateTranslationTextLeaf> = {},
): ReaderRecordPlateTranslationTextLeaf {
  return {
    text: TRANSLATION_TEXT,
    owner: "system_ai",
    sourceRole: "unit_translation_text",
    ...overrides,
  };
}

function makeParagraphBlock(
  overrides: Partial<ReaderRecordPlateParagraphBlock> = {},
): ReaderRecordPlateParagraphBlock {
  return {
    type: "paragraph",
    id: "paragraph:seg_1",
    children: [makeTextLeaf()],
    data: {
      anchorSegmentId: "seg_1",
      coveredAnchorSegmentIds: ["seg_1"],
      sentenceId: "sent_1",
      unitId: "unit_1",
      baseId: "base_1",
      baseRange: { startUtf16: 0, endUtf16: SOURCE_TEXT.length },
      unitRange: { startUtf16: 0, endUtf16: SOURCE_TEXT.length },
      textHash: "seg_1_hash",
      hashAlgorithm: "fnv1a32-utf16",
      segmentType: "sentence",
      boundaryQuality: "normal",
    },
    ...overrides,
  };
}

function makeBlockquoteBlock(
  overrides: Partial<ReaderRecordPlateBlockquoteBlock> = {},
): ReaderRecordPlateBlockquoteBlock {
  return {
    type: "blockquote",
    id: "blockquote:layer_translation_1:group_translation_1",
    children: [makeTranslationLeaf()],
    data: {
      unitId: "unit_1",
      layerId: "layer_translation_1",
      layerVersion: 1,
      groupId: "group_translation_1",
      coveredAnchorSegmentIds: ["seg_1"],
      sourceTextHash: "unit_hash_1",
    },
    ...overrides,
  };
}

function makeCalloutBlock(
  overrides: Partial<ReaderRecordPlateCalloutBlock> = {},
): ReaderRecordPlateCalloutBlock {
  return {
    type: "callout",
    id: "callout:grammar:grammar_item_1",
    variant: "grammar",
    icon: "📖",
    children: [{ type: "p", children: [{ text: "shapes acts as the predicate verb." }] }],
    data: {
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_grammar_1",
      itemId: "grammar_item_1",
      grammarPoint: "predicate verb",
      pattern: "subject + verb + object",
      note: "shapes acts as the predicate verb.",
    },
    ...overrides,
  };
}

function makeDocument(
  children: ReaderRecordPlateDocument["children"],
): ReaderRecordPlateDocument {
  return {
    type: "reader_record_plate_document",
    schemaVersion: "reader-record-plate-document/v1",
    record: {
      recordId: "record_1",
      title: "E2E spike test article",
      generation: 1,
      productState: "readable_enhancing",
      readinessState: "article_ready",
    },
    snapshot: {
      snapshotId: "snapshot_1",
      snapshotTakenAt: "2026-07-14T00:00:00Z",
      lastEventSequence: 1,
    },
    base: {
      baseId: "base_1",
      contentSha256: "sha256_1",
      textLengthUtf16: SOURCE_TEXT.length,
      hashAlgorithm: "fnv1a32-utf16",
    },
    progress: {
      overallStatus: "ready",
      layers: [],
    },
    children,
  };
}

// ---------------------------------------------------------------------------
// Content summary element builder
// ---------------------------------------------------------------------------

interface SpikeContentSummaryElement {
  type: "reader_content_summary";
  completeness: "full" | "partial" | "minimal";
  overview: string;
  researchQuestion?: string;
  methodology?: string;
  keyFindings: string[];
  limitations: string[];
  children: { text: string }[];
}

function makeContentSummaryElement(): SpikeContentSummaryElement {
  return {
    type: "reader_content_summary",
    completeness: "partial",
    overview: "E2E spike overview for production kit test.",
    researchQuestion: "Does replaceNodes preserve React state in real browser?",
    methodology: "Mount with real ReaderRecordPlateKit, expand, replace sibling.",
    keyFindings: ["Finding A"],
    limitations: ["browser E2E limitation"],
    children: [{ text: "" }],
  };
}

// ---------------------------------------------------------------------------
// Build real projected Plate value + prepend content summary
// ---------------------------------------------------------------------------

function makeProjectedPlateValue(): Descendant[] {
  const doc = makeDocument([
    makeParagraphBlock(),
    makeCalloutBlock(),
    makeBlockquoteBlock(),
  ]);
  const projected = projectReaderRecordPlateToPlateValue(doc);
  return [
    makeContentSummaryElement() as unknown as Descendant,
    ...projected,
  ];
}

function makeReplacementCallout(): ReaderCalloutElement {
  return {
    type: READER_CALLOUT_TYPE,
    id: "callout:grammar:grammar_item_1",
    children: [{ type: "p", children: [{ text: "UPDATED: shapes acts as the main predicate." }] }] as Descendant[],
    data: {
      anchorSegmentId: "seg_1",
      unitId: "unit_1",
      layerId: "layer_grammar_1",
      itemId: "grammar_item_1",
      grammarPoint: "predicate verb (updated)",
      pattern: "subject + verb + object",
      note: "UPDATED: shapes acts as the main predicate.",
    },
    variant: "grammar",
    icon: "📖",
  };
}

function makeReplacementParagraph(): ReaderParagraphElement {
  return {
    type: READER_PARAGRAPH_TYPE,
    id: "paragraph:seg_1",
    children: [{ text: "UPDATED: Institutional memory drives policy choices." }],
    data: makeParagraphBlock().data,
  };
}

// ---------------------------------------------------------------------------
// Window global type augmentation
// ---------------------------------------------------------------------------

declare global {
  interface Window {
    __spikeEditor?: unknown;
    __spikeHelpers?: {
      makeReplacementCallout: () => ReaderCalloutElement;
      makeReplacementParagraph: () => ReaderParagraphElement;
    };
    __spikeReady?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Mounted Plate harness — single editor with REAL ReaderRecordPlateKit
// ---------------------------------------------------------------------------

function SpikeHarness() {
  const editor = usePlateEditor(
    {
      plugins: SpikePlugins,
      value: makeProjectedPlateValue() as never[],
    },
    [],
  );

  const readyRef = useRef(false);
  useEffect(() => {
    if (!readyRef.current && editor) {
      readyRef.current = true;
      window.__spikeEditor = editor;
      window.__spikeHelpers = {
        makeReplacementCallout,
        makeReplacementParagraph,
      };
      window.__spikeReady = true;
    }
  }, [editor]);

  return (
    <Plate editor={editor} readOnly>
      <EditorContainer className="h-auto overflow-visible bg-transparent px-0 py-0">
        <Editor
          readOnly
          disableDefaultStyles
          className="space-y-2 px-0 py-0 outline-none"
        />
      </EditorContainer>
    </Plate>
  );
}

export default function E2EPlateSpikeHarness() {
  return (
    <main className="min-h-screen bg-background px-8 py-8">
      <h1 className="mb-4 text-lg font-semibold text-ink">
        E2E Plate Targeted Ops Spike
      </h1>
      <p className="mb-6 text-sm text-muted-foreground">
        Test-only harness. Real ReaderRecordPlateKit + real projection.
        Editor exposed on <code>window.__spikeEditor</code>.
      </p>
      <div className="mx-auto max-w-[72ch]">
        <SpikeHarness />
      </div>
    </main>
  );
}
