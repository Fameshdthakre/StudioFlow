import { escapeHtml } from "./dom.js";

export function layoutFields(prefix, recipe = {}) {
  const options = [
    ["feature", "Feature"],
    ["dimensions", "Dimensions"],
    ["pack_contents", "Pack contents"],
    ["usage", "Usage / styling"],
    ["detail_collage", "Detail collage"],
  ];
  return `<div class="fields-grid"><label>Card layout<select id="${prefix}-template">${options.map(([value, label]) => `<option value="${value}" ${recipe.template === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
    <label>Theme<select id="${prefix}-theme"><option value="light">Light</option><option value="dark" ${recipe.theme === "dark" ? "selected" : ""}>Dark</option></select></label></div>
    <label>Headline<input id="${prefix}-headline" maxlength="100" value="${escapeHtml(recipe.headline || "")}"></label>
    <label>Supporting text<textarea id="${prefix}-body" maxlength="260" rows="3">${escapeHtml(recipe.body || "")}</textarea></label>
    <div class="fields-grid"><label>Verified measurement<input id="${prefix}-measurement" type="number" min="0.01" step="any" value="${recipe.measurement || ""}"></label>
    <label>Unit<select id="${prefix}-unit">${["cm", "mm", "m", "in"].map((u) => `<option ${recipe.unit === u ? "selected" : ""}>${u}</option>`).join("")}</select></label>
    <label>Heading colour<input id="${prefix}-heading" type="color" value="${recipe.heading_color || "#78150f"}"></label>
    <label>Text colour<input id="${prefix}-color" type="color" value="${recipe.body_color || "#222222"}"></label>
    <label>Text position<select id="${prefix}-position"><option value="top_left">Top left</option><option value="top_right" ${recipe.text_position === "top_right" ? "selected" : ""}>Top right</option></select></label></div>
    <label class="confirmation"><input type="checkbox" id="${prefix}-confirmed"> I verified this text and these measurements for every selected product.</label>`;
}

export function readLayout($, prefix) {
  return {
    version: 1,
    template: $(`${prefix}-template`).value,
    theme: $(`${prefix}-theme`).value,
    headline: $(`${prefix}-headline`).value.trim(),
    body: $(`${prefix}-body`).value.trim(),
    measurement: Number($(`${prefix}-measurement`).value) || null,
    unit: $(`${prefix}-unit`).value,
    heading_color: $(`${prefix}-heading`).value,
    body_color: $(`${prefix}-color`).value,
    text_position: $(`${prefix}-position`).value,
    facts_confirmed: $(`${prefix}-confirmed`).checked,
  };
}
