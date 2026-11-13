(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const fileInput = $("fileInput");
  const dropzone = $("dropzone");
  const fileList = $("fileList");
  const documentView = $("documentView");
  const documentMeta = $("documentMeta");
  const pageInfo = $("pageInfo");
  const textEditor = $("textEditor");
  const editorStatus = $("editorStatus");
  const projectName = $("projectName");
  const promptText = $("promptText");
  const charCount = $("charCount");
  const wordCount = $("wordCount");
  const toast = $("toast");
  const processButton = $("processButton");
  const copyButton = $("copyButton");
  const exportButton = $("exportButton");
  const resetButton = $("resetButton");
  const promptSelect = $("promptSelect");
  const exampleSelect = $("exampleSelect");
  const modelSelect = $("modelSelect");

  let files = [];
  let activeIndex = -1;
  let activeUrl = null;
  let requestController = null;

  function notify(message) {
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => toast.classList.remove("show"), 3200);
  }

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} o`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
  }

  function extension(file) {
    return file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
  }

  function isTextFile(file) {
    return ["txt", "md"].includes(extension(file)) || file.type === "text/plain";
  }

  function isAccepted(file) {
    return ["pdf", "png", "jpg", "jpeg", "webp", "gif", "txt", "md"].includes(extension(file));
  }

  function updateCharCount() {
    charCount.textContent = `${promptText.value.length} caractères`;
  }

  function updateWordCount() {
    const text = textEditor.innerText.trim();
    const count = text ? text.split(/\s+/).length : 0;
    wordCount.textContent = `${count} mot${count > 1 ? "s" : ""}`;
  }

  function revokePreviewUrl() {
    if (activeUrl) URL.revokeObjectURL(activeUrl);
    activeUrl = null;
  }

  function addFiles(incoming) {
    const accepted = [...incoming].filter(isAccepted);
    if (!accepted.length) {
      notify("Format non pris en charge. Choisissez un PDF, une image ou un fichier texte.");
      return;
    }
    for (const file of accepted) {
      const duplicate = files.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified);
      if (!duplicate) files.push(file);
    }
    activeIndex = files.length - 1;
    renderFileList();
    showFile(activeIndex);
    fileInput.value = "";
  }

  function renderFileList() {
    fileList.replaceChildren();
    if (!files.length) {
      const empty = document.createElement("div");
      empty.className = "empty-file";
      empty.textContent = "Aucun fichier importé";
      fileList.appendChild(empty);
      return;
    }

    files.forEach((file, index) => {
      const item = document.createElement("div");
      item.className = "file-item";
      item.style.borderColor = index === activeIndex ? "#788c4b" : "";

      const symbol = document.createElement("span");
      symbol.className = "file-symbol";
      symbol.textContent = (extension(file).slice(0, 3) || "DOC").toUpperCase();

      const info = document.createElement("span");
      info.className = "file-info";
      const name = document.createElement("span");
      name.className = "file-name";
      name.textContent = file.name;
      const size = document.createElement("span");
      size.className = "file-size";
      size.textContent = formatSize(file.size);
      info.append(name, size);

      const remove = document.createElement("button");
      remove.className = "remove-file";
      remove.type = "button";
      remove.setAttribute("aria-label", `Retirer ${file.name}`);
      remove.textContent = "×";

      item.addEventListener("click", (event) => {
        if (event.target.closest(".remove-file")) return;
        activeIndex = index;
        renderFileList();
        showFile(index);
      });

      remove.addEventListener("click", () => {
        if (index === activeIndex) revokePreviewUrl();
        files.splice(index, 1);
        activeIndex = files.length ? Math.min(index, files.length - 1) : -1;
        renderFileList();
        if (activeIndex >= 0) showFile(activeIndex);
        else resetPreview();
      });

      item.append(symbol, info, remove);
      fileList.appendChild(item);
    });
  }

  function showFile(index) {
    const file = files[index];
    if (!file) return;
    revokePreviewUrl();
    activeUrl = URL.createObjectURL(file);
    projectName.textContent = file.name;
    documentMeta.textContent = extension(file).toUpperCase() || "FICHIER";
    pageInfo.textContent = `${formatSize(file.size)} · fichier sélectionné`;
    documentView.replaceChildren();

    if (extension(file) === "pdf") {
      const frame = document.createElement("iframe");
      frame.src = activeUrl;
      frame.title = `Aperçu de ${file.name}`;
      documentView.appendChild(frame);
    } else if (file.type.startsWith("image/")) {
      const image = document.createElement("img");
      image.src = activeUrl;
      image.alt = `Aperçu de ${file.name}`;
      documentView.appendChild(image);
    } else if (isTextFile(file)) {
      file.text().then((content) => {
        if (files[activeIndex] !== file) return;
        const pre = document.createElement("pre");
        pre.style.cssText = "white-space:pre-wrap;max-width:100%;color:#ddd;font:12px/1.7 monospace";
        pre.textContent = content;
        documentView.replaceChildren(pre);
        textEditor.textContent = content;
        editorStatus.textContent = "Fichier texte chargé · prêt à être modifié";
        updateWordCount();
      });
    }
    editorStatus.textContent = "Document chargé · prêt à être traité";
  }

  function resetPreview() {
    revokePreviewUrl();
    projectName.textContent = "Sans titre";
    documentMeta.textContent = "APERÇU";
    pageInfo.textContent = "Aucun document";
    documentView.innerHTML = `
      <div class="document-placeholder">
        <div class="placeholder-icon">▧</div>
        <strong>Votre document apparaîtra ici</strong>
        <p>Importez un PDF ou une image depuis le panneau de gauche.</p>
        <span class="placeholder-line"></span><span class="placeholder-line short"></span><span class="placeholder-line"></span>
      </div>`;
    textEditor.innerHTML = `
      <div class="editor-empty">
        <span class="editor-empty-icon">✳</span>
        <strong>Prêt à travailler</strong>
        <p>Le texte du document apparaîtra dans cet espace. Vous pourrez ensuite le corriger, le réorganiser ou le copier.</p>
      </div>`;
    editorStatus.textContent = "En attente d'un document";
    updateWordCount();
  }

  async function readError(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const data = await response.json();
      return data.detail || data.message || `Erreur HTTP ${response.status}`;
    }
    const text = await response.text();
    return text || `Erreur HTTP ${response.status}`;
  }

  async function processCurrentFile() {
    if (activeIndex < 0 || !files[activeIndex]) {
      notify("Importez d'abord un document.");
      return;
    }

    const file = files[activeIndex];
    if (isTextFile(file)) {
      textEditor.textContent = await file.text();
      editorStatus.textContent = "Texte chargé · modifiable";
      updateWordCount();
      notify("Texte chargé.");
      return;
    }

    if (requestController) requestController.abort();
    requestController = new AbortController();
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("model", modelSelect.value);
    form.append("prompt", promptText.value);
    form.append("example", exampleSelect.value);
    processButton.disabled = true;
    processButton.textContent = "Extraction en cours…";
    editorStatus.textContent = "Envoi au serveur · extraction OCR en cours";
    notify("Document envoyé au serveur pour extraction.");

    try {
      const response = await fetch("/api/extract", {
        method: "POST",
        body: form,
        signal: requestController.signal
      });
      if (!response.ok) throw new Error(await readError(response));
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        throw new Error("Le serveur a renvoyé une réponse inattendue.");
      }
      const result = await response.json();
      if (typeof result.text !== "string") throw new Error("Le serveur n'a pas renvoyé de texte exploitable.");
      textEditor.textContent = result.text;
      editorStatus.textContent = `Extraction terminée · ${result.pages_processed || 1} page(s)`;
      updateWordCount();
      notify(result.text.trim() ? "Extraction terminée." : "Aucun texte détecté dans ce document.");
    } catch (error) {
      if (error.name === "AbortError") return;
      console.error("Erreur d'extraction :", error);
      editorStatus.textContent = "Échec de l'extraction";
      notify(error.message || "Une erreur est survenue pendant l'extraction.");
    } finally {
      processButton.disabled = false;
      processButton.innerHTML = '<span class="button-spark">✦</span> Extraire le texte <span class="button-arrow">→</span>';
      requestController = null;
    }
  }

  fileInput.addEventListener("change", (event) => addFiles(event.target.files));
  ["dragenter", "dragover"].forEach((type) => dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.add("dragover");
  }));
  ["dragleave", "drop"].forEach((type) => dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragover");
  }));
  dropzone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
  window.addEventListener("dragover", (event) => {
    if (event.dataTransfer?.types?.includes("Files")) event.preventDefault();
  });
  window.addEventListener("drop", (event) => {
    if (event.dataTransfer?.types?.includes("Files")) event.preventDefault();
  });

  const promptPresets = {
    layout: "Décris précisément la mise en page du document. Repère les blocs de texte, les titres, les images et les formules. Conserve l'ordre de lecture.",
    text: "Extrais fidèlement tout le texte visible. Respecte les paragraphes, les titres et l'ordre de lecture. N'invente aucun contenu.",
    structure: "Identifie les différentes zones du document : titres, paragraphes, colonnes, tableaux, images, légendes et notes.",
    math: "Transcris le texte et les expressions mathématiques avec précision. Préserve les symboles, les indices, les exposants et la structure des formules."
  };
  const examplePresets = {
    book: "Analyse cette page de livre. Respecte les paragraphes, les titres, les notes de bas de page et l'ordre de lecture.",
    article: "Analyse cet article. Repère le titre, les colonnes, les intertitres, les images et leurs légendes.",
    math: "Analyse ce document scientifique. Préserve les formules, les symboles, les tableaux et les références."
  };

  promptSelect.addEventListener("change", (event) => {
    if (promptPresets[event.target.value]) promptText.value = promptPresets[event.target.value];
    updateCharCount();
  });
  exampleSelect.addEventListener("change", (event) => {
    if (examplePresets[event.target.value]) promptText.value = examplePresets[event.target.value];
    updateCharCount();
  });
  promptText.addEventListener("input", updateCharCount);
  textEditor.addEventListener("input", updateWordCount);
  processButton.addEventListener("click", processCurrentFile);

  copyButton.addEventListener("click", async () => {
    const text = textEditor.innerText.trim();
    if (!text) return notify("Aucun texte à copier.");
    try {
      await navigator.clipboard.writeText(text);
      notify("Texte copié.");
    } catch {
      notify("Copie non autorisée par le navigateur.");
    }
  });

  exportButton.addEventListener("click", () => {
    const text = textEditor.innerText.trim();
    if (!text) return notify("Aucun texte à exporter.");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${(files[activeIndex]?.name || "texte-extrait").replace(/\.[^.]+$/, "")}.txt`;
    link.click();
    URL.revokeObjectURL(url);
    notify("Fichier texte exporté.");
  });

  resetButton.addEventListener("click", () => {
    if (requestController) requestController.abort();
    files = [];
    activeIndex = -1;
    fileInput.value = "";
    renderFileList();
    resetPreview();
    notify("Espace de travail réinitialisé.");
  });

  document.querySelectorAll(".tool-button").forEach((button, index) => {
    button.addEventListener("click", () => {
      textEditor.focus();
      const command = index === 1 ? "bold" : index === 2 ? "italic" : "formatBlock";
      document.execCommand(command, false, index === 0 ? "p" : undefined);
      updateWordCount();
    });
  });

  renderFileList();
  updateCharCount();
  updateWordCount();
  console.log("Atelier initialisé.");
})();
