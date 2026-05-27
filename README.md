# TopTaskAI Legal

Public-facing legal documents for the TopTaskAI mobile app.

Hosted at:
- https://toptaskai.com/ (landing page)
- https://toptaskai.com/privacy
- https://toptaskai.com/terms

## Structure

```
.
├── index.html              # Landing page at root
├── privacy.html            # Rendered privacy policy (served at /privacy)
├── terms.html              # Rendered terms of service (served at /terms)
├── privacy-policy.md       # Markdown source of privacy policy (source of truth)
├── terms-of-service.md     # Markdown source of terms (source of truth)
├── vercel.json             # Vercel config (clean URLs, no .html suffix)
└── README.md
```

## How to update content

1. Edit the `.md` file (source of truth).
2. Regenerate the `.html` file with pandoc + the build script (or ask Claude to regenerate).
3. Commit both the `.md` and the `.html` changes.
4. Push to `main` — Vercel auto-deploys.

## Hosting

Deployed via Vercel free tier from the `main` branch. Custom domain (`toptaskai.com`) configured at Vercel; DNS records live at GoDaddy.
