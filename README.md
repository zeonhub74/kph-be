# KaritonPH Backend

FastAPI backend with Supabase auth + database.

## 1) Setup

1. Copy `.env.example` to `.env` and fill your keys.
2. Install deps:
   ```bash
   pip install -r requirements.txt
   ```
3. Run server:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

## 2) Supabase

1. Open Supabase SQL Editor.
2. Run `supabase_schema.sql`.
3. Add at least one admin in `public.users` by setting `role = 'admin'`.
4. If you manage roles in Supabase Auth metadata instead, set `app_metadata.role = 'admin'` for that user and the backend will sync it into `public.users` on login or `/api/me`.

### Existing DB Migration Note

If your `public.users` table was created before `email` existed, re-run `supabase_schema.sql`.
It now includes an `ALTER TABLE` + backfill step to populate `public.users.email` from `auth.users.email`.

## 3) API Endpoints

- POST `/api/login`
- POST `/api/logout`
- POST `/api/register`
- POST `/api/refresh`
- GET `/api/me`
- GET `/api/settings/product-prices`
- PUT `/api/settings/product-prices` (admin)

`/api/login`, `/api/refresh`, and `/api/me` now return the user's `profile`, including the backend-trusted `role` used for admin checks.
- GET `/api/products`
- GET `/api/products/{id}`
- POST `/api/products` (admin)
- PUT `/api/products/{id}` (admin)
- DELETE `/api/products/{id}` (admin)
- GET `/api/categories`
- POST `/api/categories` (admin)
- PUT `/api/categories/{id}` (admin)
- DELETE `/api/categories/{id}` (admin)
