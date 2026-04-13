# qww-ops

> Operational status dashboard — auto-refreshes every 30 minutes via GitHub Actions.

**Live:** https://kanisha423-coder.github.io/qww-ops

---

## Overview

`qww-ops` is a lightweight, zero-dependency ops dashboard hosted on GitHub Pages. It displays service health, incidents, deployments, and KPIs. The page is rebuilt and redeployed automatically every 30 minutes using the included GitHub Actions workflow.

---

## Files

| File | Purpose |
|------|---------|
| `index.html` | Dashboard UI with embedded demo data |
| `.github/workflows/deploy.yml` | GitHub Actions — deploys on push and every 30 min |
| `README.md` | This file |

---

## Setup

1. Fork or clone this repo
2. Go to **Settings → Pages → Source** → select **GitHub Actions**
3. Push a commit (or run the workflow manually via **Actions → Deploy Dashboard → Run workflow**)
4. Your dashboard will be live at `https://<your-username>.github.io/qww-ops` within ~60 seconds

---

## API Contract

When you're ready to wire in real data, replace the hard-coded values in `index.html` with a fetch call to your data endpoint. The dashboard expects a JSON object at a configurable URL with the following shape:

```json
{
  "updatedAt": "2026-04-13T12:00:00Z",
  "kpis": {
    "uptime": "99.8%",
    "activeServices": "12 / 13",
    "incidents24h": 2,
    "avgLatencyMs": 142
  },
  "services": [
    {
      "name": "API Gateway",
      "region": "us-east-1",
      "status": "healthy",
      "latencyMs": 38,
      "uptime7d": 1.0
    }
  ],
  "incidents": [
    {
      "id": "#1041",
      "description": "Data Pipeline latency spike",
      "severity": "medium",
      "status": "investigating"
    }
  ],
  "deployments": [
    {
      "service": "API Gateway",
      "version": "v3.12.1",
      "by": "ci-bot",
      "status": "success"
    }
  ]
}
```

### Status values

| Field | Allowed values |
|-------|---------------|
| `services[].status` | `healthy`, `degraded`, `down` |
| `incidents[].severity` | `low`, `medium`, `high`, `critical` |
| `incidents[].status` | `investigating`, `identified`, `monitoring`, `resolved` |
| `deployments[].status` | `success`, `pending`, `failed` |

---

## Local development

No build step needed — just open `index.html` in a browser.

---

## License

MIT

