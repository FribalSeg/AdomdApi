const state = {
  rows: [],
  selectedSavedId: null,
  currentPivotConfig: {},
  modelHierarchy: [],
  selectedFields: {
    rows: [],
    cols: [],
    vals: [],
    filters: [],
  },
  dragContext: null,
};

const BUCKET_KEYS = ["rows", "cols", "vals", "filters"];
const FILTER_OPERATORS = [
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "does not contains" },
  { value: "starts_with", label: "starts with" },
  { value: "not_starts_with", label: "does not starts with" },
  { value: "in", label: "in" },
  { value: "not_in", label: "not in" },
  { value: "is", label: "is" },
  { value: "is_not", label: "is not" },
  { value: "is_blank", label: "is blank" },
  { value: "is_not_blank", label: "is not blank" },
];
const FILTER_OPERATORS_WITHOUT_VALUES = new Set(["is_blank", "is_not_blank"]);

const refs = {
  languageSelect: document.getElementById("languageSelect"),
  queryInput: document.getElementById("queryInput"),
  saveName: document.getElementById("saveName"),
  fieldSearch: document.getElementById("fieldSearch"),
  hierarchyTree: document.getElementById("hierarchyTree"),
  generateCodeBtn: document.getElementById("generateCodeBtn"),
  runQueryBtn: document.getElementById("runQueryBtn"),
  saveQueryBtn: document.getElementById("saveQueryBtn"),
  updateQueryBtn: document.getElementById("updateQueryBtn"),
  clearEditorBtn: document.getElementById("clearEditorBtn"),
  copyApiCodeBtn: document.getElementById("copyApiCodeBtn"),
  copyConfigBtn: document.getElementById("copyConfigBtn"),
  loadHierarchyBtn: document.getElementById("loadHierarchyBtn"),
  loadDmvsBtn: document.getElementById("loadDmvsBtn"),
  loadDiscoverBtn: document.getElementById("loadDiscoverBtn"),
  refreshSavedBtn: document.getElementById("refreshSavedBtn"),
  deleteSavedBtn: document.getElementById("deleteSavedBtn"),
  rowsBucket: document.getElementById("rowsBucket"),
  colsBucket: document.getElementById("colsBucket"),
  valsBucket: document.getElementById("valsBucket"),
  filtersBucket: document.getElementById("filtersBucket"),
  savedList: document.getElementById("savedList"),
  metadataOutput: document.getElementById("metadataOutput"),
  statusOutput: document.getElementById("statusOutput"),
  tableOutput: document.getElementById("tableOutput"),
  pivotArea: document.getElementById("pivotArea"),
};

function setStatus(message, isError = false) {
  refs.statusOutput.textContent = message;
  refs.statusOutput.style.color = isError ? "#9f2a2a" : "#6c6657";
}

function safeJson(value) {
  return JSON.stringify(value, null, 2);
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : {};

  if (!response.ok) {
    throw new Error(data.detail || data.error || response.statusText);
  }

  return data;
}

function renderResultsTable(rows) {
  if (!rows.length) {
    refs.tableOutput.innerHTML = "<p>No rows returned.</p>";
    return;
  }

  const cols = Object.keys(rows[0]);
  const head = cols.map((col) => `<th>${escapeHtml(col)}</th>`).join("");
  const body = rows
    .slice(0, 300)
    .map((row) => {
      const tds = cols
        .map((col) => `<td>${escapeHtml(String(row[col] ?? ""))}</td>`)
        .join("");
      return `<tr>${tds}</tr>`;
    })
    .join("");

  refs.tableOutput.innerHTML = `
    <table class="table-grid">
      <thead><tr>${head}</tr></thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

function fieldKey(field) {
  return `${field.table}.${field.name}.${field.kind}`;
}

function addFieldToBucket(bucket, field) {
  const current = state.selectedFields[bucket];
  const normalizedField = normalizeFieldForBucket(field, bucket);
  const key = fieldKey(normalizedField);
  if (current.some((item) => fieldKey(item) === key)) {
    return;
  }
  current.push(normalizedField);
  renderBuckets();
}

function normalizeFieldForBucket(field, bucket) {
  const normalized = {
    table: field.table,
    name: field.name,
    kind: field.kind,
  };

  if (bucket === "filters") {
    normalized.operator = field.operator || "in";
    normalized.values = Array.isArray(field.values) ? field.values : [];
  }

  return normalized;
}

function canPlaceFieldInBucket(field, bucket) {
  if (field.kind === "measure") {
    return bucket === "vals";
  }
  return bucket !== "vals";
}

function bucketElement(bucket) {
  const map = {
    rows: refs.rowsBucket,
    cols: refs.colsBucket,
    vals: refs.valsBucket,
    filters: refs.filtersBucket,
  };
  return map[bucket];
}

function normalizeDropIndex(bucket, index) {
  const max = state.selectedFields[bucket].length;
  if (index < 0) {
    return 0;
  }
  if (index > max) {
    return max;
  }
  return index;
}

function dropIndexForBucketEvent(bucket, event) {
  const container = bucketElement(bucket);
  if (!container) {
    return state.selectedFields[bucket].length;
  }

  const chips = Array.from(container.querySelectorAll(".bucket-chip"));
  if (!chips.length) {
    return 0;
  }

  const eventTarget = event.target.closest(".bucket-chip");
  if (eventTarget && eventTarget.dataset.index) {
    const hoveredIndex = Number(eventTarget.dataset.index);
    const rect = eventTarget.getBoundingClientRect();
    const useAfter = event.clientY > rect.top + rect.height / 2;
    return hoveredIndex + (useAfter ? 1 : 0);
  }

  for (let index = 0; index < chips.length; index += 1) {
    const chip = chips[index];
    const rect = chip.getBoundingClientRect();
    if (event.clientY < rect.top + rect.height / 2) {
      return index;
    }
  }

  return chips.length;
}

function moveFieldBetweenBuckets(sourceBucket, sourceIndex, targetBucket, targetIndex) {
  const sourceItems = state.selectedFields[sourceBucket];
  if (!sourceItems || sourceIndex < 0 || sourceIndex >= sourceItems.length) {
    return;
  }

  const movingField = sourceItems[sourceIndex];
  if (!canPlaceFieldInBucket(movingField, targetBucket)) {
    setStatus(`Cannot place ${movingField.kind} in ${targetBucket}.`, true);
    return;
  }

  sourceItems.splice(sourceIndex, 1);

  const destination = state.selectedFields[targetBucket];
  let finalIndex = normalizeDropIndex(targetBucket, targetIndex);
  if (sourceBucket === targetBucket && sourceIndex < finalIndex) {
    finalIndex -= 1;
  }

  destination.splice(finalIndex, 0, normalizeFieldForBucket(movingField, targetBucket));
  renderBuckets();
}

function addFieldAtBucket(bucket, field, targetIndex) {
  if (!canPlaceFieldInBucket(field, bucket)) {
    setStatus(`Cannot place ${field.kind} in ${bucket}.`, true);
    return;
  }

  const destination = state.selectedFields[bucket];
  const normalizedField = normalizeFieldForBucket(field, bucket);
  const key = fieldKey(normalizedField);
  if (destination.some((item) => fieldKey(item) === key)) {
    return;
  }

  const finalIndex = normalizeDropIndex(bucket, targetIndex);
  destination.splice(finalIndex, 0, normalizedField);
  renderBuckets();
}

function removeFieldFromBucket(bucket, key) {
  state.selectedFields[bucket] = state.selectedFields[bucket].filter(
    (item) => fieldKey(item) !== key
  );
  renderBuckets();
}

function targetBucketForField(field) {
  if (field.kind === "measure") {
    return "vals";
  }
  return "rows";
}

function renderBuckets() {
  BUCKET_KEYS.forEach((bucket) => {
    const element = bucketElement(bucket);
    const items = state.selectedFields[bucket];
    if (!items.length) {
      element.innerHTML = "";
      return;
    }

    element.innerHTML = items
      .map((item, index) => {
        const key = fieldKey(item);
        const title = `${escapeHtml(item.table)}[${escapeHtml(item.name)}]`;
        if (bucket !== "filters") {
          return `<div class="bucket-chip" draggable="true" data-item-bucket="${bucket}" data-item-index="${index}" data-index="${index}">${title}<button data-remove-bucket="${bucket}" data-remove-key="${encodeURIComponent(
            key
          )}" title="Remove">x</button></div>`;
        }

        const filterText = Array.isArray(item.values) ? item.values.join(", ") : "";
        const selectedOperator = item.operator || "in";
        const operatorOptions = FILTER_OPERATORS.map((operator) => {
          const selected = operator.value === selectedOperator ? "selected" : "";
          return `<option value="${operator.value}" ${selected}>${operator.label}</option>`;
        }).join("");
        const valuesDisabled = FILTER_OPERATORS_WITHOUT_VALUES.has(selectedOperator)
          ? "disabled"
          : "";
        return `<div class="bucket-chip filter-chip" draggable="true" data-item-bucket="${bucket}" data-item-index="${index}" data-index="${index}">
          <div class="filter-chip-title">${title}<button data-remove-bucket="${bucket}" data-remove-key="${encodeURIComponent(
            key
          )}" title="Remove">x</button></div>
          <select class="filter-operator-select" data-filter-operator-index="${index}">${operatorOptions}</select>
          <input class="filter-values-input" data-filter-input-index="${index}" placeholder="Values: ex BR, PT" value="${escapeHtml(filterText)}" ${valuesDisabled} />
        </div>`;
      })
      .join("");
  });
}

function renderHierarchyTree() {
  const searchText = refs.fieldSearch.value.trim().toLowerCase();
  if (!state.modelHierarchy.length) {
    refs.hierarchyTree.innerHTML = "<p>No hierarchy loaded.</p>";
    return;
  }

  const tableBlocks = state.modelHierarchy
    .map((table) => {
      const tableName = String(table.table_name || "");
      const columns = Array.isArray(table.columns) ? table.columns : [];
      const measures = Array.isArray(table.measures) ? table.measures : [];

      const filteredColumns = columns.filter((col) => {
        const text = `${tableName} ${col.name}`.toLowerCase();
        return !searchText || text.includes(searchText);
      });

      const filteredMeasures = measures.filter((measure) => {
        const text = `${tableName} ${measure.name}`.toLowerCase();
        return !searchText || text.includes(searchText);
      });

      const tableMatch = !searchText || tableName.toLowerCase().includes(searchText);
      if (!tableMatch && !filteredColumns.length && !filteredMeasures.length) {
        return "";
      }

      return `
        <div class="hier-table">
          <div class="hier-title">${escapeHtml(tableName)}</div>
          <div class="hier-group">
            <div class="hier-label">Columns</div>
            <div class="hier-fields">
              ${filteredColumns
                .map(
                  (col) =>
                    `<button class="field-chip" draggable="true" data-field-kind="column" data-field-table="${escapeHtml(
                      encodeURIComponent(tableName)
                    )}" data-field-name="${escapeHtml(
                      encodeURIComponent(col.name)
                    )}">- ${escapeHtml(col.name)}</button>`
                )
                .join("")}
            </div>
          </div>
          <div class="hier-group">
            <div class="hier-label">Measures</div>
            <div class="hier-fields">
              ${filteredMeasures
                .map(
                  (measure) =>
                    `<button class="field-chip measure-chip" draggable="true" data-field-kind="measure" data-field-table="${escapeHtml(
                      encodeURIComponent(tableName)
                    )}" data-field-name="${escapeHtml(
                      encodeURIComponent(measure.name)
                    )}">- [M] ${escapeHtml(measure.name)}</button>`
                )
                .join("")}
            </div>
          </div>
        </div>
      `;
    })
    .join("");

  refs.hierarchyTree.innerHTML = tableBlocks || "<p>No fields match your filter.</p>";
}

function formatColumnRef(item) {
  return `'${item.table}'[${item.name}]`;
}

function formatMeasureRef(item) {
  return `[${item.name}]`;
}

function uniqueByRef(items, formatter) {
  const seen = new Set();
  const output = [];
  for (const item of items) {
    const ref = formatter(item);
    if (!seen.has(ref)) {
      seen.add(ref);
      output.push(item);
    }
  }
  return output;
}

function mapBuilderStateToDaxBuildPayload() {
  const rowItems = uniqueByRef(state.selectedFields.rows, formatColumnRef);
  const colItems = uniqueByRef(state.selectedFields.cols, formatColumnRef);
  const valItems = uniqueByRef(state.selectedFields.vals, formatMeasureRef);
  const filterItems = uniqueByRef(state.selectedFields.filters, formatColumnRef);

  return {
    rows: rowItems.map(formatColumnRef),
    columns: colItems.map(formatColumnRef),
    values_measures: valItems.map(formatMeasureRef),
    filters: filterItems.map((item) => ({
      column: formatColumnRef(item),
      operator: item.operator || "in",
      values: Array.isArray(item.values) ? item.values : [],
    })),
  };
}

async function generateDaxQueryFromBuilder() {
  const payload = mapBuilderStateToDaxBuildPayload();
  const hasAnyAxis = payload.rows.length || payload.columns.length;
  const hasAnyMeasure = payload.values_measures.length;

  if (!hasAnyAxis && !hasAnyMeasure) {
    setStatus("Select at least one column or measure from hierarchy.", true);
    return;
  }

  const hasFiltersWithoutValues = payload.filters.some(
    (item) => !FILTER_OPERATORS_WITHOUT_VALUES.has(item.operator || "in") && !item.values.length
  );
  if (hasFiltersWithoutValues) {
    setStatus("Fill filter values before generating DAX.", true);
    return;
  }

  setStatus("Building DAX query...");

  try {
    const data = await apiRequest("/api/dax/build", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    refs.languageSelect.value = "dax";
    refs.queryInput.value = data.query_text || "";
    setStatus("DAX code generated from backend dax_query_builder.");
  } catch (error) {
    setStatus(`DAX builder failed: ${error.message}`, true);
  }
}

async function loadHierarchy() {
  refs.hierarchyTree.innerHTML = "<p>Loading hierarchy...</p>";
  try {
    const data = await apiRequest("/api/metadata/hierarchy");
    if (data.status !== "ok") {
      throw new Error(data.detail || "Hierarchy load failed");
    }
    state.modelHierarchy = data.data?.tables || [];
    renderHierarchyTree();
    setStatus(`Hierarchy loaded: ${state.modelHierarchy.length} tables.`);
  } catch (error) {
    refs.hierarchyTree.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
    setStatus(`Hierarchy failed: ${error.message}`, true);
  }
}

function renderPivot(rows, config = {}) {
  refs.pivotArea.innerHTML = "";
  const pivotConfig = {
    rows: config.rows || [],
    cols: config.cols || [],
    vals: config.vals || [],
    aggregatorName: config.aggregatorName || "Count",
    rendererName: config.rendererName || "Table",
    hiddenAttributes: config.hiddenAttributes || [],
    sorters: config.sorters || {},
    derivedAttributes: config.derivedAttributes || {},
    onRefresh: (uiConfig) => {
      state.currentPivotConfig = {
        rows: uiConfig.rows || [],
        cols: uiConfig.cols || [],
        vals: uiConfig.vals || [],
        aggregatorName: uiConfig.aggregatorName || "Count",
        rendererName: uiConfig.rendererName || "Table",
      };
    },
  };

  $(refs.pivotArea).pivotUI(rows, pivotConfig, true, "en");

  state.currentPivotConfig = {
    rows: pivotConfig.rows,
    cols: pivotConfig.cols,
    vals: pivotConfig.vals,
    aggregatorName: pivotConfig.aggregatorName,
    rendererName: pivotConfig.rendererName,
  };
}

function currentRequestBody() {
  return {
    language: refs.languageSelect.value,
    query_text: refs.queryInput.value.trim(),
  };
}

async function runQuery() {
  const payload = currentRequestBody();
  if (!payload.query_text) {
    setStatus("Query text is required.", true);
    return;
  }

  setStatus("Running query...");

  try {
    const data = await apiRequest("/api/query", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    state.rows = Array.isArray(data.data) ? data.data : [];
    renderResultsTable(state.rows);
    renderPivot(state.rows, state.currentPivotConfig);
    setStatus(`Query completed. Rows: ${state.rows.length}`);
  } catch (error) {
    setStatus(`Query failed: ${error.message}`, true);
  }
}

async function loadMetadata(url, label) {
  refs.metadataOutput.textContent = `${label} loading...`;
  try {
    const data = await apiRequest(url);
    refs.metadataOutput.textContent = safeJson(data.data || {});
  } catch (error) {
    refs.metadataOutput.textContent = `${label} failed: ${error.message}`;
  }
}

async function refreshSavedQueries() {
  try {
    const data = await apiRequest("/api/saved");
    refs.savedList.innerHTML = "";

    for (const item of data.items || []) {
      const option = document.createElement("option");
      option.value = String(item.id);
      option.textContent = `${item.id} - ${item.name} [${item.language}]`;
      option.dataset.payload = safeJson(item);
      refs.savedList.appendChild(option);
    }

    setStatus(`Loaded ${data.items?.length || 0} saved definitions.`);
  } catch (error) {
    setStatus(`Failed to load saved items: ${error.message}`, true);
  }
}

function fillEditorFromSaved(option) {
  if (!option) {
    return;
  }

  const item = JSON.parse(option.dataset.payload);
  state.selectedSavedId = item.id;
  refs.saveName.value = item.name || "";
  refs.languageSelect.value = item.language || "dax";
  refs.queryInput.value = item.query_text || "";
  state.currentPivotConfig = item.pivot_config || {};

  if (item.pivot_config && item.pivot_config.builder_fields) {
    state.selectedFields = {
      rows: item.pivot_config.builder_fields.rows || [],
      cols: item.pivot_config.builder_fields.cols || [],
      vals: item.pivot_config.builder_fields.vals || [],
      filters: item.pivot_config.builder_fields.filters || [],
    };
    renderBuckets();
  }

  if (state.rows.length) {
    renderPivot(state.rows, state.currentPivotConfig);
  }

  setStatus(`Loaded saved definition #${item.id}.`);
}

async function saveQuery() {
  const payload = {
    ...currentRequestBody(),
    name: refs.saveName.value.trim(),
    pivot_config: {
      ...state.currentPivotConfig,
      builder_fields: state.selectedFields,
    },
  };

  if (!payload.name) {
    setStatus("Save name is required.", true);
    return;
  }

  if (!payload.query_text) {
    setStatus("Query text is required.", true);
    return;
  }

  try {
    await apiRequest("/api/saved", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setStatus("Saved new definition.");
    await refreshSavedQueries();
  } catch (error) {
    setStatus(`Save failed: ${error.message}`, true);
  }
}

async function updateQuery() {
  if (!state.selectedSavedId) {
    setStatus("Select a saved definition before updating.", true);
    return;
  }

  const payload = {
    name: refs.saveName.value.trim(),
    language: refs.languageSelect.value,
    query_text: refs.queryInput.value.trim(),
    pivot_config: {
      ...state.currentPivotConfig,
      builder_fields: state.selectedFields,
    },
  };

  try {
    await apiRequest(`/api/saved/${state.selectedSavedId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    setStatus(`Updated definition #${state.selectedSavedId}.`);
    await refreshSavedQueries();
  } catch (error) {
    setStatus(`Update failed: ${error.message}`, true);
  }
}

async function deleteQuery() {
  if (!state.selectedSavedId) {
    setStatus("Select a saved definition before deleting.", true);
    return;
  }

  try {
    await apiRequest(`/api/saved/${state.selectedSavedId}`, {
      method: "DELETE",
    });
    setStatus(`Deleted definition #${state.selectedSavedId}.`);
    state.selectedSavedId = null;
    await refreshSavedQueries();
  } catch (error) {
    setStatus(`Delete failed: ${error.message}`, true);
  }
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function copyText(text, successMessage) {
  try {
    await navigator.clipboard.writeText(text);
    setStatus(successMessage);
  } catch (error) {
    setStatus(`Clipboard failed: ${error.message}`, true);
  }
}

function copyApiCode() {
  const builderBody = mapBuilderStateToDaxBuildPayload();
  const code = `fetch("/api/dax/build", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(${safeJson(builderBody)})
}).then(r => r.json()).then(console.log);`;
  copyText(code, "Copied DAX builder API snippet.");
}

function copyPivotConfig() {
  copyText(safeJson(state.currentPivotConfig), "Copied pivot config JSON.");
}

function clearEditor() {
  refs.queryInput.value = "";
  refs.saveName.value = "";
  refs.languageSelect.value = "dax";
  state.selectedSavedId = null;
  state.currentPivotConfig = {};
  state.selectedFields = { rows: [], cols: [], vals: [], filters: [] };
  renderBuckets();
  setStatus("Editor cleared.");
}

function handleHierarchyClick(event) {
  const element = event.target;
  if (!element.dataset.fieldName) {
    return;
  }

  const field = {
    table: decodeURIComponent(element.dataset.fieldTable || ""),
    name: decodeURIComponent(element.dataset.fieldName || ""),
    kind: element.dataset.fieldKind,
  };

  const bucket = targetBucketForField(field);
  addFieldToBucket(bucket, field);
  generateDaxQueryFromBuilder();
}

function handleHierarchyDragStart(event) {
  const element = event.target.closest(".field-chip");
  if (!element || !element.dataset.fieldName) {
    return;
  }

  state.dragContext = {
    sourceType: "hierarchy",
    field: {
      table: decodeURIComponent(element.dataset.fieldTable || ""),
      name: decodeURIComponent(element.dataset.fieldName || ""),
      kind: element.dataset.fieldKind,
    },
  };

  event.dataTransfer.effectAllowed = "copyMove";
  event.dataTransfer.setData("text/plain", "field");
}

function handleBucketDragStart(event) {
  const element = event.target.closest(".bucket-chip");
  if (!element) {
    return;
  }

  const sourceBucket = element.dataset.itemBucket;
  const sourceIndex = Number(element.dataset.itemIndex);
  if (!sourceBucket || Number.isNaN(sourceIndex)) {
    return;
  }

  const sourceItems = state.selectedFields[sourceBucket];
  const field = sourceItems[sourceIndex];
  if (!field) {
    return;
  }

  state.dragContext = {
    sourceType: "bucket",
    sourceBucket,
    sourceIndex,
    field,
  };

  element.classList.add("dragging");
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", "field");
}

function handleBucketDragOver(event) {
  event.preventDefault();
  const bucket = event.currentTarget.dataset.bucket;
  if (!bucket) {
    return;
  }

  if (state.dragContext?.field && canPlaceFieldInBucket(state.dragContext.field, bucket)) {
    event.dataTransfer.dropEffect = "move";
  } else {
    event.dataTransfer.dropEffect = "none";
  }

  event.currentTarget.classList.add("drag-over");
}

function handleBucketDragLeave(event) {
  event.currentTarget.classList.remove("drag-over");
}

function handleBucketDrop(event) {
  event.preventDefault();
  const targetBucket = event.currentTarget.dataset.bucket;
  event.currentTarget.classList.remove("drag-over");

  if (!targetBucket || !state.dragContext?.field) {
    return;
  }

  const targetIndex = dropIndexForBucketEvent(targetBucket, event);

  if (state.dragContext.sourceType === "hierarchy") {
    addFieldAtBucket(targetBucket, state.dragContext.field, targetIndex);
  } else if (state.dragContext.sourceType === "bucket") {
    moveFieldBetweenBuckets(
      state.dragContext.sourceBucket,
      state.dragContext.sourceIndex,
      targetBucket,
      targetIndex
    );
  }

  generateDaxQueryFromBuilder();
}

function handleDragEnd() {
  state.dragContext = null;
  document.querySelectorAll(".bucket.drag-over").forEach((element) => {
    element.classList.remove("drag-over");
  });
  document.querySelectorAll(".bucket-chip.dragging").forEach((element) => {
    element.classList.remove("dragging");
  });
}

function handleBucketClick(event) {
  const removeBucket = event.target.dataset.removeBucket;
  const removeKey = decodeURIComponent(event.target.dataset.removeKey || "");
  if (!removeBucket || !removeKey) {
    return;
  }
  removeFieldFromBucket(removeBucket, removeKey);
  generateDaxQueryFromBuilder();
}

function handleFilterInputChange(event) {
  const target = event.target;
  if (!target.classList.contains("filter-values-input")) {
    return;
  }

  const index = Number(target.dataset.filterInputIndex);
  if (Number.isNaN(index) || index < 0 || index >= state.selectedFields.filters.length) {
    return;
  }

  const values = target.value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

  state.selectedFields.filters[index].values = values;
}

function handleFilterOperatorChange(event) {
  const target = event.target;
  if (!target.classList.contains("filter-operator-select")) {
    return;
  }

  const index = Number(target.dataset.filterOperatorIndex);
  if (Number.isNaN(index) || index < 0 || index >= state.selectedFields.filters.length) {
    return;
  }

  const operator = target.value || "in";
  state.selectedFields.filters[index].operator = operator;

  if (FILTER_OPERATORS_WITHOUT_VALUES.has(operator)) {
    state.selectedFields.filters[index].values = [];
  }

  renderBuckets();
  generateDaxQueryFromBuilder();
}

function handleFilterInputBlur(event) {
  if (!event.target.classList.contains("filter-values-input")) {
    return;
  }

  generateDaxQueryFromBuilder();
}

function moveLastField(source, target) {
  const sourceItems = state.selectedFields[source];
  if (!sourceItems.length) {
    return;
  }
  const item = sourceItems[sourceItems.length - 1];
  removeFieldFromBucket(source, fieldKey(item));
  addFieldToBucket(target, item);
  generateDaxQueryFromBuilder();
}

function handleBucketDblClick(event) {
  const bucketElement = event.currentTarget;
  const bucket = bucketElement.dataset.bucket;
  if (!bucket) {
    return;
  }

  if (bucket === "rows") {
    moveLastField("rows", "cols");
  } else if (bucket === "cols") {
    moveLastField("cols", "filters");
  } else if (bucket === "filters") {
    moveLastField("filters", "rows");
  }
}

function wireEvents() {
  refs.generateCodeBtn.addEventListener("click", generateDaxQueryFromBuilder);
  refs.runQueryBtn.addEventListener("click", runQuery);
  refs.saveQueryBtn.addEventListener("click", saveQuery);
  refs.updateQueryBtn.addEventListener("click", updateQuery);
  refs.clearEditorBtn.addEventListener("click", clearEditor);
  refs.copyApiCodeBtn.addEventListener("click", copyApiCode);
  refs.copyConfigBtn.addEventListener("click", copyPivotConfig);
  refs.loadHierarchyBtn.addEventListener("click", loadHierarchy);
  refs.loadDmvsBtn.addEventListener("click", () => loadMetadata("/api/metadata/dmvs", "DMVs"));
  refs.loadDiscoverBtn.addEventListener("click", () =>
    loadMetadata("/api/metadata/discover", "Discover")
  );
  refs.fieldSearch.addEventListener("input", renderHierarchyTree);
  refs.hierarchyTree.addEventListener("click", handleHierarchyClick);
  refs.hierarchyTree.addEventListener("dragstart", handleHierarchyDragStart);
  refs.rowsBucket.addEventListener("dragstart", handleBucketDragStart);
  refs.colsBucket.addEventListener("dragstart", handleBucketDragStart);
  refs.valsBucket.addEventListener("dragstart", handleBucketDragStart);
  refs.filtersBucket.addEventListener("dragstart", handleBucketDragStart);

  refs.rowsBucket.addEventListener("dragover", handleBucketDragOver);
  refs.colsBucket.addEventListener("dragover", handleBucketDragOver);
  refs.valsBucket.addEventListener("dragover", handleBucketDragOver);
  refs.filtersBucket.addEventListener("dragover", handleBucketDragOver);

  refs.rowsBucket.addEventListener("dragleave", handleBucketDragLeave);
  refs.colsBucket.addEventListener("dragleave", handleBucketDragLeave);
  refs.valsBucket.addEventListener("dragleave", handleBucketDragLeave);
  refs.filtersBucket.addEventListener("dragleave", handleBucketDragLeave);

  refs.rowsBucket.addEventListener("drop", handleBucketDrop);
  refs.colsBucket.addEventListener("drop", handleBucketDrop);
  refs.valsBucket.addEventListener("drop", handleBucketDrop);
  refs.filtersBucket.addEventListener("drop", handleBucketDrop);

  refs.rowsBucket.addEventListener("dragend", handleDragEnd);
  refs.colsBucket.addEventListener("dragend", handleDragEnd);
  refs.valsBucket.addEventListener("dragend", handleDragEnd);
  refs.filtersBucket.addEventListener("dragend", handleDragEnd);
  refs.hierarchyTree.addEventListener("dragend", handleDragEnd);
  refs.rowsBucket.addEventListener("click", handleBucketClick);
  refs.colsBucket.addEventListener("click", handleBucketClick);
  refs.valsBucket.addEventListener("click", handleBucketClick);
  refs.filtersBucket.addEventListener("click", handleBucketClick);
  refs.filtersBucket.addEventListener("change", handleFilterOperatorChange);
  refs.filtersBucket.addEventListener("input", handleFilterInputChange);
  refs.filtersBucket.addEventListener("blur", handleFilterInputBlur, true);
  refs.rowsBucket.addEventListener("dblclick", handleBucketDblClick);
  refs.colsBucket.addEventListener("dblclick", handleBucketDblClick);
  refs.filtersBucket.addEventListener("dblclick", handleBucketDblClick);
  if (refs.refreshSavedBtn) {
    refs.refreshSavedBtn.addEventListener("click", refreshSavedQueries);
  }
  if (refs.deleteSavedBtn) {
    refs.deleteSavedBtn.addEventListener("click", deleteQuery);
  }
  if (refs.savedList) {
    refs.savedList.addEventListener("change", () => {
      fillEditorFromSaved(refs.savedList.selectedOptions[0]);
    });
  }
}

function init() {
  wireEvents();
  renderBuckets();
  loadHierarchy();
  if (refs.savedList) {
    refreshSavedQueries();
  }
  setStatus("Ready. Build using hierarchy first, then run.");
}

init();
