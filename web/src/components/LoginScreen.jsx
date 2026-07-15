import { ArrowRight, LockKeyhole, Workflow } from "lucide-react";
import { useState } from "react";
import Button from "./Button";

export default function LoginScreen({ onLogin, checking = false }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await onLogin(username.trim(), password);
      setPassword("");
    } catch (requestError) {
      setError(requestError.message || "登录失败");
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-layout">
      <section className="login-brand" aria-label="统一流程中心">
        <div className="brand-lockup brand-lockup--large">
          <span className="brand-mark"><Workflow size={22} aria-hidden="true" /></span>
          <div>
            <strong>统一流程中心</strong>
            <span>iSStech Workflow Center</span>
          </div>
        </div>
        <div className="login-brand__signal" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
      </section>
      <section className="login-form-region">
        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-form__heading">
            <LockKeyhole size={21} aria-hidden="true" />
            <div>
              <h1>连接工作区</h1>
              <p>使用 iPSA 账号进入本地流程中心</p>
            </div>
          </div>
          <label>
            <span>账号</span>
            <input
              name="username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              disabled={submitting || checking}
              required
              autoFocus
            />
          </label>
          <label>
            <span>密码</span>
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={submitting || checking}
              required
            />
          </label>
          {error ? <div className="form-error" role="alert">{error}</div> : null}
          <Button
            type="submit"
            variant="primary"
            icon={ArrowRight}
            disabled={submitting || checking || !username.trim() || !password}
          >
            {submitting || checking ? "正在连接" : "进入工作区"}
          </Button>
        </form>
      </section>
    </main>
  );
}
