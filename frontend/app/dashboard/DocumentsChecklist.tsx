"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { API, authHeaders } from "../../lib/api";

/** Module A — Checklist métier des pièces : génération, statuts, upload, prévisualisation PDF. */

interface Doc {
  id: string;
  nom: string;
  statut: string;            // recu | valide | attendu | refuse
  type_doc?: string;
  a_fichier?: boolean;
  recu_at?: string | null;
}

const STATUT_BADGE: Record<string, { label: string; cls: string }> = {
  recu:    { label: "Reçu", cls: "bg-green-100 text-green-700 border-green-200" },
  valide:  { label: "Validé", cls: "bg-emerald-100 text-emerald-700 border-emerald-200" },
  attendu: { label: "En attente", cls: "bg-amber-100 text-amber-700 border-amber-200" },
  refuse:  { label: "Non conforme", cls: "bg-red-100 text-red-700 border-red-200" },
};
// valeurs envoyées au PATCH
const OPTIONS = [
  { v: "recu", label: "Reçu" },
  { v: "attente", label: "En attente" },
  { v: "non_conforme", label: "Non conforme" },
  { v: "valide", label: "Validé" },
];
const toPatch = (statut: string) => (statut === "attendu" ? "attente" : statut === "refuse" ? "non_conforme" : statut);

export default function DocumentsChecklist({
  dossierId, notify, onRefresh,
}: { dossierId: string; notify: (m: string, ok?: boolean) => void; onRefresh?: () => void }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const charger = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/dossiers/${dossierId}/documents`, { headers: { ...authHeaders() } }).catch(() => null);
      if (r?.ok) { const d = await r.json(); setDocs(d.documents || []); }
    } finally { setLoading(false); }
  }, [dossierId]);

  useEffect(() => { charger(); }, [charger]);

  const generer = async () => {
    setBusy("gen");
    try {
      const r = await fetch(`${API}/api/dossiers/${dossierId}/documents/generer-liste`, {
        method: "POST", headers: { ...authHeaders() },
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Erreur", false); return; }
      setDocs(d.documents || []);
      notify(`Checklist générée — ${d.total ?? 0} pièce(s)`);
      onRefresh?.();
    } finally { setBusy(null); }
  };

  const changerStatut = async (doc: Doc, statut: string) => {
    setBusy(doc.id);
    try {
      const r = await fetch(`${API}/api/documents/${doc.id}/statut`, {
        method: "PATCH", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ statut }),
      }).catch(() => null);
      if (!r?.ok) { notify("Erreur", false); return; }
      const d = await r.json();
      setDocs(prev => prev.map(x => (x.id === doc.id ? { ...x, statut: d.statut } : x)));
      onRefresh?.();
    } finally { setBusy(null); }
  };

  const televerser = async (f: File) => {
    setBusy("upload");
    try {
      const form = new FormData();
      form.append("file", f);
      const r = await fetch(`${API}/api/documents/upload/${dossierId}`, {
        method: "POST", headers: { ...authHeaders() }, body: form,
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Échec de l'upload", false); return; }
      if (d.checklist) setDocs(d.checklist); else await charger();
      notify(`« ${d.nom} » reçu ✓`);
      onRefresh?.();
    } finally { setBusy(null); if (fileRef.current) fileRef.current.value = ""; }
  };

  const apercu = (doc: Doc) => {
    if (!doc.a_fichier) { notify("Aucun fichier associé à cette pièce", false); return; }
    window.open(`${API}/api/documents/${doc.id}/fichier`, "_blank", "noopener");
  };

  // Supprime une pièce ajoutée par erreur (avec confirmation) → retirée de l'analyse IA.
  const supprimer = async (doc: Doc) => {
    const estChecklist = (doc.type_doc || "") === "checklist";
    const message = estChecklist
      ? `Retirer le fichier de « ${doc.nom} » ?\nLa pièce restera « En attente » et son contenu ne sera plus pris en compte par l'IA.`
      : `Supprimer définitivement « ${doc.nom} » ?\nLe fichier et son texte extrait seront retirés de l'analyse IA.`;
    if (!confirm(message)) return;
    setBusy(doc.id);
    try {
      const r = await fetch(`${API}/api/documents/${doc.id}`, {
        method: "DELETE", headers: { ...authHeaders() },
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Échec de la suppression", false); return; }
      if (d.checklist) setDocs(d.checklist); else await charger();
      notify(d.reset ? `Fichier retiré de « ${doc.nom} »` : `« ${doc.nom} » supprimé`);
      onRefresh?.();
    } finally { setBusy(null); }
  };

  const recus = docs.filter(d => d.statut === "recu" || d.statut === "valide").length;

  return (
    <div className="mt-4 border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 bg-blue-50/50">
        <span className="text-sm font-semibold text-blue-900">📂 Pièces du dossier {docs.length > 0 && <span className="text-xs font-normal text-gray-500">({recus}/{docs.length} reçues)</span>}</span>
        <div className="flex items-center gap-2">
          <button onClick={() => fileRef.current?.click()} disabled={busy === "upload"}
            className="text-xs border border-blue-300 text-blue-700 px-2.5 py-1.5 rounded-lg hover:bg-blue-100 disabled:opacity-50">
            {busy === "upload" ? "Envoi…" : "Téléverser"}
          </button>
          <input ref={fileRef} type="file" className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) televerser(f); }} />
          <button onClick={generer} disabled={busy === "gen"}
            className="text-xs bg-blue-600 text-white px-2.5 py-1.5 rounded-lg hover:bg-blue-700 disabled:opacity-50">
            {busy === "gen" ? "…" : "Générer la liste"}
          </button>
        </div>
      </div>

      <div className="p-4">
        {loading ? (
          <p className="text-sm text-gray-400">Chargement…</p>
        ) : docs.length === 0 ? (
          <p className="text-sm text-gray-400">Aucune pièce. Cliquez « Générer la liste » pour créer la checklist métier de ce dossier.</p>
        ) : (
          <ul className="space-y-1.5">
            {docs.map(doc => {
              const badge = STATUT_BADGE[doc.statut] || STATUT_BADGE.attendu;
              return (
                <li key={doc.id} className="flex items-center gap-2 flex-wrap">
                  <span className={`text-xs px-2 py-0.5 rounded-full border shrink-0 ${badge.cls}`}>{badge.label}</span>
                  <button onClick={() => apercu(doc)} title={doc.a_fichier ? "Prévisualiser le fichier" : "Aucun fichier"}
                    className={`text-sm text-left flex-1 min-w-[140px] ${doc.a_fichier ? "text-blue-700 hover:underline cursor-pointer" : "text-gray-700 cursor-default"}`}>
                    {doc.nom}{doc.a_fichier && " 📎"}
                  </button>
                  <select value={toPatch(doc.statut)} disabled={busy === doc.id}
                    onChange={e => changerStatut(doc, e.target.value)}
                    className="text-xs border rounded-lg px-2 py-1 text-gray-600 disabled:opacity-50">
                    {OPTIONS.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
                  </select>
                  {(doc.a_fichier || (doc.type_doc || "") !== "checklist") && (
                    <button onClick={() => supprimer(doc)} disabled={busy === doc.id}
                      title={(doc.type_doc || "") === "checklist" ? "Retirer le fichier (ne sera plus analysé par l'IA)" : "Supprimer cette pièce (ne sera plus analysée par l'IA)"}
                      className="text-xs text-red-600 border border-red-200 px-2 py-1 rounded-lg hover:bg-red-50 disabled:opacity-50 shrink-0">
                      🗑
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
