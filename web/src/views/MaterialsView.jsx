import { FilePlus2, FileText, FolderInput, LoaderCircle, UploadCloud } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { apiRequest } from "../api";
import Button from "../components/Button";
import EmptyState from "../components/EmptyState";
import StatusTag from "../components/StatusTag";
import { formatBytes, formatDateTime } from "../lib/format";

const supportedMimes = new Set([
  "application/pdf",
  "text/plain",
  "application/json",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
]);

export default function MaterialsView({ token, data, loading, refresh, notify, navigate }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [activeMaterial, setActiveMaterial] = useState(null);
  const latestExtraction = useMemo(() => {
    const map = new Map();
    for (const extraction of data.extractions) {
      if (!map.has(extraction.material_id)) map.set(extraction.material_id, extraction);
    }
    return map;
  }, [data.extractions]);
  const draftByExtraction = useMemo(
    () => new Map(data.drafts.map((draft) => [draft.extraction_id, draft])),
    [data.drafts],
  );

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setUploading(true);
    let completed = 0;
    try {
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        await apiRequest("/v1/materials", { token, method: "POST", body: form });
        completed += 1;
      }
      await refresh();
      notify({ tone: "success", message: `已入库 ${completed} 份材料` });
    } catch (error) {
      notify({ tone: "error", message: `${completed} 份成功；${error.message}` });
      await refresh();
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function startWorkflow(material) {
    setActiveMaterial(material.id);
    try {
      let extraction = latestExtraction.get(material.id);
      if (!extraction || extraction.status === "failed") {
        extraction = await apiRequest(`/v1/materials/${material.id}/extractions`, {
          token,
          method: "POST",
          body: { provider: "local_rules", confidence_threshold: 0.85 },
        });
      }
      let draft = draftByExtraction.get(extraction.extraction_id);
      if (!draft) {
        draft = await apiRequest(`/v1/extractions/${extraction.extraction_id}/drafts`, {
          token,
          method: "POST",
        });
      }
      await refresh();
      notify({ tone: "success", message: "材料已进入审阅队列" });
      navigate("drafts", { draft: draft.draft_id });
    } catch (error) {
      notify({ tone: "error", message: error.message || "抽取失败" });
      await refresh();
    } finally {
      setActiveMaterial(null);
    }
  }

  return (
    <div className="view-stack">
      <section
        className={dragging ? "drop-zone is-dragging" : "drop-zone"}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          uploadFiles(event.dataTransfer.files);
        }}
      >
        <span className="drop-zone__icon"><UploadCloud size={23} /></span>
        <div><strong>项目材料收件箱</strong><span>PDF、DOCX、XLSX、PPTX、TXT、JSON</span></div>
        <input ref={inputRef} type="file" multiple hidden onChange={(event) => uploadFiles(event.target.files)} />
        <Button icon={FolderInput} variant="primary" disabled={uploading} onClick={() => inputRef.current?.click()}>
          {uploading ? "正在入库" : "选择文件"}
        </Button>
      </section>

      <section className="content-section">
        <div className="section-heading">
          <div><h2>材料库</h2><span>{data.materials.length} 份</span></div>
        </div>
        {data.materials.length ? (
          <div className="table-wrap">
            <table className="data-table material-table">
              <thead><tr><th>文件</th><th>大小</th><th>入库</th><th>材料状态</th><th>流程状态</th><th /></tr></thead>
              <tbody>
                {data.materials.map((material) => {
                  const extraction = latestExtraction.get(material.id);
                  const draft = extraction ? draftByExtraction.get(extraction.extraction_id) : null;
                  const supported = supportedMimes.has(material.detected_mime_type);
                  const busy = activeMaterial === material.id;
                  return (
                    <tr key={material.id}>
                      <td><span className="file-cell"><FileText size={18} /><span><strong>{material.original_name}</strong><small>{material.detected_mime_type}</small></span></span></td>
                      <td>{formatBytes(material.size_bytes)}</td>
                      <td>{formatDateTime(material.created_at)}</td>
                      <td><StatusTag value={material.status} /></td>
                      <td>{draft ? <StatusTag value={draft.state} /> : extraction ? <StatusTag value={extraction.status} /> : <span className="muted">未抽取</span>}</td>
                      <td className="align-right">
                        <Button
                          icon={busy ? LoaderCircle : draft ? FilePlus2 : FileText}
                          variant={draft ? "ghost" : "secondary"}
                          disabled={busy || !supported}
                          className={busy ? "is-spinning" : ""}
                          onClick={() => startWorkflow(material)}
                          title={!supported ? "当前格式需要 OCR 或人工处理" : undefined}
                        >
                          {busy ? "处理中" : draft ? "打开草稿" : extraction?.status === "failed" ? "重新识别" : "识别并生成草稿"}
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : loading ? (
          <div className="loading-line"><LoaderCircle className="spin" size={18} />加载材料</div>
        ) : (
          <EmptyState icon={FilePlus2} title="暂无项目材料" />
        )}
      </section>
    </div>
  );
}
