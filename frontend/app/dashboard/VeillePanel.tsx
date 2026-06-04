"use client";
import { useState, useEffect, useCallback } from "react";
import { API, authHeaders } from "../../lib/api";

interface Alerte {
  id: string;
  titre: string;
  source: string;
  url?: string;
  date_publication?: string;
  impact: "CRITIQUE" | "ELEVE" | "MOYEN" | string;
  resume?: string;
  mots_cles?: string[];
  lu?: boolean;
  source_url?: string;
}

const IMPACT_STYLE: Record<string, { badge: string; bar: string; label: string }> = {
  CRITIQUE: { badge: "bg-red-100 text-red-700 border-red-200", bar: "border-l-red-500", label: "🔴 Critique" },
  ELEVE:    { badge: "bg-orange-100 text-orange-700 border-orange-200", bar: "border-l-orange-400", label: "🟠 Élevé" },
  MOYEN:    { badge: "bg-blue-100 text-blue-700 border-blue-200", bar: "border-l-blue-300", label: "🔵 Moyen" },
};
const SOURCE_EMOJI: Record<string, string> = {
  JORF: "📜", JO: "📜", CPAM: "🏥", ORDRE: "⚖️", ARS: "🏛️", PLFSS: "💶",
};

export default function VeillePanel({ notify }: { notify: (msg: string, ok?: boolean) => void }) {
  const [alertes, setAlertes] = useState<Alerte[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [filtre, setFiltre] = useState<string>("");   // "" = tous

  const charger = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/veille/alertes?limit=50`, { headers: { ...authHeaders() } }).catch(() => null);
      if (r?.ok) { const d = await r.json(); setAlertes(d.alertes || []); }
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { charger(); }, [charger]);

  const scanner = async () => {
    setScanning(true);
    try {
      const r = await fetch(`${API}/api/veille/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ resumer: true }),   // source "auto" côté serveur, filtrée par vos dossiers
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Échec du scan", false); return; }
      notify(`Veille scannée — ${d.total ?? 0} alerte(s) pertinente(s)`);
      await charger();
    } finally { setScanning(false); }
  };

  const visibles = filtre ? alertes.filter(a => a.impact === filtre) : alertes;
  const compte = (imp: string) => alertes.filter(a => a.impact === imp).length;

  return (
    <div className="space-y-4">
      {/* Barre d'actions */}
      <div className="bg-white rounded-xl border p-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <p className="font-medium text-gray-800">🔎 Veille réglementaire pharmacie</p>
            <p className="text-xs text-gray-400 mt-0.5">Flux officiels réels — <strong>filtrés sur vos dossiers actifs</strong>, résumés par l'IA</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={scanner} disabled={scanning}
              className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
              {scanning ? "Analyse IA…" : "Scanner maintenant"}
            </button>
          </div>
        </div>

        {/* Filtres par impact */}
        <div className="flex gap-2 mt-3 flex-wrap">
          <button onClick={() => setFiltre("")}
            className={`text-xs px-3 py-1 rounded-full border ${filtre === "" ? "bg-gray-800 text-white border-gray-800" : "text-gray-500 hover:bg-gray-50"}`}>
            Tous ({alertes.length})
          </button>
          {(["CRITIQUE", "ELEVE", "MOYEN"] as const).map(imp => (
            <button key={imp} onClick={() => setFiltre(filtre === imp ? "" : imp)}
              className={`text-xs px-3 py-1 rounded-full border ${filtre === imp ? IMPACT_STYLE[imp].badge : "text-gray-500 hover:bg-gray-50"}`}>
              {IMPACT_STYLE[imp].label} ({compte(imp)})
            </button>
          ))}
        </div>
      </div>

      {/* Liste */}
      {loading ? (
        <div className="bg-white rounded-xl border p-8 text-center text-gray-400">Chargement…</div>
      ) : visibles.length === 0 ? (
        <div className="bg-white rounded-xl border p-10 text-center text-gray-400">
          <p className="text-3xl mb-2">🔎</p>
          <p className="font-medium text-gray-600">Aucune alerte pertinente</p>
          <p className="text-sm mt-1">Les flux réels ont été filtrés sur vos dossiers actifs — rien ne correspond pour l'instant.<br/>Crée des dossiers (ou ajoute tes propres flux via <code>VEILLE_RSS_URLS</code>) puis relance un scan.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {visibles.map(a => {
            const st = IMPACT_STYLE[a.impact] || IMPACT_STYLE.MOYEN;
            return (
              <div key={a.id} className={`bg-white rounded-xl border p-4 border-l-4 ${st.bar} hover:shadow-sm transition-all`}>
                <div className="flex items-start gap-3">
                  <span className="text-xl mt-0.5">{SOURCE_EMOJI[a.source] || "📄"}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1.5">
                      <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${st.badge}`}>{st.label}</span>
                      <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">{a.source}</span>
                      {a.date_publication && <span className="text-xs text-gray-400">{a.date_publication}</span>}
                    </div>
                    <p className="font-semibold text-gray-900">{a.titre}</p>
                    {a.resume && <p className="text-sm text-gray-700 mt-1.5 bg-gray-50 rounded-lg px-3 py-2">{a.resume}</p>}
                    <div className="flex items-center gap-2 flex-wrap mt-2">
                      {(a.mots_cles || []).map((m, i) => (
                        <span key={i} className="text-[11px] bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded">#{m}</span>
                      ))}
                    </div>
                    {(a.source_url || a.url) && (
                      <a href={a.source_url || a.url} target="_blank" rel="noreferrer"
                        className="text-xs text-blue-600 hover:underline mt-2 inline-block">Source officielle ↗</a>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
