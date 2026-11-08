# Deploying to Hugging Face Spaces

The Space holds only the three files in `space/`. It installs this repo from GitHub, so pushing to `main`
and pressing **Factory rebuild** in the Space settings is all an update needs.

1. Create a Gradio Space named `rti-sahayak`.
2. Add `space/README.md`, `space/requirements.txt` and `space/app.py` to it.
3. Optional, for AI drafting: in the Space's **Settings → Variables and secrets**, add a secret
   `ANTHROPIC_API_KEY`. Without it the app runs in offline mode (rule-based drafting).
