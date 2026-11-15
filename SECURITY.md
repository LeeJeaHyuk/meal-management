# Security and data boundary

Keep real journals, meal/weight values, images, aliases, conversations, generated reports,
credentials, host profiles and integration state outside this code repository.
Cloud catalog agents use only public nutrition data; they must not inspect a personal journal.

This is a local domain library, not an authorization server or hostile-code sandbox.
Filesystem path guards and role rules do not isolate arbitrary processes under one OS account.
The caller must enforce role, provider, input scope and recipient permissions.

Do not send personal data to a cloud model merely because a CLI can return it.
Public ratio exports strip raw fields but can still expose personal patterns.
Generate a file first, inspect it locally, and let the user choose its sharing scope.

Nutrition source verification, OCR interpretation and human confirmation remain outside the
structural validator. A verified catalog record is a review assertion by an authorized caller.
Missing values are unknown, not zero. Consumption estimates are not medical advice.

Do not attach real records, keys, database files, private paths or full logs to public issues.
Use technical markers and an independently reproducible minimal case instead.
