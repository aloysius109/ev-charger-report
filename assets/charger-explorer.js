const root = document.querySelector("#charger-explorer");
const dataUrl = new URL("../data/derived/charger_data.json", import.meta.url);
const svgNS = "http://www.w3.org/2000/svg";
const dates = ["2026-01-01", "2026-04-01", "2026-07-01"];

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}
function svgNode(tag, attrs = {}, text) {
  const item = document.createElementNS(svgNS, tag);
  for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, String(value));
  if (text !== undefined) item.textContent = text;
  return item;
}
function fmt(value, digits = 0) {
  return Number(value).toLocaleString("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}
function metric(label, value, note) {
  const card = node("div", "area-metric");
  card.append(node("span", "area-metric-label", label), node("strong", "area-metric-value", value), node("span", "area-metric-note", note));
  return card;
}
function drawSeries(svg, area) {
  const width = 820, height = 430, left = 82, right = 24;
  const xPositions = dates.map((_, i) => left + i * (width - left - right) / (dates.length - 1));
  const panels = [
    { key: "rates", title: "All public chargers per 100,000 residents", color: "#247367", top: 34, bottom: 190 },
    { key: "rapid_rates", title: "Rapid and ultra-rapid (50kW+) per 100,000 residents", color: "#d68d38", top: 245, bottom: 385 }
  ];
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-labelledby", "area-chart-title area-chart-description");
  svg.append(svgNode("title", { id: "area-chart-title" }, `${area.name}: public charger rates in 2026`));
  svg.append(svgNode("desc", { id: "area-chart-description" }, "Two small line charts compare total and rapid-or-faster chargers per 100,000 residents in January, April and July 2026."));
  panels.forEach(panel => {
    const values = dates.map(date => area[panel.key][date]);
    const max = Math.max(1, Math.ceil(Math.max(...values) / 50) * 50);
    const y = value => panel.bottom - value / max * (panel.bottom - panel.top);
    svg.append(svgNode("text", { x: left, y: panel.top - 12, class: "area-chart-title" }, panel.title));
    for (let tick = 0; tick <= 2; tick++) {
      const value = max * tick / 2;
      svg.append(svgNode("line", { x1: left, x2: width - right, y1: y(value), y2: y(value), class: "area-grid" }));
      svg.append(svgNode("text", { x: left - 10, y: y(value) + 4, "text-anchor": "end", class: "area-tick" }, fmt(value)));
    }
    const points = values.map((value, i) => `${xPositions[i]},${y(value)}`).join(" ");
    svg.append(svgNode("polyline", { points, class: "area-trend-line", stroke: panel.color }));
    values.forEach((value, i) => {
      svg.append(svgNode("circle", { cx: xPositions[i], cy: y(value), r: 5, fill: panel.color }));
      svg.append(svgNode("text", { x: xPositions[i], y: y(value) - 10, "text-anchor": "middle", class: "area-point-label" }, fmt(value, 1)));
    });
    if (panel === panels[1]) {
      dates.forEach((date, i) => svg.append(svgNode("text", { x: xPositions[i], y: height - 12, "text-anchor": "middle", class: "area-tick" }, new Date(`${date}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" }))));
    }
  });
}
function showArea(area, areas, summary, status, chart) {
  const july = "2026-07-01";
  const ranked = areas.slice().sort((a, b) => b.rates[july] - a.rates[july]);
  const rank = ranked.findIndex(item => item.code === area.code) + 1;
  const metrics = node("div", "area-metrics");
  metrics.append(
    metric("Public chargers", fmt(area.counts[july]), "Reported operational on 1 July 2026"),
    metric("All chargers per 100,000", fmt(area.rates[july], 1), "Using DfT's published population-adjusted rate"),
    metric("Rapid or ultra-rapid per 100,000", fmt(area.rapid_rates[july], 1), "Chargers rated 50kW or above"),
    metric("Coverage rank", `${rank} of ${areas.length}`, "Ranked by chargers per 100,000")
  );
  summary.replaceChildren(metrics);
  drawSeries(chart, area);
  status.textContent = `${area.name}, ${area.country}. Chart shows the January, April and July 2026 readings.`;
}
async function start() {
  if (!root) return;
  try {
    const response = await fetch(dataUrl, { cache: "force-cache" });
    if (!response.ok) throw new Error(`Local-authority data could not be loaded (HTTP ${response.status}).`);
    const payload = await response.json();
    const areas = payload.areas.slice().sort((a, b) => a.country.localeCompare(b.country, "en-GB") || a.name.localeCompare(b.name, "en-GB"));
    const label = node("label", "authority-select-label", "Choose a local authority");
    const select = node("select", "authority-select");
    select.id = "authority-select";
    select.setAttribute("aria-label", "Choose a local authority");
    for (const country of ["England", "Northern Ireland", "Scotland", "Wales"]) {
      const group = document.createElement("optgroup");
      group.label = country;
      for (const area of areas.filter(item => item.country === country)) group.append(new Option(area.name, area.code));
      select.append(group);
    }
    const defaultArea = areas.find(area => area.name === "Hammersmith and Fulham");
    if (defaultArea) select.value = defaultArea.code;
    const controls = node("div", "authority-controls");
    controls.append(label, select);
    const status = node("p", "authority-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const summary = node("div", "authority-summary");
    const chartWrap = node("div", "authority-chart-wrap");
    const chart = svgNode("svg", { class: "authority-chart", focusable: "false" });
    chartWrap.append(chart);
    const note = node("p", "authority-chart-note", "Rates are per 100,000 residents. Local estimates use DfT-published population-adjusted figures; see the method note for the denominator and series caveats.");
    root.replaceChildren(controls, status, summary, chartWrap, note);
    const update = () => {
      const area = areas.find(item => item.code === select.value);
      if (area) showArea(area, areas, summary, status, chart);
    };
    select.addEventListener("change", update);
    update();
  } catch (error) {
    root.replaceChildren(node("p", "explorer-error", error instanceof Error ? error.message : "The local-authority data could not be loaded."));
  }
}
start();