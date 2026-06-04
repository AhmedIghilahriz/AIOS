"use client";
import { useState, useEffect, useCallback } from "react";
import { API, authHeaders } from "../../lib/api";

/** Module J — Contentieux des pharmaciens (non-concurrence / ARS / CPAM / Ordre).
 *  SQUELETTE : structure + routage prêts ; complétez les règles métier. */

const TYPES = ["CLAUSE_NON_CONCURRENCE", "RECOURS_ARS", "INDU_CPAM", "SANCTION_ORDRE", "DECONVENTIONNEMENT"];
// ids alignés sur CRITERES_BASE du backend (module_j.py)
const CRITERES_BASE = [
  { id: "limite_temps", label: "Limitée dans le temps" },
  { id: "limite_espace", label: "Limitée dans l'espace" },
  { id: "limite_activite", label: "Limitée à l'activité officinale" },
  { id: "interet_legitime", label: "Intérêt légitime à protéger" },
  { id: "proportionnee", label: "Proportionnée" },
];
const CRITERE_CONTREPARTIE = { id: "contrepartie_financiere", label: "Contrepartie financière (contrat de travail)" };
// Types de recours alignés sur DELAIS_RECOURS du backend
const RECOURS = [
  { v: "RECOURS_ARS", label: "Recours ARS (TA)" },
  { v: "RECOURS_GRACIEUX_ARS", label: "Recours gracieux ARS" },
  { v: "INDU_CPAM", label: "Indu CPAM (CRA)" },
  { v: "SANCTION_ORDRE", label: "Sanction ordinale (appel)" },
  { v: "DECONVENTIONNEMENT", label: "Déconventionnement" },
];
const NIVEAU_BADGE: Record<string, string> = {
  CRITIQUE: "bg-red-100 text-red-700 border-red-200",
  ROUGE: "bg-red-100 text-red-700 border-red-200",
  PRIORITAIRE: "bg-orange-100 text-orange-700 border-orange-200",
  INFORMATIF: "bg-blue-50 text-blue-600 border-blue-100",
};

export default function ContentieuxPharmaPanel({ dossierId, notify }: { dossierId: string; notify: (m: string, ok?: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [etat, setEtat] = useState<any>(null);
  const [type, setType] = useState(TYPES[0]);
  const [criteres, setCriteres] = useState<Set<string>>(new Set());
  const [typeClause, setTypeClause] = useState<"cession" | "travail">("cession");
  const [analyse, setAnalyse] = useState<any>(null);
  const [synthese, setSynthese] = useState("");
  // Délai de recours
  const [recours, setRecours] = useState(RECOURS[0].v);
  const [dateNotif, setDateNotif] = useState("");
  const [delai, setDelai] = useState<any>(null);

  const criteresAffiches = typeClause === "travail" ? [...CRITERES_BASE, CRITERE_CONTREPARTIE] : CRITERES_BASE;

  const charger = useCallback(async () => {
    const r = await fetch(`${API}/api/contentieux-pharma/${dossierId}`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) { const d = await r.json(); setEtat(d); if (d.type) setType(d.type); }
  }, [dossierId]);

  useEffect(() => { if (open && !etat) charger(); }, [open, etat, charger]);

  const toggle = (id: string) =>
    setCriteres(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const enregistrer = async () => {
    setBusy("type");
    try {
      const r = await fetch(`${API}/api/contentieux-pharma/${dossierId}`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ type_contentieux: type, infos: {} }),
      }).catch(() => null);
      if (!r?.ok) { notify("Erreur", false); return; }
      setEtat(await r.json()); notify(`Contentieux enregistré : ${type}`);
    } finally { setBusy(null); }
  };

  const analyserClause = async () => {
    setBusy("clause"); setAnalyse(null);
    try {
      const r = await fetch(`${API}/api/contentieux-pharma/${dossierId}/clause-non-concurrence`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ criteres_remplis: Array.from(criteres), type_clause: typeClause }),
      }).catch(() => null);
      if (!r?.ok) { notify("Erreur", false); return; }
      const d = await r.json(); setAnalyse(d);
      notify(`Clause : ${d.appreciation} (${d.nb_remplis}/${d.nb_total})`);
    } finally { setBusy(null); }
  };

  const calculerDelai = async () => {
    if (!dateNotif) { notify("Date de notification requise", false); return; }
    setBusy("delai"); setDelai(null);
    try {
      const url = `${API}/api/contentieux-pharma/${dossierId}/delai-recours?type_contentieux=${recours}&date_notification=${encodeURIComponent(new Date(dateNotif).toISOString())}`;
      const r = await fetch(url, { headers: { ...authHeaders() } }).catch(() => null);
      if (!r?.ok) { notify("Erreur", false); return; }
      const d = await r.json();
      if (d.jours_restants == null) { notify(d.message || "Type inconnu", false); return; }
      setDelai(d);
    } finally { setBusy(null); }
  };

  const genererSynthese = async () => {
    setBusy("synthese"); setSynthese("");
    try {
      const r = await fetch(`${API}/api/contentieux-pharma/${dossierId}/synthese`, { headers: { ...authHeaders() } }).catch(() => null);
      if (!r?.ok) { notify("Erreur", false); return; }
      const d = await r.json(); setSynthese(d.synthese || ""); notify("Note générée — à relire");
    } finally { setBusy(null); }
  };

  return (
    <div className="mt-4 border rounded-xl overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-rose-50/60 hover:bg-rose-50 text-left">
        <span className="text-sm font-semibold text-rose-900">💊 Contentieux pharmacien (Module J)</span>
        <span className="text-rose-700 text-lg">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="p-4 space-y-4">
          <p className="text-xs text-amber-600 bg-amber-50 rounded px-2 py-1">Squelette — complétez les règles (délais de recours, critères de validité…).</p>

          {/* Type de contentieux */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm text-gray-600">Type :</span>
            <select value={type} onChange={e => setType(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
              {TYPES.map(t => <option key={t} value={t}>{t.replaceAll("_", " ")}</option>)}
            </select>
            <button onClick={enregistrer} disabled={busy === "type"}
              className="bg-rose-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-rose-700 disabled:opacity-50">
              {busy === "type" ? "…" : "Enregistrer"}
            </button>
          </div>

          {/* Clause de non-concurrence */}
          <div className="border-t pt-3">
            <div className="flex items-center gap-2 flex-wrap mb-2">
              <p className="text-sm font-medium text-gray-800">Validité d'une clause de non-concurrence</p>
              <select value={typeClause} onChange={e => { setTypeClause(e.target.value as "cession" | "travail"); setAnalyse(null); }}
                className="border rounded-lg px-2 py-1 text-xs">
                <option value="cession">Cession (vendeur)</option>
                <option value="travail">Contrat de travail (adjoint)</option>
              </select>
            </div>
            <div className="space-y-1">
              {criteresAffiches.map(c => (
                <label key={c.id} className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                  <input type="checkbox" checked={criteres.has(c.id)} onChange={() => toggle(c.id)} />
                  <span>{c.label}</span>
                </label>
              ))}
            </div>
            <button onClick={analyserClause} disabled={busy === "clause"}
              className="mt-2 bg-rose-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-rose-700 disabled:opacity-50">
              {busy === "clause" ? "…" : "Analyser la clause"}
            </button>
            {analyse && (
              <div className="mt-2 bg-gray-50 rounded-lg p-3 text-sm">
                <span className={`text-xs px-2 py-0.5 rounded-full border ${analyse.appreciation === "VALABLE" ? "bg-green-100 text-green-700 border-green-200" : "bg-orange-100 text-orange-700 border-orange-200"}`}>
                  {analyse.appreciation}
                </span>
                <span className="text-gray-500 ml-2">{analyse.nb_remplis}/{analyse.nb_total} critères réunis</span>
                <p className="text-xs text-gray-400 mt-1">{analyse.note}</p>
              </div>
            )}
          </div>

          {/* Délai de recours */}
          <div className="border-t pt-3">
            <p className="text-sm font-medium text-gray-800 mb-2">Délai de recours</p>
            <div className="flex gap-2 flex-wrap items-end">
              <select value={recours} onChange={e => setRecours(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
                {RECOURS.map(r => <option key={r.v} value={r.v}>{r.label}</option>)}
              </select>
              <div>
                <label className="text-xs text-gray-500 block mb-1">Date de notification</label>
                <input type="date" value={dateNotif} onChange={e => setDateNotif(e.target.value)}
                  className="border rounded-lg px-3 py-2 text-sm" />
              </div>
              <button onClick={calculerDelai} disabled={busy === "delai"}
                className="bg-rose-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-rose-700 disabled:opacity-50">
                {busy === "delai" ? "…" : "Calculer"}
              </button>
            </div>
            {delai && (
              <div className="mt-2 bg-gray-50 rounded-lg p-3 text-sm">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`text-xs px-2 py-0.5 rounded-full border ${NIVEAU_BADGE[delai.niveau_alerte] || "bg-gray-100"}`}>
                    {delai.jours_restants > 0 ? `J-${delai.jours_restants}` : "délai expiré"}
                  </span>
                  <span className="text-gray-700">{delai.instance}</span>
                  <span className="text-gray-500">→ {delai.date_limite_recours}</span>
                </div>
                <p className="text-xs text-gray-400 mt-1">{delai.base_legale} — {delai.note}</p>
              </div>
            )}
          </div>

          {/* Synthèse IA */}
          <div className="border-t pt-3">
            <button onClick={genererSynthese} disabled={busy === "synthese"}
              className="bg-indigo-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
              {busy === "synthese" ? "Génération…" : "Note d'analyse (IA)"}
            </button>
            {synthese && <pre className="text-sm text-gray-700 whitespace-pre-wrap font-sans bg-gray-50 rounded-lg p-3 mt-2">{synthese}</pre>}
          </div>
        </div>
      )}
    </div>
  );
}
