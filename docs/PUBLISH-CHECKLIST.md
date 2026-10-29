# HomeBase OS v1.0.0 publishing checklist

Use this before uploading the source, publishing documentation or making a GitHub Release. Preparing this kit does not perform any of those publishing actions.

## Source folder

- [ ] The repository root is the prepared `homebase-os/` folder, containing `Dockerfile`, Compose files, `app/`, `web/`, `scripts/`, `tests/`, `docs/` and `.github/workflows/`.
- [ ] The public README and terminal guide target Raspberry Pi OS Lite **64-bit** and Docker, with version **1.0.0**.
- [ ] No `.data/`, encrypted vault, master key, `certs/`, `.setup-ca/`, assistant workspace, `.env`, private credentials or personal screenshots are included.
- [ ] Hidden `.gitignore`, `.dockerignore` and workflow files are present. The source ZIP is not accidentally nested as the repository's only file.
- [ ] `scripts/setup-pi.sh` retains its executable bit, or users are instructed to run it with `sudo bash scripts/setup-pi.sh`.
- [ ] License, third-party attribution, security policy and contribution guide are included and have been reviewed.
- [ ] Optional ISP identification discloses both external services and its opt-in, 60-second change checks. A configured remote AI provider's data use is explained.

## Verification and claims

- [ ] Review [VALIDATION.md](VALIDATION.md) and update it only with checks actually completed for this source snapshot.
- [ ] Review Python/UI fixture tests and the GitHub CI result after upload.
- [ ] Record the ARM64/AMD64 Docker build result; retain an explicit unverified status if that build has not run or passed.
- [ ] Record real Pi/network and TV/camera pairing checks separately. Do not describe fixture tests as real hardware compatibility tests.
- [ ] TV protocol limits, physical pairing, cleartext-LAN opt-ins and camera credentials are documented in [TV.md](TV.md) and [integrations.md](integrations.md).
- [ ] Release notes identify a source distribution and explain Docker's first-build internet requirement. No prebuilt SD-card image, bundled AI model, universal-TV support, independent security audit or paid activation system is claimed.

## Repository upload

- [ ] The intended GitHub account, repository visibility and repository URL have been selected.
- [ ] Review `git status --short` and `git remote -v` before pushing. No token is embedded in the URL or source.
- [ ] The uploaded repository has its public README at the root and complete source directories.
- [ ] Existing remote work has been preserved; no force-push was used to hide an unexplained conflict.

## Optional documentation website

- [ ] Decide deliberately whether the documentation should be public.
- [ ] If publishing, set the repository's Pages source to **GitHub Actions** and manually run **Publish documentation** on `main`.
- [ ] Confirm that the Pages artifact is `docs/`, and that the published site is static documentation. The Python dashboard is not hosted there.
- [ ] Open the resulting documentation URL and check installation, integration, security and release information.

## Release assets

- [ ] The `v1.0.0` tag identifies the reviewed source commit.
- [ ] The final `homebase-os-v1.0.0-source.zip` contains the intended source under `homebase-os/`, including required hidden files.
- [ ] Regenerate the ZIP if source changed after preparation, then create a new checksum from the final ZIP.
- [ ] `homebase-os-v1.0.0-source.zip.sha256` verifies successfully against that exact ZIP.
- [ ] Attach both files to the draft GitHub Release, add truthful validation notes, and choose pre-release status if appropriate.
- [ ] Save the draft until ready; choose **Publish release** only when publication is intended.

Full steps and commands: [RELEASING.md](RELEASING.md).
