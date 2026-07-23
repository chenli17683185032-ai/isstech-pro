import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiRequest } from "../api";
import Button from "../components/Button";
import EmptyState from "../components/EmptyState";
import StatusTag from "../components/StatusTag";
import { formatDateTime } from "../lib/format";

function DraftFieldEditor({ field, draft, token, onUpdated, notify }) {
  const editable = ["extracted", "needs_review"].includes(draft.state);
  const [value, setValue] = useState(field.confirmed_value ?? field.proposed_value ?? "");
  const [showEvidence, setShowEvidence] = useState(Boolean(field.human_evidence || !field.original_evidence));
  const effectiveEvidence = field.human_evidence || field.original_evidence;
  const [sourceKind, setSourceKind] = useState(effectiveEvidence?.source_kind || "document");
  const [sourceIndex, setSourceIndex] = useState(effectiveEvidence?.source_index || 1);
  const [sourceLabel, setSourceLabel] = useState(effectiveEvidence?.source_label || "Document");
  const [sourceText, setSourceText] = useState(effectiveEvidence?.source_text || "");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setValue(field.confirmed_value ?? field.proposed_value ?? "");
    const evidence = field.human_evidence || field.original_evidence;
    setShowEvidence(Boolean(field.human_evidence || !field.original_evidence));
    setSourceKind(evidence?.source_kind || "document");
    setSourceIndex(evidence?.source_index || 1);
    setSourceLabel(evidence?.source_label || "Document");
    setSourceText(evidence?.source_text || "");
  }, [field]);

  async function review(decision) {
    setBusy(true);
    try {
      const body = { decision, expected_version: draft.version };
      if (decision === "confirmed") {
        body.confirmed_value = value;
        if (showEvidence) {
          body.evidence = {
            source_kind: sourceKind,
            source_index: Number(sourceIndex),
            source_label: sourceLabel,
            source_text: sourceText,
          };
        }
      }
      const updated = await apiRequest(`/v1/drafts/${draft.draft_id}/fields/${field.field_name}`, {
        token,
        method: "PUT",
        body,
      });
      onUpdated(updated);
    } catch (error) {
      notify({ tone: "error", message: error.code === "CONFLICT" ? "草稿已更新，正在刷新" : error.message });
      if (error.code === "CONFLICT") onUpdated(null, true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="review-field">
      <div className="review-field__heading">
        <div><strong>{field.label}</strong><span className="mono">{field.field_name}</span></div>
        <div>{field.required ? <span className="required-mark">必填</span> : null}<StatusTag value={field.decision} /></div>
      </div>
      <div className="review-field__body">
        <div className="review-source">
          <span>AI 建议</span>
          <strong>{field.proposed_value || "未识别"}</strong>
          <small>置信度 {field.confidence == null ? "--" : `${Math.round(field.confidence * 100)}%`}</small>
          {field.original_evidence ? (
            <blockquote><span>{field.original_evidence.source_label} · {field.original_evidence.source_kind} {field.original_evidence.source_index}</span>{field.original_evidence.source_text}</blockquote>
          ) : <em>无原始来源</em>}
        </div>
        <div className="review-decision">
          <label><span>确认值</span><input value={value} disabled={!editable || busy} onChange={(event) => setValue(event.target.value)} /></label>
          {editable ? (
            <button className="evidence-toggle" type="button" onClick={() => setShowEvidence((current) => !current)}>
              <RotateCcw size={14} />{showEvidence ? "收起来源修正" : "修正来源"}
            </button>
          ) : null}
          {showEvidence ? (
            <div className="evidence-editor">
              <div className="evidence-editor__row">
                <label><span>类型</span><select value={sourceKind} disabled={!editable || busy} onChange={(event) => setSourceKind(event.target.value)}><option value="document">文档</option><option value="page">页面</option><option value="sheet">工作表</option><option value="slide">幻灯片</option></select></label>
                <label><span>序号</span><input type="number" min="1" value={sourceIndex} disabled={!editable || busy} onChange={(event) => setSourceIndex(event.target.value)} /></label>
                <label><span>标签</span><input value={sourceLabel} disabled={!editable || busy} onChange={(event) => setSourceLabel(event.target.value)} /></label>
              </div>
              <label><span>原文片段</span><textarea rows="3" value={sourceText} disabled={!editable || busy} onChange={(event) => setSourceText(event.target.value)} /></label>
            </div>
          ) : null}
          {editable ? (
            <div className="review-actions">
              <Button icon={Check} variant="primary" disabled={busy || !value.trim()} onClick={() => review("confirmed")}>确认</Button>
              <Button icon={X} variant="danger-ghost" disabled={busy} onClick={() => review("rejected")}>不采用此值</Button>
            </div>
          ) : null}
        </div>
      </div>
    </article>
  );
}

export default function DraftsView({
  token,
  data,
  loading,
  error,
  refresh,
  notify,
  navigate,
  navigationParams,
}) {
  const [selectedId, setSelectedId] = useState(navigationParams.draft || null);
  const [draft, setDraft] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(null);
  const [detailRequestVersion, setDetailRequestVersion] = useState(0);
  const [actionBusy, setActionBusy] = useState(false);
  const summaries = data.drafts;

  useEffect(() => {
    const requestedId = navigationParams.draft;
    if (requestedId && summaries.some((item) => item.draft_id === requestedId)) {
      setSelectedId(requestedId);
      return;
    }
    if (summaries.length) {
      const fallbackId = summaries[0].draft_id;
      setSelectedId(fallbackId);
      if (requestedId !== fallbackId) navigate("drafts", { draft: fallbackId }, { replace: true });
    } else {
      setSelectedId(null);
    }
  }, [navigate, navigationParams.draft, summaries]);

  useEffect(() => {
    if (!selectedId) {
      setDraft(null);
      setDetailError(null);
      return undefined;
    }
    let active = true;
    const controller = new AbortController();
    setDraft(null);
    setDetailError(null);
    setDetailLoading(true);
    apiRequest(`/v1/drafts/${selectedId}`, { token, signal: controller.signal })
      .then((result) => {
        if (active && result.draft_id === selectedId) setDraft(result);
      })
      .catch((requestError) => {
        if (active && requestError.code !== "REQUEST_ABORTED") setDetailError(requestError);
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [detailRequestVersion, selectedId, token]);

  async function handleUpdated(updated, forceRefresh = false) {
    if (forceRefresh) setDetailRequestVersion((current) => current + 1);
    else if (updated) setDraft(updated);
    await refresh();
  }

  async function transition(action) {
    if (!draft) return;
    setActionBusy(true);
    try {
      const updated = await apiRequest(`/v1/drafts/${draft.draft_id}/${action}`, {
        token,
        method: "POST",
        body: { expected_version: draft.version },
      });
      setDraft(updated);
      await refresh();
      notify({ tone: updated.state === "needs_review" ? "error" : "success", message: action === "validate" ? (updated.state === "validated" ? "校验通过" : "校验发现待处理项") : "草稿已就绪" });
    } catch (error) {
      notify({ tone: "error", message: error.code === "CONFLICT" ? "草稿已更新，正在刷新" : error.message });
      if (error.code === "CONFLICT") setDetailRequestVersion((current) => current + 1);
    } finally {
      setActionBusy(false);
    }
  }

  const eventLabels = useMemo(() => ({
    draft_created: "创建草稿",
    field_reviewed: "字段审阅",
    validation_failed: "校验未通过",
    validation_passed: "校验通过",
    draft_ready: "标记就绪",
  }), []);

  if (error && !summaries.length) {
    return (
      <div className="inline-alert" role="alert">
        <AlertTriangle size={17} aria-hidden="true" />
        <span>草稿读取失败：{error.message}</span>
        <Button icon={RefreshCw} onClick={refresh}>重新读取</Button>
      </div>
    );
  }
  if (!summaries.length && !loading) return <EmptyState icon={ClipboardCheck} title="暂无草稿" />;

  return (
    <div className="draft-workspace">
      <aside className="draft-list" aria-label="草稿列表">
        <div className="draft-list__heading"><strong>草稿队列</strong><span>{summaries.length}</span></div>
        {summaries.map((item) => (
          <button key={item.draft_id} type="button" className={selectedId === item.draft_id ? "is-active" : ""} onClick={() => navigate("drafts", { draft: item.draft_id })}>
            <span><strong>{item.title}</strong><small>{formatDateTime(item.updated_at)}</small></span>
            <span><StatusTag value={item.state} /><ChevronRight size={16} /></span>
          </button>
        ))}
      </aside>
      <section className="draft-detail">
        {detailError ? (
          <div className="work-detail-state work-detail-state--error" role="alert">
            <AlertTriangle size={20} aria-hidden="true" />
            <strong>草稿详情读取失败</strong>
            <span>{detailError.message}</span>
            <Button icon={RefreshCw} onClick={() => setDetailRequestVersion((current) => current + 1)}>重新读取</Button>
          </div>
        ) : detailLoading || !draft ? (
          <div className="loading-line"><LoaderCircle className="spin" size={18} />加载草稿</div>
        ) : (
          <>
            <header className="draft-detail__header">
              <div><span className="mono">{draft.draft_id.slice(0, 12)}</span><h2>{summaries.find((item) => item.draft_id === draft.draft_id)?.title || "未命名草稿"}</h2><small>版本 {draft.version} · 更新 {formatDateTime(draft.updated_at)}</small></div>
              <div className="draft-detail__commands">
                <StatusTag value={draft.state} />
                <Button icon={RefreshCw} variant="ghost" size="icon" onClick={() => setDetailRequestVersion((current) => current + 1)} title="刷新草稿" aria-label="刷新草稿" />
                {["extracted", "needs_review"].includes(draft.state) ? <Button icon={ClipboardCheck} variant="primary" disabled={actionBusy} onClick={() => transition("validate")}>校验</Button> : null}
                {draft.state === "validated" ? <Button icon={CheckCircle2} variant="primary" disabled={actionBusy} onClick={() => transition("ready")}>完成审阅</Button> : null}
              </div>
            </header>
            {draft.validation_issues.length ? (
              <div className="validation-panel"><AlertTriangle size={17} /><div><strong>校验待处理</strong>{draft.validation_issues.map((issue) => <span key={`${issue.code}-${issue.field_name}`}>{issue.field_name ? `${issue.field_name}：` : ""}{issue.message}</span>)}</div></div>
            ) : null}
            <div className="review-fields">
              {draft.fields.map((field) => (
                <DraftFieldEditor key={field.field_name} field={field} draft={draft} token={token} onUpdated={handleUpdated} notify={notify} />
              ))}
            </div>
            <section className="audit-section">
              <div className="section-heading"><div><h2>审计记录</h2><span>{draft.audit_events.length} 条</span></div></div>
              <ol className="audit-list">
                {draft.audit_events.toReversed().map((event) => (
                  <li key={event.sequence}><i /><span><strong>{eventLabels[event.event_type] || event.event_type}</strong><small>{event.actor} · {formatDateTime(event.created_at)}</small></span><em>v{event.sequence}</em></li>
                ))}
              </ol>
            </section>
          </>
        )}
      </section>
    </div>
  );
}
