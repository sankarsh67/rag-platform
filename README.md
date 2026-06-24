# Enterprise Multi-Tenant RAG Backend Platform

A production-grade, multi-tenant **Retrieval-Augmented Generation (RAG)** backend built with Django, DRF, Celery, PostgreSQL, Qdrant, and Gemini. Organizations can upload private documents, have them chunked and embedded automatically, and query them through a grounded, citation-backed chat API — all under strict per-tenant data isolation.

This is a fully working system, verified end-to-end: real JWT auth with tenant/role claims, real PDF ingestion through Celery, real Gemini embeddings, real Qdrant vector storage and retrieval, and real LLM-generated answers with source citations.

## Features

- **Multi-tenant by design** — every tenant's documents live in their own Qdrant collection; every query is filtered at the JWT, ORM, and vector-DB layers
- **JWT authentication** with custom `tenant_id` / `role` claims, via SimpleJWT
- **Role-based access control** — `admin` vs `member`, enforced per-endpoint
- **Async document ingestion** — PDF upload → Celery task → text extraction → chunking → embeddings → vector storage, with status tracking (`processing` / `active` / `failed`)
- **RAG chat** — retrieval-augmented answers via Gemini, with real source citations back to the originating document chunks
- **Admin analytics** — per-tenant usage and document-status breakdowns
- **Dockerized** — Django/Gunicorn, Celery worker, PostgreSQL, Redis, Qdrant, and Nginx, all orchestrated via Docker Compose

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, Django, Django REST Framework |
| Auth | JWT (SimpleJWT), custom tenant/role claims |
| Database | PostgreSQL |
| Vector store | Qdrant (Pinecone supported as an alternative) |
| LLM / Embeddings | Gemini, via LangChain |
| Background jobs | Celery + Redis |
| Document processing | pypdf, Unstructured, LangChain text splitters |
| Infra | Docker, Docker Compose, Nginx, Gunicorn |

## Architecture

```
Client
  │
  ▼
Nginx  ──────────────────────────────►  Django + DRF API
                                            │  (Auth, RBAC, Tenant Isolation)
                                            │
                        ┌───────────────────┼───────────────────┐
                        ▼                   ▼                   ▼
                  PostgreSQL          Qdrant (per-tenant      Redis
            (Tenants, Users,           collections)        (Celery broker)
             Docs, Chats)                                       │
                                                                 ▼
                                                          Celery Worker
                                                    (PDF ingestion & embedding)
```

## Tenant isolation, enforced at three layers

1. **JWT** — every access token carries `tenant_id` and `role` claims; re-validated against the DB on every request
2. **ORM** — every queryset is explicitly scoped via `Model.objects.for_tenant(tenant_id)`, with no implicit global filtering
3. **Vector DB** — one Qdrant collection per tenant (`tenant_<uuid>`), so even a relational-filter bug can't leak another tenant's vectors

## Quickstart

### Option A — Full Docker

```bash
git clone <this-repo-url>
cd rag_platform
cp .env.example .env   # fill in GEMINI_API_KEY and a real DJANGO_SECRET_KEY
docker compose build
docker compose up -d
docker compose exec api python manage.py migrate
docker compose exec api python manage.py createsuperuser
```

API is then live at `http://localhost/api/`.

### Option B — Hybrid (infra in Docker, Django in a local venv)

```bash
docker compose up -d postgres redis qdrant
python -m venv venv && source venv/bin/activate   # venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.local.example .env.local
export $(grep -v '^#' .env.local | xargs)          # Git Bash; see .env.local.example for PowerShell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver

# second terminal, same exported env
celery -A rag_platform worker -l info
```

Full setup notes, including the Windows/MINGW64-specific gotchas (stale Nginx DNS after recreating `api`, `env_file` not reloading on `restart` vs `up -d`, etc.), are documented inline in `docker-compose.yml` and `.env.local.example`.

## API overview

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/register` | Create a new tenant + its first admin user |
| `POST` | `/api/auth/login` | Get JWT access/refresh tokens |
| `POST` | `/api/auth/refresh` | Rotate refresh token |
| `GET/PATCH` | `/api/tenant/{id}/` | Manage own tenant (admin) |
| `GET/POST/PATCH/DELETE` | `/api/users/` | Tenant-scoped user management (admin) |
| `POST` | `/api/documents/upload` | Upload a PDF, triggers async ingestion |
| `GET/DELETE` | `/api/documents/{id}/` | List / delete documents |
| `POST` | `/api/chat/sessions/` | Create a chat session |
| `GET` | `/api/chat/{session_id}` | List messages in a session |
| `POST` | `/api/chat/{session_id}/message` | Send a message, get a RAG answer + citations |
| `GET` | `/api/admin/usage` | Per-tenant usage counters (admin) |
| `GET` | `/api/admin/document-stats` | Document counts by status (admin) |

## Project structure

```
rag_platform/
├── manage.py
├── rag_platform/          # project package — settings, urls, celery app
├── core/                  # the app
│   ├── models.py          # Tenant, User, Document, DocumentChunk, ChatSession, ChatMessage
│   ├── managers.py        # tenant-aware QuerySet/Manager pattern
│   ├── authentication.py  # JWT auth with tenant/role claim validation
│   ├── permissions.py     # RBAC permission classes
│   ├── vector_db.py       # Qdrant/Pinecone abstraction
│   ├── utils.py           # PDF extraction, chunking, embeddings
│   ├── rag_utils.py       # RAG orchestrator (retrieval + LLM + citations)
│   ├── tasks.py           # Celery ingestion & cleanup tasks
│   ├── views.py / urls.py / serializers.py / admin.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── nginx.conf
├── gunicorn_config.py
├── .env.example
└── .env.local.example
```

## Known limitations

- No automated test suite yet (manual end-to-end verification only)
- Pinecone backend is implemented but not live-tested against a real Pinecone account (Qdrant is the proven path)
- No DB-level row-level security — isolation relies on application-level filtering plus per-tenant vector collections
- No SSO, hybrid search, or streaming responses yet

## License

Add a license of your choice (MIT, Apache-2.0, etc.) before making this repository public.