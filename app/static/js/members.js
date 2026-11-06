/**
 * "Share & collaborators" (admin): logins that belong to one project. Whoever has such a login can open, change and
 * compute exactly this project and enter measurements – nothing else. Passwords are shown once, when they are made.
 */
import { t } from "./core/i18n.js";
import { $, escapeHtml, fmtDateTime } from "./core/util.js";
import { api } from "./core/api.js";

const dlg = () => $("membersDlg");

export async function openMembers(project) {
  if (!project?.id) return;
  const link = `${location.origin}${location.pathname}?p=${encodeURIComponent(project.id)}`;
  let list = null, error = "";
  try { list = await api(`/api/projects/${encodeURIComponent(project.id)}/members`); } catch (e) { error = e.message; }
  dlg().innerHTML = `<h3>${t("Share “{name}”", { name: escapeHtml(project.name) })}</h3>
    <p class="muted">${project.public ? t("The project is public: everybody with the link can look at it.") : t("The project is private: only you and its collaborators can open it.")}
      ${t("Collaborators can change and compute this project and enter measurements – nothing else.")}</p>
    <label>${t("Link")}<div class="row"><input readonly value="${escapeHtml(link)}"><button data-a="copylink" style="flex:0 0 auto">${t("copy")}</button></div></label>
    <h4>${t("Collaborators")}</h4>
    ${list === null ? `<div class="warn">${escapeHtml(error)}</div>` : list.length ? `<table class="mini">${list.map((m) => `<tr>
        <td class="wrap"><b>${escapeHtml(m.name)}</b>${m.note ? `<br><span class="muted">${escapeHtml(m.note)}</span>` : ""}</td>
        <td class="muted">${m.created ? fmtDateTime(m.created) : ""}</td>
        <td><button class="small" data-reset="${escapeHtml(m.name)}">${t("new password")}</button>
          <button class="small danger" data-del="${escapeHtml(m.name)}">🗑</button></td></tr>`).join("")}</table>`
      : `<p class="muted">${t("No collaborators yet.")}</p>`}
    <div class="grid2">
      <label>${t("Login name")}<input data-k="name" placeholder="${escapeHtml(t("e.g. firstname"))}" maxlength="32"></label>
      <label>${t("Note (only for you)")}<input data-k="note" maxlength="200"></label>
    </div>
    <div class="actions"><button class="primary" data-a="add">＋ ${t("Create login")}</button></div>
    <div data-out></div>
    <div class="actions"><button data-a="close">${t("Close")}</button></div>`;
  if (!dlg().open) dlg().showModal();
  const out = () => dlg().querySelector("[data-out]");
  const fail = (e) => { out().innerHTML = `<div class="warn">${escapeHtml(e.message)}</div>`; };
  const showSecret = (name, password) => {
    const text = t("TerraRF project “{project}”: {link} – log in with “{name}” and the password {password}", { project: project.name, link, name, password });
    out().innerHTML = `<div class="calc secret"><div>${t("Password for “{name}” (shown only now)", { name: escapeHtml(name) })}:
      <b><code data-pw>${escapeHtml(password)}</code></b></div>
      <textarea readonly rows="3">${escapeHtml(text)}</textarea><button class="small" data-a="copytext">${t("copy message")}</button></div>`;
    out().querySelector('[data-a="copytext"]').onclick = () => navigator.clipboard?.writeText(text);
  };
  dlg().querySelector('[data-a="copylink"]').onclick = () => navigator.clipboard?.writeText(link);
  dlg().querySelector('[data-a="close"]').onclick = () => dlg().close();
  dlg().querySelector('[data-a="add"]').onclick = async () => {
    const name = dlg().querySelector('[data-k="name"]').value.trim(), note = dlg().querySelector('[data-k="note"]').value;
    try {
      const r = await api(`/api/projects/${encodeURIComponent(project.id)}/members`, { method: "POST", body: { name, note } });
      await openMembers(project);
      showSecret(r.name, r.password);
    } catch (e) { fail(e); }
  };
  dlg().querySelectorAll("[data-reset]").forEach((b) => b.onclick = async () => {
    if (!confirm(t("Give “{name}” a new password? The old one stops working.", { name: b.dataset.reset }))) return;
    try { showSecret(b.dataset.reset, (await api(`/api/projects/${encodeURIComponent(project.id)}/members/${encodeURIComponent(b.dataset.reset)}/password`, { method: "POST", body: {} })).password); }
    catch (e) { fail(e); }
  });
  dlg().querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
    if (!confirm(t("Delete the login “{name}”?", { name: b.dataset.del }))) return;
    try { await api(`/api/projects/${encodeURIComponent(project.id)}/members/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); openMembers(project); }
    catch (e) { fail(e); }
  });
}
