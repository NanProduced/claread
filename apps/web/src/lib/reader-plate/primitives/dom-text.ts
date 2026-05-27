export function copyDomRect(rect: DOMRect): DOMRect {
  const x = Number.isFinite(rect.x) ? rect.x : rect.left;
  const y = Number.isFinite(rect.y) ? rect.y : rect.top;
  const width = rect.width;
  const height = rect.height;
  const copiedRect = {
    x,
    y,
    width,
    height,
    top: y,
    left: x,
    right: x + width,
    bottom: y + height,
    toJSON() {
      return {
        x,
        y,
        width,
        height,
        top: y,
        left: x,
        right: x + width,
        bottom: y + height,
      };
    },
  };

  return copiedRect as DOMRect;
}

export function firstUsableRangeRect(range: Range): DOMRect | null {
  const rects = Array.from(range.getClientRects());
  const rect = rects.find((item) => item.width > 0 && item.height > 0) ?? range.getBoundingClientRect();

  if (rect.width === 0 && rect.height === 0) {
    return null;
  }

  return copyDomRect(rect);
}

export function textOffsetWithinElement(element: HTMLElement, node: Node, offset: number): number | null {
  if (!element.contains(node)) {
    return null;
  }

  const range = document.createRange();
  range.selectNodeContents(element);
  try {
    range.setEnd(node, offset);
    return range.toString().length;
  } catch {
    return null;
  } finally {
    range.detach();
  }
}

function textNodePointAtOffset(
  element: HTMLElement,
  targetOffset: number,
): { node: Node; offset: number } | null {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let remaining = targetOffset;
  let current = walker.nextNode();

  while (current) {
    const length = current.textContent?.length ?? 0;
    if (remaining <= length) {
      return { node: current, offset: remaining };
    }
    remaining -= length;
    current = walker.nextNode();
  }

  return null;
}

export function rectForTextOffsets(
  element: HTMLElement,
  startOffset: number,
  endOffset: number,
): DOMRect | null {
  if (startOffset < 0 || endOffset <= startOffset) {
    return null;
  }

  const startPoint = textNodePointAtOffset(element, startOffset);
  const endPoint = textNodePointAtOffset(element, endOffset);
  if (!startPoint || !endPoint) {
    return null;
  }

  const range = document.createRange();
  try {
    range.setStart(startPoint.node, startPoint.offset);
    range.setEnd(endPoint.node, endPoint.offset);
    return firstUsableRangeRect(range);
  } catch {
    return null;
  } finally {
    range.detach();
  }
}
