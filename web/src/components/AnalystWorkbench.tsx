"use client";

import { useEffect, useMemo, useState } from "react";

import { buildAuthHeaders } from "@/lib/auth/client-headers";

type DealItem = {
  id: string;
  company_name: string;
  sector: string | null;
  status: string;
  priority: string;
  recommendation_class: string | null;
  conviction_score: number | null;
  next_action_date: string | null;
  updated_at: string;
};

type BatchResult = {
  companyName: string;
  matchedCompany: string | null;
  score: number;
  recommendation: string;
  confidenceRange: number;
  dataCompleteness: number;
};

type TaskItem = {
  id: string;
  title: string;
  status: string;
  due_date: string | null;
  assignee_email: string;
};

type DealComment = {
  id: string;
  body: string;
  author_email: string;
  created_at: string;
};

type ActivityEvent = {
  id: string;
  entity_type: string;
  action: string;
  actor_email: string;
  created_at: string;
};

const STATUS_OPTIONS = ["new", "screening", "diligence", "ic", "pass", "invest"];

interface AnalystWorkbenchProps {
  userEmail: string;
  authEnabled: boolean;
  isAuthenticated: boolean;
}

type ModelHealth = {
  latestBacktest: {
    runDate: string | null;
    allPassed: boolean;
    survivalAuc: number | null;
    calibrationEce: number | null;
    qualityVsRandom: number | null;
  } | null;
  latestModel: {
    family: string | null;
    version: string | null;
    trainedAt: string | null;
    releaseStatus: string | null;
    daysSinceTrain: number | null;
  } | null;
  checks: {
    calibrationHealthy: boolean;
    releaseGateOpen: boolean;
    retrainRecommended: boolean;
  };
  segmentEvidence: Array<{
    segmentKey: string;
    sampleSize: number;
    survivalAuc: number | null;
    calibrationEce: number | null;
    releaseGateOpen: boolean;
    lastBacktestDate: string | null;
    evidenceOk: boolean;
  }>;
  valuationAudit: {
    realizedCoverage: number;
    meanAbsoluteError: number | null;
  };
};

export default function AnalystWorkbench({
  userEmail,
  authEnabled,
  isAuthenticated,
}: AnalystWorkbenchProps) {
  const [deals, setDeals] = useState<DealItem[]>([]);
  const [loadingDeals, setLoadingDeals] = useState(false);
  const [batchInput, setBatchInput] = useState("");
  const [batchResults, setBatchResults] = useState<BatchResult[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const [selectedDealId, setSelectedDealId] = useState<string | null>(null);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [comments, setComments] = useState<DealComment[]>([]);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [taskTitle, setTaskTitle] = useState("");
  const [commentBody, setCommentBody] = useState("");
  const [modelHealth, setModelHealth] = useState<ModelHealth | null>(null);

  const stats = useMemo(() => {
    const byStatus = new Map<string, number>();
    for (const d of deals) byStatus.set(d.status, (byStatus.get(d.status) ?? 0) + 1);
    return {
      total: deals.length,
      diligence: byStatus.get("diligence") ?? 0,
      ic: byStatus.get("ic") ?? 0,
      invest: byStatus.get("invest") ?? 0,
    };
  }, [deals]);

  const selectedDeal = useMemo(
    () => deals.find((d) => d.id === selectedDealId) ?? null,
    [deals, selectedDealId],
  );

  async function refreshDeals() {
    if (authEnabled && !isAuthenticated) {
      setDeals([]);
      return;
    }
    setLoadingDeals(true);
    try {
      const resp = await fetch("/api/deals", {
        headers: await buildAuthHeaders(userEmail),
      });
      const data = (await resp.json()) as { deals?: DealItem[] };
      const nextDeals = data.deals ?? [];
      setDeals(nextDeals);
      if (!selectedDealId && nextDeals.length > 0) {
        setSelectedDealId(nextDeals[0].id);
      }
    } finally {
      setLoadingDeals(false);
    }
  }

  async function refreshSelected(dealId: string) {
    if (authEnabled && !isAuthenticated) {
      setTasks([]);
      setComments([]);
      setEvents([]);
      return;
    }
    const headers = await buildAuthHeaders(userEmail);
    const [tasksResp, commentsResp, eventsResp] = await Promise.all([
      fetch(`/api/deals/${dealId}/tasks`, { headers }),
      fetch(`/api/deals/${dealId}/comments`, { headers }),
      fetch(`/api/deals/${dealId}/activity`, { headers }),
    ]);
    const tasksData = (await tasksResp.json()) as { tasks?: TaskItem[] };
    const commentsData = (await commentsResp.json()) as { comments?: DealComment[] };
    const eventsData = (await eventsResp.json()) as { events?: ActivityEvent[] };
    setTasks(tasksData.tasks ?? []);
    setComments(commentsData.comments ?? []);
    setEvents(eventsData.events ?? []);
  }

  useEffect(() => {
    void refreshDeals();
  }, [authEnabled, isAuthenticated, userEmail]);

  useEffect(() => {
    if (selectedDealId) {
      void refreshSelected(selectedDealId);
    }
  }, [selectedDealId, userEmail, authEnabled, isAuthenticated]);

  useEffect(() => {
    async function refreshModelHealth() {
      if (authEnabled && !isAuthenticated) {
        setModelHealth(null);
        return;
      }
      const response = await fetch("/api/model-health", {
        headers: await buildAuthHeaders(userEmail),
      });
      if (!response.ok) {
        setModelHealth(null);
        return;
      }
      const data = (await response.json()) as ModelHealth;
      setModelHealth(data);
    }
    void refreshModelHealth();
  }, [authEnabled, isAuthenticated, userEmail]);

  async function updateStatus(id: string, status: string) {
    await fetch(`/api/deals/${id}`, {
      method: "PATCH",
      headers: await buildAuthHeaders(userEmail, "json"),
      body: JSON.stringify({ status, ownerEmail: userEmail }),
    });
    await refreshDeals();
    await refreshSelected(id);
  }

  async function runBatch() {
    const names = batchInput
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    if (names.length === 0) return;
    setBatchLoading(true);
    try {
      const resp = await fetch("/api/score/batch", {
        method: "POST",
        headers: await buildAuthHeaders(userEmail, "json"),
        body: JSON.stringify({
          deals: names.map((name) => ({ companyName: name })),
        }),
      });
      const data = (await resp.json()) as { results?: BatchResult[] };
      setBatchResults(data.results ?? []);
    } finally {
      setBatchLoading(false);
    }
  }

  async function addToPipeline(item: BatchResult) {
    await fetch("/api/deals", {
      method: "POST",
      headers: await buildAuthHeaders(userEmail, "json"),
      body: JSON.stringify({
        companyName: item.companyName,
        recommendationClass: item.recommendation.toLowerCase().replace(/\s+/g, "_"),
        convictionScore: item.score,
        status: "screening",
        priority: item.score >= 65 ? "high" : "medium",
        ownerEmail: userEmail,
      }),
    });
    await refreshDeals();
  }

  async function addTask() {
    if (!selectedDealId || !taskTitle.trim()) return;
    await fetch(`/api/deals/${selectedDealId}/tasks`, {
      method: "POST",
      headers: await buildAuthHeaders(userEmail, "json"),
      body: JSON.stringify({
        title: taskTitle.trim(),
        assigneeEmail: userEmail,
        evidenceRequired: true,
      }),
    });
    setTaskTitle("");
    await refreshSelected(selectedDealId);
  }

  async function addComment() {
    if (!selectedDealId || !commentBody.trim()) return;
    await fetch(`/api/deals/${selectedDealId}/comments`, {
      method: "POST",
      headers: await buildAuthHeaders(userEmail, "json"),
      body: JSON.stringify({ body: commentBody.trim(), authorEmail: userEmail }),
    });
    setCommentBody("");
    await refreshSelected(selectedDealId);
  }

  async function requestApproval() {
    if (!selectedDealId) return;
    await fetch(`/api/deals/${selectedDealId}/approve`, {
      method: "POST",
      headers: await buildAuthHeaders(userEmail, "json"),
      body: JSON.stringify({
        requestedBy: userEmail,
        approverEmail: userEmail,
        status: "pending",
      }),
    });
    await refreshSelected(selectedDealId);
  }

  return (
    <section className="mt-10 space-y-6">
      <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-5">
        <h3 className="mb-3 text-sm font-semibold text-neutral-200">
          Analyst Workbench
        </h3>
        <div className="grid grid-cols-2 gap-3 text-xs text-neutral-400 sm:grid-cols-4">
          <div>
            Total deals: <span className="text-neutral-200">{stats.total}</span>
          </div>
          <div>
            In diligence:{" "}
            <span className="text-neutral-200">{stats.diligence}</span>
          </div>
          <div>
            At IC: <span className="text-neutral-200">{stats.ic}</span>
          </div>
          <div>
            Invested: <span className="text-neutral-200">{stats.invest}</span>
          </div>
        </div>
      </div>

      {modelHealth && (
        <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-5">
          <h4 className="mb-3 text-sm font-semibold text-neutral-200">
            Model Governance
          </h4>
          <div className="grid gap-3 text-xs text-neutral-400 sm:grid-cols-3">
            <div>
              <div className="text-neutral-500">Latest AUC</div>
              <div className="text-neutral-200">
                {modelHealth.latestBacktest?.survivalAuc ?? "n/a"}
              </div>
            </div>
            <div>
              <div className="text-neutral-500">Latest ECE</div>
              <div className="text-neutral-200">
                {modelHealth.latestBacktest?.calibrationEce ?? "n/a"}
              </div>
            </div>
            <div>
              <div className="text-neutral-500">Model Age (days)</div>
              <div className="text-neutral-200">
                {modelHealth.latestModel?.daysSinceTrain ?? "n/a"}
              </div>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span
              className={`rounded px-2 py-1 ${
                modelHealth.checks.releaseGateOpen
                  ? "bg-green-900/40 text-green-300"
                  : "bg-red-900/40 text-red-300"
              }`}
            >
              Release Gate {modelHealth.checks.releaseGateOpen ? "Open" : "Blocked"}
            </span>
            <span
              className={`rounded px-2 py-1 ${
                modelHealth.checks.calibrationHealthy
                  ? "bg-green-900/40 text-green-300"
                  : "bg-amber-900/40 text-amber-300"
              }`}
            >
              Calibration {modelHealth.checks.calibrationHealthy ? "Healthy" : "Warning"}
            </span>
            <span
              className={`rounded px-2 py-1 ${
                modelHealth.checks.retrainRecommended
                  ? "bg-amber-900/40 text-amber-300"
                  : "bg-neutral-800 text-neutral-300"
              }`}
            >
              Retrain {modelHealth.checks.retrainRecommended ? "Recommended" : "Not Required"}
            </span>
          </div>
          <div className="mt-4 border-t border-neutral-800 pt-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
              Segment Evidence
            </div>
            <div className="space-y-1 text-xs text-neutral-400">
              {modelHealth.segmentEvidence.length === 0 && (
                <div>No segment evidence available yet.</div>
              )}
              {modelHealth.segmentEvidence.map((segment) => (
                <div key={segment.segmentKey} className="flex items-center justify-between gap-2">
                  <span>{segment.segmentKey}</span>
                  <span>
                    n={segment.sampleSize}
                    {" · "}AUC {segment.survivalAuc ?? "n/a"}
                    {" · "}ECE {segment.calibrationEce ?? "n/a"}
                    {" · "}
                    <span className={segment.evidenceOk ? "text-green-300" : "text-amber-300"}>
                      {segment.evidenceOk ? "usable" : "weak"}
                    </span>
                  </span>
                </div>
              ))}
            </div>
            <div className="mt-3 text-xs text-neutral-500">
              Valuation audits with realized outcomes: {modelHealth.valuationAudit.realizedCoverage}
              {" · "}
              MAE: {modelHealth.valuationAudit.meanAbsoluteError ?? "n/a"}
            </div>
          </div>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-5">
          <h4 className="mb-2 text-sm font-semibold text-neutral-200">
            Pipeline Queue
          </h4>
          <p className="mb-3 text-xs text-neutral-500">
            Daily workflow: screening {"->"} diligence {"->"} IC {"->"} invest/pass.
          </p>
          {loadingDeals ? (
            <p className="text-xs text-neutral-500">Loading deals...</p>
          ) : deals.length === 0 ? (
            <p className="text-xs text-neutral-500">No deals in pipeline yet.</p>
          ) : (
            <div className="space-y-2">
              {deals.slice(0, 20).map((deal) => (
                <div
                  key={deal.id}
                  className={`cursor-pointer rounded-lg border px-3 py-2 ${
                    selectedDealId === deal.id
                      ? "border-blue-600 bg-blue-950/20"
                      : "border-neutral-800"
                  }`}
                  onClick={() => setSelectedDealId(deal.id)}
                >
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <div className="truncate text-sm text-neutral-200">
                        {deal.company_name}
                      </div>
                      <div className="text-xs text-neutral-500">
                        {deal.sector ?? "unknown sector"} · priority {deal.priority}
                      </div>
                    </div>
                    <select
                      value={deal.status}
                      onChange={(e) => void updateStatus(deal.id, e.target.value)}
                      className="rounded-md border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs text-neutral-200"
                    >
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-5">
          <h4 className="mb-2 text-sm font-semibold text-neutral-200">
            Batch Triage
          </h4>
          <p className="mb-3 text-xs text-neutral-500">
            Paste one company per line to rank opportunities quickly.
          </p>
          <textarea
            rows={5}
            value={batchInput}
            onChange={(e) => setBatchInput(e.target.value)}
            className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm text-neutral-100"
            placeholder={"Company A\nCompany B\nCompany C"}
          />
          <button
            type="button"
            onClick={() => void runBatch()}
            disabled={batchLoading}
            className="mt-3 rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white disabled:opacity-50"
          >
            {batchLoading ? "Running..." : "Run Batch Score"}
          </button>
          {batchResults.length > 0 && (
            <div className="mt-4 space-y-2">
              {batchResults.map((r) => (
                <div
                  key={`${r.companyName}-${r.score}`}
                  className="flex items-center justify-between rounded-lg border border-neutral-800 px-3 py-2"
                >
                  <div>
                    <div className="text-sm text-neutral-200">{r.companyName}</div>
                    <div className="text-xs text-neutral-500">
                      {r.recommendation} · {r.dataCompleteness}% completeness
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-sm text-neutral-200">{r.score}</div>
                    <button
                      type="button"
                      onClick={() => void addToPipeline(r)}
                      className="rounded-md border border-neutral-700 px-2 py-1 text-xs text-neutral-200"
                    >
                      Add
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {selectedDeal && (
        <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-5">
          <div className="mb-3 flex items-center justify-between">
            <h4 className="text-sm font-semibold text-neutral-200">
              Deep Diligence: {selectedDeal.company_name}
            </h4>
            <button
              type="button"
              onClick={() => void requestApproval()}
              className="rounded-md border border-neutral-700 px-2 py-1 text-xs text-neutral-200"
            >
              Request Approval
            </button>
          </div>

          <div className="grid gap-5 lg:grid-cols-3">
            <div>
              <h5 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-400">
                Tasks
              </h5>
              <div className="space-y-2">
                {tasks.map((task) => (
                  <div key={task.id} className="rounded border border-neutral-800 p-2">
                    <div className="text-sm text-neutral-200">{task.title}</div>
                    <div className="text-xs text-neutral-500">
                      {task.status}
                      {task.due_date ? ` · due ${task.due_date}` : ""}
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex gap-2">
                <input
                  value={taskTitle}
                  onChange={(e) => setTaskTitle(e.target.value)}
                  placeholder="Add diligence task"
                  className="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs text-neutral-200"
                />
                <button
                  type="button"
                  onClick={() => void addTask()}
                  className="rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-200"
                >
                  Add
                </button>
              </div>
            </div>

            <div>
              <h5 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-400">
                Notes
              </h5>
              <div className="max-h-56 space-y-2 overflow-auto">
                {comments.map((comment) => (
                  <div key={comment.id} className="rounded border border-neutral-800 p-2">
                    <div className="text-sm text-neutral-200">{comment.body}</div>
                    <div className="text-xs text-neutral-500">
                      {comment.author_email} ·{" "}
                      {new Date(comment.created_at).toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex gap-2">
                <input
                  value={commentBody}
                  onChange={(e) => setCommentBody(e.target.value)}
                  placeholder="Add comment"
                  className="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs text-neutral-200"
                />
                <button
                  type="button"
                  onClick={() => void addComment()}
                  className="rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-200"
                >
                  Save
                </button>
              </div>
            </div>

            <div>
              <h5 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-400">
                Activity History
              </h5>
              <div className="max-h-72 space-y-2 overflow-auto">
                {events.map((ev) => (
                  <div key={ev.id} className="rounded border border-neutral-800 p-2">
                    <div className="text-sm text-neutral-200">
                      {ev.entity_type} · {ev.action}
                    </div>
                    <div className="text-xs text-neutral-500">
                      {ev.actor_email} · {new Date(ev.created_at).toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
