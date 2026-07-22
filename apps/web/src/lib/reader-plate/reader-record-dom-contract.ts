/**
 * Single source of truth for the Reader Record DOM navigation contract.
 *
 * The navigation rail, the agentic DOM navigation adapter, and the Plate node
 * renderer (`reader-blocks-kit`) all agree on these attribute names through this
 * module — the literal strings live ONLY here. A node is a *navigable* scroll
 * target when it opts in (carries the node attr) with a unit id; unit-start and
 * anchor-segment are optional precision hints. The contract is source-agnostic:
 * a paragraph today, a Markdown heading / source block later — consumers never
 * branch on the node *value*, only on attribute presence.
 */

export const READER_RECORD_NAV_ATTRS = {
  node: "data-reader-record-node",
  unitId: "data-unit-id",
  unitStart: "data-reader-record-unit-start",
  anchorSegment: "data-anchor-segment-id",
} as const;

export const READER_RECORD_NAV_NODE_ATTR = READER_RECORD_NAV_ATTRS.node;
export const READER_RECORD_UNIT_ID_ATTR = READER_RECORD_NAV_ATTRS.unitId;
export const READER_RECORD_UNIT_START_ATTR = READER_RECORD_NAV_ATTRS.unitStart;
export const READER_RECORD_ANCHOR_SEGMENT_ATTR =
  READER_RECORD_NAV_ATTRS.anchorSegment;

/** Selector for any navigable node (source-agnostic: paragraph / heading / …). */
export const READER_RECORD_NAVIGABLE_NODE_SELECTOR = `[${READER_RECORD_NAV_ATTRS.node}][${READER_RECORD_NAV_ATTRS.unitId}]`;

/** Selector for any node carrying an anchor segment id. */
export const READER_RECORD_ANCHOR_SEGMENT_SELECTOR = `[${READER_RECORD_NAV_ATTRS.anchorSegment}]`;

/** The plate document root that scopes every navigation search. */
export const READER_RECORD_PLATE_DOCUMENT_SELECTOR =
  ".reader-record-plate-document";

/**
 * Known node kinds (open: any string is accepted). `"heading"` is reserved for
 * the future Markdown heading renderer, which reuses
 * {@link readerRecordNavigableNodeAttrs} unchanged.
 */
export type ReaderRecordNodeKind =
  | "paragraph"
  | "blockquote"
  | "callout"
  | "callout-group"
  | "sentence-analysis"
  | "heading"
  | (string & {});

export interface ReaderRecordNavigableNodeAttrsInput {
  nodeKind: ReaderRecordNodeKind;
  unitId?: string | null;
  isUnitStart?: boolean | null;
  anchorSegmentId?: string | null;
}

type NavAttrKey =
  | typeof READER_RECORD_NAV_ATTRS.node
  | typeof READER_RECORD_NAV_ATTRS.unitId
  | typeof READER_RECORD_NAV_ATTRS.unitStart
  | typeof READER_RECORD_NAV_ATTRS.anchorSegment;

/**
 * Props to spread onto a rendered reading node so it joins the shared navigation
 * contract. Only present, truthy hints are emitted; `nodeKind` is always emitted.
 * The type carries explicit `data-*` keys (no index signature) so it spreads
 * cleanly onto intrinsic JSX elements.
 */
export type ReaderRecordNavigableNodeAttrs = Partial<
  Record<NavAttrKey, string>
> &
  Record<typeof READER_RECORD_NAV_ATTRS.node, string>;

export function readerRecordNavigableNodeAttrs(
  input: ReaderRecordNavigableNodeAttrsInput,
): ReaderRecordNavigableNodeAttrs {
  const attrs: ReaderRecordNavigableNodeAttrs = {
    [READER_RECORD_NAV_ATTRS.node]: input.nodeKind,
  };
  if (input.unitId) {
    attrs[READER_RECORD_NAV_ATTRS.unitId] = input.unitId;
  }
  if (input.isUnitStart) {
    attrs[READER_RECORD_NAV_ATTRS.unitStart] = "true";
  }
  if (input.anchorSegmentId) {
    attrs[READER_RECORD_NAV_ATTRS.anchorSegment] = input.anchorSegmentId;
  }
  return attrs;
}

/** A node joins navigation when it opts in (node attr) with a unit id. */
export function isReaderRecordNavigableNode(el: Element): boolean {
  return (
    el.hasAttribute(READER_RECORD_NAV_ATTRS.node) &&
    el.hasAttribute(READER_RECORD_NAV_ATTRS.unitId)
  );
}
