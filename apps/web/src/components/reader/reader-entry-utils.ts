export interface AnalysisChunk {
  order: string;
  label: string;
  text: string;
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
