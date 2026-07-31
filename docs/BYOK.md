# BYOK — bring your own key

Use your own API keys for the AI stages: chapter analysis, highlight picking,
script rewriting, and voice narration. The app fetches the model list from the
key you supply, so the models offered are exactly the ones that key can reach.

Nothing here is required. With no keys at all the app still runs end to end on
the offline engines (rule-based analysis + espeak-ng), which is the v1.0
behaviour and is covered by tests so it cannot silently regress.

## Why keys are per workspace, not per env var

v1.0 read `MS_LLM_API_KEY` from the environment. That still works, but it means
one key for the whole server, editable only by whoever can restart the process.
BYOK stores keys per workspace in the database, encrypted, so they can be added,
rotated, and removed from the UI while the app is running.

Both paths coexist. Resolution order for every AI stage:

1. A **verified BYOK credential** with a model selected, for this workspace.
2. Otherwise the **environment** provider (`MS_LLM_*`, `MS_TTS_HTTP_*`).
3. Otherwise the **offline** engine (rules, espeak-ng).

Rule 3 is why the app never hard-fails for want of a key.

## Capabilities

| Kind  | Used for                                                    |
|-------|-------------------------------------------------------------|
| `llm` | Chapter analysis, highlights, beats, script rewriting       |
| `tts` | Voice-over narration                                        |

Split by capability rather than vendor, because one vendor can serve both roles
and one role can be served by many vendors.

## Supported providers

**LLM** — OpenAI, Anthropic (Claude), Google AI Studio (Gemini), OpenRouter,
Groq, DeepSeek, Mistral, Together AI, xAI (Grok), and any OpenAI-compatible
endpoint.

**TTS** — OpenAI Speech, ElevenLabs, and any endpoint exposing `/audio/speech`.

Most vendors speak the OpenAI wire format, so they share one adapter that is
parameterised by base URL. Anthropic (`x-api-key` + `/messages`) and Google
(key as query param + `generateContent`) have their own adapters because their
shapes genuinely differ.

### Self-hosted and proxies

Pick **Custom OpenAI-compatible** and supply the base URL. Works with Ollama,
LM Studio, vLLM, LiteLLM, and openedai-speech:

```
Ollama      http://127.0.0.1:11434/v1
LM Studio   http://127.0.0.1:1234/v1
vLLM        http://127.0.0.1:8000/v1
```

Local servers that need no auth still require a non-empty key field; any
placeholder string works.

## Adding a key in the UI

1. Log in, open **Kunci AI kamu (BYOK)** → **Atur kunci**.
2. Choose the capability and provider. The hint line shows the default base URL
   and links to that vendor's key page.
3. Paste the key. For a custom endpoint, fill in the base URL too.
4. Press **Tes & ambil daftar model**. This calls the provider and lists the
   models your key can reach. This is also the verification step.
5. Pick a model and press **Simpan kunci**.

The key field is cleared as soon as the key is stored, and the value is never
sent back to the browser again.

The panel at the top always states which provider each stage will actually use,
so you are never guessing whether a render will hit your paid key or the offline
engine.

## Adding a key over the API

```bash
# 1. What's supported (public: static metadata, no user data)
curl -s localhost:8000/api/credentials/providers | jq .

# 2. Verify a key and list its models WITHOUT saving
curl -s -b cookies.txt -X POST localhost:8000/api/credentials/test \
  -H 'Content-Type: application/json' \
  -d '{"kind":"llm","provider":"openai","api_key":"sk-..."}' | jq .

# 3. Save it with a chosen model
curl -s -b cookies.txt -X POST localhost:8000/api/credentials \
  -H 'Content-Type: application/json' \
  -d '{"kind":"llm","provider":"openai","api_key":"sk-...","model":"gpt-4o-mini"}' | jq .

# 4. Confirm what each stage will use
curl -s -b cookies.txt localhost:8000/api/credentials/active | jq .
```

Full route reference in [API.md](API.md#byok-credentials).

## How keys are protected

- **Encrypted at rest** with Fernet, using `data/.fernet_key` (0600) — the same
  key material that already protects YouTube OAuth tokens. The database column
  holds a `gAAAAA...` token, never the key.
- **Never returned.** Responses carry `key_hint` (last four characters) so you
  can tell two keys apart. There is no endpoint that reveals a stored key.
- **Never logged.** Audit entries record provider, model, and key hint. Provider
  error text is scrubbed before it is surfaced, in case a vendor echoes the key
  back in an error body.
- **Workspace-scoped.** Every route resolves through the caller's workspace, so
  one account cannot read or delete another's credentials.
- **Deleted means deleted.** Removing a credential deletes the row and its
  ciphertext rather than flagging it inactive.

There is a test for each of those claims in `tests/test_byok.py`, including one
that opens the SQLite file directly and asserts the plaintext key is not in it.

### What this does not protect against

Anyone who can read both `data/manhwashorts.db` and `data/.fernet_key` can
decrypt the keys. They sit in the same directory by default. This design keeps
a single-user local install simple; it is not a secrets manager. If that matters
for your setup:

- Keep `data/` on an encrypted volume.
- Set `MS_FERNET_KEY` from a secrets manager so the key never lands on disk next
  to the database.
- Prefer keys scoped to the minimum needed, and rotate them.

## Behaviour when a provider fails

Deliberately different per stage, because the right answer differs:

**Analysis** degrades to the offline analyser and records why in
`low_confidence_notes`, which surface in the UI. A failed API call costs you a
weaker analysis, not a dead pipeline — but it is never silent.

**Narration** raises an error and stops. You chose to pay for a specific voice;
quietly substituting robotic espeak audio into a video you are about to publish
would be the worse outcome.

## Model selection rules

- A model you did not choose is never substituted. Asking for a model the key
  cannot reach is an error, not a silent swap to something billable.
- A credential with no model selected is treated as not configured, so the
  pipeline uses the offline engine rather than guessing.
- **Muat ulang model** re-fetches the list and re-checks the key. If your
  selected model has been retired upstream, the selection is cleared and the
  status message says so.

## Cost control

The app does not track spend. Practical guards:

- Analysis sends at most 12,000 characters of source text per call.
- Analysis runs once per draft, not per render. Re-rendering does not re-analyse.
- TTS is called once per script beat (five by default), only when you generate a
  voice-over.
- The seed script and the test suite force the offline provider, so automated
  runs cannot spend your credits.

Set spending limits in your provider's console. That is the only real ceiling.

## Troubleshooting

**"HTTP 401: the key was rejected"** — key is wrong, revoked, or lacks
permission. For Google, confirm the Generative Language API is enabled.

**"could not connect. Check the base URL"** — for a local server, confirm it is
running and the URL ends in `/v1`.

**"the key works but no models were returned"** — the key is valid but the
account has no model access yet. Common on brand-new accounts with no billing.

**"model 'x' is not available on this key"** — press **Tes & ambil daftar
model** and pick from the returned list.

**Analysis quality did not improve** — check the BYOK panel actually shows
`kunci kamu` for analysis. If it shows `offline`, the credential is unverified or
has no model selected.
