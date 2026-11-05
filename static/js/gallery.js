import { escapeHtml } from "./dom.js";
import { layoutFields, readLayout } from "./layout.js";

export function createGalleryScreen({
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
  const { document, confirm, window } = environment;
  const api = (...args) => client.request(...args);
  const { requestKey } = submissions;
  const selection = new Map();
  let pageCursor = null,
    nextCursor = null,
    previousCursors = [],
    libraryRequest = 0,
    composing = false;
  let renderRecords = [],
    selected = null,
    isRevising = false,
    historyRequest = 0,
    modalReturnFocus = null;

  async function loadRenders() {
    const request = ++libraryRequest;
    try {
      if (config.features?.library) {
        const params = new URLSearchParams({
          q: $("filter-sku").value,
          status: $("filter-status").value,
        });
        if (pageCursor) params.set("cursor", pageCursor);
        const result = await api("/api/library?" + params);
        if (request !== libraryRequest) return;
        renderRecords = result.items;
        nextCursor = result.next_cursor;
        $("library-tools").classList.toggle(
          "hidden",
          !config.features?.exports,
        );
        $("library-next").classList.toggle("hidden", !nextCursor);
        $("library-previous").classList.toggle(
          "hidden",
          !previousCursors.length,
        );
      } else {
        renderRecords = await api("/api/renders");
        if (request !== libraryRequest) return;
      }
      for (const record of renderRecords)
        if (selection.has(record.id)) selection.set(record.id, record);
      drawGallery();
    } catch (e) {
      if (!config.login_required) toast(e.message, true);
    }
  }

  function drawGallery() {
    const filter = $("filter-status").value;
    const sku = $("filter-sku").value.toLowerCase();
    const records = config.features?.library
      ? renderRecords
      : renderRecords.filter(
          (r) =>
            (filter === "all" ||
              (filter === "approved" && r.approved) ||
              r.status === filter) &&
            `${r.sku} ${r.prompt}`.toLowerCase().includes(sku),
        );
    const container = $("gallery-content");
    container.innerHTML = "";
    if (!records.length) {
      container.innerHTML =
        '<div class="placeholder">No images match your filters. Create your first production job to populate the gallery.</div>';
      selectionCount();
      return;
    }
    for (const r of records) {
      const card = document.createElement("div");
      card.className = "image-card";
      const statusLabel =
        r.status === "completed"
          ? r.approved
            ? "Approved"
            : "Needs review"
          : r.status === "failed"
            ? "Failed"
            : r.status;
      const modelDisplay = r.model.replace("gpt-image-2.5-", "");
      card.innerHTML = `<div class="image-photo">${r.url ? `<img src="${r.url}" loading="lazy" alt="${escapeHtml(r.sku)} generated image">` : `<span>${r.status === "failed" ? "⚠" : "◷"}</span>`}<span class="photo-status ${r.approved ? "good" : r.status === "failed" ? "fail" : ""}">${escapeHtml(statusLabel)}</span></div>
        <div class="image-meta">
          <h3>${escapeHtml(r.sku)} · ${escapeHtml(r.kind.toUpperCase())}</h3>
          <div class="scene" title="${escapeHtml(r.prompt)}">${escapeHtml(r.prompt)}</div>
          <div class="details">${escapeHtml(r.user)} · ${escapeHtml(modelDisplay)} · ${escapeHtml(r.quality)}</div>
          <div class="card-buttons">${r.status === "completed" ? `<button class="review-btn">Review / Revise</button><button class="approve-btn">${r.approved ? "✓ Approved" : "✓ Approve"}</button>` : `<small>${escapeHtml(r.error || "Processing...")}</small>`}</div>
        </div>`;
      const im = card.querySelector(".image-photo img");
      if (im && r.thumbnail_url) im.src = r.thumbnail_url;
      if (config.features?.exports && r.status === "completed") {
        const label = document.createElement("label");
        label.className = "library-choice";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selection.has(r.id);
        checkbox.setAttribute(
          "aria-label",
          `Select ${r.download_filename || r.sku}`,
        );
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selection.set(r.id, r);
          else selection.delete(r.id);
          card.classList.toggle("is-selected", checkbox.checked);
          selectionCount();
        });
        label.append(checkbox);
        card.querySelector(".image-photo").append(label);
        card.classList.toggle("is-selected", selection.has(r.id));
      }
      if (im) im.addEventListener("click", () => openModal(r));
      card
        .querySelector(".review-btn")
        ?.addEventListener("click", () => openModal(r));
      card
        .querySelector(".approve-btn")
        ?.addEventListener("click", () => setApproval(r, !r.approved));
      container.append(card);
    }
    selectionCount();
  }

  function selectionCount() {
    if (!$("library-selection")) return;
    const visible = new Set(renderRecords.map((r) => r.id));
    const hidden = [...selection.keys()].filter(
      (id) => !visible.has(id),
    ).length;
    $("library-selection").textContent =
      `${selection.size} selected${hidden ? ` · ${hidden} outside this page` : ""}`;
    $("library-export").disabled = !selection.size || selection.size > 250;
  }

  function resetLibrary() {
    pageCursor = null;
    previousCursors = [];
    return loadRenders();
  }

  async function selectFiltered() {
    try {
      const params = new URLSearchParams({
        q: $("filter-sku").value,
        status: $("filter-status").value,
        selection: "true",
      });
      const result = await api("/api/library?" + params);
      for (const record of result.items) selection.set(record.id, record);
      drawGallery();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function previewExport() {
    const snapshot = {
      render_ids: [...selection.keys()],
      identifier: $("library-naming").value,
    };
    try {
      const options = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(snapshot),
      };
      const result = await api("/api/exports/preview", options);
      const panel = $("export-preview");
      panel.classList.remove("hidden");
      panel.innerHTML = `<h3>${result.items.length} images · ${(result.total_bytes / 1048576).toFixed(1)} MiB</h3><ul>${result.items.map((i) => `<li>${escapeHtml(i.filename)}</li>`).join("")}</ul>`;
      const button = document.createElement("button");
      button.className = "primary-btn";
      button.textContent = "Prepare ZIP";
      panel.append(button);
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const job = await api("/api/exports", options);
          button.textContent = "Preparing download…";
          const poll = async () => {
            try {
              const status = await api(`/api/exports/${job.id}`);
              if (status.status === "failed") throw new Error(status.error);
              if (status.download_url) {
                const link = document.createElement("a");
                link.href = status.download_url;
                link.textContent = "Download ZIP";
                link.className = "outline-link";
                link.download = "studioflow-images.zip";
                panel.replaceChildren(link);
                link.click();
              } else environment.setTimeout(poll, 1500);
            } catch (error) {
              button.textContent = "Prepare ZIP";
              button.disabled = false;
              toast(error.message, true);
            }
          };
          await poll();
        } catch (error) {
          button.disabled = false;
          toast(error.message, true);
        }
      });
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function setApproval(r, approved) {
    try {
      await api(`/api/renders/${r.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved }),
      });
      r.approved = approved;
      toast(approved ? "Image approved" : "Approval removed");
      drawGallery();
      loadStats();
      if (selected?.id === r.id) showModalButton();
    } catch (e) {
      toast(e.message, true);
    }
  }

  function showModalButton() {
    $("approve-modal-btn").textContent = selected?.approved
      ? "✓ Approved (click to undo)"
      : "✓ Approve image";
  }

  function closeModal() {
    $("modal").classList.add("hidden");
    historyRequest++;
    if (modalReturnFocus?.isConnected) modalReturnFocus.focus();
  }

  function modalKeydown(e) {
    if ($("modal").classList.contains("hidden")) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closeModal();
      return;
    }
    if (e.key !== "Tab") return;
    const focusable = [
      ...$("modal").querySelectorAll(
        'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
      ),
    ].filter((el) => el.getClientRects().length);
    if (!focusable.length) {
      e.preventDefault();
      return;
    }
    const first = focusable[0],
      last = focusable[focusable.length - 1];
    if (
      e.shiftKey &&
      (document.activeElement === first ||
        !$("modal").contains(document.activeElement))
    ) {
      e.preventDefault();
      last.focus();
    } else if (
      !e.shiftKey &&
      (document.activeElement === last ||
        !$("modal").contains(document.activeElement))
    ) {
      e.preventDefault();
      first.focus();
    }
  }

  function openModal(r) {
    if ($("modal").classList.contains("hidden"))
      modalReturnFocus = document.activeElement;
    selected = r;
    $("remember-revision").checked = false;
    loadRevisionHistory(r.id);
    $("modal-title").textContent = `${r.sku} · ${r.kind.toUpperCase()}`;
    $("modal-img").src = r.url;
    $("modal-status").textContent =
      `${r.model} · ${r.quality} quality · ${r.native_width ? r.native_width + " × " + r.native_height : r.native_size || "standard"} master resolution · ${r.approved ? "Approved" : "Not approved"}`;
    $("revision-prompt").value = "";
    $("download-modal").href = config.features?.library
      ? `/api/renders/${r.id}/download`
      : r.url;
    $("download-modal").download =
      r.download_filename || `${r.sku}_${r.id.slice(0, 6)}.jpg`;
    if (config.features?.composition) showLayoutEditor(r);
    showModalButton();
    $("modal").classList.remove("hidden");
    $("close-modal").focus();
  }

  function showLayoutEditor(record) {
    document.getElementById("layout-editor")?.remove();
    const editor = document.createElement("details");
    editor.id = "layout-editor";
    editor.className = "layout-editor";
    editor.innerHTML = `<summary>Edit text and layout · no API charge</summary>${layoutFields("edit-card", record.recipe || {})}<label>Transparent single-item cutout<input id="edit-cutout" type="file" accept="image/png,image/webp"></label><label>Verified pack quantity<input id="edit-pack" type="number" min="1" max="12" value="${record.recipe?.pack_count || 1}"></label><label class="confirmation"><input type="checkbox" id="edit-cutout-confirmed"> I verified the cutout shows one correct product.</label><button class="secondary-btn" type="button" id="save-layout">Create card revision</button>`;
    $("download-modal").parentElement.append(editor);
    let uploadedCutout = record.recipe?.cutout_asset_id || null,
      lastFile = null;
    $("save-layout").addEventListener("click", async () => {
      if (composing) return;
      const recipe = readLayout($, "edit-card");
      const file = $("edit-cutout").files[0];
      recipe.pack_count = Number($("edit-pack").value);
      recipe.cutout_approved = $("edit-cutout-confirmed").checked;
      recipe.cutout_is_single_item = recipe.cutout_approved;
      composing = true;
      $("save-layout").disabled = true;
      try {
        if (file && file !== lastFile) {
          const body = new environment.FormData();
          body.append("file", file, file.name);
          body.append("role", "cutout");
          const result = await api("/api/assets", { method: "POST", body });
          uploadedCutout = result.asset_id;
          lastFile = file;
        }
        recipe.cutout_asset_id = uploadedCutout;
        const body = JSON.stringify({ recipe });
        await api(`/api/renders/${record.id}/compose`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": requestKey("compose", record.id + body),
          },
          body,
        });
        submissions.clear("compose");
        toast("Card revision queued · no paid API request");
        closeModal();
        setView("jobs");
        await loadJobs();
      } catch (error) {
        toast(error.message, true);
      } finally {
        composing = false;
        document.getElementById("save-layout")?.removeAttribute("disabled");
      }
    });
    if (!record.variant) {
      const label = document.createElement("label");
      label.textContent = "Historical export slot (MAIN or PT01–PT99)";
      const input = document.createElement("input");
      input.placeholder = "PT01";
      label.append(input);
      editor.append(label);
      const assign = document.createElement("button");
      assign.textContent = "Assign export slot";
      assign.className = "secondary-btn";
      editor.append(assign);
      assign.addEventListener("click", async () => {
        try {
          await api(`/api/renders/${record.id}/variant`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ variant: input.value.trim().toUpperCase() }),
          });
          toast("Export slot saved");
          await loadRenders();
        } catch (error) {
          toast(error.message, true);
        }
      });
    }
  }

  async function revise() {
    if (!selected || isRevising) return;
    const text = $("revision-prompt").value.trim();
    if (text.length < 5)
      return toast("Please explain the correction first.", true);
    if (
      config.mode === "live" &&
      !confirm("Send a new PAID image edit request?")
    )
      return;
    const snapshot = {
      render_id: selected.id,
      instructions: text,
      model: $("revision-model").value,
      quality: $("revision-quality").value,
      remember_rule: $("remember-revision").checked,
    };
    const key = requestKey("revise", JSON.stringify(snapshot));
    isRevising = true;
    $("revise-btn").disabled = true;
    try {
      const result = await api("/api/revise", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify(snapshot),
      });
      submissions.clear("revise");
      if (selected?.id === snapshot.render_id) closeModal();
      toast(
        result.pending_rule
          ? "Revision queued. The correction will be saved when you approve the result."
          : result.saved_rule
            ? "Revision queued and saved as permanent product rule"
            : result.mode === "demo"
              ? "Demo revision created"
              : "Revision queued for generation",
      );
      setView("jobs");
      await loadJobs();
    } catch (e) {
      toast(e.message, true);
    } finally {
      isRevising = false;
      $("revise-btn").disabled = false;
    }
  }

  async function loadRevisionHistory(id) {
    const request = ++historyRequest;
    const el = $("revision-history");
    el.textContent = "Loading version history...";
    try {
      const hist = await api(`/api/renders/${encodeURIComponent(id)}/history`);
      if (request !== historyRequest || selected?.id !== id) return;
      el.innerHTML = "<b>Revision history · " + hist.length + " version(s)</b>";
      for (const [i, v] of hist.entries()) {
        const row = document.createElement("div");
        row.className = "revision-item";
        row.innerHTML = `<span>v${i + 1} · ${escapeHtml(v.user)} · ${new Date(v.created_at).toLocaleString()}</span><small>${escapeHtml(v.prompt)}</small>`;
        const rules = v.memory_snapshot?.rules || [];
        const tag = document.createElement("small");
        tag.textContent = `Memory applied: ${rules.length} saved SKU correction(s) · Brand: ${v.memory_snapshot?.branding?.name || "none"}`;
        row.append(tag);
        if (v.url && v.id !== id) {
          const btn = document.createElement("button");
          btn.className = "revision-link";
          btn.textContent = "View this version";
          btn.addEventListener("click", async () => {
            await loadRenders();
            const rec = renderRecords.find((x) => x.id === v.id);
            if (rec) openModal(rec);
            else window.open(v.url, "_blank");
          });
          row.append(btn);
        }
        el.append(row);
      }
    } catch (e) {
      if (request === historyRequest)
        el.textContent = "Could not load revision history: " + e.message;
    }
  }

  function bind() {
    $("gallery-refresh").addEventListener("click", loadRenders);
    $("filter-status").addEventListener("change", () =>
      config.features?.library ? resetLibrary() : drawGallery(),
    );
    $("filter-sku").addEventListener("input", () =>
      config.features?.library ? resetLibrary() : drawGallery(),
    );
    $("library-select-page")?.addEventListener("click", () => {
      for (const r of renderRecords)
        if (r.status === "completed") selection.set(r.id, r);
      drawGallery();
    });
    $("library-select-filtered")?.addEventListener("click", selectFiltered);
    $("library-clear")?.addEventListener("click", () => {
      selection.clear();
      drawGallery();
    });
    $("library-export")?.addEventListener("click", previewExport);
    $("library-next")?.addEventListener("click", () => {
      if (nextCursor) {
        previousCursors.push(pageCursor);
        pageCursor = nextCursor;
        loadRenders();
      }
    });
    $("library-previous")?.addEventListener("click", () => {
      pageCursor = previousCursors.pop() ?? null;
      loadRenders();
    });
    $("close-modal").addEventListener("click", closeModal);
    $("modal").addEventListener("click", (e) => {
      if (e.target === $("modal")) closeModal();
    });
    document.addEventListener("keydown", modalKeydown);
    $("revise-btn").addEventListener("click", revise);
    $("approve-modal-btn").addEventListener(
      "click",
      () => selected && setApproval(selected, !selected.approved),
    );
  }

  return { bind, loadRenders, openModal, revise };
}
