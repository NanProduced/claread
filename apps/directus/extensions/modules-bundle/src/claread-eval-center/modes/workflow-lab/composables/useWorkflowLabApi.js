import { useApi } from "@directus/extensions-sdk";

function dataOf(response) {
  return response?.data?.data ?? response?.data;
}

function directusError(error, fallback) {
  return error?.response?.data?.errors?.map((item) => item.message).filter(Boolean).join("; ")
    || error?.response?.data?.message
    || error?.message
    || fallback;
}

export function useWorkflowLabApi() {
  const api = useApi();

  return {
    directusError,
    async listRuns(limit = 100) {
      const data = dataOf(await api.get("/eval-center/runs", { params: { limit } }));
      return Array.isArray(data?.runs) ? data.runs : [];
    },
    async getRunDetail(runId) {
      return dataOf(await api.get(`/eval-center/runs/${encodeURIComponent(runId)}`));
    },
    async getCaseArtifact(runId, caseId) {
      return dataOf(await api.get(`/eval-center/runs/${encodeURIComponent(runId)}/cases/${encodeURIComponent(caseId)}`));
    },
    async listRunRequests(status = "all") {
      return dataOf(await api.get("/eval-center/workflow-runs/requests", {
        params: { status, limit: 60 },
      })) || [];
    },
    async createRunRequest(payload) {
      return dataOf(await api.post("/eval-center/workflow-runs/requests", payload));
    },
    async runSingleWorkflow(payload) {
      return dataOf(await api.post("/eval-center/workflow-lab/single-run", payload));
    },
    async saveSingleRunToHistory(payload) {
      return dataOf(await api.post("/eval-center/workflow-lab/run-history/single-run", payload));
    },
    async cancelRunRequest(requestId) {
      return dataOf(await api.post(`/eval-center/workflow-runs/requests/${encodeURIComponent(requestId)}/cancel`));
    },
    async retryRunRequest(requestId, payload = {}) {
      return dataOf(await api.post(`/eval-center/workflow-runs/requests/${encodeURIComponent(requestId)}/retry`, payload));
    },
    async listReadyCandidates() {
      return dataOf(await api.get("/eval-center/prompt-variants/ready")) || [];
    },
    async listModelProfiles() {
      return dataOf(await api.get("/eval-center/article-analysis/model-profiles")) || [];
    },
    async listDatasets() {
      const data = dataOf(await api.get("/eval-center/workflow-runs/datasets")) || [];
      return Array.isArray(data) ? data : [];
    },
    async createDataset(payload) {
      return dataOf(await api.post("/eval-center/workflow-runs/datasets", payload));
    },
    async addDatasetCase(datasetId, payload) {
      return dataOf(await api.post(`/eval-center/workflow-runs/datasets/${encodeURIComponent(datasetId)}/cases`, payload));
    },
    async getJudgeResult(runId, judgeRunId) {
      if (!runId || !judgeRunId) return null;
      return dataOf(await api.get(
        `/eval-center/runs/${encodeURIComponent(runId)}/judge/${encodeURIComponent(judgeRunId)}`,
      ));
    },
    async listCandidateDrafts() {
      return dataOf(await api.get("/items/eval_prompt_variant_drafts", {
        params: { sort: "-date_updated,-date_created", limit: 100 },
      })) || [];
    },
    async previewCandidate(payload) {
      return dataOf(await api.post("/eval-center/prompt-variants/manifest-preview", payload));
    },
    async loadBaselineBundle(payload) {
      return dataOf(await api.post("/eval-center/workflow-lab/baseline-bundle", payload));
    },
    async saveCandidateDraft(payload, id = "") {
      if (id) {
        return dataOf(await api.patch(`/items/eval_prompt_variant_drafts/${encodeURIComponent(id)}`, payload));
      }
      return dataOf(await api.post("/items/eval_prompt_variant_drafts", payload));
    },
    async createCompare(payload) {
      return dataOf(await api.post("/eval-center/workflow-lab/compare", payload));
    },
    async listRubrics() {
      return dataOf(await api.get("/eval-center/judge/rubrics")) || [];
    },
    async listJudgeRequests(params = {}) {
      return dataOf(await api.get("/eval-center/judge/requests", {
        params: { status: "all", limit: 50, ...params },
      })) || [];
    },
    async createJudgeRequest(payload) {
      return dataOf(await api.post("/eval-center/judge/requests", payload));
    },
    async cancelJudgeRequest(requestId) {
      return dataOf(await api.post(`/eval-center/judge/requests/${encodeURIComponent(requestId)}/cancel`));
    },
  };
}
