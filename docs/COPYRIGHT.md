> **CURRENT POLICY — 2026-09-03:** Rights metadata is retained for audit, but
> enforcement is disabled by default (`MS_REQUIRE_RIGHTS_DECLARATION=false`).
> Missing/rejected declarations are not production blockers unless enforcement is
> explicitly enabled. This policy does not grant or imply legal permission.

# Copyright and rights handling

**This is not legal advice.** This document explains what the software does. It
cannot tell you whether your specific use is lawful. That depends on your
jurisdiction, how much of the work you use, and how transformative your
commentary is. When in doubt, get permission or consult a lawyer.

## The honest position

Manhwa recap videos sit in genuinely contested territory. Reaction and commentary
channels get taken down regularly, and so do channels that thought they were
fine. What survives tends to have three properties:

1. The commentary is substantial and original, not a readthrough.
2. Only as much source material is shown as the commentary needs.
3. The video does not substitute for reading the original.

ManhwaShorts Studio helps you record permissions and nudges you toward those
three properties. It does not make your use lawful, and it will not stop you from
publishing something you should not.

## What the software enforces

### Rights metadata is auditable; enforcement is optional

Assets can carry owner, licence basis, permission reference, attribution, and declaration state. The ingest layer normalizes that metadata into a rights status; incomplete/unknown declarations remain visible rather than being silently treated as permission.

The current production default is `MS_REQUIRE_RIGHTS_DECLARATION=false`. Under that default, missing/rejected declarations are audit/policy findings but do not block render or publish. A deployment may intentionally set the flag to true, in which case the rights policy becomes blocking. Changing that policy requires configuration/tests/docs to move together.

Recognized licence bases include `owned`, `licensed`, `permission_granted`, `public_domain`, `creative_commons`, and `unknown`. "Fair use" is deliberately not represented as a licence assertion because whether a use qualifies is a legal conclusion outside the software's competence.

### Verbatim copying is blocked

Narration is compared against your source material using 5-gram shingle overlap:

| Similarity | Result |
|---|---|
| ≥50% | **Blocked** — `policy.not_transformative` |
| 25–49% | Warning — `policy.high_similarity` |
| <25% | Pass |

This gate caught a real bug during development: the rules-based generator was
extractive, copying source sentences directly, and produced 60% verbatim
narration. The gate refused to render it. The fix was to make the generator
actually summarise.

Expect 25–35% similarity with the offline rules generator — it compresses your
sentences rather than genuinely rewriting them. An LLM provider gets this lower.

### Panel volume is capped

More than 8 images from one chapter (configurable via
`MS_MAX_CONSECUTIVE_PANELS_PER_CHAPTER`) raises `policy.panel_volume`. A recap
that reprints most of a chapter panel-by-panel is much harder to defend as
commentary.

This is a warning, not a block, because the right number depends on your format.
Overriding it records your reason.

### Public visibility is explicit

Publishing defaults to `private`. An explicit `privacy_status: public` request publishes Public directly; `unlisted` publishes Unlisted. There is no second Public-confirmation gate, and a channel-level Public Upload default cannot override an omitted/private request because browser publishing explicitly selects and verifies requested visibility.

This is an operational visibility rule, not a copyright determination. A Public request does not weaken source/evidence/QC gates or grant permission to use source material.

### Everything is audited

`AuditLog` records asset uploads with their rights status, script approvals with
the approver, warning overrides with the stated reason, renders, and uploads.
If you ever need to show your process, the record exists.

## What the software refuses to do

- **No watermark removal.** It is not part of the production pipeline.
- **No implicit publication.** Upload happens only through an explicit publish boundary. Normal UI/manual approval remains explicit; a trusted local agent may approve-and-publish only when the user explicitly requests `until: "publish"` with `approval_mode: "trusted_agent"` and `confirm_publish_intent: true`.
- **No claim that source acquisition equals permission.** Manual uploads and optional Suwayomi imports both retain rights/source metadata and enter the same policy/audit model. The Suwayomi connector is a source transport, not a rights bypass.
- **No training on your material by this application.** Provider calls, when configured, are ordinary inference requests governed by the selected provider; local/offline modes avoid those external requests.

## What remains your responsibility

The software cannot verify that:

- You actually hold the rights you declared.
- Your permission covers YouTube distribution specifically.
- Your commentary is substantial enough to be transformative in your
  jurisdiction.
- The publisher will not issue a takedown regardless.

A declaration in this app is a record of **your** assertion. It is not
verification.

## Practical guidance

**Safest material, in rough order:**

1. Your own original art and writing.
2. Officially licensed press or promotional assets, used within their terms.
3. Material with written permission from the rights holder.
4. Public domain or permissively licensed CC works.

**Reduce risk regardless of source:**

- Write genuinely original commentary — analysis, theories, reactions — not a
  plot readthrough.
- Show fewer panels than you think you need.
- Never post the full chapter's key reveals; leave a reason to read the original.
- Credit the creator and publisher in the description. The app adds a rights
  notice automatically; add specific credit yourself.
- Link to the official source where readers can pay for it.
- Upload as private first and watch it back before going public.

**If you receive a takedown:**

- Do not counter-notify reflexively. A rejected counter-notice is worse than a
  removed video.
- Pull the audit log for the project: it shows what you declared and when.
- If your permission was genuine, contact the rights holder directly before
  involving YouTube's dispute process.

## Deleting material

`DELETE /api/projects/{id}` removes the project and every blob no other project references. YouTube browser authentication lives in persistent Chrome profiles outside the project database; remove/revoke that browser session separately when retiring an account. If a rights holder asks you to stop using their material, you can remove the project/source artifacts through the normal storage boundary.

## Configuration reference

| Variable | Default | Effect |
|---|---|---|
| `MS_REQUIRE_RIGHTS_DECLARATION` | `false` | Rights metadata remains auditable. Set `true` only for an intentional deployment that wants rights findings to block. |
| `MS_MAX_CONSECUTIVE_PANELS_PER_CHAPTER` | `8` | Panel volume warning threshold. |
