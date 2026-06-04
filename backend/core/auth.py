"""
AIOS — Authentification via Supabase Auth
GRATUIT jusqu'à 50 000 utilisateurs actifs/mois
Remplace Auth0 (~23€/mois)
"""
from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import create_client, Client
from sqlalchemy.orm import Session
from core.models import Avocat
from core.database import get_db
import os

supabase: Client = create_client(
    os.getenv("SUPABASE_URL", ""),
    os.getenv("SUPABASE_SERVICE_KEY", "")
)

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(get_db)
) -> Avocat:
    """Vérifie le JWT Supabase et retourne l'avocat connecté."""
    token = credentials.credentials
    try:
        user = supabase.auth.get_user(token)
        if not user or not user.user:
            raise HTTPException(status_code=401, detail="Token invalide")

        avocat = db.query(Avocat).filter(
            Avocat.supabase_user_id == user.user.id
        ).first()

        if not avocat:
            raise HTTPException(status_code=403, detail="Utilisateur non autorisé")

        return avocat
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentification échouée: {str(e)}")


def creer_utilisateur_supabase(email: str, password: str) -> dict:
    """Crée un utilisateur dans Supabase Auth."""
    result = supabase.auth.admin.create_user({
        "email": email,
        "password": password,
        "email_confirm": True
    })
    return {"supabase_user_id": result.user.id, "email": result.user.email}


def verifier_token_supabase(token: str):
    """Valide un access_token Supabase et retourne l'objet user (lève 401 sinon)."""
    try:
        res = supabase.auth.get_user(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token invalide : {e}")
    if not res or not getattr(res, "user", None):
        raise HTTPException(status_code=401, detail="Token Supabase invalide")
    return res.user


def get_or_create_avocat(db: Session, supabase_user) -> Avocat:
    """Récupère l'avocat lié au compte Supabase, sinon le lie par email, sinon le crée."""
    import uuid
    from core.models import Cabinet, Specialite

    avocat = db.query(Avocat).filter(Avocat.supabase_user_id == supabase_user.id).first()
    if avocat:
        return avocat

    email = supabase_user.email
    avocat = db.query(Avocat).filter(Avocat.email == email).first()
    if avocat:
        avocat.supabase_user_id = supabase_user.id
        db.commit()
        return avocat

    cabinet = db.query(Cabinet).filter(Cabinet.id == "default").first()
    if not cabinet:
        cabinet = Cabinet(id="default", nom="Cabinet", email="cabinet@test.fr")
        db.add(cabinet)
        db.flush()

    avocat = Avocat(
        id=str(uuid.uuid4()),
        cabinet_id="default",
        supabase_user_id=supabase_user.id,
        email=email,
        nom=(email.split("@")[0] if email else "avocat"),
        prenom="",
        specialite=Specialite.AFFAIRES,
    )
    db.add(avocat)
    db.commit()
    db.refresh(avocat)
    return avocat
