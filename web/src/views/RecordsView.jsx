import {
  BriefcaseBusiness,
  CreditCard,
  FileStack,
  ReceiptText,
} from "lucide-react";
import ReadonlyModulesView from "./ReadonlyModulesView";
import WorkItemsView from "./WorkItemsView";

const RECORD_AREAS = [
  { key: "procurement", label: "采购", icon: FileStack },
  { key: "payment", label: "付款", icon: CreditCard },
  { key: "bizcases", label: "BizCase", icon: BriefcaseBusiness },
  { key: "feeManagement", label: "费用", icon: ReceiptText },
];

function feeCount(data) {
  return [
    "travelApplications",
    "dailyExpenses",
    "travelReimbursements",
    "travelSubsidies",
  ].reduce((total, key) => total + data[key].total_count, 0);
}

export default function RecordsView({
  params,
  navigate,
  goBack,
  token,
  workspace,
  readonlyModules,
  notify,
  onWorkItemsSync,
  workItemsSyncing,
  onReadonlySync,
  readonlySyncing,
}) {
  const area = params.area || "procurement";
  const counts = {
    procurement: workspace.data.workItems.total_count,
    payment: readonlyModules.data.payment.total_count,
    bizcases: readonlyModules.data.bizcases.total_count,
    feeManagement: feeCount(readonlyModules.data),
  };

  return (
    <div className="records-view">
      <nav className="record-areas" aria-label="单据业务域">
        {RECORD_AREAS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            className={area === key ? "is-active" : ""}
            aria-current={area === key ? "page" : undefined}
            onClick={() => navigate("records", { area: key })}
          >
            <Icon size={17} aria-hidden="true" />
            <span>{label}</span>
            <strong>{counts[key]}</strong>
          </button>
        ))}
      </nav>

      {area === "procurement" ? (
        <WorkItemsView
          token={token}
          data={workspace.data}
          loading={workspace.loading}
          error={workspace.error}
          refresh={workspace.refresh}
          notify={notify}
          onSync={onWorkItemsSync}
          syncing={workItemsSyncing}
          navigationParams={params}
          navigate={navigate}
          goBack={goBack}
        />
      ) : (
        <ReadonlyModulesView
          data={readonlyModules.data}
          loading={readonlyModules.loading}
          error={readonlyModules.error}
          onReload={readonlyModules.refresh}
          onSync={onReadonlySync}
          syncing={readonlySyncing}
          activeArea={area}
          navigationParams={params}
          navigate={navigate}
          goBack={goBack}
          hideSystemNavigation
        />
      )}
    </div>
  );
}
