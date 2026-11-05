import { escapeHtml } from "./dom.js";
import { layoutFields, readLayout } from "./layout.js";

export function createPlanScreen({
  ui,
  client,
  config,
  submissions,
  setView,
  loadJobs,
  loadStats,
  environment = globalThis,
}) {
  const { $, toast } = ui;
  const { document, URL, FormData, confirm } = environment;
  const api = (...args) => client.request(...args);
  const products = [],
    selected = new Set(["lifestyle"]),
    uploads = new WeakMap();
  let mounted = false,
    busy = false;
  let resolutionChosen = false;
  const scenes = {
    main: "Pure white background, actual product only, full product visible.",
    lifestyle:
      "Warm, elegant interior with realistic light, accurate product scale and fine material detail.",
    infographic:
      "An elegant product scene with clear space for a headline at the top left; no text or numbers.",
  };

  function mount() {
    if (mounted || !$("creation-plan")) return;
    mounted = true;
    $("creation-plan").classList.remove("hidden");
    $("legacy-scenes").classList.add("hidden");
    $("task-tip").textContent =
      "Choose several image types. Product photos define the item; style references guide the setting.";
    $("creation-plan").innerHTML =
      ["main", "lifestyle", "infographic"]
        .map(
          (kind) =>
            `<div id="plan-${kind}" class="plan-section ${selected.has(kind) ? "" : "hidden"}"><h3>${kind === "main" ? "MAIN image" : kind === "infographic" ? "Finished infographic" : "Lifestyle scenes"}</h3><label>Scenes ${kind === "main" ? "" : "(one per line)"}<textarea id="plan-scenes-${kind}" rows="3">${scenes[kind]}</textarea></label>${kind === "infographic" ? `<label class="confirmation"><input id="plan-reuse" type="checkbox" checked> Reuse the first lifestyle scene without an extra generation charge</label>${layoutFields("plan-card")}` : ""}</div>`,
        )
        .join("") +
      '<label>Native generation resolution<select id="plan-resolution"><option value="">Standard model resolution</option><option value="2048x2048">2048 × 2048 · validated models only · experimental</option></select></label><p id="plan-resolution-note" class="memory-subtext"></p><p id="plan-breakdown" class="memory-subtext" aria-live="polite"></p>';
    $("creation-plan").addEventListener("input", recalc);
    $("creation-plan").addEventListener("change", recalc);
    $("model").addEventListener("change", recalc);
    $("canvas").addEventListener("change", recalc);
    $("plan-resolution").addEventListener("change", () => {
      resolutionChosen = true;
    });
    $("quality").value = "high";
    recalc();
  }

  function toggleType(kind) {
    mount();
    if (selected.has(kind)) selected.delete(kind);
    else selected.add(kind);
    for (const button of document.querySelectorAll(".type-option")) {
      button.classList.toggle("selected", selected.has(button.dataset.kind));
      button.setAttribute(
        "aria-pressed",
        String(selected.has(button.dataset.kind)),
      );
    }
    for (const kind of Object.keys(scenes))
      $("plan-" + kind).classList.toggle("hidden", !selected.has(kind));
    recalc();
  }

  function lineScenes(kind) {
    return $("plan-scenes-" + kind)
      .value.split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function counts() {
    let output = 0,
      paid = 0;
    for (const product of products)
      for (const kind of ["main", "lifestyle", "infographic"]) {
        if (!selected.has(kind)) continue;
        const count = kind === "main" ? 1 : lineScenes(kind).length;
        output += count;
        if (
          !(kind === "main" && product.cutout) &&
          !(
            kind === "infographic" &&
            selected.has("lifestyle") &&
            $("plan-reuse").checked
          )
        )
          paid += count;
      }
    return { output, paid };
  }

  function validation() {
    if (!products.length) return "Add at least one product photo";
    if (!selected.size) return "Choose at least one image type";
    if (products.length > 50) return "Use at most 50 products";
    const allFiles = new Set(
      products.flatMap((p) => [
        ...p.angles,
        ...p.styles,
        ...(p.cutout ? [p.cutout] : []),
      ]),
    );
    if (
      [...allFiles].some((f) => f.size > 18 * 1024 * 1024) ||
      [...allFiles].reduce((n, f) => n + f.size, 0) > 100 * 1024 * 1024
    )
      return "Use references under 18 MB each and 100 MB in total";
    if (
      products.some(
        (p) => !Number.isInteger(p.pack) || p.pack < 1 || p.pack > 12,
      )
    )
      return "Verify a pack quantity between 1 and 12";
    const perProduct = [...selected].reduce(
      (n, k) => n + (k === "main" ? 1 : lineScenes(k).length),
      0,
    );
    if (perProduct > 12 || [...selected].some((k) => !lineScenes(k).length))
      return "Choose 1–12 outputs per product";
    if (counts().output > 250) return "Maximum 250 outputs per job";
    if (selected.has("infographic")) {
      const recipe = readLayout($, "plan-card");
      if (!recipe.headline || !recipe.facts_confirmed)
        return "Enter and verify the infographic copy";
      if (
        ["dimensions", "pack_contents"].includes(recipe.template) &&
        products.some((p) => !p.cutout || !p.approved)
      )
        return "This template needs an approved single-item cutout for each product";
      if (recipe.template === "dimensions" && !recipe.measurement)
        return "Enter the verified measurement";
      if ($("canvas").value !== "square")
        return "Finished cards require the square canvas";
    }
    if (selected.has("main") && products.some((p) => !p.approved))
      return "Verify the pack quantities and any supplied cutouts before creating MAIN images";
    if (selected.has("main") && lineScenes("main").length > 1)
      return "Use one scene for the MAIN image";
    if (products.some((p) => !p.sku.trim()))
      return "Enter a SKU for every product";
    return "";
  }

  function recalc() {
    mount();
    if (!mounted) return;
    const model = config.models?.find((m) => m.id === $("model").value);
    const supported = !!model?.native_2k && $("canvas").value === "square";
    const resolution = $("plan-resolution");
    if (!resolutionChosen) resolution.value = supported ? "2048x2048" : "";
    resolution.options[1].disabled = !supported;
    // Never change an explicitly selected size behind the user's back.
    $("plan-resolution-note").textContent = supported
      ? "Native 2K is enabled for this model. Final square exports are 2000 × 2000."
      : "This model has not been validated for native 2K. Standard square generation is 1024 × 1024, exported at 2000 × 2000.";
    const { output, paid } = counts();
    $("estimated-count").textContent = output;
    const types = [...selected]
      .map(
        (kind) =>
          `${kind === "main" ? 1 : lineScenes(kind).length} ${kind === "main" ? "MAIN" : kind === "lifestyle" ? "Lifestyle" : "Infographic"}`,
      )
      .join(" + ");
    $("plan-breakdown").textContent =
      `${products.length} product(s) × (${types || "no image types"}) = ${output} images. ${output - paid} local compositions; ${paid} scene generations${config.mode === "demo" ? " in demo mode" : ""}.`;
    const error =
      validation() ||
      (resolution.value && !supported
        ? "Choose a validated model or standard resolution"
        : "");
    $("generate-btn").disabled = busy || !!error;
    $("generation-hint").textContent = busy
      ? "Uploading references and submitting…"
      : error ||
        `${output} finished images · ${config.mode === "live" ? paid : 0} paid requests · ${output - paid} local compositions`;
  }

  function showProducts() {
    const container = $("product-list");
    container.innerHTML = "";
    for (const product of products) {
      const row = document.createElement("div");
      row.className = "product-row plan-product";
      row.innerHTML = `<img src="${product.url}" alt="Product reference"><div><label>SKU / ASIN<input class="plan-sku" value="${escapeHtml(product.sku)}"></label><p class="memory-subtext">${product.angles.length} product angle(s) · ${product.styles.length} style reference(s) ${product.cutout ? "· Cutout attached" : ""}</p><div class="reference-controls"><label>Extra product angles<input class="plan-angles" type="file" multiple accept="image/png,image/jpeg,image/webp"></label><label>Style references<input class="plan-styles" type="file" multiple accept="image/png,image/jpeg,image/webp"></label><label>Transparent cutout<input class="plan-cutout" type="file" accept="image/png,image/webp"></label></div><label>Verified pack quantity<input class="plan-pack" type="number" min="1" max="12" value="${product.pack}"></label><label class="confirmation"><input class="plan-approved" type="checkbox" ${product.approved ? "checked" : ""}> This cutout shows one correct item, and the quantity is verified.</label></div><button class="plan-remove" type="button" aria-label="Remove product">×</button>`;
      row.querySelector(".plan-approved").parentElement.lastChild.textContent =
        product.cutout
          ? " This cutout shows one correct item, and the quantity is verified."
          : " I verified this product’s pack quantity.";
      row.querySelector(".plan-sku").addEventListener("input", (e) => {
        product.sku = e.target.value;
        product.edited = true;
        recalc();
      });
      for (const [selector, field, maximum] of [
        [".plan-angles", "angles", 12],
        [".plan-styles", "styles", 3],
      ])
        row.querySelector(selector).addEventListener("change", (e) => {
          const files = [...e.target.files];
          if (
            product[field].length + files.length > maximum ||
            files.some((f) => f.size > 18 * 1024 * 1024)
          )
            return toast(
              `Use at most ${maximum} references, each under 18 MB`,
              true,
            );
          product[field].push(...files);
          showProducts();
        });
      row.querySelector(".plan-cutout").addEventListener("change", (e) => {
        product.cutout = e.target.files[0];
        product.approved = false;
        showProducts();
      });
      row.querySelector(".plan-pack").addEventListener("input", (e) => {
        product.pack = Number(e.target.value);
        recalc();
      });
      row.querySelector(".plan-approved").addEventListener("change", (e) => {
        product.approved = e.target.checked;
        recalc();
      });
      row.querySelector(".plan-remove").addEventListener("click", () => {
        URL.revokeObjectURL(product.url);
        products.splice(products.indexOf(product), 1);
        showProducts();
      });
      container.append(row);
    }
    recalc();
  }

  async function addFiles(files) {
    mount();
    const added = [];
    for (const file of files) {
      if (
        products.length >= 50 ||
        file.size > 18 * 1024 * 1024 ||
        !file.type.startsWith("image/")
      ) {
        toast("Use up to 50 images, each under 18 MB", true);
        continue;
      }
      const product = {
        sku: file.name
          .replace(/\.[^.]+$/, "")
          .replace(/[^\w-]/g, "_")
          .slice(0, 60),
        file,
        angles: [file],
        styles: [],
        cutout: null,
        pack: 1,
        approved: false,
        url: URL.createObjectURL(file),
      };
      products.push(product);
      added.push(product);
    }
    showProducts();
    try {
      const result = await api("/api/catalog/match-files", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filenames: added.map((p) => p.file.name) }),
      });
      for (const p of added)
        if (
          products.includes(p) &&
          !p.edited &&
          result.matches?.[p.file.name]?.matched
        )
          p.sku = result.matches[p.file.name].sku;
      showProducts();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function upload(file, role) {
    let roles = uploads.get(file);
    if (roles?.[role]) return roles[role];
    const body = new FormData();
    body.append("file", file, file.name);
    body.append("role", role);
    const result = await api("/api/assets", { method: "POST", body });
    roles ||= {};
    roles[role] = result.asset_id;
    uploads.set(file, roles);
    return result.asset_id;
  }

  async function generate() {
    if (busy) return;
    const error = validation();
    if (error) return toast(error, true);
    const { output, paid } = counts();
    if (
      config.mode === "live" &&
      paid &&
      !confirm(`Create ${output} images using ${paid} paid API requests?`)
    )
      return;
    const kinds = ["main", "lifestyle", "infographic"].filter((k) =>
      selected.has(k),
    );
    const sceneSnapshot = Object.fromEntries(
      kinds.map((k) => [k, k === "main" ? [lineScenes(k)[0]] : lineScenes(k)]),
    );
    const card = kinds.includes("infographic")
      ? readLayout($, "plan-card")
      : null;
    const reuse = kinds.includes("lifestyle") && $("plan-reuse").checked;
    const pending = products.map((p) => ({
      ...p,
      angles: [...p.angles],
      styles: [...p.styles],
    }));
    const plan = {
      version: 1,
      model: $("model").value,
      quality: $("quality").value,
      canvas: $("canvas").value,
      native_size: $("plan-resolution").value,
      products: [],
    };
    busy = true;
    recalc();
    try {
      for (const p of pending) {
        const refs = [],
          styles = [];
        for (const f of p.angles)
          refs.push(await upload(f, "product_reference"));
        for (const f of p.styles)
          styles.push(await upload(f, "style_reference"));
        const cutout = p.cutout ? await upload(p.cutout, "cutout") : null;
        const outputs = [];
        for (const kind of kinds)
          for (const scene of sceneSnapshot[kind]) {
            const item = { kind, scene };
            if (kind === "infographic") {
              item.recipe = {
                ...card,
                cutout_asset_id: cutout,
                cutout_approved: p.approved,
                cutout_is_single_item: p.approved,
                pack_count: p.pack,
              };
              if (reuse)
                item.reuse_output_index = outputs.findIndex(
                  (o) => o.kind === "lifestyle",
                );
            }
            outputs.push(item);
          }
        plan.products.push({
          sku: p.sku,
          product_references: refs,
          style_references: styles,
          cutout_asset_id: cutout,
          cutout_approved: p.approved,
          cutout_is_single_item: p.approved,
          pack_count: p.pack,
          pack_confirmed: p.approved,
          outputs,
        });
      }
      const body = new FormData();
      body.append("generation_plan", JSON.stringify(plan));
      const key = submissions.requestKey("generate-plan", JSON.stringify(plan));
      const result = await api("/api/generate", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body,
      });
      submissions.clear("generate-plan");
      toast(`${result.images_queued} images queued`);
      setView("jobs");
      await Promise.all([loadJobs(), loadStats()]);
    } catch (error) {
      toast(error.message, true);
    } finally {
      busy = false;
      recalc();
    }
  }
  return { mount, toggleType, recalc, addFiles, generate, counts };
}
