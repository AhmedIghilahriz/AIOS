# AIOS — Guide de démarrage complet
## Stack 100% gratuite (sauf Claude API)

---

## 📁 Structure des fichiers (ce que tu as reçu)

```raw
aios/
├── docker-compose.yml          ← Lance tout le projet (1 commande)
├── .env.example                ← Copier en .env et remplir
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt        ← Dépendances Python
│   ├── main.py                 ← API principale (point d'entrée)
│   │
│   ├── core/
│   │   ├── database.py         ← Connexion PostgreSQL
│   │   ├── models.py           ← Toutes les tables (Cabinet, Dossier, etc.)
│   │   ├── orchestrateur.py    ← LLM Claude + embeddings + Langfuse
│   │   ├── auth.py             ← Auth Supabase (remplace Auth0)
│   │   └── celery_app.py       ← Scheduler tâches auto
│   │
│   ├── modules/
│   │   ├── module_a.py         ← Collecte documents + emails Resend
│   │   ├── module_b.py         ← CRM + recherche sémantique pgvector
│   │   ├── module_e.py         ← Transcription audio Groq (gratuit)
│   │   ├── module_f.py         ← Délais légaux (ZERO LLM - déterministe)
│   │   └── module_g.py         ← Facturation + relances impayés
│   │
│   └── workers/
│       └── tasks.py            ← Tâches Celery (relances auto, alertes)
│
├── frontend/
│   ├── Dockerfile
│   └── app/dashboard/page.tsx  ← Dashboard principal
│
└── infra/
    └── init.sql                ← Initialisation PostgreSQL + pgvector
```

---

## 🚀 ÉTAPES DE DÉMARRAGE (dans l'ordre)

### ÉTAPE 1 — Obtenir les clés API gratuites

| Service | Lien | Gratuit |
|---------|------|---------|
| **Anthropic (Claude)** | https://console.anthropic.com | ~$5 crédits offerts |
| **Groq (Whisper audio)** | https://console.groq.com | ✅ 28h/mois gratuit |
| **Resend (emails)** | https://resend.com | ✅ 3 000/mois gratuit |
| **Supabase (auth)** | https://supabase.com | ✅ Gratuit jusqu'à 50k users |
| **OpenAI (embeddings)** | https://platform.openai.com | ~$5 crédits offerts (optionnel) |

---

### ÉTAPE 2 — Créer le fichier .env

```bash
# Dans le dossier aios/
cp .env.example .env
```

Puis ouvrir `.env` et remplir :
- `ANTHROPIC_API_KEY` → depuis https://console.anthropic.com
- `GROQ_API_KEY` → depuis https://console.groq.com
- `RESEND_API_KEY` → depuis https://resend.com
- `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` → depuis https://supabase.com
- `EMAIL_AVOCAT` → votre adresse email pour recevoir les alertes

---

### ÉTAPE 3 — Installer Docker

Si Docker n'est pas installé :
- **Windows/Mac** : https://www.docker.com/products/docker-desktop
- **Linux** : `curl -fsSL https://get.docker.com | sh`

---

### ÉTAPE 4 — Lancer tout le projet

```bash
# Dans le dossier aios/
docker-compose up -d
```

Attendre ~2 minutes que tout démarre. Vérifier :

```bash
docker-compose ps
# Tous les services doivent être "Up"
```

---

### ÉTAPE 5 — Vérifier que ça fonctionne

Ouvrir dans le navigateur :

| URL | Service |
|-----|---------|
| http://localhost:8000/docs | ✅ API Backend (Swagger) |
| http://localhost:3000 | ✅ Frontend Dashboard |
| http://localhost:3001 | ✅ Langfuse (monitoring LLM) |

Tester l'API :
```bash
curl http://localhost:8000/api/health
# Réponse attendue : {"status": "ok", "version": "1.0.0"}
```

---

### ÉTAPE 6 — Frontend Next.js (à faire manuellement)

Le frontend nécessite d'initialiser un projet Next.js :

```bash
cd frontend/
npx create-next-app@latest . --typescript --tailwind --app
# Accepter toutes les options par défaut

# Copier le fichier dashboard fourni
# frontend/app/dashboard/page.tsx est déjà créé
```

Ajouter dans `frontend/.env.local` :
```raw
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## 🔧 Commandes utiles

```bash
# Voir les logs
docker-compose logs -f backend

# Redémarrer un service
docker-compose restart backend

# Arrêter tout
docker-compose down

# Reconstruire après modification du code
docker-compose up -d --build backend
```

---

## 🚫 Utilisation sans Docker

1. Installer Python 3.11+ et Redis localement si tu utilises Celery.
2. Créer le fichier `.env` à la racine du projet et remplir les valeurs Supabase.
3. Depuis le dossier racine :

```bash
cd backend
python -m pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

4. Lancer le frontend sans Docker :

```bash
cd ../frontend
npm install
npm run dev
```

5. Pour Supabase PostgreSQL, remplace `DATABASE_URL` dans `.env` par l’URL fournie par Supabase.

6. Si tu n’as pas Redis localement, adapte `REDIS_URL` dans `.env` vers un service Redis ou supprime temporairement les tâches Celery.

---

## 💰 Coûts mensuels réels

| Service | Coût |
|---------|------|
| Claude Sonnet API | ~$20-100/mois selon usage |
| OpenAI Embeddings | ~$1-5/mois (très peu) |
| Hébergement (VPS OVH) | ~$20-50/mois |
| **Resend emails** | ✅ GRATUIT (3k/mois) |
| **Groq Whisper** | ✅ GRATUIT (28h/mois) |
| **Supabase Auth** | ✅ GRATUIT |
| **pgvector** | ✅ GRATUIT (intégré PostgreSQL) |
| **Langfuse** | ✅ GRATUIT (self-hosted) |
| **Tesseract OCR** | ✅ GRATUIT |

**Total estimé : $40-150/mois** (vs $400+ avec la stack originale)

---

## ⚠️ Points importants avant mise en production

1. **Module F (délais légaux)** — Faire valider la table `DELAIS_LEGAUX` par un avocat
2. **RGPD** — Configurer le chiffrement des données clients en base
3. **Backups** — Configurer des sauvegardes automatiques PostgreSQL
4. **HTTPS** — Ajouter un reverse proxy (Nginx + Let's Encrypt) en production
5. **module_g.py** — Remplacer `"client@example.com"` par `client.email` réel

---

## 📋 Modules restants à coder (non inclus dans ce livrable)

| Module | Description | Priorité |
|--------|-------------|----------|
| Module C | Qualification automatique dossiers entrants | Haute |
| Module D | Intégration Cal.com (RDV) | Moyenne |
| Portail client | Interface dépôt documents | Haute |
| Auth frontend | Login Supabase côté Next.js | Haute |
