// /upload: several PDFs, picked or dropped. A drop only fills the list, because
// an uploaded file stays archived for good; the Upload button sends. Files go
// one at a time, each as its own POST /upload, so one bad file stops nothing
// and each row carries the server's own answer. The PDF checks stay on the
// server; only what cannot or should not be sent is refused here.
(function () {
  "use strict";
  const form = document.getElementById("upload-form");
  if (!form) return;

  const zone = document.getElementById("drop-zone");
  const input = document.getElementById("upload-input");
  const list = document.getElementById("upload-list");
  const note = document.getElementById("upload-note");
  const send = document.getElementById("upload-send");
  const maxFiles = Number(form.dataset.maxFiles);
  const maxMb = Number(form.dataset.maxMb);

  // state: blocked | waiting | sending | received | refused | failed
  let entries = [];
  let sending = false;

  function size(bytes) {
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  function count(state) {
    return entries.filter((e) => e.state === state).length;
  }

  function refresh() {
    const n = count("waiting");
    send.disabled = sending || n === 0;
    if (sending) send.textContent = "Uploading...";
    else if (n === 0) send.textContent = "Upload";
    else send.textContent = n === 1 ? "Upload 1 file" : "Upload " + n + " files";
  }

  function message(entry, text, css) {
    const box = document.createElement("div");
    box.textContent = text;
    if (css) box.className = css;
    entry.status.replaceChildren(box);
  }

  function action(entry, label, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn-clear";
    button.textContent = label;
    button.addEventListener("click", handler);
    entry.action.replaceChildren(button);
  }

  function remove(entry) {
    entries = entries.filter((e) => e !== entry);
    entry.row.remove();
    refresh();
  }

  function waiting(entry) {
    entry.state = "waiting";
    message(entry, "Waiting");
    action(entry, "Remove", () => remove(entry));
  }

  function blocked(entry, text) {
    entry.state = "blocked";
    message(entry, text, "result error");
    action(entry, "Remove", () => remove(entry));
  }

  function failed(entry, text) {
    entry.state = "failed";
    message(entry, text, "result error");
    action(entry, "Try again", () => {
      waiting(entry);
      sendAll();
    });
  }

  function addRow(file) {
    const row = document.createElement("li");
    row.className = "upload-row";
    const name = document.createElement("span");
    name.className = "upload-name";
    name.textContent = file.name;
    const bytes = document.createElement("span");
    bytes.className = "hint-inline";
    const status = document.createElement("div");
    const act = document.createElement("span");
    row.append(name, bytes, status, act);
    list.append(row);
    return { file, row, bytes, status, action: act, state: "waiting" };
  }

  // `candidates`: [{ file, folder }], one drop or one pick.
  function add(candidates) {
    let repeated = 0;
    let over = 0;
    candidates.forEach(({ file, folder }) => {
      const key = [file.name, file.size, file.lastModified].join("|");
      if (entries.some((e) => e.key === key)) {
        repeated += 1;
        return;
      }
      if (count("waiting") >= maxFiles) {
        over += 1;
        return;
      }
      const entry = addRow(file);
      entry.key = key;
      entries.push(entry);
      if (folder) {
        blocked(entry, "This is a folder. Open it and drop the PDF files inside.");
        return;
      }
      entry.bytes.textContent = size(file.size);
      if (file.size > maxMb * 1048576) {
        blocked(entry, "file exceeds the " + maxMb + " MB limit");
      } else {
        waiting(entry);
      }
    });
    const lines = [];
    if (repeated) lines.push(repeated + " already added, not added again.");
    if (over) lines.push(over + " not added: " + maxFiles + " files at a time.");
    note.textContent = lines.join(" ");
    note.hidden = lines.length === 0;
    refresh();
  }

  async function upload(entry) {
    entry.state = "sending";
    message(entry, "Uploading...");
    entry.action.replaceChildren();

    const body = new FormData();
    body.append("file", entry.file, entry.file.name);
    body.append("target_group_id", form.elements.target_group_id.value);
    body.append("manual_task_id", form.elements.manual_task_id.value);

    let answer;
    let html;
    try {
      answer = await fetch("/upload", { method: "POST", body });
      html = await answer.text();
    } catch (err) {
      failed(entry, "Failed to send. Nothing was saved for this file.");
      return;
    }
    if (answer.status !== 200 && answer.status !== 422) {
      failed(entry, "Failed to send (the server answered " + answer.status + ").");
      return;
    }
    if (answer.status === 200) {
      // Its answer is now under Recent uploads. The entry stays, so the same
      // file dropped again is still recognised.
      entry.state = "received";
      entry.row.remove();
      htmx.trigger(document.body, "upload-sent");
      return;
    }
    entry.state = "refused";
    // `_result.html`, rendered and escaped by the server.
    entry.status.innerHTML = html;
  }

  async function sendAll() {
    if (sending) return;
    sending = true;
    refresh();
    let entry;
    while ((entry = entries.find((e) => e.state === "waiting"))) {
      await upload(entry);
    }
    sending = false;
    refresh();
  }

  document.getElementById("upload-choose").addEventListener("click", () => input.click());

  input.addEventListener("change", () => {
    add(Array.from(input.files, (file) => ({ file, folder: false })));
    input.value = ""; // or picking the same file again fires no change
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    sendAll();
  });

  // Read synchronously: the browser empties the drop's data after this event.
  function dropped(transfer) {
    const items = Array.from(transfer.items || []).filter((i) => i.kind === "file");
    if (items.length === 0) {
      return Array.from(transfer.files, (file) => ({ file, folder: false }));
    }
    return items.map((item) => {
      const fsEntry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
      return { file: item.getAsFile(), folder: Boolean(fsEntry && fsEntry.isDirectory) };
    }).filter((c) => c.file);
  }

  function hasFiles(event) {
    return Array.from(event.dataTransfer.types || []).includes("Files");
  }

  // On the whole page, not the zone: a file dropped beside the zone would
  // otherwise make the browser open it and leave this page.
  window.addEventListener("dragover", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    zone.classList.add("is-dragover");
  });
  window.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget) zone.classList.remove("is-dragover");
  });
  window.addEventListener("drop", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    zone.classList.remove("is-dragover");
    add(dropped(event.dataTransfer));
  });

  window.addEventListener("beforeunload", (event) => {
    if (sending) event.preventDefault();
  });
})();
