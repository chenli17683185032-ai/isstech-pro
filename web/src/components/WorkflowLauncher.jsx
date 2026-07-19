import {
  BriefcaseBusiness,
  CircleDollarSign,
  ExternalLink,
  FileSpreadsheet,
  PlaneTakeoff,
  Plus,
  ReceiptText,
  WalletCards,
  X,
} from "lucide-react";
import { useRef } from "react";
import Button from "./Button";

const IPSA_ORIGIN = "http://ipsapro.isstech.com";
const WORKFLOW_GROUPS = [
  {
    label: "采购管理",
    items: [
      {
        label: "采购立项申请",
        system: "采购管理",
        icon: FileSpreadsheet,
        href: `${IPSA_ORIGIN}/WebTP/PurchaseRequisition/ProjectSelection`,
      },
    ],
  },
  {
    label: "付款与商机",
    items: [
      {
        label: "付款申请",
        system: "付款管理",
        icon: CircleDollarSign,
        href: `${IPSA_ORIGIN}/WebPMS/selector/selecttype`,
      },
      {
        label: "BizCase",
        system: "BizCase 管理",
        icon: BriefcaseBusiness,
        href: `${IPSA_ORIGIN}/WebPMP/Main.aspx?thUrl=28%5emcontrol%5eiss.psa.webui.bizcasemanage.bizcasequery.list%5ePMP%2fBuiltItemM%2fBizcase_title.gif%5e0&url=iss.psa.webui.bizcasemanage.bizcaseapply.list&urltype=mcontrol&helpmenucode=280101`,
      },
    ],
  },
  {
    label: "费用管理",
    items: [
      {
        label: "出差申请",
        system: "费用管理",
        icon: PlaneTakeoff,
        href: `${IPSA_ORIGIN}/WebPSAOA/Fee/FeeApply/EvectionLoan/List.aspx?helpmenucode=92`,
      },
      {
        label: "日常报销申请",
        system: "费用管理",
        icon: ReceiptText,
        href: `${IPSA_ORIGIN}/WebPSAOA/Fee/FeeApply/DailyExpense/List.aspx?helpmenucode=90`,
      },
      {
        label: "差旅报销申请",
        system: "费用管理",
        icon: ReceiptText,
        href: `${IPSA_ORIGIN}/WebPSAOA/Fee/FeeApply/EvectionSubsidy/List.aspx?helpmenucode=93`,
      },
      {
        label: "差旅补助申请",
        system: "费用管理",
        icon: WalletCards,
        href: `${IPSA_ORIGIN}/WebPSAOA/Fee/FeeApply/EvectionSubsidy2/List.aspx?helpmenucode=112`,
      },
    ],
  },
];

export default function WorkflowLauncher() {
  const dialogRef = useRef(null);
  const returnFocusRef = useRef(null);

  function openLauncher() {
    returnFocusRef.current = document.activeElement;
    dialogRef.current?.showModal();
  }

  function closeLauncher() {
    dialogRef.current?.close();
  }

  return (
    <>
      <Button
        className="workflow-launcher-trigger"
        icon={Plus}
        variant="primary"
        onClick={openLauncher}
        title="发起流程"
      >
        发起流程
      </Button>
      <dialog
        ref={dialogRef}
        className="workflow-launcher-dialog"
        aria-labelledby="workflow-launcher-title"
        onClose={() => returnFocusRef.current?.focus?.()}
      >
        <header className="workflow-launcher__header">
          <div>
            <h2 id="workflow-launcher-title">发起流程</h2>
            <span>IPSA</span>
          </div>
          <Button
            autoFocus
            icon={X}
            variant="ghost"
            size="icon"
            onClick={closeLauncher}
            title="关闭"
            aria-label="关闭发起流程"
          />
        </header>
        <div className="workflow-launcher__body">
          {WORKFLOW_GROUPS.map((group) => (
            <section className="workflow-launcher__group" key={group.label}>
              <h3>{group.label}</h3>
              <div className="workflow-launcher__links">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <a
                      className="workflow-launcher__link"
                      href={item.href}
                      key={item.label}
                      target="_blank"
                      rel="noopener noreferrer"
                      referrerPolicy="no-referrer"
                      aria-label={`${item.label}，在 IPSA 中发起`}
                      onClick={closeLauncher}
                    >
                      <span className="workflow-launcher__icon">
                        <Icon size={18} aria-hidden="true" />
                      </span>
                      <span className="workflow-launcher__label">
                        <strong>{item.label}</strong>
                        <small>{item.system}</small>
                      </span>
                      <ExternalLink size={16} aria-hidden="true" />
                    </a>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      </dialog>
    </>
  );
}
