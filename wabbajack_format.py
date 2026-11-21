"""Shared Wabbajack format rules, without UI or inventory dependencies."""

class ComparisonError(ValueError):
    pass


def directive_type(value):
    """Accept Wabbajack's short names and legacy assembly-qualified aliases."""
    return str(value or "").split(",", 1)[0].strip()


def downloader_type(value):
    name = directive_type(value).split("+", 1)[0]
    return {"NexusDownloader": "Nexus", "HttpDownloader": "Http",
            "WabbajackCDNDownloader": "WabbajackCDN", "GameFileSourceDownloader": "GameFileSource"}.get(name, name)


def unverified_reason(spec):
    kind = directive_type(spec.get("type"))
    reasons = {"CreateBSA": "Generated archive: Wabbajack does not guarantee identical output bytes.",
               "RemappedInlineFile": "Paths are substituted for the installation machine.",
               "TransformedTexture": "Transformed texture: Wabbajack does not guarantee identical output bytes.",
               "ArchiveMeta": "Download metadata is adjusted during installation."}
    if kind in reasons:
        return reasons[kind]
    if kind not in {"InlineFile", "FromArchive", "PatchedFromArchive", "MergedPatch", "PropertyFile"}:
        return f"Unsupported directive type: {kind or '(missing)'}."
    if not spec.get("hash"):
        return "No final file hash is available."
    return ""


def package_path(raw):
    path = str(raw).replace("\\", "/")
    parts = path.split("/")
    if not path or any(not p or p in (".", "..") or ":" in p or any(ord(c) < 32 for c in p) for p in parts):
        raise ComparisonError(f"Unsafe Wabbajack destination: {raw!r}")
    return path


