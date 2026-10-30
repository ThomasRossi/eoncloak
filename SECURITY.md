# Security policy

eoncloak is used to remove personal data from documents, so we treat these as security
issues:

- personal data left in the output by a systematic gap (a whole DOCX part or field skipped,
  a format of identifier never detected, the leak check failing to notice something);
- document text sent to a remote endpoint while `--local-only` is enabled;
- mapping files or document text written somewhere the user did not ask for (logs, temp
  files, caches).

## Reporting

Please **do not open a public issue**. Use GitHub's
[private vulnerability reporting](https://github.com/ThomasRossi/eoncloak/security/advisories/new)
("Security" tab → "Report a vulnerability"). Include a **synthetic**
document that reproduces the problem. Never send real personal data.

We aim to acknowledge reports within a week and to publish a fix and an advisory once it is
released.

## Supported versions

Only the latest release receives fixes while the project is in alpha.
