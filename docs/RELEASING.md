# Release HomeBase OS v1.0.0

This guide prepares a GitHub repository, an optional public documentation website and a downloadable source release. The application itself runs on the user's Raspberry Pi with Docker. These steps do not publish a running dashboard.

## 1. Open the release kit

The prepared kit uses this layout:

```text
HOMEBASE GitHub Release Kit/
├── START HERE.html
├── homebase-os/                         ← repository source
├── homebase-os-v1.0.0-source.zip         ← downloadable source
└── homebase-os-v1.0.0-source.zip.sha256  ← matching checksum
```

Use `homebase-os/` as the repository root. It contains the Dockerfile, Compose files, application, local assets, tests, guides, license and `.github/workflows/`. The ZIP is source code, not a prebuilt container or Raspberry Pi SD-card image. No account, password, private CA, integration credentials or Ollama model is included.

Before uploading, work through [PUBLISH-CHECKLIST.md](PUBLISH-CHECKLIST.md). Keep the prepared source separate from a folder where you have already run the application.

## 2. Create an empty GitHub repository

Sign in to your own GitHub account and use **New repository**. Choose a name such as `homebase-os`, a description and your intended visibility. Leave automatic README, license and `.gitignore` creation off: this source already contains them. Copy the repository's HTTPS or SSH Git URL. [GitHub's repository guide](https://docs.github.com/en/get-started/start-your-journey/creating-a-repository-for-your-project-on-github) explains the form.

Do not paste a personal access token into the repository URL, a source file or a shell command. Use your existing Git credential helper or SSH authentication.

## 3. Upload the source

Git is the recommended route because it includes required hidden files. In a terminal opened in the kit's `homebase-os` folder:

```sh
git init
git add .
git update-index --chmod=+x scripts/setup-pi.sh
git status --short
```

Review the list before continuing. It should contain source and documentation, including `.github/workflows/`, `.gitignore` and `.dockerignore`. It must not include runtime data, private keys, credentials or personal screenshots.

```sh
git commit -m "Release HomeBase OS v1.0.0"
git branch -M main
git remote add origin YOUR_REPOSITORY_URL
git remote -v
git push -u origin main
```

Replace `YOUR_REPOSITORY_URL` with the copied repository URL. The push sends these reviewed files to GitHub. If an `origin` already exists, inspect it with `git remote -v`; use `git remote set-url origin YOUR_REPOSITORY_URL` only when you intend to replace that remote. Do not force-push to solve an unexpected history conflict. See [GitHub's remote repository documentation](https://docs.github.com/en/get-started/git-basics/managing-remote-repositories).

If you use GitHub's **Add file → Upload files** instead, upload the contents of `homebase-os/` into the repository root. Confirm that the hidden workflow and ignore files arrived. A ZIP attached to a release does not create an editable source repository by itself.

Browser uploads can lose executable file permissions. The Git command above records the setup script's executable bit. A Pi user can also launch it explicitly with `sudo bash scripts/setup-pi.sh` when that bit is absent.

Before the first release, replace the clone-command placeholder `YOUR_REPOSITORY_URL` in the public README and installation guides with your own repository URL. Keep documentation templates in `docs/public/` in sync if you use the exporter later.

## 4. Review verification results

Open the repository's **Actions** tab. **Verify source and images** runs fixture-based tests and ARM64/AMD64 Docker builds without pushing an image. Check the completed results before making a stable compatibility claim. See [VALIDATION.md](VALIDATION.md) for the checks actually performed while preparing this release.

The development environment did not have Docker or a physical Raspberry Pi/TV. Local test results establish controlled behavior; they do not establish a working ARM64 image or every device's compatibility. Keep those limits in release notes until you have completed the corresponding checks. You can save a draft, or label an early public release as a pre-release.

## 5. Publish the documentation website, if wanted

The repository's `docs/index.html` is a static website. Publishing it makes the documentation publicly reachable; the authenticated dashboard continues to run on the owner's LAN.

1. Open **Settings → Pages** in the repository.
2. Under the publishing source, select **GitHub Actions**.
3. Open **Actions → Publish documentation → Run workflow** and select `main`.
4. Review the successful run and the Pages URL shown by GitHub.

On a standard `OWNER.github.io/REPOSITORY/` Pages URL, the website automatically adds working GitHub source and ZIP download buttons. It makes no background request to derive those links. For a custom domain, set the `homebase-repository` meta tag in `docs/index.html` to your repository HTTPS URL before publishing. The ZIP button downloads the current `main` source; the tagged release assets remain separate.

The supplied workflow runs only when manually requested. Source pushes do not trigger that Pages workflow. Do not select the application `web/` directory as a replacement for the documentation: GitHub Pages does not run the Python backend. See [GitHub's publishing-source guide](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

## 6. Tag the reviewed v1.0.0 source

From `homebase-os/`, after confirming that the working tree contains the version you intend to release:

```sh
git status --short
git tag -a v1.0.0 -m "HomeBase OS v1.0.0"
git push origin v1.0.0
```

Use a new version/tag for subsequent releases. Preserve a published release's source and checksum; do not move its tag to silently replace the code.

## 7. Check the source ZIP

The kit already includes a ZIP and checksum. On a Mac, run this from the kit folder containing those two files:

```sh
shasum -a 256 -c homebase-os-v1.0.0-source.zip.sha256
```

On Linux:

```sh
sha256sum -c homebase-os-v1.0.0-source.zip.sha256
```

If you changed source after the kit was prepared, regenerate the ZIP from the reviewed tag so the release and archive agree. From the repository folder:

```sh
git archive --format=zip --prefix=homebase-os/ \
  --output=../homebase-os-v1.0.0-source.zip v1.0.0
cd ..
shasum -a 256 homebase-os-v1.0.0-source.zip > homebase-os-v1.0.0-source.zip.sha256
```

On Linux, use `sha256sum` instead of `shasum -a 256` for the last command. Hash the final ZIP once it is complete, then distribute that exact ZIP with its matching checksum. The archive includes only committed files; make sure all intended source was committed before tagging.

## 8. Create the GitHub Release

Open **Releases → Draft a new release**, select the `v1.0.0` tag and title it **HomeBase OS v1.0.0**. Add release notes and attach:

- `homebase-os-v1.0.0-source.zip`
- `homebase-os-v1.0.0-source.zip.sha256`

Save a draft until the source, assets and validation statements are ready. Choose **Publish release** yourself when you intend to make the release available. GitHub's automatically generated source archives are separate files; the supplied checksum matches the specifically attached ZIP. The official [release-management guide](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository) covers these controls.

Suggested release notes:

```text
HomeBase OS v1.0.0

Local-first home command centre for Raspberry Pi OS Lite 64-bit and Docker.
Includes the responsive dashboard, supported discovery/TV/camera integrations,
server metrics, Home Assistant controls and an approval-based AI assistant.

Install: extract the source ZIP, read START-HERE.md, then run the interactive
Pi setup from the source folder. Supply your own account, pairing and credentials.

Validation: see docs/VALIDATION.md. Physical Pi, Docker image and device checks
should be reported only when actually completed. Compatibility varies by model.

The download is source code. Docker's first image build needs internet access.
There is no bundled model server, SD-card image, public dashboard or activation key.
```

## 9. Install and update on a Pi

Users follow [INSTALL.md](INSTALL.md), create their own administrator account, trust their own local CA and pair authorized devices. For an existing deployment, preserve its Docker volumes, `certs/` and `.setup-ca/`; replace only source files, review the new guide, then rebuild:

```sh
sudo docker compose -f compose.yaml -f compose.lan.yaml up -d --build
```

Do not use `down -v` as an update command. See [operations and backups](operations.md).

## License and future paid features

This release contains the MIT-licensed core. The [license](../LICENSE) permits commercial redistribution with its notice conditions; [third-party notices](../THIRD_PARTY_NOTICES.md) still apply. No checkout, payment service, license server or activation/unlock system is implemented in v1.0.0. Any future commercial feature or licensing service needs its own design and implementation.
