import assert from "node:assert/strict";
import test from "node:test";

import {
  resolveProcurementSyncScope,
  resolveReadonlySyncScope,
} from "./syncScope.js";

test("limits procurement sync to a valid detail workflow", () => {
  assert.equal(resolveProcurementSyncScope({ workflow: "procurement_order" }), "procurement_order");
  assert.equal(resolveProcurementSyncScope({ workflow: "unknown" }), null);
  assert.equal(resolveProcurementSyncScope({}), null);
});

test("maps each readonly business area to one backend module", () => {
  assert.equal(resolveReadonlySyncScope({ area: "payment" }), "payment");
  assert.equal(resolveReadonlySyncScope({ area: "bizcases" }), "bizcase");
  assert.equal(resolveReadonlySyncScope({
    area: "feeManagement",
    module: "travelSubsidies",
  }), "travel_subsidy");
});

test("uses the first non-empty fee module when the route has no child scope", () => {
  const data = {
    dailyExpenses: { total_count: 0 },
    travelApplications: { total_count: 8 },
    travelReimbursements: { total_count: 2 },
    travelSubsidies: { total_count: 0 },
  };

  assert.equal(resolveReadonlySyncScope({ area: "feeManagement" }, data), "travel_application");
  assert.equal(resolveReadonlySyncScope({ area: "procurement" }, data), null);
});
