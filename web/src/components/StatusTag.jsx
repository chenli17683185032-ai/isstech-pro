import { statusLabel } from "../lib/format";

const toneMap = {
  ready: "success",
  validated: "success",
  succeeded: "success",
  confirmed: "success",
  extracted: "info",
  running: "info",
  needs_review: "warning",
  pending: "warning",
  failed: "danger",
  rejected: "danger",
  not_proposed: "neutral",
};

export default function StatusTag({ value, label }) {
  const tone = toneMap[value] || "neutral";
  return <span className={`status-tag status-tag--${tone}`}>{label || statusLabel(value)}</span>;
}
