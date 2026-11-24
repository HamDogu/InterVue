# Connect an existing Chatterbox installation

This app intentionally does not reinstall or clone Chatterbox. Inspect the existing server setup first. Use only voice references whose speaker has consented or which are licensed for this synthesis use; do not imitate actual company interviewers without permission.

1. Locate the current Chatterbox environment, model type, service endpoints and GPU allocation. Confirm installed API against [upstream examples](https://github.com/resemble-ai/chatterbox).
2. Create a small set of candidate voices (e.g. warm British English, neutral British English, neutral international English), using consented or appropriately licensed reference clips. Generate the same neutral interview greeting/question for each, and present the audio previews and licensing details to the owner for approval. Do not auto-approve any candidates.
3. Copy `examples/voices.example.json` to `data/voices.local.json`, replace examples with actual IDs/labels/reference paths, and mark **only approved** voices `approved: true`. `GET /api/voices` returns only approved IDs/labels, not private paths. The app offers those choices in Setup.
4. If using the existing HTTP service, set `CHATTERBOX_URL` to the private adapter endpoint. It must return a WAV stream for POST `{text, voice_id}` and map IDs to approved references internally. If using a native installation, an illustrative command is:

```bash
export CHATTERBOX_COMMAND='/path/to/existing/venv/bin/python /path/to/intervue/tools/chatterbox_adapter.py'
```

The adapter follows the upstream `ChatterboxTTS` example and must be tested with your installed version; command mode reloads the model each time. A persistent service is recommended for fast question playback and GPU efficiency. Keep references and caches outside Git, avoid exposing paths or raw audio to unauthorised users, and validate pronunciation/length.

5. Restart backend, select a voice and preview it in browser. Test Text only, Voice only and Text + voice. Verify that preparation starts **after** audio finishes; on a generation/playback failure the UI shows retry or explicit switch to text, never silently counts down. Test browser autoplay on the second question, after a delay and on mobile. If browser blocks playback, provide an explicit play control.
