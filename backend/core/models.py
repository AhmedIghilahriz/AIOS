"""
AIOS — Modèles de base de données
PostgreSQL + pgvector (GRATUIT — remplace Pinecone)
"""
from sqlalchemy import (
    Column, String, DateTime, Float, Text,
    ForeignKey, JSON, Enum, Boolean, Integer, UniqueConstraint
)
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from datetime import datetime
import uuid
import enum
from .database import Base


def gen_uuid():
    return str(uuid.uuid4())


# ── Enums ─────────────────────────────────────────────────────────────

class DossierStatus(str, enum.Enum):
    NOUVEAU = "nouveau"
    EN_COURS = "en_cours"
    EN_ATTENTE = "en_attente"
    CLOTURE = "cloture"
    ARCHIVE = "archive"

class Specialite(str, enum.Enum):
    AFFAIRES = "affaires"
    SOCIAL = "social"
    IMMOBILIER = "immobilier"
    FAMILLE = "famille"
    PENAL = "penal"
    FISCAL = "fiscal"
    AUTRE = "autre"

class PrioriteLevel(str, enum.Enum):
    URGENT = "urgent"
    HAUTE = "haute"
    STANDARD = "standard"
    BASSE = "basse"

class StatutDocument(str, enum.Enum):
    ATTENDU = "attendu"
    RECU = "recu"
    VALIDE = "valide"
    REFUSE = "refuse"

class StatutFacture(str, enum.Enum):
    BROUILLON = "brouillon"
    ENVOYEE = "envoyee"
    PAYEE = "payee"
    RETARD = "retard"
    LITIGE = "litige"


# ── Modèles ───────────────────────────────────────────────────────────

class Cabinet(Base):
    __tablename__ = "cabinets"

    id = Column(String, primary_key=True, default=gen_uuid)
    nom = Column(String, nullable=False)
    email = Column(String)
    telephone = Column(String)
    adresse = Column(Text)
    specialites = Column(JSON, default=list)
    supabase_org_id = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    avocats = relationship("Avocat", back_populates="cabinet")
    clients = relationship("Client", back_populates="cabinet")
    dossiers = relationship("Dossier", back_populates="cabinet")


class Avocat(Base):
    __tablename__ = "avocats"

    id = Column(String, primary_key=True, default=gen_uuid)
    supabase_user_id = Column(String, unique=True)
    email = Column(String, unique=True, nullable=False)
    nom = Column(String, nullable=False)
    prenom = Column(String, nullable=False)
    specialite = Column(Enum(Specialite))
    cabinet_id = Column(String, ForeignKey("cabinets.id"))
    actif = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    cabinet = relationship("Cabinet", back_populates="avocats")
    dossiers = relationship("Dossier", back_populates="avocat")


class Client(Base):
    __tablename__ = "clients"

    id = Column(String, primary_key=True, default=gen_uuid)
    nom = Column(String, nullable=False)
    prenom = Column(String)
    email = Column(String)
    telephone = Column(String)
    type_client = Column(String, default="particulier")
    siret = Column(String)
    adresse = Column(Text)
    notes = Column(Text)
    tags = Column(JSON, default=list)
    cabinet_id = Column(String, ForeignKey("cabinets.id"))
    embedding = Column(Vector(1536))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    cabinet = relationship("Cabinet", back_populates="clients")
    dossiers = relationship("Dossier", back_populates="client")


class Dossier(Base):
    __tablename__ = "dossiers"

    id = Column(String, primary_key=True, default=gen_uuid)
    reference = Column(String, unique=True)
    titre = Column(String, nullable=False)
    specialite = Column(Enum(Specialite), nullable=False)
    status = Column(Enum(DossierStatus), default=DossierStatus.NOUVEAU)
    priorite = Column(Enum(PrioriteLevel), default=PrioriteLevel.STANDARD)
    description = Column(Text)
    metadonnees = Column(JSON, default=dict)
    client_id = Column(String, ForeignKey("clients.id"))
    avocat_id = Column(String, ForeignKey("avocats.id"))
    cabinet_id = Column(String, ForeignKey("cabinets.id"))
    embedding = Column(Vector(1536))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = relationship("Client", back_populates="dossiers")
    avocat = relationship("Avocat", back_populates="dossiers")
    cabinet = relationship("Cabinet", back_populates="dossiers")
    documents = relationship("Document", back_populates="dossier", cascade="all, delete-orphan")
    deadlines = relationship("Deadline", back_populates="dossier", cascade="all, delete-orphan")
    factures = relationship("Facture", back_populates="dossier")
    compte_rendus = relationship("CompteRendu", back_populates="dossier")
    emails = relationship("EmailLog", back_populates="dossier")


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=gen_uuid)
    nom = Column(String, nullable=False)
    type_doc = Column(String)
    statut = Column(Enum(StatutDocument), default=StatutDocument.ATTENDU)
    chemin_stockage = Column(String)
    taille_octets = Column(Integer)
    mime_type = Column(String)
    ocr_contenu = Column(Text)
    dossier_id = Column(String, ForeignKey("dossiers.id"))
    deadline_reception = Column(DateTime)
    embedding = Column(Vector(1536))
    created_at = Column(DateTime, default=datetime.utcnow)
    recu_at = Column(DateTime)

    dossier = relationship("Dossier", back_populates="documents")


class Deadline(Base):
    __tablename__ = "deadlines"

    id = Column(String, primary_key=True, default=gen_uuid)
    titre = Column(String, nullable=False)
    description = Column(Text)
    date_echeance = Column(DateTime, nullable=False)
    type_delai = Column(String, nullable=False)
    alertes_envoyees = Column(JSON, default=list)
    acquitte = Column(Boolean, default=False)
    acquitte_par = Column(String, ForeignKey("avocats.id"))
    dossier_id = Column(String, ForeignKey("dossiers.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    dossier = relationship("Dossier", back_populates="deadlines")


class Facture(Base):
    __tablename__ = "factures"

    id = Column(String, primary_key=True, default=gen_uuid)
    numero = Column(String, unique=True, nullable=False)
    montant_ht = Column(Float, nullable=False)
    taux_tva = Column(Float, default=0.20)
    statut = Column(Enum(StatutFacture), default=StatutFacture.BROUILLON)
    type_honoraires = Column(String, default="fixe")
    description = Column(Text)
    date_emission = Column(DateTime)
    date_echeance = Column(DateTime)
    date_paiement = Column(DateTime)
    relances = Column(JSON, default=list)
    dossier_id = Column(String, ForeignKey("dossiers.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    dossier = relationship("Dossier", back_populates="factures")

    @property
    def montant_ttc(self):
        return round(self.montant_ht * (1 + self.taux_tva), 2)


class CompteRendu(Base):
    __tablename__ = "compte_rendus"

    id = Column(String, primary_key=True, default=gen_uuid)
    titre = Column(String)
    type_reunion = Column(String)
    transcription = Column(Text)
    resume = Column(Text)
    points_discutes = Column(JSON, default=list)
    decisions = Column(JSON, default=list)
    prochaines_actions = Column(JSON, default=list)
    dossier_id = Column(String, ForeignKey("dossiers.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    embedding = Column(Vector(1536))

    dossier = relationship("Dossier", back_populates="compte_rendus")


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(String, primary_key=True, default=gen_uuid)
    expediteur = Column(String)
    sujet = Column(String)
    corps = Column(Text)
    categorie = Column(String)
    traite = Column(Boolean, default=False)
    dossier_id = Column(String, ForeignKey("dossiers.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    dossier = relationship("Dossier", back_populates="emails")


class RendezVous(Base):
    __tablename__ = "rendez_vous"

    id = Column(String, primary_key=True, default=gen_uuid)
    calcom_booking_id = Column(String, unique=True)
    type_rdv = Column(String)
    duree_minutes = Column(Integer, default=60)
    date_heure = Column(DateTime)
    statut = Column(String, default="confirme")
    client_id = Column(String, ForeignKey("clients.id"))
    avocat_id = Column(String, ForeignKey("avocats.id"))
    dossier_id = Column(String, ForeignKey("dossiers.id"))
    fiche_preparation = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Intégration email via OAuth2 (Google / Microsoft) ─────────────────
# On ne stocke JAMAIS le mot de passe de l'avocat.
# On stocke uniquement le refresh_token chiffré fourni par OAuth2.

class EmailIntegration(Base):
    """
    Connecte la boîte mail d'un avocat via OAuth2.
    Fournisseurs supportés : google (Gmail), microsoft (Outlook/M365).
    Le refresh_token est chiffré en base — jamais de mot de passe.
    """
    __tablename__ = "email_integrations"

    id = Column(String, primary_key=True, default=gen_uuid)
    avocat_id = Column(String, ForeignKey("avocats.id"), nullable=False, unique=True)
    fournisseur = Column(String, nullable=False)          # "google" | "microsoft"
    email_compte = Column(String, nullable=False)         # adresse connectée
    # Tokens OAuth2 — refresh_token chiffré (AES-256 via secret_key)
    refresh_token_enc = Column(Text, nullable=False)
    access_token_enc = Column(Text)                       # cache court terme
    token_expiry = Column(DateTime)
    scopes = Column(JSON, default=list)                   # ex: ["gmail.readonly","gmail.modify"]
    actif = Column(Boolean, default=True)
    derniere_sync = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    avocat = relationship("Avocat", backref="email_integration")


class EmailClassifie(Base):
    """
    Email collecté et classifié automatiquement depuis la boîte de l'avocat.
    Lié à un dossier si détecté, sinon en attente de classement manuel.
    """
    __tablename__ = "emails_classifies"

    id = Column(String, primary_key=True, default=gen_uuid)
    integration_id = Column(String, ForeignKey("email_integrations.id"), nullable=False)
    message_id_externe = Column(String, unique=True)       # ID Gmail/Outlook
    expediteur = Column(String)
    destinataires = Column(JSON, default=list)
    sujet = Column(String)
    corps_extrait = Column(Text)                           # 1 000 premiers caractères max
    date_reception = Column(DateTime)
    # Classification IA
    categorie = Column(String)                             # "client","fournisseur","juridiction","spam","autre"
    sous_categorie = Column(String)                        # "demande_piece","urgence","relance_paiement",...
    priorite = Column(Enum(PrioriteLevel), default=PrioriteLevel.STANDARD)
    resume_ia = Column(Text)                               # résumé 1 phrase généré par Claude
    action_suggeree = Column(String)                       # "répondre","transmettre","archiver","créer dossier"
    # Lien dossier
    dossier_id = Column(String, ForeignKey("dossiers.id"))
    dossier_detecte_auto = Column(Boolean, default=False)
    proposition_thread_id = Column(String)   # lien vers la proposition de dossier (HITL), si créée
    traite = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    integration = relationship("EmailIntegration", backref="emails")
    dossier = relationship("Dossier")


class PropositionDossier(Base):
    """
    Proposition de création de dossier en attente de validation avocat (HITL LangGraph).
    id == thread_id du graphe (clé de reprise via Command(resume=...)).
    """
    __tablename__ = "propositions_dossier"

    id = Column(String, primary_key=True)                 # = thread_id du graphe
    avocat_id = Column(String, ForeignKey("avocats.id"))
    cabinet_id = Column(String, default="default")
    proposition = Column(JSON, default=dict)
    message = Column(Text)
    statut = Column(String, default="EN_ATTENTE")         # EN_ATTENTE | CREE | REJETE
    dossier_id = Column(String, ForeignKey("dossiers.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)


class VeilleAlerte(Base):
    """Module K — alerte de veille réglementaire pharmaceutique."""
    __tablename__ = "veille_alertes"

    id = Column(String, primary_key=True, default=gen_uuid)
    titre = Column(String, nullable=False)
    source = Column(String)                               # JORF | CPAM | ORDRE | ...
    url = Column(String)
    date_publication = Column(String)
    impact = Column(String, default="MOYEN")             # CRITIQUE | ELEVE | MOYEN
    resume = Column(Text)
    mots_cles = Column(JSON, default=list)
    lu = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RegleJuridique(Base):
    """
    Surcharge configurable des « recettes juridiques » (délais, critères) par
    cabinet puis par avocat. Le code fournit les valeurs par défaut ; cette table
    ne stocke QUE les surcharges (payload = override partiel fusionné sur les defaults).

    Résolution : defaults (code) ← override cabinet ← override avocat.
    module : "I" (procédure), "J" (contentieux pharmacien) — extensible (F, H…).
    """
    __tablename__ = "regles_juridiques"

    id = Column(String, primary_key=True, default=gen_uuid)
    scope = Column(String, nullable=False)        # "cabinet" | "avocat"
    scope_id = Column(String, nullable=False)     # cabinet_id ou avocat_id
    module = Column(String, nullable=False)       # "I" | "J" | ...
    payload = Column(JSON, default=dict)          # override partiel des règles
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("scope", "scope_id", "module", name="uq_regle_scope_module"),
    )


class FichePreparation(Base):
    """
    Module D — Fiche de préparation versionnée, persistée et liée à un dossier.
    Chaque génération crée une nouvelle version (v1, v2…) ; l'« écrasement »
    met à jour la dernière version en place. La plus récente = version max.
    """
    __tablename__ = "fiches_preparation"

    id = Column(String, primary_key=True, default=gen_uuid)
    dossier_id = Column(String, ForeignKey("dossiers.id"), nullable=False)
    version = Column(Integer, default=1)
    type_rdv = Column(String)
    contenu = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class ActeCession(Base):
    """
    Module L — Promesse / Acte de cession d'officine, versionné et lié à un dossier.
    Même logique de versionnement que FichePreparation (la plus récente = version max).
    `type` = "promesse" | "acte" ; `sous_type` = "cession_fonds" | "cession_parts" | …
    Document de TRAVAIL assisté (gabarit déterministe + exposé LLM) — jamais signé sans avocat.
    """
    __tablename__ = "actes_cession"

    id = Column(String, primary_key=True, default=gen_uuid)
    dossier_id = Column(String, ForeignKey("dossiers.id"), nullable=False)
    type = Column(String, default="promesse")        # promesse | acte
    sous_type = Column(String)                        # cession_fonds | cession_parts | cession_titres
    version = Column(Integer, default=1)
    contenu = Column(Text)                            # Markdown
    statut = Column(String, default="BROUILLON")     # BROUILLON | VALIDE
    created_at = Column(DateTime, default=datetime.utcnow)


class DocumentChunk(Base):
    """
    Module L — RAG. Segment (chunk) d'un document, vectorisé pour la recherche sémantique
    ciblée (retrieval par champ). Permet de n'envoyer au LLM que les passages pertinents
    et de citer la source (pièce + page). 1 document → N chunks.
    """
    __tablename__ = "document_chunks"

    id = Column(String, primary_key=True, default=gen_uuid)
    document_id = Column(String, ForeignKey("documents.id"))
    dossier_id = Column(String, ForeignKey("dossiers.id"))
    chunk_index = Column(Integer, default=0)         # ordre du chunk dans le document
    page = Column(Integer)                            # n° de page 1-indexé (None si inconnu)
    contenu = Column(Text)                            # texte du chunk
    embedding = Column(Vector(1536))
    created_at = Column(DateTime, default=datetime.utcnow)
