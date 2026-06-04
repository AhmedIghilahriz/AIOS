"use client";
import { useState } from "react";
import { API, authHeaders } from "../../lib/api";

/** Module E — Upload d'un audio de réunion → transcription + compte rendu (Groq Whisper). */

export default function TranscriptionUpload({
  dossierId, notify, onRefresh,
}: { dossierId: string; notify: (m: string, ok?: boolean) => void; onRefresh?: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [typeReunion, setTypeReunion] = useState("client");
  const [result, setResult] = useState<{ transcription: string; cr: any } | null>(null);

  const transcrire = async () => {
    if (!file) { notify("Sélectionnez un fichier audio", false); return; }
    setBusy(true); setResult(null);
    try {
      const form = new FormData();
      form.append("audio", file);
      // type_reunion passé en query (paramètre simple côté FastAPI)
      const r = await fetch(`${API}/api/reunions/transcrire/${dossierId}?type_reunion=${encodeURIComponent(typeReunion)}`, {
        method: "POST", headers: { ...authHeaders() }, body: form,
      }).catch(() => null);
      if (!r) { notify("Serveur injoignable", false); return; }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { notify(d.detail || "Échec de la transcription", false); return; }
      setResult({ transcription: d.transcription || "", cr: d.compte_rendu || {} });
      notify("Transcription + compte rendu générés ✓");
      onRefresh?.();
    } finally { setBusy(false); }
  };

  return (
    <div className="mt-4 border rounded-xl overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-violet-50/60 hover:bg-violet-50 text-left">
        <span className="text-sm font-semibold text-violet-900">🎙️ Transcription réunion → compte rendu (Module E)</span>
        <span className="text-violet-700 text-lg">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="p-4 space-y-3">
          <div className="flex gap-2 flex-wrap items-end">
            <div className="flex-1 min-w-[200px]">
              <label className="text-xs text-gray-500 block mb-1">Fichier audio (mp3, m4a, wav…)</label>
              <input type="file" accept="audio/*" onChange={e => setFile(e.target.files?.[0] || null)}
                className="block w-full text-sm text-gray-600 file:mr-3 file:py-2 file:px-3 file:rounded-lg file:border-0 file:bg-violet-100 file:text-violet-700 file:text-sm hover:file:bg-violet-200" />
            </div>
            <div>
              <label className="text-xs text-gray-500 block mb-1">Type</label>
              <select value={typeReunion} onChange={e => setTypeReunion(e.target.value)} className="border rounded-lg px-3 py-2 text-sm">
                <option value="client">Client</option>
                <option value="interne">Interne</option>
                <option value="contradictoire">Contradictoire</option>
              </select>
            </div>
            <button onClick={transcrire} disabled={busy || !file}
              className="bg-violet-600 text-white text-sm px-3 py-2 rounded-lg hover:bg-violet-700 disabled:opacity-50">
              {busy ? "Transcription…" : "Transcrire"}
            </button>
          </div>

          {result && (
            <div className="space-y-2">
              {result.cr?.titre && (
                <div className="bg-gray-50 rounded-lg p-3">
                  <p className="text-sm font-semibold text-gray-800">{result.cr.titre}</p>
                  {result.cr.resume && <p className="text-sm text-gray-600 mt-1">{result.cr.resume}</p>}
                  {Array.isArray(result.cr.prochaines_actions) && result.cr.prochaines_actions.length > 0 && (
                    <div className="mt-2">
                      <p className="text-xs font-medium text-gray-500">Prochaines actions :</p>
                      <ul className="text-xs text-gray-600 list-disc list-inside">
                        {result.cr.prochaines_actions.map((a: any, i: number) => <li key={i}>{typeof a === "string" ? a : JSON.stringify(a)}</li>)}
                      </ul>
                    </div>
                  )}
                </div>
              )}
              <details className="bg-gray-50 rounded-lg p-3">
                <summary className="text-xs font-medium text-gray-500 cursor-pointer">Transcription brute</summary>
                <pre className="text-sm text-gray-700 whitespace-pre-wrap font-sans mt-2">{result.transcription}</pre>
              </details>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
