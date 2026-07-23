import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeNavigationTarget,
  parseHash,
  serializeLocation,
} from "./navigation.js";

test("parses canonical views and each view's supported query", () => {
  assert.deepEqual(parseHash("#/overview?item=ignored"), {
    view: "overview",
    params: {},
  });
  assert.deepEqual(parseHash("#/records?area=payment&module=payment&item=PAY-1&workflow=approve&extra=no"), {
    view: "records",
    params: {
      area: "payment",
      module: "payment",
      item: "PAY-1",
      workflow: "approve",
    },
  });
  assert.deepEqual(parseHash("#/materials?material=MAT-8&draft=ignored"), {
    view: "materials",
    params: { material: "MAT-8" },
  });
  assert.deepEqual(parseHash("#/drafts?draft=DRAFT-3&material=ignored"), {
    view: "drafts",
    params: { draft: "DRAFT-3" },
  });
});

test("accepts every records area", () => {
  for (const area of ["procurement", "payment", "bizcases", "feeManagement"]) {
    assert.deepEqual(parseHash(`#/records?area=${area}`), {
      view: "records",
      params: { area },
    });
  }
});

test("serializes in a stable canonical form", () => {
  assert.equal(serializeLocation({
    view: "records",
    params: { workflow: "approve", item: "PAY-1", module: "payment", area: "payment" },
  }), "#/records?area=payment&module=payment&item=PAY-1&workflow=approve");
  assert.equal(serializeLocation({ view: "overview", params: {} }), "#/overview");
});

test("round-trips encoded query values", () => {
  const location = {
    view: "records",
    params: {
      area: "bizcases",
      module: "商机/查询",
      item: "BC 001&测试",
      workflow: "审批?下一步",
    },
  };
  const hash = serializeLocation(location);

  assert.match(hash, /%E5%95%86%E6%9C%BA%2F%E6%9F%A5%E8%AF%A2/);
  assert.doesNotMatch(hash, /商机|测试|下一步/);
  assert.deepEqual(parseHash(hash), location);
});

test("drops invalid parameters and falls back safely for invalid locations", () => {
  assert.deepEqual(normalizeNavigationTarget({
    view: "records",
    params: {
      area: "sales",
      module: null,
      item: undefined,
      workflow: [],
      extra: "ignored",
    },
  }), { view: "records", params: {} });
  assert.deepEqual(parseHash("#/unknown?area=payment"), { view: "overview", params: {} });
  assert.deepEqual(parseHash("#/%E0%A4%A"), { view: "overview", params: {} });
  assert.deepEqual(parseHash(null), { view: "overview", params: {} });
});

test("maps legacy work-items hashes to procurement records", () => {
  assert.deepEqual(parseHash("#/work-items?item=PO%2F2026%2F8&workflow=procurement_order"), {
    view: "records",
    params: {
      area: "procurement",
      item: "PO/2026/8",
      workflow: "procurement_order",
    },
  });
  assert.equal(
    serializeLocation({ view: "work-items", params: { item: "PO-8" } }),
    "#/records?area=procurement&item=PO-8",
  );
});

test("maps legacy readonly-modules hashes to the matching records area", () => {
  assert.deepEqual(parseHash("#readonly-modules?module=bizcases&item=BC-9"), {
    view: "records",
    params: { area: "bizcases", module: "bizcases", item: "BC-9" },
  });
  assert.deepEqual(parseHash("#/readonly-modules?module=dailyExpenses"), {
    view: "records",
    params: { area: "feeManagement", module: "dailyExpenses" },
  });
  assert.deepEqual(parseHash("#/readonly-modules"), {
    view: "records",
    params: { area: "payment" },
  });
});
