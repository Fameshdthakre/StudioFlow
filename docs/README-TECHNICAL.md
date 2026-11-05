# StudioFlow — Amazon Image Production Starter

> Historical starter documentation. Current installation, storage and migration instructions are in the [root README](../README.md) and [deployment runbook](DOCKER_DEPLOY.md).

A team-friendly image generation and editing interface for Amazon listing assets. Supports **multiple product uploads, SKU labels, different scene prompts, generation queues, image revisions, approvals, JPG downloads, and team logins**.

This is an **MVP/starter**, not a finished production system for 500–1000 final images/day. It is intended to demonstrate the workflow and trial real image generation before scaling.

## Try it in Replit without technical setup

1. Import the ZIP project into Replit (see https://replit.com/import). If ZIP import is not offered, create a Python project and upload the unzipped project files to its file panel.
2. Click **Run**. The project's `.replit` file installs requirements and launches the app.
3. Open the **Webview**. It starts in **DEMO mode** and does not make API calls or spend money.
4. Upload 1 product photograph, enter 2–3 scenes, and click **Generate images**. The resulting watermarked sample previews demonstrate the gallery/approval/revision workflow, not AI-generated images.

## Enable real OpenAI Image API edits

**Never paste your API key into the application, a chat prompt, or frontend code.** In Replit's **Secrets / Environment variables**, add:

```
APP_MODE=live
OPENAI_API_KEY=sk-... (your actual secret key)
TEAM_USERS=designer1:YOUR-STRONG-PASSWORD,designer2:ANOTHER-STRONG-PASSWORD
COOKIE_SECURE=1
MAX_WORKERS=3
```

Then restart the app and log in with an employee username and password. All team accounts can see the same gallery, and each generation is attributed to the logged-in employee. API usage is billed to your OpenAI Platform organisation and **is separate from ChatGPT subscriptions**. Real generation runs the Images **edit endpoint**, using the photographed product as a source image.

Make sure API billing is enabled at https://platform.openai.com/settings/organization/billing/overview. Start with a small capped prepaid balance and only 1–3 sample generations. More outputs and every revision can incur charges. OpenAI API and Replit hosting are billed separately.

## Working features

- Upload up to **10 product photos** in one job (JPG/PNG/WEBP, up to 18 MB each).
- Assign editable SKU/ASIN labels for each product.
- Select **Lifestyle / MAIN image / Infographic** and write a separate scene on every line. Each uploaded product can optionally override the default scene list with its own prompts.
- Example: 10 products × 10 scene lines = **100 image tasks**, queued rather than one giant model call.
- `gpt-image-2.5-flare` for typical drafts; `gpt-image-2.5-sunburst` for detailed edits.
- Generate up to 12 scenes per product. Internal parallel worker setting defaults to 3. Set responsibly based on your API rate limits.
- Review, revise, approve, download. Revisions use both the preceding output and the untouched original reference.
- Exact delivery pixel sizes including 2000×2000, 1464×600 and 600×450 are produced by **resizing/cropping** the AI output (not true native sizes).
- Demo mode clearly marks all images with `DEMO PREVIEW — NO AI USED`.

## Important production limitations

- **No exact product fidelity guarantee.** Images must be visually checked before publishing. For MAIN photos, a pixel-preserving cutout/white-background pipeline may be preferable to generative editing.
- **Infographics:** the Image API may misspell or distort text, so this MVP deliberately reserves space for copy. Add exact text in a downstream graphic editor or renderer.
- Local SQLite/files saved under `data/` are appropriate for preview, **not reliable cloud production storage**. Before deployment, add persistent object storage, managed database, backups, audit logging, cost tracking, proper user management, rate limiting and security review.
- No auto charge estimate or spend enforcement is built into this MVP. Use OpenAI project budgets/alerts, billing credits and provider controls; these may not act as hard caps. Run tiny tests first.
- Maximum 120 image tasks per submission, but real concurrency and throughput depend on API rate limits, output quality and provider reliability.
- This starter is not integrated with Replit's managed authentication. The basic team login is only intended for early internal trials; use a production authentication service before broad deployment.
- Job state and queued operations are process-local; restarting the app can strand queued tasks. Production needs a durable job queue with workers.
- The app is not connected to ChatGPT Shared Projects; these are separate products.
- User accounts and local files are shared in the workspace; don't use sensitive customer data in a public demo.

## Project files

- `app.py` — FastAPI server, image edits, queue, jobs, approvals.
- `static/index.html` — interface.
- `static/styles.css` — styling.
- `static/app.js` — frontend interactions.
- `requirements.txt` — dependencies.
- `.replit` — Replit startup command.
- `.env.example` — sample secrets, not real credentials.

## Test outside Replit (optional)

```
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000. The demo mode works without an API key.

## Recommended next milestones

1. Test 1 product × 3 scenes × 1 revision in live mode.
2. Add reliable cloud file storage and daily-cost reporting by employee and SKU.
3. Add exact typography rendering for Amazon A+ and infographic assets.
4. Add masks/segmentation for targeted corrections and product integrity checks.
5. Add durable queue and API rate-limit-aware workers before targeting 500–1000 final images/day.
