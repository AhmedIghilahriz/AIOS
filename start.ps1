# AIOS - Demarrage complet (Backend + Frontend + Celery)
# Equivalent de "npm run dev" pour tout le projet

$ROOT = "C:\dev\aios"
$PYTHON = "$ROOT\backend\venv\Scripts\python.exe"
$CELERY = "$ROOT\backend\venv\Scripts\celery.exe"

Clear-Host
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   AIOS - Demarrage de l'application   " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Verifier que le venv existe
if (-not (Test-Path $PYTHON)) {
    Write-Host "[ERREUR] venv Python introuvable. Executez d'abord :" -ForegroundColor Red
    Write-Host "  cd C:\dev\aios\backend" -ForegroundColor Yellow
    Write-Host "  python -m venv venv" -ForegroundColor Yellow
    Write-Host "  .\venv\Scripts\pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

# Verifier node_modules frontend
if (-not (Test-Path "$ROOT\frontend\node_modules")) {
    Write-Host "[1/3] Installation des dependances npm..." -ForegroundColor Yellow
    Set-Location "$ROOT\frontend"
    npm install
}

Write-Host "[1/3] Backend FastAPI  -> http://localhost:8000" -ForegroundColor Green
Write-Host "[2/3] Frontend Next.js -> http://localhost:3000" -ForegroundColor Green
Write-Host "[3/3] Celery Worker    -> taches en arriere-plan" -ForegroundColor Green
Write-Host ""
Write-Host "Les logs de chaque service s'affichent ci-dessous." -ForegroundColor Gray
Write-Host "Ctrl+C pour tout arreter." -ForegroundColor Gray
Write-Host "----------------------------------------" -ForegroundColor DarkGray
Write-Host ""

# Demarrer backend dans un job PowerShell (logs visibles via Receive-Job)
$jobBackend = Start-Job -Name "AIOS-Backend" -ScriptBlock {
    Set-Location "C:\dev\aios\backend"
    & "C:\dev\aios\backend\venv\Scripts\python.exe" -m uvicorn main:app --reload --port 8000
}

# Demarrer Celery worker dans un job
# --pool=solo : OBLIGATOIRE sous Windows (le pool prefork/billiard par defaut y plante).
# (Optionnel) ajouter "--beat" pour activer les taches planifiees (relances, alertes delais).
$jobCelery = Start-Job -Name "AIOS-Celery" -ScriptBlock {
    Set-Location "C:\dev\aios\backend"
    & "C:\dev\aios\backend\venv\Scripts\celery.exe" "-A" "core.celery_app" "worker" "--pool=solo" "--loglevel=info"
}

# Demarrer frontend dans un job
$jobFrontend = Start-Job -Name "AIOS-Frontend" -ScriptBlock {
    Set-Location "C:\dev\aios\frontend"
    npm run dev
}

# Boucle d'affichage des logs en temps reel
$jobs = @($jobBackend, $jobCelery, $jobFrontend)
$colors = @{
    "AIOS-Backend"  = "Green"
    "AIOS-Celery"   = "Yellow"
    "AIOS-Frontend" = "Cyan"
}

try {
    while ($true) {
        foreach ($job in $jobs) {
            $output = Receive-Job -Job $job 2>&1
            if ($output) {
                foreach ($line in $output) {
                    $prefix = "[" + $job.Name.Replace("AIOS-","") + "] "
                    Write-Host ($prefix + $line) -ForegroundColor $colors[$job.Name]
                }
            }
        }

        # Verifier si un job a plante
        foreach ($job in $jobs) {
            if ($job.State -eq "Failed") {
                Write-Host ("[ERREUR] " + $job.Name + " a plante !") -ForegroundColor Red
                Receive-Job -Job $job 2>&1 | ForEach-Object { Write-Host $_ -ForegroundColor Red }
            }
        }

        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host ""
    Write-Host "Arret de tous les services..." -ForegroundColor Red
    $jobs | Stop-Job
    $jobs | Remove-Job -Force
    Write-Host "AIOS arrete." -ForegroundColor Gray
}
