"use client";
import { useState, useEffect, useCallback } from "react";
import { API, authHeaders } from "../../lib/api";

/** Module D — RDV : liste (dont RDV auto « à confirmer »), création, fiche de préparation. */

const TYPES = [
  { v: "decouverte", label: "Découverte" },
  { v: "approfondi", label: "Approfondi" },
  { v: "suivi", label: "Suivi" },
];
interface Rdv { id: string; type_rdv: string; statut: string; duree_minutes?: number; date_heure?: string | null; motif?: string; }
const fmt = (s?: string | null) => (s ? new Date(s).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" }) : "?");
const STATUT: Record<string, string> = {
  a_confirmer: "bg-amber-100 text-amber-700 border-amber-200",
  confirme: "bg-green-100 text-green-700 border-green-200",
  annule: "bg-gray-100 text-gray-400 border-gray-200",
};

export default function RendezVousPanel({
  dossierId, notify, onRefresh,
}: { dossierId: string; notify: (m: string, ok?: boolean) => void; onRefresh?: () => void }) {
  const [open, setOpen] = useState(true);   // ouvert par défaut : la gestion des RDV est visible d'emblée
  const [busy, setBusy] = useState<string | null>(null);
  const [typeRdv, setTypeRdv] = useState("decouverte");
  const [dateHeure, setDateHeure] = useState("");
  const [duree, setDuree] = useState(60);
  const [rdvs, setRdvs] = useState<Rdv[]>([]);

  const charger = useCallback(async () => {
    const r = await fetch(`${API}/api/dossiers/${dossierId}/rdv`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) { const d = await r.json(); setRdvs(d.rdvs || []); }
  }, [dossierId]);

  useEffect(() => { charger(); }, [charger]);   // chargé au montage (et au changement de dossier)

  const creer = async () => {
    if (!dateHeure) { notify("Date et heure requises", false); return; }
    setBusy("create");
    try {
      const r = await fetch(`${API}/api/rdv`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          dossier_id: dossierId, type_rdv: typeRdv,
          date_heure: new Date(dateHeure).toISOString(), duree_minutes: Number(duree),
        }),
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Échec de la création du RDV", false); return; }
      setDateHeure("");
      notify("Rendez-vous créé ✓");
      await charger(); onRefresh?.();
    } finally { setBusy(null); }
  };

  const changerStatut = async (rdv: Rdv, statut: string) => {
    setBusy(rdv.id);
    try {
      const r = await fetch(`${API}/api/rdv/${rdv.id}/statut`, {
        method: "PATCH", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ statut }),
      }).catch(() => null);
      if (!r?.ok) { notify("Erreur", false); return; }
      notify(statut === "confirme" ? "RDV confirmé ✓" : statut === "annule" ? "RDV annulé" : "Statut mis à jour");
      await charger(); onRefresh?.();
    } finally { setBusy(null); }
  };

  // Suppression d'un RDV (urgence / annulation de dernière minute) — avec confirmation.
  const supprimer = async (rdv: Rdv) => {
    if (!confirm(`Supprimer ce rendez-vous ?\n${rdv.type_rdv} — ${fmt(rdv.date_heure)}`)) return;
    setBusy(rdv.id);
    try {
      const r = await fetch(`${API}/api/rdv/${rdv.id}`, { method: "DELETE", headers: { ...authHeaders() } }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      if (r.ok || r.status === 404) {              // idempotent : déjà supprimé = succès
        setRdvs(prev => prev.filter(x => x.id !== rdv.id));
        notify("Rendez-vous supprimé");
        onRefresh?.();
        return;
      }
      const d = await r.json().catch(() => ({}));
      notify(d.detail || "Échec de la suppression", false);
    } finally { setBusy(null); }
  };

  const aConfirmer = rdvs.filter(r => r.statut === "a_confirmer").length;

  return (
    <div className="mt-4 border rounded-xl overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-sky-50/60 hover:bg-sky-50 text-left">
        <span className="text-sm font-semibold text-sky-900">📅 Rendez-vous (Module D)
          {rdvs.length > 0 && <span className="text-xs font-normal text-gray-500"> · {rdvs.length}</span>}
          {aConfirmer > 0 && <span className="ml-2 text-xs bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full">{aConfirmer} à confirmer</span>}
        </span>
        <span className="text-sky-700 text-lg">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="p-4 space-y-4">
          {/* Liste des RDV (dont auto « à confirmer ») */}
          {rdvs.length > 0 && (
            <ul className="space-y-1.5">
              {rdvs.map(r => (
                <li key={r.id} className="flex items-center gap-2 flex-wrap text-sm">
                  <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUT[r.statut] || "bg-gray-100"}`}>{r.statut}</span>
                  <span className="text-gray-800">{fmt(r.date_heure)}</span>
                  <span className="text-xs text-gray-400">{r.type_rdv}{r.motif ? ` · ${r.motif}` : ""}</span>
                  <span className="flex gap-1 ml-auto">
                    {r.statut === "a_confirmer" && (
                      <>
                        <button onClick={() => changerStatut(r, "confirme")} disabled={busy === r.id}
                          className="text-xs bg-green-600 text-white px-2 py-1 rounded-lg hover:bg-green-700 disabled:opacity-50">Confirmer</button>
                        <button onClick={() => changerStatut(r, "annule")} disabled={busy === r.id}
                          className="text-xs border text-gray-500 px-2 py-1 rounded-lg hover:bg-gray-50 disabled:opacity-50">Annuler</button>
                      </>
                    )}
                    <button onClick={() => supprimer(r)} disabled={busy === r.id}
                      title="Supprimer ce rendez-vous (urgence / annulation)"
                      className="text-xs border border-red-200 text-red-600 px-2 py-1 rounded-lg hover:bg-red-50 disabled:opacity-50">🗑</button>
                  </span>
                </li>
              ))}
            </ul>
          )}

          {/* Création manuelle */}
          <div className="border-t pt-3 flex gap-2 flex-wrap items-end">
            <div>
              <label className="text-xs text-gray-500 block mb-1">Type</label>
              <select value={typeRdv} onChange={e => setTypeRdv(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
                {TYPES.map(t => <option key={t.v} value={t.v}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-500 block mb-1">Date &amp; heure</label>
              <input type="datetime-local" value={dateHeure} onChange={e => setDateHeure(e.target.value)}
                className="border rounded-lg px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="text-xs text-gray-500 block mb-1">Durée (min)</label>
              <input type="number" value={duree} onChange={e => setDuree(Number(e.target.value))}
                className="border rounded-lg px-3 py-2 text-sm w-24" />
            </div>
            <button onClick={creer} disabled={busy === "create"}
              className="bg-sky-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-sky-700 disabled:opacity-50">
              {busy === "create" ? "Création…" : "Créer le RDV"}
            </button>
          </div>
          <p className="text-xs text-gray-400">Un RDV « approfondi » exige ≥ 50 % de documents reçus. Les RDV « à confirmer » sont détectés automatiquement dans les emails du client.</p>
          <p className="text-xs text-gray-400">📝 La trame de préparation (synthèse + questions à poser) se trouve désormais dans la section <span className="font-medium text-teal-700">Fiche de préparation</span>.</p>
        </div>
      )}
    </div>
  );
}
