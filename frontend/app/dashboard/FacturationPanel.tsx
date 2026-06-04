"use client";
import { useState } from "react";
import { API, authHeaders } from "../../lib/api";

/** Module G — Création de facture (honoraires) + déclenchement des relances impayés. */

const TYPES = [
  { v: "fixe", label: "Forfait fixe" },
  { v: "horaire", label: "Taux horaire" },
  { v: "resultat", label: "Au résultat" },
];

export default function FacturationPanel({
  dossierId, notify, onRefresh,
}: { dossierId: string; notify: (m: string, ok?: boolean) => void; onRefresh?: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [montantHt, setMontantHt] = useState("");
  const [type, setType] = useState("fixe");
  const [description, setDescription] = useState("");
  const [derniere, setDerniere] = useState<any>(null);

  const creer = async () => {
    if (!montantHt) { notify("Montant HT requis", false); return; }
    setBusy("create");
    try {
      const r = await fetch(`${API}/api/factures`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          dossier_id: dossierId, montant_ht: Number(montantHt),
          type_honoraires: type, description,
        }),
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Échec de la création", false); return; }
      setDerniere(d); setMontantHt(""); setDescription("");
      notify(`Facture ${d.numero} créée (${d.montant_ttc} € TTC) ✓`);
      onRefresh?.();
    } finally { setBusy(null); }
  };

  const lancerRelances = async () => {
    setBusy("relances");
    try {
      const r = await fetch(`${API}/api/factures/relances`, {
        method: "POST", headers: { ...authHeaders() },
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Erreur", false); return; }
      notify(`Relances exécutées — ${d.factures_impayees_traitees ?? 0} facture(s) impayée(s)`);
      onRefresh?.();
    } finally { setBusy(null); }
  };

  return (
    <div className="mt-4 border rounded-xl overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-amber-50/60 hover:bg-amber-50 text-left">
        <span className="text-sm font-semibold text-amber-900">💶 Facturation & relances (Module G)</span>
        <span className="text-amber-700 text-lg">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="p-4 space-y-3">
          <div className="flex gap-2 flex-wrap items-end">
            <div>
              <label className="text-xs text-gray-500 block mb-1">Montant HT (€)</label>
              <input type="number" value={montantHt} onChange={e => setMontantHt(e.target.value)}
                className="border rounded-lg px-3 py-2 text-sm w-36" placeholder="1500" />
            </div>
            <div>
              <label className="text-xs text-gray-500 block mb-1">Honoraires</label>
              <select value={type} onChange={e => setType(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
                {TYPES.map(t => <option key={t.v} value={t.v}>{t.label}</option>)}
              </select>
            </div>
            <div className="flex-1 min-w-[180px]">
              <label className="text-xs text-gray-500 block mb-1">Description</label>
              <input value={description} onChange={e => setDescription(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 text-sm" placeholder="Consultation, rédaction d'acte…" />
            </div>
            <button onClick={creer} disabled={busy === "create"}
              className="bg-amber-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-amber-700 disabled:opacity-50">
              {busy === "create" ? "…" : "Créer la facture"}
            </button>
          </div>

          {derniere && (
            <p className="text-sm text-gray-600 bg-gray-50 rounded-lg px-3 py-2">
              Dernière : <strong>{derniere.numero}</strong> — {derniere.montant_ttc} € TTC,
              échéance {derniere.date_echeance ? new Date(derniere.date_echeance).toLocaleDateString("fr-FR") : "?"} ({derniere.statut})
            </p>
          )}

          <div className="border-t pt-3 flex items-center justify-between gap-2 flex-wrap">
            <p className="text-xs text-gray-400">Relances automatiques : J+30 (1<sup>re</sup>), J+45 (2<sup>e</sup>), J+60 (mise en demeure), J+90 (alerte avocat).</p>
            <button onClick={lancerRelances} disabled={busy === "relances"}
              className="border border-amber-300 text-amber-700 text-sm px-3 py-2 rounded-lg hover:bg-amber-50 disabled:opacity-50">
              {busy === "relances" ? "Traitement…" : "Lancer les relances impayés"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
