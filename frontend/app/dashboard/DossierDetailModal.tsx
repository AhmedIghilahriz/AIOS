"use client";
import { useState, useEffect, useRef } from "react";
import { API, authHeaders } from "../../lib/api";
import PharmacieDDPanel from "./PharmacieDDPanel";
import CessionPanel from "./CessionPanel";
import ContentieuxPanel from "./ContentieuxPanel";
import ContentieuxPharmaPanel from "./ContentieuxPharmaPanel";
import RendezVousPanel from "./RendezVousPanel";
import TranscriptionUpload from "./TranscriptionUpload";
import FacturationPanel from "./FacturationPanel";
import DeadlinesTimeline from "./DeadlinesTimeline";
import DocumentsChecklist from "./DocumentsChecklist";
import FichesPreparation from "./FichesPreparation";

function Section({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="mt-4">
      <p className="text-sm font-medium text-gray-800 mb-1">{title}</p>
      {items.length === 0
        ? <p className="text-sm text-gray-400">—</p>
        : <ul className="text-sm text-gray-600 space-y-1">{items.map((it, i) => <li key={i} className="bg-gray-50 rounded px-2 py-1">{it}</li>)}</ul>}
    </div>
  );
}

export default function DossierDetailModal({
  dossier, onClose, notify, onRefresh, autoReply, onDeleted,
}: {
  dossier: any;
  onClose: () => void;
  notify: (msg: string, ok?: boolean) => void;
  onRefresh?: () => void;
  autoReply?: boolean;
  onDeleted?: () => void;
}) {
  const id = dossier.id;
  const [busy, setBusy] = useState<string | null>(null);
  const [resultat, setResultat] = useState<{ titre: string; contenu: string } | null>(null);
  const replyRef = useRef<HTMLDivElement>(null);
  const [statut, setStatut] = useState<string>(dossier.status);

  // Workflow de réponse client (HITL)
  const [draft, setDraft] = useState<string | null>(null);
  const [objet, setObjet] = useState("");
  const [destinataire, setDestinataire] = useState<string>(dossier.client?.email || "");
  const [sending, setSending] = useState(false);
  const [contexteExtrait, setContexteExtrait] = useState("");   // extrait du dernier message client
  const [nbContexte, setNbContexte] = useState(0);
  const [meta, setMeta] = useState<any>(null);                  // intention + scénario détectés

  // Helper générique pour les 3 Actions IA
  const action = async (
    cle: string,
    req: () => Promise<Response | null>,
    render: (d: any) => { titre: string; contenu: string },
  ) => {
    setBusy(cle); setResultat(null);
    try {
      const r = await req();
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Erreur", false); return; }
      setResultat(render(d));
      notify("Action terminée ✓");
    } finally { setBusy(null); }
  };

  const qualifier = () => action(
    "qualifier",
    () => fetch(`${API}/api/dossiers/${id}/qualifier`, { method: "POST", headers: { ...authHeaders() } }).catch(() => null),
    (d) => ({
      titre: "Qualification",
      contenu: `Score : ${d.score ?? "?"} — ${d.categorie ?? ""}\n${d.justification ?? ""}\n\n`
        + (d.questions_source === "llm"
            ? `Questions générées par l'IA${d.contexte_detecte ? ` (contexte : ${d.contexte_detecte})` : ""} :\n- `
            : "Questions de qualification (modèle standard) :\n- ")
        + (d.questions_formulaire || d.questions_cles || []).join("\n- "),
    }),
  );

  // Suggérer une réponse (génération)
  const suggererReponse = async () => {
    setBusy("reponse");
    try {
      const r = await fetch(`${API}/api/dossiers/${id}/generer-reponse`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ message_client: "" }),
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Échec de la génération", false); return; }
      setDraft(d.corps || "");
      setObjet(d.objet || `Votre dossier ${dossier.reference || ""}`);
      if (d.destinataire) setDestinataire(d.destinataire);
      setContexteExtrait(d.extrait_dernier_message || "");
      setNbContexte(d.nb_messages_contexte || 0);
      setMeta({ intention: d.intention, scenario: d.scenario, recus: d.documents_recus, total: d.documents_total });
      notify(
        d.nb_messages_contexte
          ? `Brouillon généré à partir des ${d.nb_messages_contexte} derniers messages — relisez avant d'envoyer`
          : "Brouillon généré — relisez avant d'envoyer",
      );
    } finally { setBusy(null); }
  };

  // Valider & envoyer (via Gmail OAuth de l'avocat)
  const envoyer = async () => {
    if (!draft || !draft.trim()) { notify("Le message est vide", false); return; }
    if (!destinataire) { notify("Destinataire manquant", false); return; }
    setSending(true);
    try {
      const r = await fetch(`${API}/api/emails/envoyer-reponse`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ dossier_id: id, objet, corps: draft, destinataire }),
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Échec de l'envoi", false); return; }
      notify(`Email envoyé à ${d.destinataire || destinataire} ✓`);
      setDraft(null); setContexteExtrait(""); setNbContexte(0); setMeta(null);
    } finally { setSending(false); }
  };

  // Clôturer / rouvrir le dossier
  const cloture = statut === "cloture";
  const basculerCloture = async () => {
    const cible = cloture ? "en_cours" : "cloture";
    setBusy("statut");
    try {
      const r = await fetch(`${API}/api/dossiers/${id}/statut`, {
        method: "PATCH", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ statut: cible }),
      }).catch(() => null);
      if (!r?.ok) { notify("Échec du changement de statut", false); return; }
      const d = await r.json();
      setStatut(d.status);
      notify(cible === "cloture" ? "Dossier clôturé ✓" : "Dossier rouvert");
      onRefresh?.();
    } finally { setBusy(null); }
  };

  // Suppression définitive du dossier
  const supprimer = async () => {
    if (!confirm("Supprimer DÉFINITIVEMENT ce dossier et ses données liées (documents, délais, factures, RDV, fiches) ?\nLes emails seront conservés mais détachés.")) return;
    setBusy("delete");
    try {
      const r = await fetch(`${API}/api/dossiers/${id}`, { method: "DELETE", headers: { ...authHeaders() } }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      // Idempotent côté UI : 2xx OU 404/410 (déjà supprimé) ⇒ on retire le dossier.
      if (r.ok || r.status === 404 || r.status === 410) {
        notify(r.ok ? "Dossier supprimé" : "Dossier déjà supprimé");
        onDeleted?.();
        return;
      }
      const d = await r.json().catch(() => ({}));
      notify(d.detail || "Échec de la suppression", false);
    } finally { setBusy(null); }
  };

  // Item 1 — ouverture directe sur le panneau Réponse (depuis "Répondre")
  useEffect(() => {
    if (!autoReply) return;
    const t = setTimeout(() => {
      replyRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      suggererReponse();
    }, 150);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoReply]);

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[88vh] overflow-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-6 py-4 sticky top-0 bg-white z-10">
          <h3 className="font-semibold text-gray-900">Dossier {dossier.reference || ""}
            {cloture && <span className="ml-2 text-xs bg-gray-200 text-gray-600 px-2 py-0.5 rounded-full">clôturé</span>}
          </h3>
          <div className="flex items-center gap-2">
            <button onClick={basculerCloture} disabled={busy === "statut"}
              className={`text-xs px-3 py-1.5 rounded-lg border disabled:opacity-50 ${cloture ? "text-blue-700 border-blue-300 hover:bg-blue-50" : "text-gray-600 hover:bg-gray-50"}`}>
              {busy === "statut" ? "…" : cloture ? "Rouvrir le dossier" : "✓ Clôturer le dossier"}
            </button>
            <button onClick={supprimer} disabled={busy === "delete"}
              className="text-xs px-3 py-1.5 rounded-lg border border-red-300 text-red-600 hover:bg-red-50 disabled:opacity-50">
              {busy === "delete" ? "…" : "🗑 Supprimer"}
            </button>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-2xl leading-none">×</button>
          </div>
        </div>

        <div className="p-6">
          <h4 className="font-semibold text-gray-900">{dossier.titre}</h4>
          <div className="flex gap-2 flex-wrap my-2">
            <span className="text-xs bg-gray-100 px-2 py-0.5 rounded-full">{dossier.specialite}</span>
            <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">{statut}</span>
            <span className="text-xs bg-orange-100 text-orange-700 px-2 py-0.5 rounded-full">{dossier.priorite}</span>
          </div>
          {dossier.client && <p className="text-sm text-gray-600">Client : {dossier.client.prenom} {dossier.client.nom} · {dossier.client.email}</p>}
          {dossier.description && <p className="text-sm text-gray-700 mt-2 bg-gray-50 rounded-lg p-3">{dossier.description}</p>}

          {/* ── Panneau Actions IA ── */}
          <div className="mt-5 border rounded-xl p-4 bg-indigo-50/40">
            <p className="text-sm font-semibold text-indigo-900 mb-2">⚡ Actions IA</p>
            <div className="flex flex-wrap gap-2">
              <button onClick={qualifier} disabled={!!busy}
                className="bg-indigo-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
                {busy === "qualifier" ? "…" : "Qualifier"}
              </button>
            </div>
            {resultat && (
              <div className="mt-3 bg-white border rounded-lg p-3">
                <p className="text-xs font-medium text-gray-500 mb-1">{resultat.titre}</p>
                <pre className="text-sm text-gray-700 whitespace-pre-wrap font-sans">{resultat.contenu}</pre>
              </div>
            )}
          </div>

          {/* ── Workflow réponse client (HITL) ── */}
          <div ref={replyRef} className="mt-4 border rounded-xl p-4">
            <p className="text-sm font-semibold text-gray-800 mb-2">✉️ Réponse au client</p>
            {draft === null ? (
              <button onClick={suggererReponse} disabled={!!busy}
                className="bg-blue-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50">
                {busy === "reponse" ? "Génération…" : "Suggérer une réponse"}
              </button>
            ) : (
              <div className="space-y-2">
                {meta?.scenario && (
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded-full border border-indigo-200">
                      {({
                        REPONDRE_QUESTION: "💬 Réponse directe à une question",
                        DEMANDER_PIECES: "📎 Demande de pièces manquantes",
                        ACCUSER_RECEPTION: "✅ Accusé de réception — analyse en cours",
                        INFOS_INSUFFISANTES: "📞 Infos insuffisantes → RDV proposé",
                      } as Record<string, string>)[meta.scenario] || meta.scenario}
                    </span>
                    {meta.intention && <span className="text-xs text-gray-400">intention : {meta.intention}</span>}
                    {meta.total > 0 && <span className="text-xs text-gray-400">· pièces {meta.recus}/{meta.total}</span>}
                  </div>
                )}
                {contexteExtrait ? (
                  <div className="bg-amber-50 border-l-4 border-amber-400 rounded px-3 py-2">
                    <p className="text-xs font-medium text-amber-800">↩︎ Réponse en réponse à :</p>
                    <p className="text-xs text-amber-700 italic mt-0.5 line-clamp-3">« {contexteExtrait} »</p>
                  </div>
                ) : (
                  <p className="text-xs text-gray-400">Aucun message client lié au dossier — brouillon générique.</p>
                )}
                <input className="w-full border rounded-lg px-3 py-2 text-sm" value={destinataire}
                  onChange={e => setDestinataire(e.target.value)} placeholder="Destinataire" />
                <input className="w-full border rounded-lg px-3 py-2 text-sm" value={objet}
                  onChange={e => setObjet(e.target.value)} placeholder="Objet" />
                <textarea className="w-full border rounded-lg px-3 py-2 text-sm h-48 font-sans" value={draft}
                  onChange={e => setDraft(e.target.value)} />
                <div className="flex gap-2 justify-end">
                  <button onClick={() => { setDraft(null); setContexteExtrait(""); setNbContexte(0); setMeta(null); }} disabled={sending}
                    className="border text-gray-500 text-sm px-3 py-2 rounded-lg hover:bg-gray-50 disabled:opacity-50">
                    Annuler
                  </button>
                  <button onClick={envoyer} disabled={sending}
                    className="bg-green-600 text-white text-sm px-4 py-2 rounded-lg hover:bg-green-700 disabled:opacity-50">
                    {sending ? "Envoi…" : "Valider & Envoyer"}
                  </button>
                </div>
                <p className="text-xs text-gray-400">L'email part de votre boîte Gmail connectée.</p>
              </div>
            )}
          </div>

          {/* ── Données du dossier ── */}
          {/* ── Module A — Checklist métier des pièces (statuts + aperçu PDF) ── */}
          <DocumentsChecklist dossierId={id} notify={notify} onRefresh={onRefresh} />
          <DeadlinesTimeline deadlines={dossier.deadlines || []} />
          {/* La liste des RDV (avec suppression) est gérée par le panneau interactif RendezVousPanel ci-dessous. */}
          <Section title={`Factures (${dossier.factures?.length || 0})`} items={(dossier.factures || []).map((x: any) => `${x.numero} — ${x.montant_ttc}€ (${x.statut})`)} />
          <Section title={`Comptes rendus (${dossier.comptes_rendus?.length || 0})`} items={(dossier.comptes_rendus || []).map((x: any) => x.titre)} />

          {/* ── Module D — Fiche de préparation (persistée + versionnée) ── */}
          <FichesPreparation dossierId={id} notify={notify} />
          {/* ── Module D — Rendez-vous ── */}
          <RendezVousPanel dossierId={id} notify={notify} onRefresh={onRefresh} />
          {/* ── Module E — Transcription audio → compte rendu ── */}
          <TranscriptionUpload dossierId={id} notify={notify} onRefresh={onRefresh} />
          {/* ── Module G — Facturation + relances ── */}
          <FacturationPanel dossierId={id} notify={notify} onRefresh={onRefresh} />
          {/* ── Module H — Pharmacie (Due Diligence / Valorisation / ARS) ── */}
          <PharmacieDDPanel dossierId={id} notify={notify} />
          {/* ── Module L — Cession : paramètres de l'acte + versement SECIB ── */}
          <CessionPanel dossierId={id} notify={notify} />
          {/* ── Modules I & J — Contentieux (squelettes) ── */}
          <ContentieuxPanel dossierId={id} notify={notify} />
          <ContentieuxPharmaPanel dossierId={id} notify={notify} />
        </div>
      </div>
    </div>
  );
}
