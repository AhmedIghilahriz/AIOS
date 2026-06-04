"use client";
import { useState, useEffect, useCallback } from "react";
import { API, authHeaders } from "../../lib/api";

/** Module D — Fiche de préparation persistée & versionnée + historique + impression PDF. */

interface Fiche { id: string; version: number; type_rdv?: string; contenu?: string; created_at?: string | null; }

const TYPES = [
  { v: "decouverte", label: "Découverte" },
  { v: "approfondi", label: "Approfondi" },
  { v: "suivi", label: "Suivi" },
];
const fmtDate = (s?: string | null) => (s ? new Date(s).toLocaleString("fr-FR") : "");

/** Modale dédiée : lecture d'une fiche + impression. */
function FicheViewer({ fiche, onClose }: { fiche: Fiche; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[60] bg-black/50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[88vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-6 py-4">
          <h3 className="font-semibold text-gray-900">Fiche de préparation — v{fiche.version}
            <span className="text-xs font-normal text-gray-400 ml-2">{fiche.type_rdv} · {fmtDate(fiche.created_at)}</span>
          </h3>
          <div className="flex items-center gap-2">
            <button onClick={() => window.open(`${API}/api/fiches/${fiche.id}/imprimer`, "_blank", "noopener")}
              className="bg-[#1a3a5c] text-white text-sm px-3 py-1.5 rounded-lg hover:opacity-90">🖨 Imprimer / PDF</button>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-2xl leading-none">×</button>
          </div>
        </div>
        <div className="p-6 overflow-auto">
          <pre className="text-sm text-gray-800 whitespace-pre-wrap font-sans leading-relaxed">{fiche.contenu || "(vide)"}</pre>
        </div>
      </div>
    </div>
  );
}

export default function FichesPreparation({ dossierId, notify }: { dossierId: string; notify: (m: string, ok?: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [latest, setLatest] = useState<Fiche | null>(null);
  const [versions, setVersions] = useState<Fiche[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [choix, setChoix] = useState(false);            // affiche le choix nouvelle version / écraser
  const [typeRdv, setTypeRdv] = useState("decouverte");
  const [viewer, setViewer] = useState<Fiche | null>(null);

  const charger = useCallback(async () => {
    setLoading(true);
    try {
      const [r1, r2] = await Promise.all([
        fetch(`${API}/api/dossiers/${dossierId}/fiche-preparation`, { headers: { ...authHeaders() } }).catch(() => null),
        fetch(`${API}/api/dossiers/${dossierId}/fiches`, { headers: { ...authHeaders() } }).catch(() => null),
      ]);
      if (r1?.ok) { const d = await r1.json(); setLatest(d.fiche || null); if (d.fiche?.type_rdv) setTypeRdv(d.fiche.type_rdv); }
      if (r2?.ok) { const d = await r2.json(); setVersions(d.fiches || []); }
    } finally { setLoading(false); }
  }, [dossierId]);

  useEffect(() => { if (open) charger(); }, [open, charger]);

  const generer = async (ecraser: boolean) => {
    setBusy("gen"); setChoix(false);
    try {
      const r = await fetch(`${API}/api/dossiers/${dossierId}/fiche-preparation`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ type_rdv: typeRdv, ecraser }),
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Échec de la génération", false); return; }
      notify(ecraser ? `Fiche mise à jour (v${d.version})` : `Nouvelle fiche générée (v${d.version})`);
      await charger();
      setViewer(d);   // ouvre directement la nouvelle fiche
    } finally { setBusy(null); }
  };

  const ouvrir = async (f: Fiche) => {
    // si le contenu n'est pas déjà chargé (item d'historique), on le récupère
    if (f.contenu != null) { setViewer(f); return; }
    const r = await fetch(`${API}/api/fiches/${f.id}`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) setViewer(await r.json()); else notify("Fiche introuvable", false);
  };

  return (
    <div className="mt-4 border rounded-xl overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-teal-50/60 hover:bg-teal-50 text-left">
        <span className="text-sm font-semibold text-teal-900">📝 Fiche de préparation {versions.length > 0 && <span className="text-xs font-normal text-gray-500">(v{latest?.version ?? versions[0]?.version})</span>}</span>
        <span className="text-teal-700 text-lg">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="p-4 space-y-3">
          {loading ? (
            <p className="text-sm text-gray-400">Chargement…</p>
          ) : latest ? (
            <div className="bg-gray-50 rounded-lg p-3">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <p className="text-sm font-medium text-gray-800">Dernière version : v{latest.version}
                  <span className="text-xs text-gray-400 ml-2">{latest.type_rdv} · {fmtDate(latest.created_at)}</span>
                </p>
                <div className="flex gap-2">
                  <button onClick={() => ouvrir(latest)} className="text-xs bg-teal-600 text-white px-2.5 py-1.5 rounded-lg hover:bg-teal-700">Voir / Imprimer</button>
                  <button onClick={() => setChoix(c => !c)} disabled={busy === "gen"}
                    className="text-xs border border-teal-300 text-teal-700 px-2.5 py-1.5 rounded-lg hover:bg-teal-50 disabled:opacity-50">
                    {busy === "gen" ? "…" : "Régénérer"}
                  </button>
                </div>
              </div>
              <p className="text-xs text-gray-500 mt-2 line-clamp-2">{latest.contenu}</p>
            </div>
          ) : (
            <div className="flex items-center justify-between flex-wrap gap-2">
              <p className="text-sm text-gray-400">Aucune fiche générée pour ce dossier.</p>
              <button onClick={() => setChoix(true)} className="text-xs bg-teal-600 text-white px-3 py-1.5 rounded-lg hover:bg-teal-700">Générer la fiche</button>
            </div>
          )}

          {/* Choix : nouvelle version vs écraser */}
          {choix && (
            <div className="border rounded-lg p-3 bg-white space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm text-gray-600">Type de RDV :</span>
                <select value={typeRdv} onChange={e => setTypeRdv(e.target.value)} className="border rounded-lg px-2 py-1 text-sm">
                  {TYPES.map(t => <option key={t.v} value={t.v}>{t.label}</option>)}
                </select>
              </div>
              <div className="flex gap-2 flex-wrap">
                <button onClick={() => generer(false)} disabled={busy === "gen"}
                  className="text-xs bg-teal-600 text-white px-3 py-2 rounded-lg hover:bg-teal-700 disabled:opacity-50">
                  + Nouvelle version (garder l'historique)
                </button>
                {latest && (
                  <button onClick={() => generer(true)} disabled={busy === "gen"}
                    className="text-xs border border-amber-300 text-amber-700 px-3 py-2 rounded-lg hover:bg-amber-50 disabled:opacity-50">
                    Écraser la version actuelle
                  </button>
                )}
                <button onClick={() => setChoix(false)} className="text-xs text-gray-400 px-2 py-2 hover:text-gray-600">Annuler</button>
              </div>
            </div>
          )}

          {/* Historique */}
          {versions.length > 0 && (
            <div className="border-t pt-2">
              <button onClick={() => setShowHistory(h => !h)} className="text-xs text-gray-500 hover:text-gray-700">
                {showHistory ? "▾" : "▸"} Historique des fiches ({versions.length})
              </button>
              {showHistory && (
                <ul className="mt-2 space-y-1">
                  {versions.map(v => (
                    <li key={v.id} className="flex items-center gap-2 text-sm">
                      <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">v{v.version}</span>
                      <span className="text-gray-500 flex-1">{v.type_rdv} · {fmtDate(v.created_at)}</span>
                      <button onClick={() => ouvrir(v)} className="text-xs text-teal-700 hover:underline">Voir</button>
                      <button onClick={() => window.open(`${API}/api/fiches/${v.id}/imprimer`, "_blank", "noopener")}
                        className="text-xs text-blue-600 hover:underline">Imprimer</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      {viewer && <FicheViewer fiche={viewer} onClose={() => setViewer(null)} />}
    </div>
  );
}
