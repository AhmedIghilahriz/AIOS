"use client";
import { useState, useEffect, useCallback } from "react";
import { API, authHeaders } from "../../lib/api";

/** Module I — Contentieux général (suivi de procédure).
 *  SQUELETTE : structure + routage prêts ; complétez les règles métier. */

const ETAPES = [
  "MISE_EN_DEMEURE", "ASSIGNATION", "MISE_EN_ETAT", "CONCLUSIONS",
  "CLOTURE", "PLAIDOIRIE", "DELIBERE", "JUGEMENT", "APPEL",
];
const JURIDICTIONS = [
  { v: "TJ", label: "Tribunal judiciaire" },
  { v: "TC", label: "Tribunal de commerce" },
  { v: "CPH", label: "Conseil de prud'hommes" },
  { v: "TA", label: "Tribunal administratif" },
  { v: "CA", label: "Cour d'appel" },
  { v: "REFERE", label: "Référé" },
];
const NIVEAU_BADGE: Record<string, string> = {
  CRITIQUE: "bg-red-100 text-red-700 border-red-200",
  ROUGE: "bg-red-100 text-red-700 border-red-200",
  PRIORITAIRE: "bg-orange-100 text-orange-700 border-orange-200",
  INFORMATIF: "bg-blue-50 text-blue-600 border-blue-100",
};

export default function ContentieuxPanel({ dossierId, notify }: { dossierId: string; notify: (m: string, ok?: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [etat, setEtat] = useState<any>(null);
  const [etape, setEtape] = useState(ETAPES[0]);
  const [synthese, setSynthese] = useState("");
  // Délais procéduraux
  const [jur, setJur] = useState("TJ");
  const [dateDecision, setDateDecision] = useState("");
  const [delais, setDelais] = useState<any[] | null>(null);

  const charger = useCallback(async () => {
    const r = await fetch(`${API}/api/contentieux/${dossierId}`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) { const d = await r.json(); setEtat(d); if (d.etape_actuelle) setEtape(d.etape_actuelle); }
  }, [dossierId]);

  useEffect(() => { if (open && !etat) charger(); }, [open, etat, charger]);

  const enregistrer = async () => {
    setBusy("etape");
    try {
      const r = await fetch(`${API}/api/contentieux/${dossierId}/etape`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ etape, infos: {} }),
      }).catch(() => null);
      if (!r?.ok) { notify("Erreur", false); return; }
      setEtat(await r.json()); notify(`Étape enregistrée : ${etape}`);
    } finally { setBusy(null); }
  };

  const calculerDelais = async () => {
    if (!dateDecision) { notify("Date de décision requise", false); return; }
    setBusy("delais"); setDelais(null);
    try {
      const url = `${API}/api/contentieux/${dossierId}/delais?juridiction=${jur}&date_decision=${encodeURIComponent(new Date(dateDecision).toISOString())}`;
      const r = await fetch(url, { headers: { ...authHeaders() } }).catch(() => null);
      if (!r?.ok) { notify("Erreur", false); return; }
      const d = await r.json(); setDelais(d.delais || []);
      if ((d.delais || []).length === 0) notify(d.message || "Aucun délai calculé", false);
    } finally { setBusy(null); }
  };

  const genererSynthese = async () => {
    setBusy("synthese"); setSynthese("");
    try {
      const r = await fetch(`${API}/api/contentieux/${dossierId}/synthese`, { headers: { ...authHeaders() } }).catch(() => null);
      if (!r?.ok) { notify("Erreur", false); return; }
      const d = await r.json(); setSynthese(d.synthese || ""); notify("Note générée — à relire");
    } finally { setBusy(null); }
  };

  return (
    <div className="mt-4 border rounded-xl overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 hover:bg-slate-100 text-left">
        <span className="text-sm font-semibold text-slate-800">⚖️ Contentieux — procédure (Module I)</span>
        <span className="text-slate-600 text-lg">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="p-4 space-y-3">
          <p className="text-xs text-amber-600 bg-amber-50 rounded px-2 py-1">Squelette — complétez les règles (délais par juridiction, enchaînement des étapes…).</p>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm text-gray-600">Étape actuelle :</span>
            <select value={etape} onChange={e => setEtape(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
              {ETAPES.map(e => <option key={e} value={e}>{e.replaceAll("_", " ")}</option>)}
            </select>
            <button onClick={enregistrer} disabled={busy === "etape"}
              className="bg-slate-700 text-white text-sm px-3 py-2 rounded-lg hover:bg-slate-800 disabled:opacity-50">
              {busy === "etape" ? "…" : "Enregistrer"}
            </button>
          </div>

          {/* Délais procéduraux */}
          <div className="border-t pt-3">
            <p className="text-sm font-medium text-gray-800 mb-2">Délais de recours</p>
            <div className="flex gap-2 flex-wrap items-end">
              <select value={jur} onChange={e => setJur(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
                {JURIDICTIONS.map(j => <option key={j.v} value={j.v}>{j.label}</option>)}
              </select>
              <div>
                <label className="text-xs text-gray-500 block mb-1">Date de décision / signification</label>
                <input type="date" value={dateDecision} onChange={e => setDateDecision(e.target.value)}
                  className="border rounded-lg px-3 py-2 text-sm" />
              </div>
              <button onClick={calculerDelais} disabled={busy === "delais"}
                className="bg-slate-700 text-white text-sm px-3 py-2 rounded-lg hover:bg-slate-800 disabled:opacity-50">
                {busy === "delais" ? "…" : "Calculer"}
              </button>
            </div>
            {delais && delais.length > 0 && (
              <ul className="mt-2 space-y-1">
                {delais.map((d, i) => (
                  <li key={i} className="flex items-center gap-2 flex-wrap text-sm">
                    <span className={`text-xs px-2 py-0.5 rounded-full border ${NIVEAU_BADGE[d.niveau_alerte] || "bg-gray-100"}`}>
                      {d.jours_restants > 0 ? `J-${d.jours_restants}` : "expiré"}
                    </span>
                    <span className="font-medium text-gray-800">{d.type}</span>
                    <span className="text-gray-500">→ {d.echeance}</span>
                    <span className="text-xs text-gray-400">({d.base_legale})</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <button onClick={genererSynthese} disabled={busy === "synthese"}
            className="bg-indigo-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
            {busy === "synthese" ? "Génération…" : "Note de stratégie (IA)"}
          </button>
          {synthese && <pre className="text-sm text-gray-700 whitespace-pre-wrap font-sans bg-gray-50 rounded-lg p-3">{synthese}</pre>}
        </div>
      )}
    </div>
  );
}
