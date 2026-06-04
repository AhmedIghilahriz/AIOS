"use client";

/** Module F — Timeline des délais avec alertes J-30 / J-14 / J-7 / J-1.
 *  Affichage pur (calcul des jours restants côté client). */

interface DeadlineItem {
  titre: string;
  date_echeance?: string | null;
  type_delai?: string;
  acquitte?: boolean;
}

function joursRestants(dateIso?: string | null): number | null {
  if (!dateIso) return null;
  const d = new Date(dateIso).getTime();
  if (isNaN(d)) return null;
  return Math.ceil((d - Date.now()) / 86_400_000);
}

function palier(j: number | null, acquitte?: boolean) {
  if (acquitte) return { dot: "bg-gray-300", badge: "bg-gray-100 text-gray-400", label: "acquitté" };
  if (j == null) return { dot: "bg-gray-300", badge: "bg-gray-100 text-gray-500", label: "—" };
  if (j < 0) return { dot: "bg-red-600", badge: "bg-red-100 text-red-700 border border-red-200", label: `échu (J+${-j})` };
  if (j <= 7) return { dot: "bg-red-500", badge: "bg-red-100 text-red-700 border border-red-200", label: `J-${j}` };
  if (j <= 14) return { dot: "bg-orange-500", badge: "bg-orange-100 text-orange-700 border border-orange-200", label: `J-${j}` };
  if (j <= 30) return { dot: "bg-amber-400", badge: "bg-amber-100 text-amber-700 border border-amber-200", label: `J-${j}` };
  return { dot: "bg-blue-400", badge: "bg-blue-50 text-blue-600 border border-blue-100", label: `J-${j}` };
}

export default function DeadlinesTimeline({ deadlines }: { deadlines: DeadlineItem[] }) {
  // Tri : non-acquittés d'abord, par échéance croissante ; acquittés en bas.
  const items = [...(deadlines || [])].sort((a, b) => {
    if (!!a.acquitte !== !!b.acquitte) return a.acquitte ? 1 : -1;
    const ta = a.date_echeance ? new Date(a.date_echeance).getTime() : Infinity;
    const tb = b.date_echeance ? new Date(b.date_echeance).getTime() : Infinity;
    return ta - tb;
  });

  return (
    <div className="mt-4">
      <p className="text-sm font-medium text-gray-800 mb-2">Délais ({items.length}) <span className="text-xs text-gray-400">— alertes J-30 / J-14 / J-7 / J-1</span></p>
      {items.length === 0 ? (
        <p className="text-sm text-gray-400">—</p>
      ) : (
        <ol className="relative border-l border-gray-200 ml-1.5 space-y-3">
          {items.map((d, i) => {
            const j = joursRestants(d.date_echeance);
            const p = palier(j, d.acquitte);
            return (
              <li key={i} className="ml-4">
                <span className={`absolute -left-[7px] mt-1.5 w-3 h-3 rounded-full ${p.dot}`} />
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`text-xs px-2 py-0.5 rounded-full ${p.badge}`}>{p.label}</span>
                  <span className={`text-sm ${d.acquitte ? "text-gray-400 line-through" : "text-gray-800"}`}>{d.titre}</span>
                  {d.type_delai && <span className="text-[11px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">{d.type_delai}</span>}
                </div>
                {d.date_echeance && (
                  <p className="text-xs text-gray-400 mt-0.5">
                    {new Date(d.date_echeance).toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "short", year: "numeric" })}
                  </p>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
