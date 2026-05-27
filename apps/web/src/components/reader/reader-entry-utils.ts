export interface AnalysisChunk {
  order: string;
  label: string;
  text: string;
}

export interface SentenceAnalysisSegment {
  start: number;
  end: number;
  label: string;
  index: number;
}

export function parseSentenceAnalysisContent(content: string): {
  summary: string;
  chunks: AnalysisChunk[];
} {
  const chunks: AnalysisChunk[] = [];
  const summaryLines: string[] = [];
  const chunkRegex = /^-\s*\*\*(?:(\d+)\.\s*)?([^*]+)\*\*[：:]\s*[`'"](.+)[`'"]$/;

  content.split("\n").forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      return;
    }

    const match = trimmed.match(chunkRegex);
    if (match) {
      chunks.push({
        order: match[1] || "",
        label: match[2].trim(),
        text: match[3].trim(),
      });
      return;
    }

    if (chunks.length === 0) {
      summaryLines.push(trimmed);
    }
  });

  return {
    summary: summaryLines.join("\n"),
    chunks: chunks.sort((a, b) => {
      if (!a.order || !b.order || a.order === b.order) {
        return 0;
      }
      return Number.parseInt(a.order, 10) - Number.parseInt(b.order, 10);
    }),
  };
}

function normalizeWords(value: string): string[] {
  return value.toLowerCase().match(/[a-z0-9]+/g) ?? [];
}

function findFuzzyMatch(
  fullText: string,
  subText: string,
  fromIndex = 0,
): { start: number; length: number } | null {
  const subWords = normalizeWords(subText);
  if (subWords.length === 0) {
    return null;
  }

  let currentPos = fromIndex;
  let matchStart = -1;
  let matchEnd = -1;

  for (const word of subWords) {
    const regex = new RegExp(word, "i");
    const textToSearch = fullText.slice(currentPos);
    const match = textToSearch.match(regex);
    if (!match || match.index === undefined) {
      return null;
    }

    const absolutePos = currentPos + match.index;
    if (matchStart < 0) {
      matchStart = absolutePos;
    }
    matchEnd = absolutePos + match[0].length;
    currentPos = matchEnd;
  }

  if (matchStart < 0 || matchEnd <= matchStart) {
    return null;
  }

  return {
    start: matchStart,
    length: matchEnd - matchStart,
  };
}

export function buildSentenceAnalysisSegments(
  text: string,
  chunks: Array<Pick<AnalysisChunk, "label" | "text">>,
): SentenceAnalysisSegment[] {
  const segments: SentenceAnalysisSegment[] = [];
  let lastIndex = 0;

  chunks.forEach((chunk, index) => {
    const match = findFuzzyMatch(text, chunk.text, lastIndex);
    if (!match) {
      return;
    }

    segments.push({
      start: match.start,
      end: match.start + match.length,
      label: chunk.label,
      index,
    });
    lastIndex = match.start + match.length;
  });

  return segments;
}
