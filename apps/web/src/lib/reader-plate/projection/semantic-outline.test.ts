import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { ReaderSemanticOutlineProjectionDto } from "./semantic-outline";
import {
  semanticOutlineValidationContextFromUnknown,
  semanticOutlineValidationInputFromUnknown,
  validateSemanticOutlineProjection,
} from "./semantic-outline";

type FixtureCase = {
  id: string;
  context: unknown;
  input: unknown;
  expected: {
    status: string;
    node_ids: string[];
    titles: string[];
    drop_reasons: string[];
    skipped_node_count?: number;
    start_anchor_segment_ids?: Array<string | null>;
  };
};

const fixturePath = resolve(
  process.cwd(),
  "../../services/api/tests/fixtures/semantic_outline/v1/cases.json",
);
const fixtureCases = JSON.parse(readFileSync(fixturePath, "utf8")) as {
  cases: FixtureCase[];
};
const canonicalOutlineSample = {
  schema_kind: "reader_semantic_outline",
  schema_version: 1,
  status: "ready",
  source_identity: { base_id: "base_a", generation: 1 },
  publication: { outline_revision: "outline_r1" },
  provenance: { kind: "llm", builder: "outline_builder", model: "test-model" },
  nodes: [
    {
      node_id: "oln_1",
      parent_node_id: null,
      depth: 1,
      title: "Chapter One",
      start_unit_id: "u1",
      end_unit_id: "u1",
      start_anchor_segment_id: null,
      end_anchor_segment_id: null,
      order_index: 1,
    },
  ],
  diagnostics: { drops: [], skipped_node_count: 0 },
} as const satisfies ReaderSemanticOutlineProjectionDto;

void canonicalOutlineSample;

describe("semantic outline validator shared contract", () => {
  for (const fixture of fixtureCases.cases) {
    it(fixture.id, () => {
      const result = validateSemanticOutlineProjection(
        semanticOutlineValidationContextFromUnknown(fixture.context),
        semanticOutlineValidationInputFromUnknown(fixture.input),
      );

      expect(result.status).toBe(fixture.expected.status);
      expect(result.nodes.map((node) => node.node_id)).toEqual(fixture.expected.node_ids);
      expect(result.nodes.map((node) => node.title)).toEqual(fixture.expected.titles);
      expect(result.diagnostics.drops.map((drop) => drop.reason_code).sort()).toEqual(
        [...fixture.expected.drop_reasons].sort(),
      );
      if (fixture.expected.skipped_node_count !== undefined) {
        expect(result.diagnostics.skipped_node_count).toBe(fixture.expected.skipped_node_count);
      }
      if (fixture.expected.start_anchor_segment_ids) {
        expect(result.nodes.map((node) => node.start_anchor_segment_id)).toEqual(
          fixture.expected.start_anchor_segment_ids,
        );
      }
    });
  }
});
