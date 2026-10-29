# Third-party notices

The MIT application license does not relicense third-party artwork, datasets, trademarks or dependencies. Company names and logos identify a selected or detected device/provider; they imply no affiliation or endorsement.

## Brand artwork

Transparent SVGs are served from the application itself. Their source URLs, source revisions and any light variants are listed in [web/assets/brands/CREDITS.md](web/assets/brands/CREDITS.md). Most source paths are from Simple Icons under CC0-1.0; manufacturer/provider trademarks remain with their owners. The Exetel asset is sourced from its official website, with its original geometry preserved. Unknown names use a generic icon rather than an unrelated company's mark.

## Manufacturer registry

The router prefix table is a selected extract of the public [IEEE MA-L registry](https://standards-oui.ieee.org/oui/oui.csv). Source and generation metadata are retained in `app/router_ouis.py`. Registry names identify registered manufacturers and may differ from retail brands; they are not an authorization mechanism. No MIT license claim is made for IEEE's registry or manufacturers' names.

## Runtime dependencies

Python is distributed under its PSF license. The container base and Debian packages retain their upstream licenses. The following direct dependencies retain their own notices; project metadata is linked for review.

| Package | Upstream license |
| --- | --- |
| [cryptography](https://pypi.org/project/cryptography/) | Apache-2.0 OR BSD-3-Clause |
| [androidtvremote2](https://pypi.org/project/androidtvremote2/) | Apache-2.0 |
| [pyatv](https://pypi.org/project/pyatv/) | MIT |
| [websockets](https://pypi.org/project/websockets/) | BSD-3-Clause |
| [zeroconf](https://pypi.org/project/zeroconf/) | LGPL-2.1-or-later |

Transitive dependencies retain their own terms. In particular, [miniaudio's Python package](https://pypi.org/project/miniaudio/) reports MIT and may build from source on ARM64. Dependency installation/building happens in the Docker image stages; no package is claimed as original application code. Consult the installed distributions' license files when redistributing a built image.

## GitHub Actions

Workflow actions are referenced at pinned upstream commits. Their source and licensing remain with the `actions` and `docker` projects linked in the workflow files. The workflows build/test source and optionally publish static documentation; they do not publish a Docker image or expose a running dashboard.
