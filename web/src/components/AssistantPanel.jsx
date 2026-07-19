import { useState } from "react";
import {
  AlertTriangle,
  Bot,
  Clock3,
  RefreshCw,
  Send,
  Trash2,
} from "lucide-react";
import Button from "./Button";
import EmptyState from "./EmptyState";
import StatusTag from "./StatusTag";
import { formatDateTime } from "../lib/format";

function sourceLabel(brief) {
  if (brief.source === "model") return "模型分析";
  if (brief.provider_configured) return "模型失败，已本地排序";
  return "本地排序";
}

export default function AssistantPanel({ assistant, navigate }) {
  const [preference, setPreference] = useState("");
  const { data, loading, updating, error } = assistant;
  const brief = data.brief;
  const activePreference = data.preferences.at(-1);

  async function handleSubmit(event) {
    event.preventDefault();
    const normalized = preference.trim();
    if (!normalized || updating) return;
    try {
      await assistant.addPreference(normalized);
      setPreference("");
    } catch {
      // The existing briefing remains visible and the inline error provides feedback.
    }
  }

  async function handleClear() {
    try {
      await assistant.clearPreferences();
    } catch {
      // Preserve the existing briefing until the local request succeeds.
    }
  }

  async function handleGenerate() {
    try {
      await assistant.generate();
    } catch {
      // Preserve the existing briefing until the local request succeeds.
    }
  }

  return (
    <section className="content-section assistant-panel" aria-labelledby="assistant-panel-title">
      <div className="section-heading assistant-panel__heading">
        <div>
          <span className="assistant-panel__icon"><Bot size={16} aria-hidden="true" /></span>
          <h2 id="assistant-panel-title">催办助手</h2>
          <span>{brief ? formatDateTime(brief.generated_at) : "今日简报"}</span>
        </div>
        <Button
          icon={RefreshCw}
          variant="ghost"
          size="icon"
          onClick={handleGenerate}
          disabled={updating}
          className={updating ? "is-spinning" : ""}
          aria-label="刷新催办简报"
          title="刷新催办简报"
        />
      </div>

      {error ? (
        <div className="assistant-panel__alert" role="alert">
          <AlertTriangle size={15} aria-hidden="true" />
          <span>{error.message || "催办简报更新失败"}</span>
        </div>
      ) : null}

      {loading && !brief ? (
        <div className="assistant-panel__loading" aria-label="正在读取催办简报">
          <span /><span /><span />
        </div>
      ) : null}

      {!loading && brief ? (
        <>
          <div className="assistant-panel__summary">
            <p>{brief.summary}</p>
            <div>
              <StatusTag
                value={brief.source === "model" ? "succeeded" : "not_proposed"}
                label={sourceLabel(brief)}
              />
              {data.stale ? <span>快照待更新</span> : null}
            </div>
          </div>

          {brief.items.length ? (
            <ol className="assistant-priority-list">
              {brief.items.map((item, index) => (
                <li key={item.item_key}>
                  <button
                    type="button"
                    onClick={() => navigate(item.destination, item.target)}
                  >
                    <span className="assistant-priority-list__rank">{index + 1}</span>
                    <span className="assistant-priority-list__main">
                      <strong>{item.title}</strong>
                      <small>{item.category} · {item.reference}</small>
                      <em>{item.reason}</em>
                    </span>
                    <span className="assistant-priority-list__wait">
                      <Clock3 size={13} aria-hidden="true" />
                      <strong>{item.waiting_days == null ? "未知" : `${item.waiting_days} 天`}</strong>
                      <small>{item.waiting_basis === "submission_date_estimate" ? "日期估算" : "等待中"}</small>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState icon={Bot} title="暂无需要催办的单据" />
          )}

          <form className="assistant-preference-form" onSubmit={handleSubmit}>
            <label className="sr-only" htmlFor="assistant-preference">优先级偏好</label>
            <input
              id="assistant-preference"
              value={preference}
              onChange={(event) => setPreference(event.target.value)}
              maxLength={500}
              placeholder="类目、项目或单据编号"
              disabled={updating}
            />
            <Button
              icon={Send}
              variant="primary"
              size="icon"
              type="submit"
              disabled={!preference.trim() || updating}
              aria-label="提交优先级偏好"
              title="提交优先级偏好"
            />
          </form>

          {activePreference ? (
            <div className="assistant-preference-current">
              <span><strong>当前偏好</strong>{activePreference.text}</span>
              <Button
                icon={Trash2}
                variant="ghost"
                size="icon"
                onClick={handleClear}
                disabled={updating}
                aria-label="清空优先级偏好"
                title="清空优先级偏好"
              />
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
