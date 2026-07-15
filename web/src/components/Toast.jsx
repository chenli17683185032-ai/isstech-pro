import { CheckCircle2, X, XCircle } from "lucide-react";
import Button from "./Button";

export default function Toast({ toast, onClose }) {
  if (!toast) return null;
  const Icon = toast.tone === "error" ? XCircle : CheckCircle2;
  return (
    <div className={`toast toast--${toast.tone || "success"}`} role="status">
      <Icon size={18} aria-hidden="true" />
      <span>{toast.message}</span>
      <Button
        icon={X}
        variant="ghost"
        size="icon"
        aria-label="关闭通知"
        title="关闭通知"
        onClick={onClose}
      />
    </div>
  );
}
