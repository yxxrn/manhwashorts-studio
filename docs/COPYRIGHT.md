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

### Rights declaration is mandatory

Every asset needs an owner and a licence basis before it can reach publication.
The check is stricter than a checkbox:

```python
# services/ingest.py
@property
def status(self) -> str:
    if not self.declared:
        return RightsStatus.UNDECLARED
    if self.license_type == LicenseType.UNKNOWN or not self.rights_owner.strip():
        return RightsStatus.UNDECLARED
    return RightsStatus.DECLARED
```

Ticking "I have the right" with no owner named leaves the asset `UNDECLARED`, and
`rights.undeclared_assets` blocks the render. This is deliberate: a checkbox
alone is not a record of anything.

Licence bases recognised: `owned`, `licensed`, `permission_granted`,
`public_domain`, `creative_commons`, `unknown`.

Note that "fair use" is **not** a licence type. The app will not let you tick a
box asserting fair use, because that is a legal conclusion a tool cannot make for
you.

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

### Public publishing is double-gated

```
MS_ALLOW_PUBLIC_PUBLISH=true     (config, requires a restart)
        AND
"confirm_public": true            (per request)
```

Both are required. The default is private. This exists so an automation bug
cannot publish to your channel — the failure mode of an over-eager script is an
unlisted video, not a strike.

### Everything is audited

`AuditLog` records asset uploads with their rights status, script approvals with
the approver, warning overrides with the stated reason, renders, and uploads.
If you ever need to show your process, the record exists.

## What the software refuses to do

- **No scraping.** There is no code path that fetches material from a manhwa
  site. Material enters only through your upload. This is a structural choice,
  not a setting.
- **No watermark removal.** Not implemented, and not a feature request that will
  be accepted.
- **No auto-publish.** Every video requires an explicit approval action.
- **No training on your material.** Your uploads are never used to train models.
  With `MS_LLM_PROVIDER=rules` (the default) nothing leaves your machine at all.

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

`DELETE /api/projects/{id}` removes the project and every blob no other project
references. Disconnecting a channel erases its stored OAuth tokens immediately.
If a rights holder asks you to stop using their material, you can comply
completely and quickly.

## Configuration reference

| Variable | Default | Effect |
|---|---|---|
| `MS_REQUIRE_RIGHTS_DECLARATION` | `true` | The primary safeguard. Setting `false` logs a warning and weakens the gate — do not do this on a channel you care about. |
| `MS_MAX_CONSECUTIVE_PANELS_PER_CHAPTER` | `8` | Panel volume warning threshold. |
| `MS_ALLOW_PUBLIC_PUBLISH` | `false` | Must be `true` before any public upload is possible. |
