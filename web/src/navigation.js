const VIEWS = new Set(["overview", "records", "materials", "drafts"]);
const RECORD_AREAS = new Set(["procurement", "payment", "bizcases", "feeManagement"]);
const VIEW_PARAMS = {
  overview: [],
  records: ["area", "module", "item", "workflow"],
  materials: ["material"],
  drafts: ["draft"],
};
const FEE_MODULES = new Set([
  "travelApplications",
  "dailyExpenses",
  "travelReimbursements",
  "travelSubsidies",
]);

function defaultLocation() {
  return { view: "overview", params: {} };
}

function cleanParam(value) {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const normalized = String(value).trim();
  return normalized || null;
}

function readonlyArea(module) {
  if (module === "bizcase" || module === "bizcases") return "bizcases";
  if (FEE_MODULES.has(module)) return "feeManagement";
  return "payment";
}

export function normalizeNavigationTarget(target) {
  if (!target || typeof target !== "object" || Array.isArray(target)) {
    return defaultLocation();
  }

  const requestedView = cleanParam(target.view);
  const isWorkItems = requestedView === "work-items";
  const isReadonlyModules = requestedView === "readonly-modules";
  const view = isWorkItems || isReadonlyModules ? "records" : requestedView;
  if (!VIEWS.has(view)) return defaultLocation();

  const source = target.params && typeof target.params === "object" && !Array.isArray(target.params)
    ? target.params
    : {};
  const params = {};
  for (const key of VIEW_PARAMS[view]) {
    const value = cleanParam(source[key]);
    if (value !== null) params[key] = value;
  }

  if (view === "records" && params.area && !RECORD_AREAS.has(params.area)) {
    delete params.area;
  }
  if (isWorkItems && !params.area) params.area = "procurement";
  if (isReadonlyModules && !params.area) params.area = readonlyArea(params.module);

  return { view, params };
}

export function parseHash(hash) {
  if (typeof hash !== "string") return defaultLocation();

  const value = hash.startsWith("#") ? hash.slice(1) : hash;
  const separator = value.indexOf("?");
  const rawPath = separator === -1 ? value : value.slice(0, separator);
  const rawQuery = separator === -1 ? "" : value.slice(separator + 1);
  let view;
  try {
    view = decodeURIComponent(rawPath.replace(/^\/+|\/+$/g, ""));
  } catch {
    return defaultLocation();
  }

  return normalizeNavigationTarget({
    view,
    params: Object.fromEntries(new URLSearchParams(rawQuery)),
  });
}

export function serializeLocation(target) {
  const location = normalizeNavigationTarget(target);
  const search = new URLSearchParams();
  for (const key of VIEW_PARAMS[location.view]) {
    if (location.params[key]) search.set(key, location.params[key]);
  }
  const query = search.toString();
  return `#/${location.view}${query ? `?${query}` : ""}`;
}
