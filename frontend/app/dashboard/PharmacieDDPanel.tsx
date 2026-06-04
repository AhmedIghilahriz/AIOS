"use client";
import { useState, useEffect, useCallback } from "react";
import { API, authHeaders } from "../../lib/api";

/** Module H — Cession d'officine : Due Diligence + Valorisation + Suivi ARS.
 *  Monté dans la modale dossier (replié par défaut). */

interface ChecklistItem { id: string; categorie: string; label: string; obligatoire: boolean; statut?: string; }
interface Evaluation {
  completude_pct: number; nb_recus: number; nb_total: number;
  points_bloquants: string[]; recommandation: string; documents: ChecklistItem[];
}

const RECO_STYLE: Record<string, string> = {
  POURSUIVRE: "bg-green-100 text-green-700 border-green-200",
  DILIGENCE_COMPLEMENTAIRE: "bg-orange-100 text-orange-700 border-orange-200",
  ABANDON: "bg-red-100 text-red-700 border-red-200",
};
const ARS_STYLE: Record<string, string> = {
  CRITIQUE: "bg-red-100 text-red-700", ROUGE: "bg-red-100 text-red-700",
  PRIORITAIRE: "bg-orange-100 text-orange-700", INFORMATIF: "bg-blue-100 text-blue-700",
};

export default function PharmacieDDPanel({ dossierId, notify }: { dossierId: string; notify: (m: string, ok?: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  // Due diligence
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);
  const [recus, setRecus] = useState<Set<string>>(new Set());
  const [eval_, setEval] = useState<Evaluation | null>(null);

  // Valorisation
  const [caHt, setCaHt] = useState("");
  const [typeOfficine, setTypeOfficine] = useState("urbaine");
  const [valo, setValo] = useState<any>(null);

  // ARS
  const [ars, setArs] = useState<any>(null);
  const [dateDepot, setDateDepot] = useState("");
  const [piecesManquantes, setPiecesManquantes] = useState("");

  const chargerChecklist = useCallback(async () => {
    const r = await fetch(`${API}/api/pharmacie/checklist`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) { const d = await r.json(); setChecklist(d.checklist || []); }
  }, []);
  const chargerArs = useCallback(async () => {
    const r = await fetch(`${API}/api/pharmacie/${dossierId}/ars`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) setArs(await r.json());
  }, [dossierId]);

  useEffect(() => { if (open && checklist.length === 0) { chargerChecklist(); chargerArs(); } }, [open, checklist.length, chargerChecklist, chargerArs]);

  const toggle = (label: string) => {
    setRecus(prev => { const n = new Set(prev); n.has(label) ? n.delete(label) : n.add(label); return n; });
  };

  const evaluer = async () => {
    setBusy("dd");
    try {
      const r = await fetch(`${API}/api/pharmacie/${dossierId}/due-diligence`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ documents_recus: Array.from(recus) }),
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Erreur", false); return; }
      setEval(d); notify(`Complétude : ${d.completude_pct}% — ${d.recommandation}`);
    } finally { setBusy(null); }
  };

  const valoriser = async () => {
    if (!caHt) { notify("Saisissez le CA HT", false); return; }
    setBusy("valo");
    try {
      const r = await fetch(`${API}/api/pharmacie/${dossierId}/valorisation?ca_ht=${encodeURIComponent(caHt)}&type_officine=${typeOfficine}`,
        { headers: { ...authHeaders() } }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Erreur", false); return; }
      setValo(d);
    } finally { setBusy(null); }
  };

  const enregistrerDepot = async () => {
    if (!dateDepot) { notify("Date de dépôt requise", false); return; }
    setBusy("ars");
    try {
      const r = await fetch(`${API}/api/pharmacie/${dossierId}/ars/depot`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          date_depot: new Date(dateDepot).toISOString(),
          pieces_manquantes: piecesManquantes.split("\n").map(s => s.trim()).filter(Boolean),
        }),
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Erreur", false); return; }
      setArs(d); notify("Dépôt ARS enregistré — échéance 4 mois créée");
    } finally { setBusy(null); }
  };

  // Regroupe la checklist par catégorie pour l'affichage
  const parCategorie = checklist.reduce<Record<string, ChecklistItem[]>>((acc, it) => {
    (acc[it.categorie] ||= []).push(it); return acc;
  }, {});
  const fmt = (n?: number) => (n != null ? n.toLocaleString("fr-FR") : "—");

  return (
    <div className="mt-4 border rounded-xl overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-emerald-50/60 hover:bg-emerald-50 text-left">
        <span className="text-sm font-semibold text-emerald-900">🏥 Pharmacie — Due Diligence, Valorisation & ARS</span>
        <span className="text-emerald-700 text-lg">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="p-4 space-y-5">
          {/* ── Due diligence ── */}
          <div>
            <p className="text-sm font-medium text-gray-800 mb-2">Check-list de due diligence</p>
            <div className="space-y-3 max-h-64 overflow-auto pr-1">
              {Object.entries(parCategorie).map(([cat, items]) => (
                <div key={cat}>
                  <p className="text-xs uppercase tracking-wide text-gray-400 mb-1">{cat}</p>
                  <div className="space-y-1">
                    {items.map(it => (
                      <label key={it.id} className="flex items-start gap-2 text-sm text-gray-700 cursor-pointer">
                        <input type="checkbox" className="mt-0.5" checked={recus.has(it.label)} onChange={() => toggle(it.label)} />
                        <span>{it.label}</span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <button onClick={evaluer} disabled={busy === "dd" || checklist.length === 0}
              className="mt-2 bg-emerald-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-emerald-700 disabled:opacity-50">
              {busy === "dd" ? "…" : "Évaluer la complétude"}
            </button>
            {eval_ && (
              <div className="mt-3 bg-gray-50 rounded-lg p-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-semibold text-gray-800">{eval_.completude_pct}% complet</span>
                  <span className="text-xs text-gray-500">({eval_.nb_recus}/{eval_.nb_total} pièces)</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full border ${RECO_STYLE[eval_.recommandation] || "bg-gray-100"}`}>{eval_.recommandation}</span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-2 mt-2">
                  <div className="bg-emerald-500 h-2 rounded-full transition-all" style={{ width: `${eval_.completude_pct}%` }} />
                </div>
                {eval_.points_bloquants?.length > 0 && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-red-600">Pièces manquantes :</p>
                    <ul className="text-xs text-gray-600 list-disc list-inside">
                      {eval_.points_bloquants.map((p, i) => <li key={i}>{p}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── Valorisation ── */}
          <div className="border-t pt-4">
            <p className="text-sm font-medium text-gray-800 mb-2">Valorisation indicative</p>
            <div className="flex gap-2 flex-wrap items-end">
              <input type="number" value={caHt} onChange={e => setCaHt(e.target.value)} placeholder="CA HT annuel (€)"
                className="border rounded-lg px-3 py-2 text-sm w-44" />
              <select value={typeOfficine} onChange={e => setTypeOfficine(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
                <option value="urbaine">Urbaine</option>
                <option value="rurale">Rurale</option>
                <option value="monopole">Monopole</option>
              </select>
              <button onClick={valoriser} disabled={busy === "valo"}
                className="bg-emerald-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-emerald-700 disabled:opacity-50">
                {busy === "valo" ? "…" : "Estimer"}
              </button>
            </div>
            {valo && (
              <div className="mt-3 bg-gray-50 rounded-lg p-3">
                <p className="text-sm text-gray-800">
                  Fourchette : <strong>{fmt(valo.fourchette_bas)} €</strong> – <strong>{fmt(valo.fourchette_haut)} €</strong>
                  <span className="text-xs text-gray-400"> (×{valo.coefficient_bas}–{valo.coefficient_haut} du CA HT)</span>
                </p>
                <p className="text-xs text-gray-400 mt-1">{valo.note}</p>
              </div>
            )}
          </div>

          {/* ── Suivi ARS ── */}
          <div className="border-t pt-4">
            <p className="text-sm font-medium text-gray-800 mb-2">Suivi instruction ARS <span className="text-xs text-gray-400">(silence = refus à 4 mois — art. R.5125-10 CSP)</span></p>
            {ars && ars.date_depot && (
              <div className="bg-gray-50 rounded-lg p-3 mb-2 text-sm">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${ARS_STYLE[ars.niveau_alerte] || "bg-gray-100 text-gray-600"}`}>{ars.niveau_alerte}</span>
                  <span className="text-gray-700">Échéance : <strong>{ars.date_limite_decision}</strong></span>
                  {ars.jours_restants != null && (
                    <span className={ars.jours_restants <= 30 ? "text-red-600 font-medium" : "text-gray-500"}>
                      {ars.jours_restants > 0 ? `J-${ars.jours_restants}` : "délai écoulé (refus implicite)"}
                    </span>
                  )}
                </div>
              </div>
            )}
            <div className="flex gap-2 flex-wrap items-end">
              <div>
                <label className="text-xs text-gray-500 block mb-1">Date de dépôt</label>
                <input type="date" value={dateDepot} onChange={e => setDateDepot(e.target.value)} className="border rounded-lg px-3 py-2 text-sm" />
              </div>
              <div className="flex-1 min-w-[180px]">
                <label className="text-xs text-gray-500 block mb-1">Pièces manquantes (une par ligne)</label>
                <textarea value={piecesManquantes} onChange={e => setPiecesManquantes(e.target.value)}
                  className="w-full border rounded-lg px-3 py-2 text-sm h-[42px]" placeholder="(optionnel)" />
              </div>
              <button onClick={enregistrerDepot} disabled={busy === "ars"}
                className="bg-emerald-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-emerald-700 disabled:opacity-50">
                {busy === "ars" ? "…" : "Enregistrer le dépôt"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
