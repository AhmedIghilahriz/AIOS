"use client";
import { useState, useEffect, useCallback } from "react";
import { API, authHeaders } from "../../lib/api";

/** Module L — Cession d'officine : extraction des paramètres (Fiche de cession),
 *  validation avocat (HITL) et versement SECIB.
 *  Monté dans la modale dossier (replié par défaut). cf. docs/CDC_cession_officine.md (S1). */

const TYPES_OP: Record<string, string> = {
  inconnu: "À déterminer",
  cession_fonds: "Cession de fonds de commerce",
  cession_parts: "Cession de parts de SEL",
  cession_titres: "Cession de titres (SELAS)",
};
const AVANT_CONTRAT: Record<string, string> = {
  inconnu: "À déterminer",
  promesse_synallagmatique: "Promesse synallagmatique (compromis)",
  promesse_unilaterale_vente: "Promesse unilatérale de vente",
  promesse_unilaterale_achat: "Promesse unilatérale d'achat",
};
const STATUT_CS: Record<string, string> = {
  EN_ATTENTE: "bg-gray-100 text-gray-600",
  LEVEE: "bg-green-100 text-green-700",
  DEFAILLIE: "bg-red-100 text-red-700",
};

const toNum = (s: string): number | null => (s.trim() === "" || isNaN(Number(s)) ? null : Number(s));
const fmt = (n?: number | null) => (n != null ? n.toLocaleString("fr-FR") : "—");

export default function CessionPanel({ dossierId, notify }: { dossierId: string; notify: (m: string, ok?: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [fiche, setFiche] = useState<any | null>(null);   // null = pas encore chargée
  const [secib, setSecib] = useState<any | null>(null);
  const [actes, setActes] = useState<any[]>([]);

  const aFiche = !!fiche && fiche.type_operation !== undefined;

  // Champs critiques requis pour générer la promesse (EF-3.2, contrôle côté client).
  const critiquesOk = aFiche && fiche.type_operation !== "inconnu"
    && (fiche.cedants?.length > 0) && (fiche.cessionnaires?.length > 0)
    && fiche.prix?.montant_global != null;

  // État des conditions applicables (même logique que le backend).
  const condsApplicables = (fiche?.conditions_suspensives || []).filter((c: any) => c.applicable !== false);
  const nbLevees = condsApplicables.filter((c: any) => c.statut === "LEVEE").length;
  const nbDefaillies = condsApplicables.filter((c: any) => c.statut === "DEFAILLIE").length;
  const pretesPourActe = condsApplicables.length > 0 && nbLevees === condsApplicables.length;

  const charger = useCallback(async () => {
    const r = await fetch(`${API}/api/cession/${dossierId}/fiche`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) { const d = await r.json(); setFiche(d?.existe === false ? { existe: false } : d); }
  }, [dossierId]);

  const chargerActes = useCallback(async () => {
    const r = await fetch(`${API}/api/cession/${dossierId}/actes`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) { const d = await r.json(); setActes(d.actes || []); }
  }, [dossierId]);

  useEffect(() => { if (open && fiche === null) { charger(); chargerActes(); } }, [open, fiche, charger, chargerActes]);

  const extraire = async () => {
    setBusy("extract");
    try {
      const r = await fetch(`${API}/api/cession/${dossierId}/extraire-fiche`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Erreur", false); return; }
      setFiche(d);
      notify(`Fiche pré-remplie — ${d.champs_incertains?.length || 0} point(s) à vérifier`);
    } finally { setBusy(null); }
  };

  // Enregistre la fiche locale (silencieux) — réutilisé par les générateurs (auto-save).
  const putFiche = async (): Promise<any | null> => {
    const r = await fetch(`${API}/api/cession/${dossierId}/fiche`, {
      method: "PUT", headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(fiche),
    }).catch(() => null);
    if (!r) { notify("Serveur injoignable", false); return null; }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { notify(d.detail || "Erreur de validation", false); return null; }
    setFiche(d);
    return d;
  };

  const enregistrer = async () => {
    setBusy("save");
    try {
      const d = await putFiche();
      if (d) notify("Fiche validée (avocat)" + (d.champs_incertains?.length ? ` — ${d.champs_incertains.length} point(s) restant(s)` : " — complète"));
    } finally { setBusy(null); }
  };

  const verserSecib = async () => {
    setBusy("secib");
    try {
      const r = await fetch(`${API}/api/cession/${dossierId}/secib`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Erreur SECIB", false); return; }
      setSecib(d);
      notify(d.statut === "VIDE" ? "Aucune pièce à verser" : `Paquet SECIB prêt — ${d.nb_pieces} pièce(s)`, d.statut !== "VIDE");
    } finally { setBusy(null); }
  };

  const majCondition = async (code: string, patch: { statut?: string; date_butoir?: string }) => {
    const r = await fetch(`${API}/api/cession/${dossierId}/conditions/${code}`, {
      method: "PATCH", headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(patch),
    }).catch(() => null);
    if (!r) { notify("Serveur injoignable", false); return; }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { notify(d.detail || "Erreur", false); return; }
    const maj = (d.fiche?.conditions_suspensives || []).find((c: any) => c.code === code);
    if (maj) setFiche((f: any) => ({
      ...f, conditions_suspensives: f.conditions_suspensives.map((c: any) =>
        c.code === code ? { ...c, statut: maj.statut, date_butoir: maj.date_butoir, preuve_doc_id: maj.preuve_doc_id } : c),
    }));
    if (patch.statut) notify(`Condition « ${code} » → ${patch.statut}`);
  };

  const genererPromesse = async () => {
    setBusy("promesse");
    try {
      if (!(await putFiche())) return;   // auto-save : le backend lit la fiche persistée
      const r = await fetch(`${API}/api/cession/${dossierId}/promesse`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Génération impossible", false); return; }
      notify(`Promesse générée (v${d.version})`);
      chargerActes();
    } finally { setBusy(null); }
  };

  const genererActe = async () => {
    setBusy("acte");
    try {
      if (!(await putFiche())) return;
      const r = await fetch(`${API}/api/cession/${dossierId}/acte`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Acte verrouillé", false); return; }
      const nf = d.formalites?.length || 0;
      notify(`Acte généré (v${d.version})` + (nf ? ` — ${nf} échéance(s) de formalités créées (Module F)` : ""));
      chargerActes();
    } finally { setBusy(null); }
  };

  // ── Helpers de mise à jour immuable ──
  const setTop = (k: string, v: any) => setFiche((f: any) => ({ ...f, [k]: v }));
  const setObj = (obj: string, k: string, v: any) => setFiche((f: any) => ({ ...f, [obj]: { ...(f[obj] || {}), [k]: v } }));
  const setPartie = (liste: string, i: number, k: string, v: any) =>
    setFiche((f: any) => ({ ...f, [liste]: f[liste].map((p: any, idx: number) => idx === i ? { ...p, [k]: v } : p) }));
  const addPartie = (liste: string, role: string) =>
    setFiche((f: any) => ({ ...f, [liste]: [...(f[liste] || []), { role, type: "personne_physique", source: "avocat", confiance: 1 }] }));
  const delPartie = (liste: string, i: number) =>
    setFiche((f: any) => ({ ...f, [liste]: f[liste].filter((_: any, idx: number) => idx !== i) }));
  const setCondition = (i: number, k: string, v: any) =>
    setFiche((f: any) => ({ ...f, conditions_suspensives: f.conditions_suspensives.map((c: any, idx: number) => idx === i ? { ...c, [k]: v } : c) }));

  const champField = (label: string, value: any, onChange: (v: string) => void, type = "text", ph = "") => (
    <div className="flex flex-col">
      <label className="text-xs text-gray-500 mb-0.5">{label}</label>
      <input type={type} value={value ?? ""} placeholder={ph} onChange={e => onChange(e.target.value)}
        className="border rounded-lg px-2 py-1.5 text-sm" />
    </div>
  );

  const editeurParties = (liste: string, role: string, titre: string) => (
    <div>
      <div className="flex items-center justify-between mb-1">
        <p className="text-xs uppercase tracking-wide text-gray-400">{titre}</p>
        <button onClick={() => addPartie(liste, role)} className="text-xs text-indigo-600 hover:underline">+ Ajouter</button>
      </div>
      <div className="space-y-2">
        {(fiche[liste] || []).map((p: any, i: number) => (
          <div key={i} className="bg-gray-50 rounded-lg p-2 grid grid-cols-2 gap-2">
            <div className="flex flex-col">
              <label className="text-xs text-gray-500 mb-0.5">Type</label>
              <select value={p.type || "personne_physique"} onChange={e => setPartie(liste, i, "type", e.target.value)}
                className="border rounded-lg px-2 py-1.5 text-sm">
                {["personne_physique", "SELARL", "SELAS", "SELURL", "SELAFA", "SNC", "SARL", "autre"].map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            {champField("Dénomination (si société)", p.denomination, v => setPartie(liste, i, "denomination", v))}
            {champField("Nom", p.nom, v => setPartie(liste, i, "nom", v))}
            {champField("Prénom", p.prenom, v => setPartie(liste, i, "prenom", v))}
            {champField("RCS / SIREN", p.rcs_siren, v => setPartie(liste, i, "rcs_siren", v))}
            {champField("Inscription Ordre (Section A)", p.inscription_ordre, v => setPartie(liste, i, "inscription_ordre", v))}
            <div className="col-span-2 flex items-center justify-between">
              <span className="text-[11px] text-gray-400">{p.source ? `source : ${p.source}` : ""}</span>
              <button onClick={() => delPartie(liste, i)} className="text-xs text-red-500 hover:underline">Supprimer</button>
            </div>
          </div>
        ))}
        {(fiche[liste] || []).length === 0 && <p className="text-xs text-gray-400 italic">Aucune partie renseignée.</p>}
      </div>
    </div>
  );

  return (
    <div className="mt-4 border rounded-xl overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-indigo-50/60 hover:bg-indigo-50 text-left">
        <span className="text-sm font-semibold text-indigo-900">📜 Cession — Paramètres de l'acte & versement SECIB</span>
        <span className="text-indigo-700 text-lg">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="p-4 space-y-5">
          {/* ── État initial : pas de fiche ── */}
          {!aFiche && (
            <div className="text-center py-4">
              <p className="text-sm text-gray-600 mb-3">
                Pré-remplit la <strong>Fiche de cession</strong> à partir des pièces du dossier et de la transcription de l'appel
                (montants &amp; numéros déterministes, qualification proposée par l'IA).
              </p>
              <button onClick={extraire} disabled={busy === "extract"}
                className="bg-indigo-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
                {busy === "extract" ? "Extraction…" : "⚡ Extraire les paramètres"}
              </button>
            </div>
          )}

          {/* ── Fiche éditable ── */}
          {aFiche && (
            <>
              {/* Champs incertains */}
              {fiche.champs_incertains?.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                  <p className="text-xs font-semibold text-amber-800 mb-1">⚠️ À vérifier / compléter par l'avocat :</p>
                  <ul className="text-xs text-amber-700 list-disc list-inside">
                    {fiche.champs_incertains.map((c: string, i: number) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
              )}

              {/* Nature de l'opération */}
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col">
                  <label className="text-xs text-gray-500 mb-0.5">Type d'opération</label>
                  <select value={fiche.type_operation} onChange={e => setTop("type_operation", e.target.value)}
                    className="border rounded-lg px-2 py-1.5 text-sm">
                    {Object.entries(TYPES_OP).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
                <div className="flex flex-col">
                  <label className="text-xs text-gray-500 mb-0.5">Avant-contrat</label>
                  <select value={fiche.avant_contrat} onChange={e => setTop("avant_contrat", e.target.value)}
                    className="border rounded-lg px-2 py-1.5 text-sm">
                    {Object.entries(AVANT_CONTRAT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
              </div>

              {/* Officine */}
              <div className="border-t pt-3">
                <p className="text-xs uppercase tracking-wide text-gray-400 mb-2">Officine</p>
                <div className="grid grid-cols-2 gap-2">
                  {champField("Nom", fiche.officine?.nom, v => setObj("officine", "nom", v))}
                  {champField("FINESS", fiche.officine?.finess, v => setObj("officine", "finess", v))}
                  {champField("Adresse", fiche.officine?.adresse, v => setObj("officine", "adresse", v))}
                  {champField("Licence ARS", fiche.officine?.licence_ars, v => setObj("officine", "licence_ars", v))}
                  <div className="flex flex-col">
                    <label className="text-xs text-gray-500 mb-0.5">Zone</label>
                    <select value={fiche.officine?.type_zone || "inconnue"} onChange={e => setObj("officine", "type_zone", e.target.value)}
                      className="border rounded-lg px-2 py-1.5 text-sm">
                      {["inconnue", "urbaine", "rurale", "monopole"].map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </div>
                  {champField("CA HT (€)", fiche.officine?.ca_ht, v => setObj("officine", "ca_ht", toNum(v)), "number")}
                </div>
              </div>

              {/* Parties */}
              <div className="border-t pt-3 space-y-3">
                {editeurParties("cedants", "cedant", "Cédant(s) — vendeur(s)")}
                {editeurParties("cessionnaires", "cessionnaire", "Cessionnaire(s) — acquéreur(s)")}
              </div>

              {/* Prix */}
              <div className="border-t pt-3">
                <p className="text-xs uppercase tracking-wide text-gray-400 mb-2">Prix de cession (€)</p>
                <div className="grid grid-cols-2 gap-2">
                  {champField("Montant global", fiche.prix?.montant_global, v => setObj("prix", "montant_global", toNum(v)), "number")}
                  {champField("Part incorporel (clientèle/bail/licence)", fiche.prix?.part_incorporel, v => setObj("prix", "part_incorporel", toNum(v)), "number")}
                  {champField("Part matériel", fiche.prix?.part_materiel, v => setObj("prix", "part_materiel", toNum(v)), "number")}
                  {champField("Part stock (inventaire)", fiche.prix?.part_stock, v => setObj("prix", "part_stock", toNum(v)), "number")}
                </div>
                {champField("Date d'entrée en jouissance prévue", fiche.date_jouissance_prevue, v => setTop("date_jouissance_prevue", v || null), "date")}
              </div>

              {/* Conditions suspensives */}
              <div className="border-t pt-3">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs uppercase tracking-wide text-gray-400">Conditions suspensives</p>
                  {condsApplicables.length > 0 && (
                    <span className={`text-[11px] px-2 py-0.5 rounded-full ${pretesPourActe ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"}`}>
                      {nbLevees}/{condsApplicables.length} levée(s){nbDefaillies ? ` · ${nbDefaillies} défaillie(s)` : ""}
                      {pretesPourActe ? " · prêtes pour l'acte" : ""}
                    </span>
                  )}
                </div>
                <div className="space-y-2 max-h-72 overflow-auto pr-1">
                  {(fiche.conditions_suspensives || []).map((c: any, i: number) => (
                    <div key={c.code} className="flex items-start gap-2 text-sm">
                      <input type="checkbox" className="mt-1.5" title="Applicable ?" checked={c.applicable !== false} onChange={e => setCondition(i, "applicable", e.target.checked)} />
                      <div className="flex-1">
                        <div>
                          <span className={c.applicable === false ? "text-gray-400 line-through" : "text-gray-700"}>{c.libelle}</span>
                          {c.detecte_dans_pieces && <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded-full bg-indigo-100 text-indigo-700">détectée pièces</span>}
                        </div>
                        {c.applicable !== false && (
                          <div className="flex gap-2 mt-1 items-center">
                            <select value={c.statut} onChange={e => majCondition(c.code, { statut: e.target.value })}
                              className={`text-xs border rounded-lg px-1.5 py-1 ${STATUT_CS[c.statut] || ""}`}>
                              <option value="EN_ATTENTE">En attente</option>
                              <option value="LEVEE">Levée</option>
                              <option value="DEFAILLIE">Défaillie</option>
                            </select>
                            <input type="date" value={c.date_butoir || ""} title="Date butoir (→ alerte Module F)"
                              onChange={e => majCondition(c.code, { date_butoir: e.target.value })}
                              className="text-xs border rounded-lg px-1.5 py-1" />
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                <p className="text-[11px] text-gray-400 mt-1">Une date butoir crée une alerte (Module F : J-30/14/7/1). Le statut est une décision de l'avocat.</p>
              </div>

              {/* Clauses de l'acte définitif */}
              <div className="border-t pt-3">
                <p className="text-xs uppercase tracking-wide text-gray-400 mb-2">Clauses de l'acte définitif</p>
                <div className="grid grid-cols-3 gap-2">
                  {champField("GAP — durée (mois)", fiche.garantie_actif_passif?.duree_mois, v => setObj("garantie_actif_passif", "duree_mois", toNum(v)), "number")}
                  {champField("GAP — plafond (€)", fiche.garantie_actif_passif?.plafond, v => setObj("garantie_actif_passif", "plafond", toNum(v)), "number")}
                  {champField("GAP — franchise (€)", fiche.garantie_actif_passif?.franchise, v => setObj("garantie_actif_passif", "franchise", toNum(v)), "number")}
                  {champField("Non-concurrence — km", fiche.non_concurrence?.perimetre_km, v => setObj("non_concurrence", "perimetre_km", toNum(v)), "number")}
                  {champField("Non-concurrence — mois", fiche.non_concurrence?.duree_mois, v => setObj("non_concurrence", "duree_mois", toNum(v)), "number")}
                  <div className="flex flex-col">
                    <label className="text-xs text-gray-500 mb-0.5">Séquestre</label>
                    <select value={fiche.sequestre?.type || "inconnu"} onChange={e => setObj("sequestre", "type", e.target.value)}
                      className="border rounded-lg px-2 py-1.5 text-sm">
                      <option value="inconnu">À désigner</option>
                      <option value="carpa_avocat">CARPA (avocat)</option>
                      <option value="notaire">Notaire</option>
                      <option value="autre">Autre</option>
                    </select>
                  </div>
                </div>
              </div>

              {/* Génération des documents + historique */}
              <div className="border-t pt-3">
                <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                  <p className="text-xs uppercase tracking-wide text-gray-400">Documents générés</p>
                  <div className="flex gap-2">
                    <button onClick={genererPromesse} disabled={busy === "promesse" || !critiquesOk}
                      title={critiquesOk ? "" : "Renseignez type, parties et prix"}
                      className="bg-indigo-600 text-white text-xs px-3 py-1.5 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
                      {busy === "promesse" ? "…" : "📝 Promesse"}
                    </button>
                    <button onClick={genererActe} disabled={busy === "acte" || !critiquesOk || !pretesPourActe}
                      title={!critiquesOk ? "Renseignez type, parties et prix" : (!pretesPourActe ? "Toutes les conditions suspensives doivent être levées" : "")}
                      className="bg-slate-700 text-white text-xs px-3 py-1.5 rounded-lg hover:bg-slate-800 disabled:opacity-50">
                      {busy === "acte" ? "…" : "📜 Acte définitif"}
                    </button>
                  </div>
                </div>
                {!critiquesOk && <p className="text-[11px] text-amber-600 mb-2">Complétez type, parties et prix pour activer la génération.</p>}
                {critiquesOk && !pretesPourActe && <p className="text-[11px] text-gray-400 mb-2">L'acte définitif se débloque une fois toutes les conditions suspensives levées.</p>}
                {actes.length > 0 ? (
                  <ul className="space-y-1">
                    {actes.map((a: any) => (
                      <li key={a.id} className="flex items-center justify-between text-sm bg-gray-50 rounded-lg px-3 py-1.5">
                        <span className="text-gray-700">
                          {a.type === "promesse" ? "Promesse" : "Acte"} v{a.version}
                          <span className="text-xs text-gray-400"> · {a.sous_type || ""} · {a.created_at ? new Date(a.created_at).toLocaleDateString("fr-FR") : ""}</span>
                        </span>
                        <button onClick={() => window.open(`${API}/api/cession/actes/${a.id}/imprimer`, "_blank", "noopener")}
                          className="text-xs text-indigo-600 hover:underline">🖨 Imprimer / PDF</button>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-[11px] text-gray-400 italic">Aucune promesse générée pour l'instant.</p>}
              </div>

              {/* Actions */}
              <div className="border-t pt-3 flex flex-wrap gap-2 items-center">
                <button onClick={enregistrer} disabled={busy === "save"}
                  className="bg-indigo-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
                  {busy === "save" ? "…" : "💾 Valider la fiche (avocat)"}
                </button>
                <button onClick={extraire} disabled={busy === "extract"}
                  className="text-sm px-3 py-2 rounded-lg border border-gray-300 hover:bg-gray-50 disabled:opacity-50">
                  {busy === "extract" ? "…" : "↻ Ré-extraire depuis les pièces"}
                </button>
                <button onClick={verserSecib} disabled={busy === "secib"}
                  className="ml-auto bg-slate-700 text-white text-sm px-4 py-2 rounded-lg hover:bg-slate-800 disabled:opacity-50">
                  {busy === "secib" ? "…" : "📤 Verser dans SECIB"}
                </button>
              </div>

              {secib && (
                <div className="bg-slate-50 border rounded-lg p-3 text-sm">
                  <p className="text-gray-800">
                    {secib.statut === "VIDE"
                      ? "Aucune pièce disponible à verser (téléversez d'abord les documents)."
                      : <>Paquet prêt : <strong>{secib.nb_pieces}</strong> pièce(s) · <code className="text-xs">{secib.chemin_paquet}</code></>}
                  </p>
                  {secib.note && <p className="text-[11px] text-gray-400 mt-1">{secib.note}</p>}
                </div>
              )}

              <p className="text-[11px] text-gray-400 border-t pt-2">
                {fiche.note_methode || "Fiche assistée — [VERIFICATION REQUISE PAR L'AVOCAT]."}
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
