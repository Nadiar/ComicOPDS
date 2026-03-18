![](docs/img/ComicOPDS_Header.png)

# 📚 ComicOPDS (Community Fork)

> **Note:** This repository is an enhanced fork of the original [ComicOPDS by Frederik Baerentsen](https://gitea.baerentsen.space/FrederikBaerentsen/ComicOPDS). All credit for the core application goes to the original author. This community fork introduces several significant enhancements including full OPDS 2.0 support, background scanning, user administration, and trusted proxy support.

ComicOPDS is a lightweight **OPDS 1.2 and OPDS 2.0** server written in Python, designed for serving **CBZ comics** with metadata extracted from `ComicInfo.xml`.

It's optimized for large libraries (10k–100k+ comics), supports FastAPI + SQLite + FTS5 search, thumbnail caching, and page streaming (OPDS PSE 1.1 for OPDS 1.2, DiViNa manifests for OPDS 2.0).

Works great with [Panels for iOS](https://panels.app) and other OPDS readers.

---

## ✨ Features

**🚀 Added in this Fork:**
- **OPDS 2.0 Support:** Automatic content-negotiation serving OPDS 1.2 or 2.0 specs depending on the reader client's `Accept` header.
- **DiViNa Page Streaming (OPDS 2.0):** Native page-level streaming via Readium Web Publication Manifest (DiViNa profile), separate from OPDS PSE 1.1 used in OPDS 1.2 feeds.
- **Incremental Background Scanner:** Fast, non-destructive background scanning using file modification times, scheduled natively via Docker `cron`.
- **User Administration UI:** Manage application users via a visual dashboard interface.
- **Role-Based Access Control:** Separate standard "read-only" users from dashboard administrators.
- **Trusted Proxy Support:** Accurately handle CIDR-defined reverse proxy `X-Forwarded-For` and `X-Real-IP` headers.

**📦 Original Core Features:**

- 📂 Browse your folder hierarchy
- 🔍 Full-text search (title, series, writer, publisher, year, etc.)
- 📥 Download comics (CBZ)
- 📖 Page streaming (OPDS PSE 1.1)
- 🖼️ Thumbnail extraction & caching (from CBZ covers)
- 📊 Dashboard with stats & charts
- 🧠 Smart Lists (saved search filters)
- 🔐 Optional Basic Auth
- 🐋 Runs easily with Docker / Docker Compose
- ⚡ Fast indexing with SQLite FTS5
- 🔄 File system watching for auto-updates
- 📱 Mobile-optimized dashboard

<a href="https://www.buymeacoffee.com/frederikb" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="41" width="174"></a>

---

## 📱 Clients

**Supported Clients**

| App | Downloads | Search | Streaming | OPDS Version | Streaming Protocol |
|---|---|---|---|---|---|
| Panels (iOS)                 | ✔️  |✔️  |✔️  | OPDS 1.2 | PSE 1.1 |
| KyBook 3 (iOS)               | ✔️  | ✔️  | ❌  | OPDS 1.2 | — |
| Cantook (iOS)                | ✔️  | ❌  | ❌  | OPDS 1.2 | — |
| Marvin 3 (iOS)                 | ✔️  | ❌  | ❌  | OPDS 1.2 | — |
| Chunky (iOS)                 | ✔️  | ❌  | ❌  | OPDS 1.2 | — |

---

## 📋 Documentation

- 🚀 [Quick Start](docs/quickstart.md)
- 🔧 [Configuration](docs/configuration.md)
- 🌐 [API & Endpoints](docs/api.md)
- 📊 [Dashboard](docs/dashboard.md)
- 🧠 [Smart Lists](docs/smartlists.md)
- 🔍 [Search](docs/search.md)
- 📱 [Client Setup](docs/clients.md)
- 🎯 [Project Scope](docs/scope.md)
- 🛠️ [Troubleshooting](docs/troubleshooting.md)
- 📄 [License](license.md)

---

## 💪 Stress Test

ComicOPDS has been stress tested using **170k+ CBZ files** generated using [CBZGenerator](https://gitea.baerentsen.space/FrederikBaerentsen/CBZGenerator).

**Performance Results:**
- **Initial scan**: ~10 minutes for full library indexing
- **Thumbnail generation**: ~30 minutes (depending on hardware)
- **Hardware**: Tested on low-powered Intel N100 CPU with no performance issues
- **Search**: Very fast response times with SQLite FTS5 even at this scale

The server remains responsive during indexing and handles concurrent OPDS requests without degradation. Memory usage stays reasonable even with large libraries.

---

## 🔗 Links

- **Repository**: [Gitea](https://gitea.baerentsen.space/FrederikBaerentsen/ComicOPDS)
- **OPDS Specifications**:
  - [OPDS 1.2](https://specs.opds.io/opds-1.2)
  - [OPDS 2.0](https://specs.opds.io/opds-2.0)
- **OPDS Page Streaming Extension**: [OPDS PSE 1.1](https://anansi-project.github.io/docs/opds-pse/specs/v1.1)
- **Readium Web Publication Manifest**: [RWPM / DiViNa](https://readium.org/webpub-manifest/profiles/divina)
- **Buy Me a Coffee**: [frederikb](https://www.buymeacoffee.com/frederikb)

---

*Made with ❤️ for comic book enthusiasts*
