# Deploying to Hugging Face Spaces

The Space holds only the three files in `space/`. It installs this repo from GitHub at a pinned commit.
To update it, push to `main`, then change the commit in the Space's `requirements.txt`. (A plain restart
reuses the cached install, so the Space keeps running the old code.)

1. Create a Gradio Space named `rti-sahayak`. (Free accounts get ZeroGPU hardware, which is why `space/app.py` has a placeholder `@spaces.GPU` function.)
2. Add `space/README.md`, `space/requirements.txt` and `space/app.py` to it.
3. Optional, for AI drafting: in the Space's **Settings → Variables and secrets**, add a secret
   `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`). Without it the app runs in offline mode (rule-based drafting).
