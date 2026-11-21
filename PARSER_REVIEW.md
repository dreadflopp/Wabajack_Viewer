# Parser behavior and format notes

The shared `PackageModel` validates manifests and retains raw authored fields.
It rejects unsafe/duplicate destinations and reports unsupported instructions,
missing payloads, missing archives/patches, incomplete generated ingredients and
source cycles. Generated archives resolve through TempID/FileStates to temporary
ingredients and original downloads; merged patches resolve through source
destinations. Nested archive chains are preserved.

Generated archives, remapped files, transformed textures and unsupported outputs
remain unverified. Source provenance does not prove installed byte equality.
In particular, generated archive hashes can be nondeterministic; a different hash
does not establish a verification failure.

Format references used during implementation:

- [Compatibility aliases](https://github.com/wabbajack-tools/wabbajack/blob/main/Wabbajack.DTOs.Test/Compatibility/TypeNameTests.cs)
- [CreateBSA](https://github.com/wabbajack-tools/wabbajack/blob/main/Wabbajack.DTOs/ModList/Directives/CreateBSA.cs)
- [RemappedInlineFile](https://github.com/wabbajack-tools/wabbajack/blob/main/Wabbajack.DTOs/ModList/Directives/RemappedInlineFile.cs)
- [TransformedTexture](https://github.com/wabbajack-tools/wabbajack/blob/main/Wabbajack.DTOs/ModList/Directives/TransformedTexture.cs)

Installed BSA/BA2 member-byte verification, plugin dependency analysis and
deployment/conflict resolution remain outside the app's scope. Unknown formats
are retained and diagnosed; compatibility with unseen extensions is not assumed.
Profile text difference previews are limited to 512 KiB.

See [README.md](README.md) and [UI_WORKFLOW.md](UI_WORKFLOW.md) for current controls
and supported workflows. Regression tests use temporary synthetic fixtures.
