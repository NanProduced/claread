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
    async runSingleWorkflow(payload) {
      return dataOf(await api.post("/eval-center/workflow-lab/single-run", payload));
    },
    async runSingleRunCompare(payload) {
      return dataOf(await api.post("/eval-center/workflow-lab/single-run-compare", payload));
    },
    async listCompares(limit = 100) {
      const data = dataOf(await api.get("/eval-center/workflow-lab/compares", { params: { limit } }));
      return Array.isArray(data?.records) ? data.records : [];
    },
    async getCompareDetail(compareId) {
      return dataOf(await api.get(`/eval-center/workflow-lab/compares/${encodeURIComponent(compareId)}`));
    },
    async getCompareCaseEvidence(compareId, caseId) {
      return dataOf(await api.get(`/eval-center/workflow-lab/compares/${encodeURIComponent(compareId)}/cases/${encodeURIComponent(caseId)}`));
    },
    async listReadyCandidates() {
      return dataOf(await api.get("/eval-center/prompt-variants/ready")) || [];
    },
    async listModelProfiles() {
      return dataOf(await api.get("/eval-center/article-analysis/model-profiles")) || [];
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
    async listCompareJudgeRequests(compareId, params = {}) {
      return dataOf(await api.get(`/eval-center/workflow-lab/compares/${encodeURIComponent(compareId)}/judge-requests`, {
        params: { status: "all", limit: 50, ...params },
      })) || [];
    },
    async createCompareJudgeRequest(compareId, payload) {
      return dataOf(await api.post(`/eval-center/workflow-lab/compares/${encodeURIComponent(compareId)}/judge-requests`, payload));
    },
    async cancelCompareJudgeRequest(compareId, requestId) {
      return dataOf(await api.post(`/eval-center/workflow-lab/compares/${encodeURIComponent(compareId)}/judge-requests/${encodeURIComponent(requestId)}/cancel`));
    },
    async retryCompareJudgeRequest(compareId, requestId, payload = {}) {
      return dataOf(await api.post(`/eval-center/workflow-lab/compares/${encodeURIComponent(compareId)}/judge-requests/${encodeURIComponent(requestId)}/retry`, payload));
    },
  };
}
