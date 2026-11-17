# Web frontend (Next.js)

Screens: `/login`, `/chat` (patients and staff), `/dashboard` (profile, appointments, labs, bills), `/admin` (document upload and versions).

```bash
npm install
npm run dev        # http://localhost:3000 (expects the API on http://localhost:8000)
```

Set `NEXT_PUBLIC_API_URL` in `.env.local` to point at a different API (see `.env.local.example`).
