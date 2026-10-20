# Security and manuscript privacy

The MCP server reads and writes local manuscript databases. Connect it only to AI clients you trust. Tool responses, backups and the operation journal may contain complete unpublished text.

Use a synthetic reproduction when reporting an issue. Do not attach actual `Room.db`, `History.db`, backup ZIPs, account configuration, tokens or private manuscript excerpts to public issues.

For a vulnerability that could expose private data or bypass write protections, use the repository's private vulnerability reporting feature if enabled. Otherwise open a minimal public issue asking for a private reporting channel, without exploit details or sensitive data.

Supported public release: 2.0.x on Windows. Database compatibility is checked at runtime; unknown schemas and unsupported journal modes must fail closed. App updates can change the native schema, so keep independent backups.
