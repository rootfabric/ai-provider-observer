/* Admin cabinet front-end: auth (login / first-run setup) and provider keys. */

const $ = (sel) => document.querySelector(sel);
const authView = $("#auth-view");
const cabinetView = $("#cabinet-view");
const authMsg = $("#auth-msg");
let currentSlug = null;

function msg(el, text, ok = false) {
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "err");
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...opts,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = body && body.detail;
    const text = typeof detail === "string" ? detail : (detail && detail.error) || `Ошибка ${res.status}`;
    throw new Error(text);
  }
  return body;
}

/* ---------- boot ---------- */

async function boot() {
  let session;
  try {
    session = await api("/api/auth/session");
  } catch (e) {
    showAuth(false, e.message);
    return;
  }
  if (session.authenticated) {
    showCabinet(session.username);
  } else {
    showAuth(session.needs_setup);
  }
}

function showAuth(needsSetup, errorText = "") {
  authView.hidden = false;
  cabinetView.hidden = true;
  if (needsSetup) {
    $("#auth-title").textContent = "Первичная настройка";
    $("#auth-sub").textContent = "Создайте единственную учётную запись администратора кабинета.";
    $("#auth-submit").textContent = "Создать аккаунт и войти";
    $("#password").setAttribute("autocomplete", "new-password");
    $("#login-form").dataset.mode = "setup";
  } else {
    $("#auth-title").textContent = "Вход";
    $("#auth-sub").textContent = "Личный кабинет наблюдателя";
    $("#auth-submit").textContent = "Войти";
    $("#password").setAttribute("autocomplete", "current-password");
    delete $("#login-form").dataset.mode;
  }
  if (errorText) msg(authMsg, errorText);
}

function showCabinet(username) {
  authView.hidden = true;
  cabinetView.hidden = false;
  $("#who").textContent = username || "";
  loadProviders();
}

/* ---------- auth flows ---------- */

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const mode = $("#login-form").dataset.mode === "setup" ? "/api/auth/setup" : "/api/auth/login";
  try {
    await api(mode, {
      method: "POST",
      body: JSON.stringify({
        username: $("#username").value,
        password: $("#password").value,
      }),
    });
    location.reload();
  } catch (e) {
    msg(authMsg, e.message);
  }
});

$("#logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST", body: "{}" }).catch(() => {});
  location.href = "/";
});

$("#pw-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const pwMsg = $("#pw-msg");
  try {
    await api("/api/admin/change-password", {
      method: "POST",
      body: JSON.stringify({
        old_password: $("#old_password").value,
        new_password: $("#new_password").value,
      }),
    });
    msg(pwMsg, "Пароль изменён.", true);
    ev.target.reset();
  } catch (e) {
    msg(pwMsg, e.message);
  }
});

/* ---------- providers ---------- */

async function loadProviders() {
  const rows = $("#prov-rows");
  rows.innerHTML = "<tr><td colspan=4>Загрузка…</td></tr>";
  try {
    const data = await api("/api/admin/providers");
    rows.innerHTML = "";
    for (const p of data.providers) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${escapeHtml(p.label)}</strong><br><span style="color:#8b93a3;font-size:12px;">${p.slug}</span></td>
        <td>${p.enabled
          ? '<span class="badge on">вкл</span>'
          : '<span class="badge off">отключён</span>'}</td>
        <td>${p.overridden
          ? '<span class="badge on">из кабинета</span>'
          : '<span class="badge env">.env</span>'}</td>
        <td style="white-space:nowrap;">
          <button class="secondary edit-btn" data-slug="${p.slug}">Изменить</button>
          ${p.overridden ? `<button class="secondary reset-btn" data-slug="${p.slug}">Сбросить</button>` : ""}
        </td>`;
      rows.appendChild(tr);
    }
    rows.querySelectorAll(".edit-btn").forEach((b) =>
      b.addEventListener("click", () => openEdit(b.dataset.slug, data.providers))
    );
    rows.querySelectorAll(".reset-btn").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm(`Сбросить настройки «${b.dataset.slug}» к .env?`)) return;
        await api(`/api/admin/providers/${b.dataset.slug}`, { method: "DELETE" }).catch((e) => alert(e.message));
        loadProviders();
      })
    );
  } catch (e) {
    rows.innerHTML = `<tr><td colspan=4>${escapeHtml(e.message)}</td></tr>`;
  }
}

function openEdit(slug, providers) {
  const p = providers.find((x) => x.slug === slug);
  if (!p) return;
  currentSlug = slug;
  $("#edit-title").textContent = p.label;
  const wrap = $("#edit-fields");
  wrap.innerHTML = "";
  for (const f of p.fields) {
    const div = document.createElement("div");
    div.className = "field";
    const inputHtml = f.secret
      ? `<input id="fld-${f.name}" type="password" placeholder="${f.is_set ? "•••• (задан)" : ""}" autocomplete="new-password">`
      : `<input id="fld-${f.name}" type="text" value="${escapeHtml(f.value || "")}"
           placeholder="${escapeHtml(f.placeholder || "")}">`;
    div.innerHTML = `<label for="fld-${f.name}">${escapeHtml(f.label)}${f.secret && f.is_set ? " — уже задан, оставьте пустым чтобы не менять" : ""}</label>${inputHtml}`;
    wrap.appendChild(div);
  }
  $("#edit-enabled").checked = !!p.enabled;
  $("#edit-dialog").showModal();
}

$("#edit-cancel").addEventListener("click", () => $("#edit-dialog").close());

$("#edit-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!currentSlug) return;
  const payload = { enabled: $("#edit-enabled").checked };
  document.querySelectorAll("#edit-fields input[id^='fld-']").forEach((inp) => {
    payload[inp.id.replace("fld-", "")] = inp.value;
  });
  try {
    await api(`/api/admin/providers/${currentSlug}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    $("#edit-dialog").close();
    loadProviders();
  } catch (e) {
    alert(e.message);
  }
});

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

boot();
