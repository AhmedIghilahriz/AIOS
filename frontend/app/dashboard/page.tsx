"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { supabase } from "../../lib/supabaseClient";
import { API, setAccessToken, authHeaders } from "../../lib/api";
import DossierDetailModal from "./DossierDetailModal";
import VeillePanel from "./VeillePanel";

interface Stats { total: number; urgents: number; en_attente_docs: number; revenus_mois: number; }
interface Email {
  id: string; expediteur: string; sujet: string; categorie: string;
  priorite: string; resume_ia: string; action_suggeree: string;
  dossier_id: string | null; dossier_cree_auto: boolean; date_reception: string;
  proposition_thread_id?: string | null;
}
interface Dossier { id: string; reference: string; titre: string; status: string; priorite: string; client_nom?: string; }

const PRIORITE_COLOR: Record<string, string> = {
  urgent: "bg-red-100 text-red-700 border-red-200",
  haute: "bg-orange-100 text-orange-700 border-orange-200",
  standard: "bg-blue-100 text-blue-700 border-blue-200",
  basse: "bg-gray-100 text-gray-500 border-gray-200",
};
const CATEGORIE_EMOJI: Record<string, string> = {
  client: "👤", prospect: "🌱", juridiction: "⚖️",
  fournisseur: "🏭", administratif: "🏛️", interne: "🏢", spam: "🗑️", autre: "📬",
};
const ACTION_LABEL: Record<string, { label: string; color: string }> = {
  "répondre":           { label: "Répondre",       color: "bg-blue-600" },
  "créer_dossier":      { label: "Confirmer dossier", color: "bg-green-600" },
  "ajouter_deadline":   { label: "Confirmer deadline", color: "bg-orange-600" },
  "transmettre_avocat": { label: "Transmettre",    color: "bg-purple-600" },
  "marquer_urgent":     { label: "Marquer urgent", color: "bg-red-600" },
  "valider_dossier":    { label: "🪄 Voir la proposition", color: "bg-purple-600" },
  "archiver":           { label: "Archiver",       color: "bg-gray-500" },
};

// ─────────────────────────────────────────────
//  PANEL SETUP
// ─────────────────────────────────────────────
function SetupPanel({ onDone }: { onDone: (id: string) => void }) {
  const [step, setStep] = useState<"form" | "gmail">("form");
  const [loading, setLoading] = useState(false);
  const [erreur, setErreur] = useState("");
  const [nom, setNom] = useState("");
  const [prenom, setPrenom] = useState("");
  const [email, setEmail] = useState("");
  const [avocatId, setAvocatId] = useState("");
  const [gmailUrl, setGmailUrl] = useState("");

  const creerAvocat = async () => {
    if (!nom || !prenom || !email) { setErreur("Tous les champs sont requis"); return; }
    setErreur(""); setLoading(true);
    try {
      const r = await fetch(`${API}/api/setup/avocat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nom, prenom, email, specialite: "droit des affaires" }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Erreur");
      const id = d.avocat_id;
      localStorage.setItem("aios_avocat_id", id);
      setAvocatId(id);
      setStep("gmail");
    } catch (e: any) { setErreur(e.message); }
    finally { setLoading(false); }
  };

  const connecterGmail = async () => {
    setLoading(true); setErreur("");
    try {
      const r = await fetch(`${API}/api/email/connect/google?avocat_id=${avocatId}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Erreur");
      setGmailUrl(d.url);
      window.open(d.url, "_blank");
    } catch (e: any) { setErreur(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center p-6">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-8">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold text-gray-900">AIOS</h1>
          <p className="text-gray-500 text-sm mt-1">Assistant IA — Cabinet d&apos;avocats pharmacie</p>
        </div>
        <div className="flex gap-2 mb-8">
          {["1. Votre profil", "2. Gmail"].map((label, i) => (
            <div key={label} className={`flex-1 h-1.5 rounded-full transition-all ${
              (step === "form" && i === 0) || step === "gmail" ? "bg-blue-600" : "bg-gray-200"
            }`} />
          ))}
        </div>

        {step === "form" && (
          <div className="space-y-4">
            <h2 className="font-semibold text-gray-800">Créez votre profil avocat</h2>
            <div>
              <label className="text-sm text-gray-600 block mb-1">Prénom</label>
              <input className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={prenom} onChange={e => setPrenom(e.target.value)} placeholder="Marie" />
            </div>
            <div>
              <label className="text-sm text-gray-600 block mb-1">Nom</label>
              <input className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={nom} onChange={e => setNom(e.target.value)} placeholder="Dupont" />
            </div>
            <div>
              <label className="text-sm text-gray-600 block mb-1">Email professionnel</label>
              <input className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={email} onChange={e => setEmail(e.target.value)} placeholder="marie.dupont@cabinet.fr" type="email" />
            </div>
            {erreur && <p className="text-red-600 text-sm bg-red-50 px-3 py-2 rounded-lg">{erreur}</p>}
            <button onClick={creerAvocat} disabled={loading}
              className="w-full bg-blue-600 text-white rounded-lg py-3 font-medium hover:bg-blue-700 disabled:opacity-50">
              {loading ? "Création en cours..." : "Créer mon profil →"}
            </button>
          </div>
        )}

        {step === "gmail" && (
          <div className="space-y-4">
            <h2 className="font-semibold text-gray-800">Connectez votre Gmail</h2>
            <div className="bg-blue-50 rounded-xl p-4 text-sm text-blue-800">
              <p className="font-medium mb-2">L&apos;IA va automatiquement :</p>
              <ul className="space-y-1 text-blue-700">
                <li>• Classifier vos emails (client, juridiction, spam...)</li>
                <li>• Créer les dossiers clients automatiquement</li>
                <li>• Vous proposer l&apos;action à faire (vous validez d&apos;un clic)</li>
              </ul>
            </div>
            <button onClick={connecterGmail} disabled={loading}
              className="w-full bg-red-500 text-white rounded-lg py-3 font-medium hover:bg-red-600 disabled:opacity-50">
              {loading ? "Génération du lien..." : "🔗 Connecter mon Gmail"}
            </button>
            {gmailUrl && (
              <div className="bg-gray-50 rounded-xl p-3 text-xs text-gray-500">
                <p className="mb-1">Lien ouvert dans un nouvel onglet. Autorisez puis revenez ici.</p>
                <a href={gmailUrl} target="_blank" rel="noreferrer" className="text-blue-600 underline break-all">{gmailUrl}</a>
              </div>
            )}
            {erreur && <p className="text-red-600 text-sm bg-red-50 px-3 py-2 rounded-lg">{erreur}</p>}
            <button onClick={() => onDone(avocatId)}
              className="w-full bg-green-600 text-white rounded-lg py-3 font-medium hover:bg-green-700">
              {gmailUrl ? "J'ai autorisé Gmail → Accéder au dashboard" : "Accéder au dashboard sans Gmail"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
//  LOGIN SUPABASE (auth optionnelle)
// ─────────────────────────────────────────────
function LoginSupabase() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [loading, setLoading] = useState(false);
  const [erreur, setErreur] = useState("");
  const [info, setInfo] = useState("");

  const submit = async () => {
    if (!supabase) return;
    setErreur(""); setInfo(""); setLoading(true);
    try {
      if (mode === "signup") {
        const { error } = await supabase.auth.signUp({ email, password });
        if (error) throw error;
        setInfo("Compte créé. Si la confirmation email est activée, validez votre email puis connectez-vous.");
        setMode("login");
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      }
    } catch (e: any) { setErreur(e.message || "Erreur d'authentification"); }
    finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center p-6">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-8">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold text-gray-900">AIOS</h1>
          <p className="text-gray-500 text-sm mt-1">{mode === "login" ? "Connexion avocat" : "Créer un compte"}</p>
        </div>
        <div className="space-y-4">
          <input className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            type="email" placeholder="email professionnel" value={email} onChange={e => setEmail(e.target.value)} />
          <input className="w-full border rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            type="password" placeholder="mot de passe" value={password} onChange={e => setPassword(e.target.value)}
            onKeyDown={e => e.key === "Enter" && submit()} />
          {erreur && <p className="text-red-600 text-sm bg-red-50 px-3 py-2 rounded-lg">{erreur}</p>}
          {info && <p className="text-green-700 text-sm bg-green-50 px-3 py-2 rounded-lg">{info}</p>}
          <button onClick={submit} disabled={loading}
            className="w-full bg-blue-600 text-white rounded-lg py-3 font-medium hover:bg-blue-700 disabled:opacity-50">
            {loading ? "..." : mode === "login" ? "Se connecter" : "Créer le compte"}
          </button>
          <button onClick={() => { setMode(mode === "login" ? "signup" : "login"); setErreur(""); setInfo(""); }}
            className="w-full text-sm text-gray-500 hover:text-gray-700">
            {mode === "login" ? "Pas de compte ? Créer un compte" : "Déjà un compte ? Se connecter"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
//  MODALE (détails email / dossier)
// ─────────────────────────────────────────────
function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[85vh] overflow-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-6 py-4 sticky top-0 bg-white">
          <h3 className="font-semibold text-gray-900">{title}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-2xl leading-none">×</button>
        </div>
        <div className="p-6">{children}</div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
//  DASHBOARD PRINCIPAL
// ─────────────────────────────────────────────
export default function Dashboard() {
  const [avocatId, setAvocatId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [stats, setStats] = useState<Stats | null>(null);
  const [emails, setEmails] = useState<Email[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [lastSync, setLastSync] = useState<string>("");
  const [tab, setTab] = useState<"emails" | "dossiers" | "propositions" | "veille">("emails");
  const [dossiers, setDossiers] = useState<Dossier[]>([]);
  const [recherche, setRecherche] = useState("");
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [notif, setNotif] = useState<{ msg: string; ok: boolean } | null>(null);
  const [streamConnected, setStreamConnected] = useState(false);
  const [newEmailAlert, setNewEmailAlert] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const [session, setSession] = useState<any>(null);
  const [authReady, setAuthReady] = useState(!supabase); // si pas d'auth configurée, prêt direct
  const [propositions, setPropositions] = useState<any[]>([]);
  const [propForm, setPropForm] = useState({ expediteur: "", sujet: "", categorie: "client" });
  const [highlightProp, setHighlightProp] = useState<string | null>(null);
  const [selectedEmail, setSelectedEmail] = useState<any>(null);
  const [selectedDossier, setSelectedDossier] = useState<any>(null);
  const [dossierAutoReply, setDossierAutoReply] = useState(false);
  const [triage, setTriage] = useState<any>(null);
  const [triageBusy, setTriageBusy] = useState(false);
  const [filtreUrgent, setFiltreUrgent] = useState(false);   // Cas 8 — filtre « dossiers urgents »

  const notify = (msg: string, ok = true) => { setNotif({ msg, ok }); setTimeout(() => setNotif(null), 5000); };

  useEffect(() => {
    const saved = localStorage.getItem("aios_avocat_id");
    if (saved) setAvocatId(saved);
    setReady(true);
  }, []);

  // Auth Supabase (optionnelle — ignorée si non configurée)
  useEffect(() => {
    if (!supabase) { setAuthReady(true); return; }
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session); setAccessToken(data.session?.access_token ?? null); setAuthReady(true);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => {
      setSession(s); setAccessToken(s?.access_token ?? null);
      if (!s) setAvocatId(null);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  // Synchronise l'avocat lié au compte Supabase connecté
  useEffect(() => {
    if (!supabase || !session || avocatId) return;
    fetch(`${API}/api/auth/sync`, { method: "POST", headers: { ...authHeaders() } })
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (d?.avocat_id) { localStorage.setItem("aios_avocat_id", d.avocat_id); setAvocatId(d.avocat_id); } })
      .catch(() => {});
  }, [session, avocatId]);

  const chargerStats = useCallback(async (id?: string) => {
    const aid = id || avocatId;
    const r = await fetch(`${API}/api/stats${aid ? `?avocat_id=${aid}` : ""}`).catch(() => null);
    if (r?.ok) setStats(await r.json());
  }, [avocatId]);

  const chargerEmails = useCallback(async (id: string) => {
    const r = await fetch(`${API}/api/emails/actions-en-attente?avocat_id=${id}`).catch(() => null);
    if (r?.ok) { const d = await r.json(); setEmails(d.emails || []); }
  }, []);

  const chargerDossiers = useCallback(async (id?: string) => {
    const aid = id || avocatId;
    if (recherche.trim()) {
      // Recherche sémantique
      const r = await fetch(`${API}/api/dossiers/recherche`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: recherche, cabinet_id: "default" })
      }).catch(() => null);
      if (r?.ok) { const d = await r.json(); setDossiers(d.resultats || []); }
    } else {
      // Liste directe sans embeddings
      const params = aid ? `?avocat_id=${aid}` : "";
      const r = await fetch(`${API}/api/dossiers${params}`).catch(() => null);
      if (r?.ok) { const d = await r.json(); setDossiers(d.resultats || []); }
    }
  }, [recherche, avocatId]);

  const chargerPropositions = useCallback(async (id?: string) => {
    const aid = id || avocatId;
    const params = aid ? `?avocat_id=${aid}` : "";
    const r = await fetch(`${API}/api/dossiers/propositions${params}`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) { const d = await r.json(); setPropositions(d.propositions || []); }
  }, [avocatId]);

  const proposerDossier = async () => {
    if (!propForm.expediteur) { notify("Expéditeur requis", false); return; }
    const r = await fetch(`${API}/api/dossiers/proposer`, {
      method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ ...propForm, avocat_id: avocatId }),
    }).catch(() => null);
    if (r?.ok) { setPropForm({ expediteur: "", sujet: "", categorie: "client" }); notify("Proposition créée — en attente de validation"); await chargerPropositions(); }
    else notify("Erreur lors de la création de la proposition", false);
  };

  const validerProposition = async (threadId: string, decision: "valider" | "rejeter") => {
    setLoadingAction(threadId + decision);
    try {
      const r = await fetch(`${API}/api/dossiers/valider/${threadId}`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ decision }),
      });
      if (r.ok) {
        const d = await r.json();
        setPropositions(p => p.filter(x => x.thread_id !== threadId));
        notify(decision === "valider" ? `Dossier créé (${(d.dossier_id || "").slice(0, 8)}…)` : "Proposition rejetée");
        await chargerStats();
        if (avocatId) await chargerEmails(avocatId);   // l'email lié est désormais traité → il disparaît
        if (tab === "dossiers") chargerDossiers();
      }
    } finally { setLoadingAction(null); }
  };

  // Connexion SSE (temps réel)
  useEffect(() => {
    if (!avocatId) return;
    chargerStats(avocatId);
    chargerEmails(avocatId);
    chargerDossiers(avocatId);
    chargerPropositions(avocatId);
    // Ouvrir le stream SSE
    const es = new EventSource(`${API}/api/email/stream/${avocatId}`);
    esRef.current = es;
    es.onopen = () => setStreamConnected(true);
    es.onerror = () => setStreamConnected(false);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === "update") {
          setNewEmailAlert(true);
          chargerEmails(avocatId);
          chargerStats();
          notify("Nouvel email detecte et classifie par l'IA !");
          setTimeout(() => setNewEmailAlert(false), 5000);
        }
      } catch {}
    };
    return () => { es.close(); setStreamConnected(false); };
  }, [avocatId, chargerStats, chargerEmails]);

  const syncEmails = async () => {
    if (!avocatId) return;
    setSyncing(true);
    try {
      const r = await fetch(`${API}/api/email/sync/${avocatId}`, { method: "POST" }).catch(() => null);
      if (!r) { notify("Impossible de contacter le serveur", false); return; }
      if (r.ok) {
        setLastSync(new Date().toLocaleTimeString("fr-FR"));
        await chargerEmails(avocatId);
        await chargerStats();
        await chargerDossiers();
        notify("Emails synchronisés et classifiés par IA");
      } else {
        const d = await r.json().catch(() => ({}));
        notify(d.detail || "Erreur sync — Gmail non connecté ?", false);
      }
    } finally { setSyncing(false); }
  };

  const confirmerAction = async (emailId: string) => {
    setLoadingAction(emailId);
    try {
      const r = await fetch(`${API}/api/emails/${emailId}/confirmer`, { method: "POST" });
      if (r.ok) { setEmails(p => p.filter(e => e.id !== emailId)); notify("Action confirmée"); await chargerStats(); }
    } finally { setLoadingAction(null); }
  };

  const ignorerEmail = async (emailId: string) => {
    setLoadingAction(emailId + "_ig");
    try {
      await fetch(`${API}/api/emails/${emailId}/ignorer`, { method: "POST" });
      setEmails(p => p.filter(e => e.id !== emailId));
    } finally { setLoadingAction(null); }
  };

  const deconnecter = () => {
    if (!confirm("Déconnecter votre compte ? Vous devrez reconfigurer Gmail.")) return;
    if (supabase) supabase.auth.signOut();
    localStorage.removeItem("aios_avocat_id");
    setAvocatId(null);
    setEmails([]);
    setStats(null);
    if (esRef.current) { esRef.current.close(); setStreamConnected(false); }
  };

  const ouvrirEmail = async (id: string) => {
    setTriage(null);
    const r = await fetch(`${API}/api/emails/${id}`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) setSelectedEmail(await r.json());
  };

  // Module A.4 — rejoue le graphe de triage LangGraph sur l'email pour exposer le détail
  const analyserTriage = async () => {
    if (!selectedEmail) return;
    setTriageBusy(true);
    try {
      const r = await fetch(`${API}/api/emails/trier`, {
        method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ expediteur: selectedEmail.expediteur, sujet: selectedEmail.sujet, corps: selectedEmail.corps || "" }),
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Échec de l'analyse", false); return; }
      setTriage(d);
    } finally { setTriageBusy(false); }
  };
  const ouvrirDossier = async (id: string, autoReply = false) => {
    const r = await fetch(`${API}/api/dossiers/${id}/details`, { headers: { ...authHeaders() } }).catch(() => null);
    if (r?.ok) { setDossierAutoReply(autoReply); setSelectedDossier(await r.json()); }
    else notify("Dossier introuvable ou serveur injoignable", false);
  };

  // Item 1 — clic sur l'action d'un email : ouvre le dossier (panneau Réponse) au lieu d'archiver en silence
  const traiterEmail = (email: Email) => {
    if (email.action_suggeree === "valider_dossier") {
      setHighlightProp(email.proposition_thread_id || null);
      setTab("propositions"); chargerPropositions();
      setTimeout(() => setHighlightProp(null), 4000);
    } else if (email.dossier_id) {
      ouvrirDossier(email.dossier_id, email.action_suggeree === "répondre");
    } else if (email.proposition_thread_id) {
      setHighlightProp(email.proposition_thread_id);
      setTab("propositions"); chargerPropositions();
      setTimeout(() => setHighlightProp(null), 4000);
    } else {
      confirmerAction(email.id);
    }
  };

  // Cas 8 — vue filtrée « urgents » (le badge/priorité vient du backend : urgence déterministe).
  const dossiersAffiches = dossiers.filter(d => !filtreUrgent || d.priorite === "urgent");

  if (!ready || !authReady) return null;
  if (supabase && !session) return <LoginSupabase />;
  if (!avocatId) return supabase
    ? <div className="min-h-screen flex items-center justify-center text-gray-500">Connexion…</div>
    : <SetupPanel onDone={(id) => setAvocatId(id)} />;

  return (
    <div className="min-h-screen bg-gray-50">
      {notif && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg text-sm font-medium text-white transition-all ${notif.ok ? "bg-green-600" : "bg-red-600"}`}>
          {notif.msg}
        </div>
      )}

      {newEmailAlert && (
        <div className="fixed top-16 right-4 z-50 px-4 py-3 rounded-lg shadow-xl text-sm font-bold text-white bg-purple-600 animate-bounce">
          Nouvel email detecte !
        </div>
      )}

      {/* Header */}
      <div className="bg-white border-b px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold text-gray-900">AIOS</h1>
          <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full font-medium">Cabinet d'Avocats</span>
          {lastSync && <span className="text-xs text-gray-400">Sync : {lastSync}</span>}
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${streamConnected ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-400"}`}>
            {streamConnected ? "Temps reel actif" : "Hors ligne"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={syncEmails} disabled={syncing} className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
            {syncing ? "Synchronisation..." : "🔄 Sync Gmail"}
          </button>
          <button onClick={deconnecter} className="border text-gray-500 px-3 py-2 rounded-lg text-sm hover:bg-gray-50">
            Déconnecter
          </button>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-6">


        {/* Stats — « Dossiers actifs » et « Urgents » sont cliquables (Cas 8) */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {[
            { label: "Dossiers actifs", value: stats?.total ?? 0, icon: "📁", color: "border-blue-200 text-blue-700",
              onClick: () => { setFiltreUrgent(false); setTab("dossiers"); chargerDossiers(); } },
            { label: "Emails à traiter", value: emails.length, icon: "📬", color: "border-purple-200 text-purple-700" },
            { label: "Urgents", value: stats?.urgents ?? 0, icon: "🔴", color: "border-red-200 text-red-700",
              onClick: () => { setFiltreUrgent(true); setTab("dossiers"); chargerDossiers(); } },
            { label: "CA ce mois", value: `${stats?.revenus_mois ?? 0}€`, icon: "💰", color: "border-green-200 text-green-700" },
          ].map(s => (
            <div key={s.label} onClick={s.onClick}
              className={`bg-white border rounded-xl p-4 ${s.color} ${s.onClick ? "cursor-pointer hover:shadow-md transition-shadow" : ""} ${s.label === "Urgents" && filtreUrgent ? "ring-2 ring-red-400" : ""}`}>
              <div className="flex justify-between items-center mb-2">
                <span className="text-xs font-medium opacity-70 uppercase tracking-wide">{s.label}</span>
                <span className="text-lg">{s.icon}</span>
              </div>
              <p className="text-2xl font-bold">{s.value}</p>
              {s.onClick && <p className="text-[11px] opacity-60 mt-1">{s.label === "Urgents" ? "Voir les dossiers urgents →" : "Voir tous les dossiers →"}</p>}
            </div>
          ))}
        </div>

        {/* Onglets */}
        <div className="flex gap-1 mb-4 bg-white border rounded-xl p-1 w-fit">
          {([ "emails", "propositions", "dossiers", "veille"] as const).map(t => (
            <button key={t} onClick={() => { setTab(t); if (t === "dossiers") chargerDossiers(); if (t === "propositions") chargerPropositions(); }}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${tab === t ? "bg-blue-600 text-white" : "text-gray-500 hover:text-gray-700"}`}>
              {t === "emails" ? `📬 Emails IA (${emails.length})` : t === "propositions" ? `🪄 Propositions (${propositions.length})` : t === "dossiers" ? "📁 Dossiers" : "🔎 Veille"}
            </button>
          ))}
        </div>

        {/* Tab Emails */}
        {tab === "emails" && (
          <div className="space-y-3">
            {emails.length === 0 && (
              <div className="bg-white rounded-xl border p-12 text-center">
                <p className="text-5xl mb-3">📭</p>
                <p className="font-semibold text-gray-700 text-lg">Aucun email en attente</p>
                <p className="text-sm text-gray-400 mt-1 mb-6">Cliquez "Sync Gmail" pour récupérer et classifier automatiquement vos emails non lus</p>
                <div className="bg-gray-50 rounded-xl p-4 text-left max-w-md mx-auto text-sm text-gray-600">
                  <p className="font-medium mb-2">L'IA va automatiquement :</p>
                  <ul className="space-y-1">
                    <li>✅ Filtrer les spams et pubs</li>
                    <li>✅ Classifier les emails professionnels</li>
                    <li>✅ Créer les dossiers clients automatiquement</li>
                    <li>✅ Suggérer une action (répondre, deadline...)</li>
                  </ul>
                </div>
                <button onClick={syncEmails} disabled={syncing} className="mt-6 bg-blue-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-blue-700">
                  {syncing ? "En cours..." : "🔄 Synchroniser Gmail maintenant"}
                </button>
              </div>
            )}

            {emails.map(email => (
              <div key={email.id} className={`bg-white rounded-xl border p-4 hover:shadow-sm transition-all ${email.priorite === "urgent" ? "border-l-4 border-l-red-500" : email.priorite === "haute" ? "border-l-4 border-l-orange-400" : ""}`}>
                <div className="flex items-start gap-3">
                  <span className="text-2xl mt-0.5">{CATEGORIE_EMOJI[email.categorie] || "📬"}</span>
                  <div className="flex-1 min-w-0 cursor-pointer" onClick={() => ouvrirEmail(email.id)}>
                    <div className="flex items-center gap-2 flex-wrap mb-1.5">
                      <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${PRIORITE_COLOR[email.priorite] || PRIORITE_COLOR.standard}`}>
                        {email.priorite}
                      </span>
                      <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">{email.categorie}</span>
                      {email.dossier_cree_auto && <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full border border-green-200">✨ Dossier créé auto</span>}
                      {email.dossier_id && !email.dossier_cree_auto && <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">📁 Dossier lié</span>}
                      {email.action_suggeree === "valider_dossier" && <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full border border-purple-200">🪄 Proposition créée</span>}
                    </div>
                    <p className="font-semibold text-gray-900 truncate">{email.sujet}</p>
                    <p className="text-sm text-gray-500">{email.expediteur}</p>
                    <p className="text-sm text-gray-700 mt-2 bg-gray-50 rounded-lg px-3 py-2">{email.resume_ia}</p>
                  </div>
                  <div className="flex flex-col gap-2 shrink-0">
                    <button onClick={() => traiterEmail(email)} disabled={loadingAction === email.id}
                      className={`text-white text-xs px-3 py-2 rounded-lg font-medium ${ACTION_LABEL[email.action_suggeree]?.color || "bg-blue-600"} disabled:opacity-50`}>
                      {loadingAction === email.id ? "..." : ACTION_LABEL[email.action_suggeree]?.label || email.action_suggeree}
                    </button>
                    <button onClick={() => ignorerEmail(email.id)} disabled={loadingAction === email.id + "_ig"}
                      className="text-gray-400 text-xs px-3 py-2 rounded-lg border hover:bg-gray-50">
                      Ignorer
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Tab Propositions (validation humaine — LangGraph interrupt) */}
        {tab === "propositions" && (
          <div className="space-y-4">
            <div className="bg-white rounded-xl border p-4">
              <p className="font-medium text-gray-800 mb-3">Proposer un dossier <span className="text-xs text-gray-400">(créé uniquement après votre validation)</span></p>
              <div className="grid md:grid-cols-4 gap-2">
                <input className="border rounded-lg px-3 py-2 text-sm md:col-span-2" placeholder="Expéditeur (ex: Pharmacie X <x@p.fr>)"
                  value={propForm.expediteur} onChange={e => setPropForm({ ...propForm, expediteur: e.target.value })} />
                <input className="border rounded-lg px-3 py-2 text-sm" placeholder="Sujet"
                  value={propForm.sujet} onChange={e => setPropForm({ ...propForm, sujet: e.target.value })} />
                <select className="border rounded-lg px-3 py-2 text-sm" value={propForm.categorie}
                  onChange={e => setPropForm({ ...propForm, categorie: e.target.value })}>
                  <option value="client">client</option>
                  <option value="prospect">prospect</option>
                  <option value="fournisseur">fournisseur</option>
                  <option value="administratif">administratif</option>
                  <option value="juridiction">juridiction</option>
                </select>
              </div>
              <button onClick={proposerDossier} className="mt-3 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700">
                Créer la proposition
              </button>
            </div>

            {propositions.length === 0 ? (
              <div className="bg-white rounded-xl border p-8 text-center text-gray-400">
                <p className="text-3xl mb-2">🪄</p>
                Aucune proposition en attente. Le dossier ne sera créé qu'après votre validation explicite.
              </div>
            ) : propositions.map(p => (
              <div key={p.thread_id}
                ref={el => { if (el && highlightProp && p.thread_id === highlightProp) el.scrollIntoView({ behavior: "smooth", block: "center" }); }}
                className={`bg-white rounded-xl border p-4 flex items-start gap-4 transition-all ${p.thread_id === highlightProp ? "ring-2 ring-purple-400 border-purple-300" : ""}`}>
                <span className="text-2xl mt-0.5">🪄</span>
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-gray-900">{p.proposition?.titre || "Nouveau dossier"}</p>
                  <p className="text-sm text-gray-600 mt-1">{p.message}</p>
                  <p className="text-xs text-gray-400 mt-1">Client : {p.proposition?.client_nom} · {p.proposition?.client_email}</p>
                </div>
                <div className="flex flex-col gap-2 shrink-0">
                  <button onClick={() => validerProposition(p.thread_id, "valider")} disabled={loadingAction === p.thread_id + "valider"}
                    className="bg-green-600 text-white text-xs px-3 py-2 rounded-lg font-medium hover:bg-green-700 disabled:opacity-50">
                    {loadingAction === p.thread_id + "valider" ? "..." : "✓ Valider & créer"}
                  </button>
                  <button onClick={() => validerProposition(p.thread_id, "rejeter")} disabled={loadingAction === p.thread_id + "rejeter"}
                    className="border text-gray-500 text-xs px-3 py-2 rounded-lg hover:bg-gray-50 disabled:opacity-50">
                    Rejeter
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Tab Dossiers */}
        {tab === "dossiers" && (
          <div>
            <div className="flex gap-3 mb-4">
              <input className="flex-1 border rounded-lg px-4 py-2.5 text-sm"
                placeholder='Recherche : "cession officine Lyon", "bail commercial pharmacie"...'
                value={recherche} onChange={e => setRecherche(e.target.value)}
                onKeyDown={e => e.key === "Enter" && chargerDossiers()} />
              <button onClick={() => chargerDossiers()} className="bg-blue-600 text-white px-4 py-2.5 rounded-lg text-sm font-medium">Rechercher</button>
            </div>
            {/* Cas 8 — filtre actif « urgents » */}
            {filtreUrgent && (
              <div className="flex items-center gap-2 mb-3">
                <span className="text-xs bg-red-100 text-red-700 px-3 py-1 rounded-full border border-red-200 font-medium">🔴 Filtre actif : dossiers urgents ({dossiersAffiches.length})</span>
                <button onClick={() => setFiltreUrgent(false)} className="text-xs text-gray-500 underline hover:text-gray-700">Retirer le filtre</button>
              </div>
            )}
            <div className="space-y-2">
              {dossiersAffiches.length === 0 ? (
                <div className="bg-white rounded-xl border p-8 text-center text-gray-400">
                  <p className="text-3xl mb-2">{filtreUrgent ? "🔴" : "📁"}</p>
                  {filtreUrgent
                    ? "Aucun dossier urgent. L'urgence est posée automatiquement par le triage (mise en demeure, délai de recours…)."
                    : "Aucun dossier trouvé. Synchronisez vos emails pour créer des dossiers automatiquement."}
                </div>
              ) : dossiersAffiches.map((d, i) => (
                <div key={i} onClick={() => ouvrirDossier(d.id)} className={`bg-white rounded-xl border p-4 flex items-center gap-4 hover:shadow-sm cursor-pointer ${d.priorite === "urgent" ? "border-l-4 border-l-red-500" : ""}`}>
                  <span className="text-sm font-mono bg-gray-100 px-2 py-1 rounded">{d.reference}</span>
                  <div className="flex-1">
                    <p className="font-medium text-gray-900">{d.titre}</p>
                    {d.client_nom && <p className="text-sm text-gray-500">Client : {d.client_nom}</p>}
                  </div>
                  {d.priorite === "urgent" && <span className="text-xs bg-red-600 text-white px-2 py-1 rounded-full font-bold">🔴 URGENT</span>}
                  <span className={`text-xs px-2 py-1 rounded-full border ${d.priorite === "urgent" ? "bg-red-100 text-red-700 border-red-200" : "bg-blue-100 text-blue-700 border-blue-200"}`}>
                    {d.status}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tab Veille réglementaire (Module K) */}
        {tab === "veille" && <VeillePanel notify={notify} />}
      </div>

      {selectedEmail && (
        <Modal title="Email" onClose={() => setSelectedEmail(null)}>
          <p className="text-xs text-gray-400 mb-1">{selectedEmail.expediteur}</p>
          <h4 className="font-semibold text-gray-900 mb-3">{selectedEmail.sujet}</h4>
          <div className="flex gap-2 flex-wrap mb-4">
            <span className="text-xs bg-gray-100 px-2 py-0.5 rounded-full">{selectedEmail.categorie}</span>
            <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">{selectedEmail.priorite}</span>
            {selectedEmail.proposition_thread_id && (
              <button onClick={() => { setHighlightProp(selectedEmail.proposition_thread_id); setSelectedEmail(null); setTab("propositions"); chargerPropositions(); setTimeout(() => setHighlightProp(null), 4000); }}
                className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full border border-purple-200 hover:bg-purple-200">🪄 Voir la proposition</button>
            )}
          </div>
          <p className="text-sm text-gray-700 whitespace-pre-wrap bg-gray-50 rounded-lg p-4">{selectedEmail.corps || "(aperçu indisponible)"}</p>
          {selectedEmail.resume_ia && <p className="text-sm text-gray-500 mt-3"><strong>Résumé IA :</strong> {selectedEmail.resume_ia}</p>}

          {/* ── Module A.4 — détail du triage LangGraph ── */}
          <div className="mt-4 border-t pt-3">
            <button onClick={analyserTriage} disabled={triageBusy}
              className="bg-indigo-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
              {triageBusy ? "Analyse du graphe…" : "🔬 Détail du triage IA (anti-injection · urgence)"}
            </button>
            {triage && (
              <div className="mt-3 bg-indigo-50/50 border rounded-lg p-3 space-y-2 text-sm">
                {/* Sécurité (anti-injection) */}
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-medium text-gray-500">Sécurité :</span>
                  {(triage.security_flags || []).includes("SUSPICIOUS_INJECTION")
                    ? <span className="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-full border border-red-200">⚠️ Injection suspecte — email non traité par l'IA</span>
                    : <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full border border-green-200">✓ Aucun signal d'injection</span>}
                </div>
                {/* Urgence + source */}
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-medium text-gray-500">Priorité :</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full border ${PRIORITE_COLOR[triage.priorite] || PRIORITE_COLOR.standard}`}>{triage.priorite}</span>
                  <span className="text-xs text-gray-500">source :</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${triage.urgence_source === "deterministe" ? "bg-amber-100 text-amber-700" : "bg-gray-100 text-gray-600"}`}>
                    {triage.urgence_source === "deterministe" ? "règle déterministe" : "classification LLM"}
                  </span>
                </div>
                {/* Classification */}
                <p className="text-gray-700">
                  <span className="text-xs font-medium text-gray-500">Classement : </span>
                  {triage.categorie}{triage.sous_categorie ? ` · ${triage.sous_categorie}` : ""}
                  {triage.dossier_reference ? ` · dossier ${triage.dossier_reference}` : ""}
                </p>
                {triage.resume && <p className="text-gray-600 italic">« {triage.resume} »</p>}
                {/* Chemin dans le graphe */}
                {Array.isArray(triage.chemin) && triage.chemin.length > 0 && (
                  <div className="flex items-center gap-1 flex-wrap pt-1">
                    <span className="text-xs font-medium text-gray-500">Graphe :</span>
                    {triage.chemin.map((n: string, i: number) => (
                      <span key={i} className="text-[11px] bg-white border text-gray-600 px-1.5 py-0.5 rounded">
                        {n}{i < triage.chemin.length - 1 ? " →" : ""}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </Modal>
      )}

      {selectedDossier && (
        <DossierDetailModal dossier={selectedDossier} onClose={() => setSelectedDossier(null)} notify={notify}
          autoReply={dossierAutoReply} onRefresh={() => ouvrirDossier(selectedDossier.id)}
          onDeleted={() => {
            const delId = selectedDossier?.id;
            setSelectedDossier(null);
            setDossiers(prev => prev.filter((d: any) => d.id !== delId));   // retrait local immédiat
            chargerStats();
          }} />
      )}

      <div className="fixed bottom-3 left-3 text-xs text-gray-300">ID : {avocatId.slice(0, 8)}...</div>
    </div>
  );
}
