export const FEE_MODULE_KEYS = [
  "dailyExpenses",
  "travelApplications",
  "travelReimbursements",
  "travelSubsidies",
];

export const READONLY_MODULE_BY_KEY = {
  payment: "payment",
  bizcases: "bizcase",
  travelApplications: "travel_application",
  dailyExpenses: "daily_expense",
  travelReimbursements: "travel_reimbursement",
  travelSubsidies: "travel_subsidy",
};

const PROCUREMENT_WORKFLOWS = new Set([
  "purchase_requisition",
  "procurement_contract",
  "procurement_order",
  "cost_confirmation",
  "check_acceptance",
]);

export function resolveProcurementSyncScope(params = {}) {
  return PROCUREMENT_WORKFLOWS.has(params.workflow) ? params.workflow : null;
}

export function resolveReadonlySyncScope(params = {}, data = {}) {
  if (params.area === "payment") return READONLY_MODULE_BY_KEY.payment;
  if (params.area === "bizcases") return READONLY_MODULE_BY_KEY.bizcases;
  if (params.area !== "feeManagement") return null;

  const requested = READONLY_MODULE_BY_KEY[params.module];
  if (requested && FEE_MODULE_KEYS.includes(params.module)) return requested;
  const activeKey = FEE_MODULE_KEYS.find((key) => data[key]?.total_count > 0)
    || FEE_MODULE_KEYS[0];
  return READONLY_MODULE_BY_KEY[activeKey];
}
